"""
Orbbec RGB 相机封装。

无 SDK / use_mock 时用彩色测试图；真机优先 pyorbbecsdk（按 serial），
失败再试 OpenCV（限时，禁止堵死 HMI 线程）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

log = logging.getLogger(__name__)

_OPEN_TIMEOUT_S = 8.0
_NODE_TRY_S = 1.2

# 已占用的 serial / 节点，防止空 serial 的 cam1 去抢已经打开的 cam2
_claimed_serials: dict[str, str] = {}
_claimed_nodes: dict[str, str] = {}


def _looks_orbbec_serial(serial: str) -> bool:
    """Orbbec 序列号类似 CV27561000FH；0001 这种是普通 UVC 摄像头。"""
    s = (serial or "").strip()
    if len(s) < 6:
        return False
    return any(c.isalpha() for c in s) and any(c.isdigit() for c in s)


def _by_id_matches_serial(name: str, serial: str) -> bool:
    sn = (serial or "").strip()
    if not sn:
        return False
    if _looks_orbbec_serial(sn):
        return sn in name
    token = f"_{sn}-"
    return token in name or name.endswith(sn) or f"_{sn}." in name


def _is_meta_v4l(path: str, *, orbbec: bool = False) -> bool:
    """
    Orbbec Gemini：by-id 的 video-index0/1 是元数据，read() 会卡死。
    普通 UVC（如 04f2 笔记本摄像头）：index0 才是画面，index1 才是元数据。
    """
    name = Path(path).name.lower()
    if orbbec:
        return "video-index0" in name or "video-index1" in name
    return "video-index1" in name


def _resolve_node(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _unique_existing(scored: list[tuple[int, str]], *, orbbec: bool) -> list[str]:
    uniq: list[str] = []
    for _, path in scored:
        if path in uniq or not Path(path).exists():
            continue
        if _is_meta_v4l(path, orbbec=orbbec):
            continue
        uniq.append(path)
    return uniq


def _v4l_node_score(path: str, *, orbbec: bool = False) -> int:
    """越小越优先。"""
    name = path.lower()
    if orbbec:
        if "video-index2" in name:
            return 0
        if "video-index4" in name:
            return 1
        if "video-index0" in name or "video-index1" in name:
            return 80
        if "video-index" in name:
            return 40
        return 20
    if "video-index0" in name or name.endswith("video0"):
        return 0
    if name.startswith("video") and name[5:].isdigit() and int(name[5:]) == 0:
        return 0
    if "video-index1" in name or name.endswith("video1"):
        return 90
    return 20


def _color_frame_to_bgr(color) -> Optional[object]:
    """
    Gemini 默认彩色常为 YUYV（每像素 2 字节），不能按 RGB 三通道 reshape。
    按 format / 数据长度转成 OpenCV BGR。
    """
    if color is None or cv2 is None:
        return None
    w = int(color.get_width())
    h = int(color.get_height())
    data = np.frombuffer(color.get_data(), dtype=np.uint8)
    n = int(data.size)
    if w < 1 or h < 1 or n < 1:
        return None

    fmt = None
    fmt_name = ""
    try:
        fmt = color.get_format()
        fmt_name = str(fmt).upper()
    except Exception:
        pass

    try:
        from pyorbbecsdk import OBFormat  # type: ignore
    except Exception:
        OBFormat = None  # type: ignore

    try:
        if OBFormat is not None and fmt is not None:
            if fmt == OBFormat.RGB:
                return cv2.cvtColor(data.reshape((h, w, 3)), cv2.COLOR_RGB2BGR)
            if fmt == OBFormat.BGR:
                return data.reshape((h, w, 3))
            if fmt == OBFormat.YUYV:
                return cv2.cvtColor(data.reshape((h, w, 2)), cv2.COLOR_YUV2BGR_YUYV)
            if fmt == getattr(OBFormat, "YUY2", None):
                return cv2.cvtColor(data.reshape((h, w, 2)), cv2.COLOR_YUV2BGR_YUY2)
            if fmt == OBFormat.UYVY:
                return cv2.cvtColor(data.reshape((h, w, 2)), cv2.COLOR_YUV2BGR_UYVY)
            if fmt == OBFormat.MJPG:
                return cv2.imdecode(np.frombuffer(color.get_data(), dtype=np.uint8), cv2.IMREAD_COLOR)
            if fmt == getattr(OBFormat, "RGBA", None):
                return cv2.cvtColor(data.reshape((h, w, 4)), cv2.COLOR_RGBA2BGR)
            if fmt == getattr(OBFormat, "BGRA", None):
                return cv2.cvtColor(data.reshape((h, w, 4)), cv2.COLOR_BGRA2BGR)
    except Exception:
        pass

    if n == h * w * 3:
        img = data.reshape((h, w, 3))
        if "BGR" in fmt_name and "RGB" not in fmt_name:
            return img
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if n == h * w * 2:
        img = data.reshape((h, w, 2))
        if "UYVY" in fmt_name:
            return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_UYVY)
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_YUY2)
    if n == h * w * 4:
        img = data.reshape((h, w, 4))
        if "BGRA" in fmt_name:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    if n >= 4 and int(data[0]) == 0xFF and int(data[1]) == 0xD8:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    return None


def _depth_frame_to_mm(frames) -> Optional[object]:
    """从 Orbbec frames 取深度，单位毫米。失败返回 None。"""
    if frames is None:
        return None
    try:
        depth = frames.get_depth_frame()
    except Exception:
        depth = None
    if depth is None:
        return None
    try:
        w = int(depth.get_width())
        h = int(depth.get_height())
        raw = np.frombuffer(depth.get_data(), dtype=np.uint16)
        if w < 1 or h < 1 or raw.size < w * h:
            return None
        arr = raw.reshape((h, w)).astype(np.float32)
        scale = 1.0
        try:
            scale = float(depth.get_depth_scale())
        except Exception:
            pass
        if scale <= 0:
            scale = 1.0
        # SDK 常见：scale=1 已是 mm；scale≈0.001 则是米
        if scale < 0.1:
            arr = arr * (scale * 1000.0)
        else:
            arr = arr * scale
        return arr
    except Exception:
        return None


class OrbbecCamera:
    def __init__(self, name: str, index: int = 0, serial: str = "", use_mock: bool = True):
        self.name = name
        self.index = index
        self.serial = serial
        self.use_mock = use_mock
        self._cap = None
        self._pipeline = None
        self._ob_ctx = None
        self.opened = False
        self.last_error = ""
        self._opening = False
        self._open_token = 0
        self._open_lock = threading.Lock()
        self._grab_lock = threading.Lock()
        self.last_depth = None  # 最近一帧深度 mm（无深度则为 None）
        self.last_color = None  # 最近一帧彩色 BGR

    @property
    def opening(self) -> bool:
        return bool(self._opening)

    def open(self) -> bool:
        """同步打开（带超时）。HMI 请用 open_async，避免无响应。"""
        if self.use_mock:
            self.opened = True
            self.last_error = ""
            log.info("[%s] Mock 相机 serial=%s index=%s", self.name, self.serial, self.index)
            return True
        return self._open_with_timeout(_OPEN_TIMEOUT_S)

    def open_async(self) -> None:
        """后台打开，不阻塞界面。"""
        if self.use_mock:
            self.open()
            return
        with self._open_lock:
            if self._opening or self.opened:
                return
            self._opening = True
            self.last_error = "正在连接…"

        def _run() -> None:
            try:
                self._open_with_timeout(_OPEN_TIMEOUT_S)
            finally:
                self._opening = False

        threading.Thread(target=_run, daemon=True, name=f"open-{self.name}").start()

    def _open_with_timeout(self, timeout_s: float) -> bool:
        holder: dict = {"ok": False}
        done = threading.Event()
        self._open_token += 1
        token = self._open_token

        def _run() -> None:
            try:
                ok = bool(self._open_impl())
                if token != self._open_token:
                    if ok:
                        try:
                            self.close()
                        except Exception:
                            pass
                    return
                holder["ok"] = ok
            except Exception as e:
                if token == self._open_token:
                    self.last_error = str(e)
                    holder["ok"] = False
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True, name=f"open-impl-{self.name}")
        t.start()
        if not done.wait(timeout=float(timeout_s)):
            self._open_token += 1
            self.opened = False
            self.last_error = (
                f"打开超时 {timeout_s:.0f}s（serial={self.serial or '-'} "
                f"index={self.index}）。可能选错 /dev/video，或未装 pyorbbecsdk。"
            )
            log.error("[%s] %s", self.name, self.last_error)
            return False
        return bool(holder.get("ok"))

    def _claim(self, serial: str = "", node: str = "") -> None:
        sn = (serial or "").strip()
        if sn:
            _claimed_serials[sn] = self.name
        if node:
            _claimed_nodes[_resolve_node(node)] = self.name

    def _unclaim(self) -> None:
        for d in (_claimed_serials, _claimed_nodes):
            for k, v in list(d.items()):
                if v == self.name:
                    del d[k]

    def _serial_taken_by_other(self, serial: str) -> Optional[str]:
        sn = (serial or "").strip()
        if not sn:
            return None
        owner = _claimed_serials.get(sn)
        if owner and owner != self.name:
            return owner
        return None

    def _open_impl(self) -> bool:
        self.close()
        self.last_error = ""
        if self._open_orbbec():
            return True
        if self._open_opencv():
            return True
        if not self.last_error:
            sn = (self.serial or "").strip() or f"index={self.index}"
            self.last_error = f"无法打开相机 {sn}（无 SDK 画面且 OpenCV 失败）"
        self.opened = False
        log.warning("[%s] %s", self.name, self.last_error)
        return False

    def _open_orbbec(self) -> bool:
        sn = (self.serial or "").strip()
        if not _looks_orbbec_serial(sn):
            return False
        taken = self._serial_taken_by_other(sn)
        if taken:
            self.last_error = f"serial={sn} 已被「{taken}」占用，不能两路同时打开同一台"
            log.warning("[%s] %s", self.name, self.last_error)
            return False
        try:
            from pyorbbecsdk import Config, Context, OBSensorType, Pipeline  # type: ignore
        except Exception as e:
            self.last_error = f"未安装 pyorbbecsdk，将试 OpenCV: {e}"
            log.warning("[%s] %s", self.name, self.last_error)
            return False
        pipe = None
        ctx = None
        try:
            ctx = Context()
            device_list = ctx.query_devices()
            n = int(device_list.get_count()) if device_list is not None else 0
            if n < 1:
                self.last_error = "未发现 Orbbec 设备（查 USB/驱动）"
                return False
            device = self._device_by_serial(device_list, sn, n)
            if device is None:
                self.last_error = f"找不到 serial={sn}（已枚举 {n} 台）"
                return False
            pipe = Pipeline(device)
            cfg = Config()
            profile_list = pipe.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
            color_profile = profile_list.get_default_video_stream_profile()
            cfg.enable_stream(color_profile)
            pipe.start(cfg)
            self._ob_ctx = ctx
            self._pipeline = pipe
            self.opened = True
            self.last_error = ""
            info = device.get_device_info()
            got_sn = str(info.get_serial_number() or sn)
            self._claim(got_sn)
            log.info(
                "[%s] Orbbec 已打开 name=%s serial=%s pid=0x%04X",
                self.name,
                info.get_name(),
                got_sn,
                int(info.get_pid()),
            )
            return True
        except Exception as e:
            self.last_error = f"Orbbec SDK 打开失败: {e}"
            log.warning("[%s] %s", self.name, self.last_error)
            if pipe is not None:
                try:
                    pipe.stop()
                except Exception:
                    pass
            self._pipeline = None
            self._ob_ctx = None
            return False

    @staticmethod
    def _device_by_serial(device_list, serial: str, count: int):
        serial = str(serial).strip()
        try:
            return device_list.get_device_by_serial_number(serial)
        except Exception:
            pass
        for i in range(count):
            try:
                got = str(device_list.get_device_serial_number_by_index(i) or "")
                if got == serial:
                    return device_list.get_device_by_index(i)
            except Exception:
                continue
        return None

    def _v4l_candidates(self) -> list[str]:
        """普通 UVC 用 video0；Orbbec 只用彩色口（index2/4）。"""
        sn = (self.serial or "").strip()
        orbbec = _looks_orbbec_serial(sn)
        scored: list[tuple[int, str]] = []
        by_id = Path("/dev/v4l/by-id")
        if by_id.is_dir() and sn:
            for p in by_id.iterdir():
                if not _by_id_matches_serial(p.name, sn):
                    continue
                if _is_meta_v4l(p.name, orbbec=orbbec):
                    continue
                real = _resolve_node(str(p))
                if _is_meta_v4l(real, orbbec=orbbec):
                    continue
                owner = _claimed_nodes.get(real)
                if owner and owner != self.name:
                    continue
                scored.append((_v4l_node_score(p.name, orbbec=orbbec), real))
            scored.sort(key=lambda x: x[0])
            found = _unique_existing(scored, orbbec=orbbec)
            if found:
                return found
        try:
            idx = int(self.index)
        except Exception:
            idx = -1
        if idx >= 0:
            if orbbec and idx in (0, 1):
                idx = -1
        if idx >= 0:
            idx_path = f"/dev/video{idx}"
            if Path(idx_path).exists() and not _is_meta_v4l(idx_path, orbbec=orbbec):
                real = _resolve_node(idx_path)
                owner = _claimed_nodes.get(real)
                if not owner or owner == self.name:
                    scored.append((_v4l_node_score(idx_path, orbbec=orbbec), real))
        scored.sort(key=lambda x: x[0])
        return _unique_existing(scored, orbbec=orbbec)

    @staticmethod
    def _v4l_capture_src(path: str):
        """OpenCV 5 用节点号比 by-id 符号链接更稳。"""
        name = Path(path).name
        if name.startswith("video") and name[5:].isdigit():
            return int(name[5:])
        return path

    def _try_opencv_node(self, path: str, timeout_s: float) -> Optional[object]:
        """限时打开单个节点；打不开或读不出帧则返回 None（不堵死总超时）。"""
        if _is_meta_v4l(path, orbbec=_looks_orbbec_serial(self.serial)):
            log.warning("[%s] 拒绝元数据口 %s", self.name, path)
            return None
        holder: dict = {"cap": None}
        done = threading.Event()

        def _run() -> None:
            cap = None
            try:
                cap = cv2.VideoCapture(self._v4l_capture_src(path), cv2.CAP_V4L2)
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    return
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.release()
                    return
                holder["cap"] = cap
            except Exception:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            finally:
                done.set()

        t = threading.Thread(target=_run, daemon=True, name=f"v4l-{Path(path).name}")
        t.start()
        if not done.wait(timeout=float(timeout_s)):
            log.warning("[%s] 跳过超时节点 %s", self.name, path)
            return None
        return holder.get("cap")

    def _open_opencv(self) -> bool:
        if cv2 is None:
            if not self.last_error:
                self.last_error = "未安装 opencv-python"
            return False
        cands = self._v4l_candidates()
        if not cands:
            sn = (self.serial or "").strip()
            if _looks_orbbec_serial(sn):
                self.last_error = (
                    f"没有可用的 Orbbec 彩色节点（serial={sn}）。"
                    "请确认该序列号的 video-index2 存在，且没被别的相机占用。"
                )
            else:
                self.last_error = (
                    "这是普通 USB 摄像头（不是 Orbbec）。"
                    "serial 填 0001 或留空，index 填彩色口编号，你这台一般是 0（/dev/video0），"
                    "不要填 1。填好后点「写入serial并重开」，再取消 Mock。"
                )
            log.warning("[%s] %s", self.name, self.last_error)
            return False
        tried: list[str] = []
        for path in cands:
            tried.append(path)
            cap = self._try_opencv_node(path, _NODE_TRY_S)
            if cap is None:
                continue
            self._cap = cap
            self.opened = True
            self.last_error = ""
            self._claim((self.serial or "").strip(), path)
            log.info("[%s] OpenCV 已打开 %s", self.name, path)
            return True
        hint = "、".join(tried[:8]) if tried else "无匹配节点"
        self.last_error = (
            f"OpenCV 无法打开彩色画面（serial={self.serial or '-'} 已试: {hint}）。"
        )
        return False

    def grab(self, wait_s: float = 0.0) -> Optional[object]:
        if self._opening:
            return None
        if not self.opened:
            return None
        if self.use_mock:
            try:
                img = np.zeros((480, 640, 3), dtype=np.uint8)
                img[:] = (40, 40, 40)
                if cv2 is not None:
                    cv2.putText(
                        img,
                        f"MOCK {self.name}",
                        (40, 240),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1.0,
                        (0, 255, 255),
                        2,
                    )
                self.last_color = img
                return img
            except Exception:
                return None
        wait_s = float(wait_s or 0.0)
        if wait_s > 0:
            got = self._grab_lock.acquire(timeout=wait_s)
        else:
            got = self._grab_lock.acquire(blocking=False)
        if not got:
            return None
        try:
            if self._pipeline is not None:
                frames = self._pipeline.wait_for_frames(200 if wait_s > 0 else 80)
                if not frames:
                    return None
                color = frames.get_color_frame()
                if not color:
                    return None
                img = _color_frame_to_bgr(color)
                if img is None:
                    log.warning(
                        "[%s] 彩色帧无法转 BGR %sx%s",
                        self.name,
                        color.get_width(),
                        color.get_height(),
                    )
                    return None
                self.last_depth = _depth_frame_to_mm(frames)
                self.last_error = ""
                self.last_color = img
                return img
            if self._cap is not None and cv2 is not None:
                ok, frame = self._cap.read()
                if ok:
                    self.last_color = frame
                return frame if ok else None
            return None
        except Exception as e:
            log.warning("[%s] 取图异常: %s", self.name, e)
            return None
        finally:
            self._grab_lock.release()

    def close(self) -> None:
        self._unclaim()
        if self._cap is not None and cv2 is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass
            self._pipeline = None
        self._ob_ctx = None
        self.opened = False

    @property
    def connected(self) -> bool:
        return bool(self.opened) or bool(self.use_mock)

    def refresh_link(self) -> bool:
        if self.use_mock:
            self.opened = True
            return True
        if self._opening:
            return False
        if self._cap is not None and cv2 is not None:
            self.opened = bool(self._cap.isOpened())
            return self.opened
        return bool(self.opened)

    def reconnect(self) -> bool:
        if self.use_mock:
            return self.open()
        if self._opening:
            return False
        if self.opened:
            return True
        self.open_async()
        return False


def enumerate_devices_text() -> str:
    """给 HMI 用：列出 Orbbec serial 和 V4L by-id，便于填 yaml。"""
    lines: list[str] = []
    try:
        from pyorbbecsdk import Context  # type: ignore

        ctx = Context()
        device_list = ctx.query_devices()
        n = int(device_list.get_count()) if device_list is not None else 0
        lines.append(f"Orbbec SDK 枚举到 {n} 台：")
        if n <= 0:
            lines.append("  （无设备。检查 USB / udev / 是否被占用）")
        for i in range(n):
            sn = "?"
            name = ""
            try:
                sn = str(device_list.get_device_serial_number_by_index(i) or "?")
            except Exception:
                pass
            try:
                dev = device_list.get_device_by_index(i)
                info = dev.get_device_info()
                name = str(info.get_name() or "")
            except Exception:
                pass
            lines.append(f"  [{i}] serial={sn}  {name}".rstrip())
    except Exception as e:
        lines.append(f"Orbbec SDK 不可用：{e}")

    by_id = Path("/dev/v4l/by-id")
    if by_id.is_dir():
        entries = sorted(by_id.iterdir())
        lines.append(f"V4L by-id（{len(entries)}）：")
        for p in entries:
            try:
                real = p.resolve()
            except Exception:
                real = p
            mark = "  ← 跳过(index0/1会卡住)" if "video-index0" in p.name or "video-index1" in p.name else ""
            if "video-index2" in p.name.lower() or "video-index4" in p.name.lower():
                mark = "  ← 彩色口"
            lines.append(f"  {p.name} → {real}{mark}")
    else:
        lines.append("没有 /dev/v4l/by-id")
    return "\n".join(lines)
