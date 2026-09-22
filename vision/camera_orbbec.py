"""
Orbbec RGB-D 相机封装。

无 SDK / use_mock 时用彩色测试图 + 模拟深度。真机默认 RSDT orbbec_camera
（vision.orbbec_backend=rsdt）；失败再本仓 pyorbbecsdk / OpenCV。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from vision.numpy_compat import np
from vision.orbbec_backend import normalize_orbbec_backend
from vision.depth_vis import colorize_depth_mm, depth_stats_text, mock_depth_mm, mock_depth_vis_bgr

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

log = logging.getLogger(__name__)

_OPEN_TIMEOUT_S = 8.0
_NODE_TRY_S = 1.2
# 官方例程 wait_for_frames(100~1000)。8ms 会拿到只有彩色的 frameset，深度永远是空的。
_PIPELINE_WAIT_MS = 100
_PIPELINE_WAIT_MS_COLOR = 20
# HMI 流线程不必跟满相机硬件帧率；太高会占满 GIL，界面显示「无响应」
_HMI_STREAM_MAX_FPS = 10.0

# 已占用的 serial / 节点，防止空 serial 的 cam1 去抢已经打开的 cam2
_claimed_serials: dict[str, str] = {}
_claimed_nodes: dict[str, str] = {}


def _stop_pipeline_limited(pipe: object, timeout_s: float = 1.5) -> None:
    """pipeline.stop 可能和 wait_for_frames 互相等死；限时放弃，避免卡死调用线程。"""
    if pipe is None:
        return
    done = threading.Event()

    def _run() -> None:
        try:
            pipe.stop()
        except Exception:
            pass
        done.set()

    threading.Thread(target=_run, daemon=True, name="orbbec-pipe-stop").start()
    if not done.wait(timeout=float(timeout_s)):
        log.warning("pipeline.stop 超过 %.1fs，不再等待", timeout_s)


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


def _v4l_card_name(path: str) -> str:
    node = Path(path).name
    sys_name = Path("/sys/class/video4linux") / node / "name"
    try:
        return sys_name.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _path_looks_orbbec(path: str) -> bool:
    blob = f"{path} {_v4l_card_name(path)}".lower()
    return "orbbec" in blob or "gemini" in blob


def _is_meta_v4l(path: str, *, orbbec: bool = False) -> bool:
    """
    Gemini 336L by-id：
      index0 = 深度 Z16，index1/3/5 = 元数据（read 会卡），
      index2 = 红外 GREY，index4 = 彩色 Bayer BA81（须去马赛克）。
    普通 UVC：index1 才是元数据。
    """
    name = Path(path).name.lower()
    if orbbec:
        for token in (
            "video-index0",
            "video-index1",
            "video-index3",
            "video-index5",
        ):
            if token in name:
                return True
        return False
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
    """越小越优先。336L 彩色在 index4（Bayer），index2 是红外。"""
    name = path.lower()
    if orbbec:
        if "video-index4" in name:
            return 0
        if "video-index2" in name:
            return 50
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


def _fourcc_to_str(value: object) -> str:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return ""
    if raw <= 0:
        return ""
    chars = [chr((raw >> (8 * i)) & 0xFF) for i in range(4)]
    return "".join(chars).upper().replace("\x00", " ").strip()


def _bayer8_to_bgr(gray: object) -> Optional[object]:
    """BA81 / SBGGR8 → BGR。336L 彩色 UVC 口就是这种，不当场去马赛克会绿花屏。"""
    if cv2 is None or gray is None:
        return None
    arr = np.asarray(gray)
    if arr.ndim != 2 or arr.size < 16:
        return None
    try:
        return cv2.cvtColor(arr, cv2.COLOR_BayerBG2BGR)
    except Exception:
        return None


# 336L 彩色常见分辨率。SDK 偶发把 Bayer 报成 921600×1（=1280×720 拉成一行）。
_COMMON_COLOR_WH: tuple[tuple[int, int], ...] = (
    (1280, 720),
    (640, 480),
    (848, 480),
    (1920, 1080),
    (1280, 800),
    (960, 540),
    (640, 400),
)


def _infer_color_hw(n: int, w: int, h: int) -> tuple[int, int]:
    """用缓冲区字节数还原宽高。w/h 已合理则原样返回。

    n: int: 像素字节数
    w: int: SDK/V4L 报的宽
    h: int: SDK/V4L 报的高
    return: tuple[int, int]: (宽, 高)
    """
    if int(w) >= 8 and int(h) >= 8:
        return int(w), int(h)
    nbytes = int(n)
    for ww, hh in _COMMON_COLOR_WH:
        plane = ww * hh
        if nbytes in (plane, plane * 2, plane * 3, plane * 4):
            return ww, hh
    return int(w), int(h)


def _opencv_frame_to_bgr(frame: object, fourcc: str = "") -> Optional[object]:
    """OpenCV 读到的 V4L 帧转 BGR；Bayer / 灰度单独处理。"""
    if frame is None or cv2 is None:
        return None
    arr = np.asarray(frame)
    if arr.ndim == 2 and min(int(arr.shape[0]), int(arr.shape[1])) < 8:
        ww, hh = _infer_color_hw(int(arr.size), int(arr.shape[1]), int(arr.shape[0]))
        if ww >= 8 and hh >= 8 and ww * hh == int(arr.size):
            arr = arr.reshape((hh, ww))
    code = (fourcc or "").upper().replace(" ", "")
    if arr.ndim == 2:
        if code in ("GREY", "GRAY", "Y8"):
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if code in ("Z16", "Y16"):
            return None
        return _bayer8_to_bgr(arr)
    if arr.ndim == 3 and arr.shape[2] == 1:
        return _opencv_frame_to_bgr(arr[:, :, 0], fourcc)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return arr[:, :, :3]
    return None


def _color_frame_to_bgr(color) -> Optional[object]:
    """
    Gemini 彩色可能是 YUYV / RGB / Bayer BA81，不能一律按三通道 RGB reshape。
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
    w, h = _infer_color_hw(n, w, h)
    if w < 8 or h < 8:
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
            if fmt == getattr(OBFormat, "BA81", None) or fmt == getattr(OBFormat, "BYR2", None):
                return _bayer8_to_bgr(data.reshape((h, w)))
            if fmt == getattr(OBFormat, "GRAY", None) or fmt == getattr(OBFormat, "Y8", None):
                return cv2.cvtColor(data.reshape((h, w)), cv2.COLOR_GRAY2BGR)
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
    if n == h * w:
        gray = data.reshape((h, w))
        if "GREY" in fmt_name or "GRAY" in fmt_name or fmt_name.endswith("Y8"):
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        converted = _bayer8_to_bgr(gray)
        if converted is not None:
            return converted
    if n >= 4 and int(data[0]) == 0xFF and int(data[1]) == 0xD8:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    return None


def _as_video_profile(profile: object) -> object:
    """StreamProfile → VideoStreamProfile；失败则原样返回。"""
    if profile is None:
        return None
    try:
        if profile.is_video_stream_profile():
            return profile.as_video_stream_profile()
    except Exception:
        pass
    try:
        return profile.as_video_stream_profile()
    except Exception:
        return profile


def _extract_depth_frame(frames) -> Optional[object]:
    """从 FrameSet 取出 DepthFrame（兼容 get_depth_frame / 按类型取）。"""
    if frames is None:
        return None
    depth = None
    try:
        depth = frames.get_depth_frame()
    except Exception:
        depth = None
    if depth is not None:
        return depth
    try:
        depth = frames.as_depth_frame()
    except Exception:
        depth = None
    if depth is not None:
        return depth
    try:
        from pyorbbecsdk import OBFrameType  # type: ignore

        depth = frames.get_frame_by_type(OBFrameType.DEPTH_FRAME)
    except Exception:
        depth = None
    return depth


def _depth_frame_to_mm(frames) -> Optional[object]:
    """从 Orbbec frames 取深度，单位毫米。失败返回 None。"""
    depth = _extract_depth_frame(frames)
    if depth is None:
        return None
    try:
        w = int(depth.get_width())
        h = int(depth.get_height())
        raw = np.frombuffer(depth.get_data(), dtype=np.uint16)
        if w < 1 or h < 1 or raw.size < w * h:
            return None
        arr = raw[: w * h].reshape((h, w)).astype(np.float32)
        scale = 1.0
        try:
            scale = float(depth.get_depth_scale())
        except Exception:
            pass
        if scale <= 0:
            scale = 1.0
        # SDK 常见：scale=1 已是 mm；scale≈0.001 则是米；scale=0.1 表示 0.1mm 单位
        if 0 < scale < 0.01:
            arr = arr * (scale * 1000.0)
        else:
            arr = arr * scale
        return arr
    except Exception:
        return None


def _v4l_z16_to_mm(frame: object) -> Optional[object]:
    """OpenCV 读到的 Z16 / 16bit 深度 → 毫米。"""
    if frame is None:
        return None
    arr = np.asarray(frame)
    if arr.size < 16:
        return None
    if arr.ndim == 3 and arr.shape[2] == 2:
        lo = arr[:, :, 0].astype(np.uint16)
        hi = arr[:, :, 1].astype(np.uint16)
        return (lo + (hi << 8)).astype(np.float32)
    if arr.ndim == 2:
        if arr.dtype == np.uint16 or arr.dtype == np.int32:
            return arr.astype(np.float32)
        if arr.dtype == np.uint8:
            return arr.astype(np.float32)
    return None


class OrbbecCamera:
    def __init__(
        self,
        name: str,
        index: int = 0,
        serial: str = "",
        use_mock: bool = True,
        fps: int = 30,
        color_width: int = 0,
        color_height: int = 0,
        enable_depth: bool = True,
        orbbec_backend: str = "rsdt",
    ):
        self.name = name
        self.index = index
        self.serial = serial
        self.use_mock = use_mock
        self.enable_depth = bool(enable_depth)
        self.orbbec_backend = normalize_orbbec_backend(orbbec_backend)
        self._rsdt = None
        self._target_fps = max(1, int(fps or 30))
        self._color_width = max(0, int(color_width or 0))
        self._color_height = max(0, int(color_height or 0))
        self._cap = None
        self._cap_depth = None
        self._pipeline = None
        self._ob_ctx = None
        self.opened = False
        self.last_error = ""
        self._opening = False
        self._open_token = 0
        self._open_lock = threading.Lock()
        self._grab_lock = threading.Lock()
        self._stream_running = False
        self._stream_thread: threading.Thread | None = None
        self._align_filter = None
        self._logged_depth = False
        self._mock_key = None
        self._depth_vis_ts = 0.0
        self._device_fps = 0
        self.last_depth = None  # 最近一帧深度 mm（无深度则为 None）
        self.last_color = None  # 最近一帧彩色 BGR
        self.last_depth_vis = None  # 深度伪彩 BGR，与彩色同尺寸
        self.last_depth_stats = ""  # 深度范围文案，供 UI 直接用
        self._v4l_fourcc = ""
        self._has_depth_stream = False

    @property
    def target_fps(self) -> int:
        return int(self._target_fps)

    @property
    def opening(self) -> bool:
        return bool(self._opening)

    @property
    def has_hardware(self) -> bool:
        """是否已打开 Orbbec/OpenCV 设备（Mock 也会把 opened 设为 True）。"""
        return (
            self._pipeline is not None
            or self._cap is not None
            or self._rsdt is not None
        )

    def apply_use_mock(self, mock: bool) -> None:
        """只切换模拟标志。切模拟时不准 pipeline.stop，否则 GIL 卡死界面。"""
        self.use_mock = bool(mock)
        if self.use_mock:
            self.last_error = ""
            self.rebuild_mock_frame()
            return
        if self.has_hardware:
            self.last_error = ""
            self.opened = True
            if not self._stream_running:
                self._start_stream()
            return
        # 之前只是 Mock 占了 opened，没有真机管道
        self.opened = False
        self.open_async()

    def open(self) -> bool:
        """同步打开（带超时）。HMI 请用 open_async，避免无响应。"""
        if self.use_mock:
            self.opened = True
            self.last_error = ""
            log.info("[%s] Mock 相机 serial=%s index=%s", self.name, self.serial, self.index)
            self.rebuild_mock_frame()
            return True
        ok = self._open_with_timeout(_OPEN_TIMEOUT_S)
        if ok:
            self._start_stream()
        return ok

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
                ok = bool(self._open_with_timeout(_OPEN_TIMEOUT_S))
                if ok:
                    self._start_stream()
            finally:
                self._opening = False

        threading.Thread(target=_run, daemon=True, name=f"open-{self.name}").start()

    def reopen_async(self) -> None:
        """后台 close + open。禁止在界面线程 pipeline.stop。"""
        with self._open_lock:
            if self._opening:
                return
            self._opening = True
            self.last_error = "正在重开…"

        def _run() -> None:
            try:
                try:
                    self.close()
                except Exception:
                    pass
                if self.use_mock:
                    self.open()
                    return
                ok = bool(self._open_with_timeout(_OPEN_TIMEOUT_S))
                if ok:
                    self._start_stream()
            finally:
                self._opening = False

        threading.Thread(target=_run, daemon=True, name=f"reopen-{self.name}").start()

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

    def _pick_color_profile(self, profile_list) -> tuple:
        """按配置分辨率选帧率最高的彩色 profile。"""
        want_w, want_h = self._color_width, self._color_height

        def _scan(require_res: bool) -> tuple[object | None, int]:
            best_p = None
            best_fps = -1
            try:
                n = int(profile_list.get_count())
            except Exception:
                n = 0
            for i in range(n):
                try:
                    p = profile_list.get_stream_profile_by_index(i)
                    fps = int(p.get_fps())
                    w = int(p.get_width())
                    h = int(p.get_height())
                except Exception:
                    continue
                if require_res and want_w > 0 and w != want_w:
                    continue
                if require_res and want_h > 0 and h != want_h:
                    continue
                if fps > best_fps:
                    best_fps = fps
                    best_p = p
            return best_p, best_fps

        best, best_fps = _scan(require_res=True)
        if best is None:
            best, best_fps = _scan(require_res=False)
        if best is not None:
            return best, best_fps
        default = profile_list.get_default_video_stream_profile()
        try:
            best_fps = int(default.get_fps())
        except Exception:
            best_fps = self._target_fps
        return default, best_fps

    def _pick_depth_profile(self, pipe, _color_profile: object) -> object:
        """选深度 profile：默认 Y16，避免用彩色分辨率去套深度。"""
        try:
            from pyorbbecsdk import OBFormat, OBSensorType  # type: ignore
        except Exception:
            return None
        try:
            dlist = pipe.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
        except Exception as e:
            log.info("[%s] 无 DEPTH_SENSOR：%s", self.name, e)
            return None
        if dlist is None:
            return None
        try:
            n = int(dlist.get_count())
        except Exception:
            n = 0
        if n < 1:
            log.info("[%s] DEPTH_SENSOR profile 数量为 0", self.name)
            return None
        try:
            p = dlist.get_default_video_stream_profile()
            if p is not None:
                return _as_video_profile(p)
        except Exception:
            pass
        for fmt in (
            getattr(OBFormat, "Y16", None),
            getattr(OBFormat, "Z16", None),
        ):
            if fmt is None:
                continue
            try:
                p = dlist.get_video_stream_profile(0, 0, fmt, 0)
                if p is not None:
                    return _as_video_profile(p)
            except Exception:
                continue
        p, _fps = self._pick_color_profile(dlist)
        return _as_video_profile(p) if p is not None else None

    def _apply_opencv_fps(self, cap) -> None:
        if cv2 is None or cap is None:
            return
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            cap.set(cv2.CAP_PROP_FPS, float(self._target_fps))
        except Exception:
            pass
        cw, ch = self._color_width, self._color_height
        if cw > 0:
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(cw))
            except Exception:
                pass
        if ch > 0:
            try:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(ch))
            except Exception:
                pass

    def _start_stream(self) -> None:
        if not self.opened or self._stream_running:
            return
        self._stream_running = True
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"cam-stream-{self.name}",
        )
        self._stream_thread.start()

    def _stop_stream(self) -> None:
        self._stream_running = False

    def _stream_loop(self) -> None:
        while self._stream_running:
            if not self.opened or self.use_mock:
                time.sleep(0.05)
                continue
            t0 = time.monotonic()
            self._capture_frame(wait_s=0.0)
            fps = min(float(self._target_fps), _HMI_STREAM_MAX_FPS)
            min_interval = 1.0 / fps if fps > 0 else 0.1
            remain = min_interval - (time.monotonic() - t0)
            if remain > 0:
                time.sleep(remain)

    def rebuild_mock_frame(self) -> None:
        """按当前 enable_depth 重做一张静态 Mock 图（不启流线程）。"""
        self._mock_key = None
        self._grab_mock_frame()

    def _grab_mock_frame(self) -> Optional[object]:
        key = (self.name, bool(self.enable_depth))
        if self.last_color is not None and getattr(self, "_mock_key", None) == key:
            return self.last_color
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
            if self.enable_depth:
                h, w = int(img.shape[0]), int(img.shape[1])
                self.last_depth = mock_depth_mm(h, w)
                self.last_depth_vis = mock_depth_vis_bgr(h, w)
                self.last_depth_stats = "深度:模拟"
                self._depth_vis_ts = time.monotonic()
            else:
                self.last_depth = None
                self.last_depth_vis = None
                self.last_depth_stats = ""
            self._mock_key = key
            return img
        except Exception:
            return None

    def _capture_frame(self, wait_s: float = 0.0) -> bool:
        wait_s = float(wait_s or 0.0)
        if wait_s > 0:
            got = self._grab_lock.acquire(timeout=wait_s)
        else:
            got = self._grab_lock.acquire(blocking=False)
        if not got:
            return False
        try:
            if self._rsdt is not None:
                want_vis = bool(self.enable_depth)
                rgb, depth, depth_color = self._rsdt.get_one_frame(
                    draw_depth_colormap=want_vis
                )
                if rgb is None:
                    return False
                self.last_color = rgb
                if want_vis and depth is not None:
                    self.last_depth = depth
                    if depth_color is not None:
                        self.last_depth_vis = depth_color
                        self._depth_vis_ts = time.monotonic()
                    else:
                        self._refresh_depth_vis(force=True)
                elif not want_vis:
                    self.last_depth = None
                    self.last_depth_vis = None
                    self.last_depth_stats = ""
                return True
            if self._pipeline is not None:
                if self._has_depth_stream:
                    timeout_ms = _PIPELINE_WAIT_MS
                else:
                    timeout_ms = _PIPELINE_WAIT_MS_COLOR
                if wait_s > 0:
                    timeout_ms = max(timeout_ms, min(500, int(wait_s * 1000)))
                frames = self._pipeline.wait_for_frames(timeout_ms)
                if not frames:
                    return False
                raw_frames = frames
                color = frames.get_color_frame() if frames is not None else None
                if not color and raw_frames is not frames:
                    self._align_filter = False
                    frames = raw_frames
                    color = frames.get_color_frame()
                img = _color_frame_to_bgr(color) if color is not None else None
                mm = None
                if self.enable_depth:
                    mm = _depth_frame_to_mm(frames)
                    if mm is None and raw_frames is not frames:
                        mm = _depth_frame_to_mm(raw_frames)
                else:
                    self.last_depth = None
                    self.last_depth_vis = None
                    self.last_depth_stats = ""
                if mm is not None:
                    self.last_depth = mm
                    if not self._logged_depth:
                        self._logged_depth = True
                        log.info(
                            "[%s] 已收到深度 %dx%d",
                            self.name,
                            int(mm.shape[1]),
                            int(mm.shape[0]),
                        )
                elif self.enable_depth and self._cap_depth is not None:
                    self._read_opencv_depth()
                    if self.last_depth is not None and not self._logged_depth:
                        self._logged_depth = True
                        log.info("[%s] 已收到 OpenCV 深度", self.name)
                if img is None:
                    if mm is not None:
                        self._refresh_depth_vis()
                    return False
                self.last_error = ""
                self.last_color = img
                self._refresh_depth_vis()
                return True
            if self._cap is not None and cv2 is not None:
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    bgr = _opencv_frame_to_bgr(frame, self._v4l_fourcc)
                    if bgr is None:
                        return False
                    self.last_color = bgr
                    self.last_error = ""
                    self._read_opencv_depth()
                    self._refresh_depth_vis()
                    return True
                return False
            return False
        except Exception as e:
            log.warning("[%s] 取图异常: %s", self.name, e)
            return False
        finally:
            self._grab_lock.release()

    def _refresh_depth_vis(self, *, force: bool = False) -> None:
        """按当前彩色尺寸刷新深度伪彩。无新深度时保留上一张。"""
        if not self.enable_depth or self.last_depth is None:
            return
        now = time.monotonic()
        if (
            not force
            and self.last_depth_vis is not None
            and (now - float(self._depth_vis_ts)) < 0.25
        ):
            return
        vis = colorize_depth_mm(self.last_depth)
        if vis is not None:
            self.last_depth_vis = vis
            self._depth_vis_ts = now
            self.last_depth_stats = depth_stats_text(self.last_depth)

    def _maybe_align_frames(self, frames: object) -> object:
        """软件 AlignFilter 把深度投到彩色坐标系；失败则原样返回。"""
        if frames is None:
            return frames
        if self._align_filter is False:
            return frames
        if self._align_filter is None:
            try:
                from pyorbbecsdk import AlignFilter, OBStreamType  # type: ignore

                self._align_filter = AlignFilter(align_to_stream=OBStreamType.COLOR_STREAM)
            except Exception as e:
                log.info("[%s] AlignFilter 不可用: %s", self.name, e)
                self._align_filter = False
                return frames
        try:
            out = self._align_filter.process(frames)
            if out is None:
                return frames
            if _extract_depth_frame(out) is None and _extract_depth_frame(frames) is not None:
                return frames
            return out
        except Exception:
            return frames

    def _read_opencv_depth(self) -> None:
        if not self.enable_depth or self._cap_depth is None or cv2 is None:
            return
        try:
            ok, frame = self._cap_depth.read()
        except Exception:
            return
        if not ok or frame is None:
            return
        mm = _v4l_z16_to_mm(frame)
        if mm is not None:
            self.last_depth = mm

    def _open_impl(self) -> bool:
        self.close()
        self.last_error = ""
        if self.orbbec_backend == "rsdt" and self._open_rsdt():
            return True
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

    def _open_rsdt(self) -> bool:
        """用 RSDT orbbec_camera.connect_camera / get_one_frame。"""
        sn = (self.serial or "").strip()
        taken = self._serial_taken_by_other(sn)
        if taken:
            self.last_error = f"serial={sn} 已被「{taken}」占用，不能两路同时打开同一台"
            log.warning("[%s] %s", self.name, self.last_error)
            return False
        try:
            from vision.orbbec_backend import (
                create_rsdt_orbbec,
                pick_depth_res,
                pick_fps,
                pick_rgb_res,
            )

            cam = create_rsdt_orbbec(sn or str(self.index))
            rgb = pick_rgb_res(self._color_width, self._color_height)
            depth = pick_depth_res(self._color_width, self._color_height)
            fps = pick_fps(self._target_fps)
            ok = bool(
                cam.connect_camera(
                    rgb,
                    depth,
                    fps,
                    color_auto_exposure=True,
                )
            )
            if not ok:
                self.last_error = (
                    f"RSDT 奥比 connect_camera 失败 serial={sn or '-'} "
                    f"rgb={rgb} depth={depth} fps={fps}"
                )
                log.warning("[%s] %s", self.name, self.last_error)
                try:
                    cam.stop()
                except Exception:
                    pass
                return False
            self._rsdt = cam
            self._has_depth_stream = True
            self._device_fps = int(fps)
            self.opened = True
            self.last_error = ""
            if sn:
                self._claim(sn)
            log.info(
                "[%s] RSDT 奥比已打开 serial=%s rgb=%s fps=%s",
                self.name,
                sn or "-",
                rgb,
                fps,
            )
            return True
        except Exception as e:
            self.last_error = f"RSDT 奥比打开失败，将试本仓 SDK: {e}"
            log.warning("[%s] %s", self.name, self.last_error)
            self._rsdt = None
            return False

    def _open_orbbec(self) -> bool:
        sn = (self.serial or "").strip()
        if sn and not _looks_orbbec_serial(sn):
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
            if _looks_orbbec_serial(sn):
                device = self._device_by_serial(device_list, sn, n)
                if device is None:
                    self.last_error = f"找不到 serial={sn}（已枚举 {n} 台）"
                    return False
            elif n == 1:
                device = device_list.get_device_by_index(0)
            else:
                self.last_error = (
                    f"已枚举 {n} 台 Orbbec，必须填写 serial（本机设备页可复制）"
                )
                return False
            pipe = Pipeline(device)
            profile_list = pipe.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
            color_profile, got_fps = self._pick_color_profile(profile_list)
            color_profile = _as_video_profile(color_profile)
            depth_profile = self._pick_depth_profile(pipe, color_profile)

            def _make_cfg(*, with_depth: bool, align: str) -> object:
                from pyorbbecsdk import (  # type: ignore
                    OBAlignMode,
                    OBFrameAggregateOutputMode,
                    OBSensorType as _ST,
                )

                local = Config()
                local.enable_stream(color_profile)
                if with_depth:
                    if depth_profile is not None:
                        local.enable_stream(depth_profile)
                    else:
                        local.enable_stream(_ST.DEPTH_SENSOR)
                    try:
                        local.set_frame_aggregate_output_mode(
                            OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE
                        )
                    except Exception:
                        pass
                    try:
                        if align == "hw":
                            local.set_align_mode(OBAlignMode.HW_MODE)
                        elif align == "sw":
                            local.set_align_mode(OBAlignMode.SW_MODE)
                        else:
                            local.set_align_mode(OBAlignMode.DISABLE)
                    except Exception:
                        pass
                return local

            started_depth = False
            last_start_err: Exception | None = None
            if self.enable_depth:
                for align in ("off", "sw", "hw"):
                    try:
                        pipe.start(_make_cfg(with_depth=True, align=align))
                        started_depth = True
                        log.info("[%s] 彩色+深度已启动 align=%s", self.name, align)
                        break
                    except Exception as e:
                        last_start_err = e
                        log.warning(
                            "[%s] 彩色+深度启动失败 align=%s: %s",
                            self.name,
                            align,
                            e,
                        )
                        try:
                            pipe.stop()
                        except Exception:
                            pass
            if not started_depth:
                if self.enable_depth and last_start_err is not None:
                    log.warning("[%s] 改纯彩色: %s", self.name, last_start_err)
                elif not self.enable_depth:
                    log.info("[%s] 配置关闭深度输出，只开彩色", self.name)
                pipe.start(_make_cfg(with_depth=False, align="off"))
            try:
                pipe.enable_frame_sync()
            except Exception:
                pass
            self._has_depth_stream = started_depth
            if got_fps > 0:
                self._device_fps = int(got_fps)
            self._ob_ctx = ctx
            self._pipeline = pipe
            self.opened = True
            self.last_error = ""
            info = device.get_device_info()
            got_sn = str(info.get_serial_number() or sn)
            if got_sn and not (self.serial or "").strip():
                self.serial = got_sn
            self._claim(got_sn)
            if not started_depth:
                self._open_opencv_depth()
            log.info(
                "[%s] Orbbec 已打开 name=%s serial=%s pid=0x%04X fps=%s depth=%s",
                self.name,
                info.get_name(),
                got_sn,
                int(info.get_pid()),
                self._target_fps,
                "on" if self._has_depth_stream else "off",
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
        """普通 UVC 用 video0；336L 彩色用 video-index4（Bayer），跳过深度/红外/元数据。"""
        sn = (self.serial or "").strip()
        orbbec = _looks_orbbec_serial(sn)
        try:
            idx = int(self.index)
        except Exception:
            idx = -1
        if not orbbec and idx >= 0:
            orbbec = _path_looks_orbbec(f"/dev/video{idx}")
        scored: list[tuple[int, str]] = []
        by_id = Path("/dev/v4l/by-id")
        if by_id.is_dir() and (sn or orbbec):
            for p in by_id.iterdir():
                name_l = p.name.lower()
                if sn:
                    if not _by_id_matches_serial(p.name, sn):
                        continue
                elif "orbbec" not in name_l and "gemini" not in name_l:
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
        """限时打开单个节点；打不开或读不出彩色帧则返回 None（不堵死总超时）。"""
        orbbec = _looks_orbbec_serial(self.serial) or _path_looks_orbbec(path)
        if _is_meta_v4l(path, orbbec=orbbec):
            log.warning("[%s] 拒绝元数据口 %s", self.name, path)
            return None
        holder: dict = {"cap": None, "fourcc": ""}
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
                if orbbec:
                    try:
                        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                    except Exception:
                        pass
                    try:
                        cap.set(
                            cv2.CAP_PROP_FOURCC,
                            cv2.VideoWriter_fourcc("B", "A", "8", "1"),
                        )
                    except Exception:
                        pass
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.release()
                    return
                fourcc = _fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
                bgr = _opencv_frame_to_bgr(frame, fourcc)
                if bgr is None:
                    cap.release()
                    return
                holder["cap"] = cap
                holder["fourcc"] = fourcc
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
        cap = holder.get("cap")
        if cap is not None:
            self._v4l_fourcc = str(holder.get("fourcc") or "")
        return cap

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
                    "336L 彩色是 video-index4（Bayer），不要用 index=4 当普通 UVC。"
                    "请填写 serial 后走 SDK，或确认 video-index4 存在。"
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
            self._apply_opencv_fps(self._cap)
            self.opened = True
            self.last_error = ""
            self._claim((self.serial or "").strip(), path)
            log.info("[%s] OpenCV 已打开 %s", self.name, path)
            self._open_opencv_depth()
            return True
        hint = "、".join(tried[:8]) if tried else "无匹配节点"
        self.last_error = (
            f"OpenCV 无法打开彩色画面（serial={self.serial or '-'} 已试: {hint}）。"
        )
        return False

    def _open_opencv_depth(self) -> None:
        """OpenCV 回退时再开 336L 的 Z16 口（video-index0）。失败不影响彩色。"""
        if not self.enable_depth:
            return
        if cv2 is None or self._cap_depth is not None:
            return
        sn = (self.serial or "").strip()
        by_id = Path("/dev/v4l/by-id")
        if not by_id.is_dir():
            return
        paths: list[str] = []
        try:
            entries = list(by_id.iterdir())
        except OSError:
            return
        for p in entries:
            name = p.name.lower()
            if "video-index0" not in name:
                continue
            if sn and not _by_id_matches_serial(p.name, sn):
                continue
            if "orbbec" not in name and "gemini" not in name:
                continue
            paths.append(_resolve_node(str(p)))
        if not paths:
            return
        path = paths[0]
        try:
            cap = cv2.VideoCapture(self._v4l_capture_src(path), cv2.CAP_V4L2)
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                return
            try:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            except Exception:
                pass
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("Z", "1", "6", " "))
            except Exception:
                pass
            ok, frame = cap.read()
            if not ok or _v4l_z16_to_mm(frame) is None:
                cap.release()
                return
            self._cap_depth = cap
            self._has_depth_stream = True
            log.info("[%s] OpenCV 深度已打开 %s", self.name, path)
        except Exception as e:
            log.info("[%s] OpenCV 深度未开: %s", self.name, e)

    def grab(self, wait_s: float = 0.0) -> Optional[object]:
        if self._opening:
            return None
        if not self.opened:
            return None
        if self.use_mock:
            return self._grab_mock_frame()
        wait_s = float(wait_s or 0.0)
        if wait_s <= 0 and self.last_color is not None and self._stream_running:
            return self.last_color
        if self._capture_frame(wait_s=wait_s):
            return self.last_color
        return self.last_color if self.last_color is not None else None

    def close(self) -> None:
        self._stream_running = False
        rsdt = self._rsdt
        self._rsdt = None
        if rsdt is not None:
            try:
                rsdt.stop()
            except Exception:
                pass
        pipe = self._pipeline
        self._pipeline = None
        if pipe is not None:
            _stop_pipeline_limited(pipe, timeout_s=1.5)
        th = self._stream_thread
        self._stream_thread = None
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=0.8)
        self._unclaim()
        if self._cap is not None and cv2 is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._v4l_fourcc = ""
        if self._cap_depth is not None and cv2 is not None:
            try:
                self._cap_depth.release()
            except Exception:
                pass
        self._cap_depth = None
        self.last_depth = None
        self.last_depth_vis = None
        self.last_depth_stats = ""
        self._has_depth_stream = False
        self._align_filter = None
        self._logged_depth = False
        self._mock_key = None
        self._depth_vis_ts = 0.0
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
        if self._rsdt is not None:
            self.opened = bool(getattr(self._rsdt, "connected", False))
            return self.opened
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
            mark = ""
            low = p.name.lower()
            if "video-index0" in low:
                mark = "  ← 深度 Z16"
            elif "video-index1" in low or "video-index3" in low or "video-index5" in low:
                mark = "  ← 跳过(元数据，read会卡住)"
            elif "video-index2" in low:
                mark = "  ← 红外 GREY"
            elif "video-index4" in low:
                mark = "  ← 彩色 Bayer（须去马赛克；优先填 serial 走 SDK）"
            lines.append(f"  {p.name} → {real}{mark}")
    else:
        lines.append("没有 /dev/v4l/by-id")
    return "\n".join(lines)
