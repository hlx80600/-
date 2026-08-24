"""视觉业务：皮带取鞋、放料槽、取料槽、鞋头对位。

★ 四台相机可各自 Mock：看 cameras.camN.use_mock（或运行中 cam.use_mock）
  cam1 皮带 YOLO / cam2 鞋头对位 / cam3 放料槽有无鞋 / cam4 取料槽+压杆
  该相机 use_mock=true → 用模拟检测结果；false → 抓图+YOLO

检测一律旧压鞋机 YOLO（OBB / 分类 / 深度 / 手眼）。没有形状模板备用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from devices.pose_utils import is_left_shoe_flag
from vision.camera_orbbec import OrbbecCamera
from vision.guide import GuideResult
from vision.monitor_frames import annotate_bgr, copy_bgr
from algorithm_module import algo

log = logging.getLogger(__name__)


@dataclass
class BeltPickResult:
    ok: bool
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 180.0
    ry: float = 0.0
    rz: float = 0.0
    is_left_shoe: bool = True
    message: str = ""
    source: str = ""  # "handeye" | "shield_mock" | ...
    # 鞋头在「抓取TCP坐标系」下的偏移 mm；抓取后用于把工具TCP改到鞋头
    toe_offset_in_grasp_tcp: Optional[list] = None
    # 抓取中心→鞋头直线距离 mm（cam1 实测；Mock 由 yaml 偏移算出）
    shoe_length_mm: float = 0.0


@dataclass
class SlotPhotoResult:
    ok: bool
    has_material: bool = False
    is_left_slot: Optional[bool] = None
    message: str = ""


class VisionService:
    def __init__(self, cameras: Dict[str, OrbbecCamera], cfg: Dict[str, Any], use_mock: bool = True):
        self.cameras = cameras
        self.cfg = cfg
        # 仅作「相机未配置 use_mock 时」的兜底；正常以各相机 cam.use_mock 为准
        self.use_mock = use_mock
        self.mock_place_has_material = False
        self.mock_place_is_left = True
        self.mock_pick_has_material = True
        self._guide_force_align_next = False
        # 屏蔽皮带：左右鞋轮询下标（Station1 确认取鞋后 +1）
        self._belt_mock_idx = 0
        self._belt_mock_last_label = ""
        # 最近一次皮带测长（全图像素），HMI 画抓取点→鞋头
        self.last_belt_debug: Optional[Dict[str, Any]] = None
        # 取料槽压杆视觉 XY 微调（毫米），Station5 叠加到示教取料点
        self.last_pick_xy_offset_mm: Optional[list] = None
        # 相机监控页：每路最近原图 / 计算结果
        self.last_raw: Dict[str, Any] = {}
        self.last_vis: Dict[str, Any] = {}
        self.last_raw_ts: Dict[str, float] = {}
        self.last_vis_meta: Dict[str, Dict[str, Any]] = {}

    def cam_is_mock(self, cam_key: str) -> bool:
        """该相机是否走模拟（硬件+该路视觉逻辑）。"""
        cam = self.cameras.get(cam_key)
        if cam is not None:
            return bool(cam.use_mock)
        return bool(self.use_mock)

    def set_cam_mock(self, cam_key: str, mock: bool) -> None:
        """运行中切换某相机 Mock。切真机后台打开，避免堵死 HMI。"""
        cam = self.cameras.get(cam_key)
        if cam is None:
            return
        want = bool(mock)
        if bool(cam.use_mock) == want and (want or cam.opened or cam.opening):
            return
        try:
            cam.close()
        except Exception:
            pass
        cam.use_mock = want
        if want:
            cam.open()
            log.info("[视觉] %s 已切模拟", cam_key)
            return
        cam.open_async()
        log.info("[视觉] %s 切真机，后台连接 serial=%s index=%s", cam_key, cam.serial, cam.index)

    def method(self) -> str:
        from vision.legacy_pipeline import vision_method

        return vision_method(self.cfg)

    def stack_status_text(self) -> str:
        st = algo.stack_status()
        return f"方法={self.method()} | {st.get('message','')} | {algo.model_status_text(self.cfg)}"

    def grab_raw(self, cam_id: str, wait_s: float = 0.0):
        cam = self.cameras.get(cam_id)
        img = None
        fresh = False
        if cam is not None:
            try:
                img = cam.grab(wait_s=float(wait_s))
                fresh = img is not None
            except Exception:
                img = None
            if img is None:
                img = getattr(cam, "last_color", None)
        if img is not None:
            self.last_raw[cam_id] = copy_bgr(img)
            if fresh or cam_id not in self.last_raw_ts:
                self.last_raw_ts[cam_id] = time.time()
        return img

    def publish_vis(
        self,
        cam_id: str,
        vis: Any,
        message: str = "",
        ok: bool = True,
        raw: Any = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if raw is not None:
            self.last_raw[cam_id] = copy_bgr(raw)
            self.last_raw_ts[cam_id] = time.time()
        base = vis if vis is not None else raw
        if base is None:
            base = self.last_raw.get(cam_id)
        tag = "OK" if ok else "FAIL"
        shown = vis if vis is not None else annotate_bgr(
            base, [tag, str(message or "")[:60]], ok=ok, cam_id=cam_id, kind="VIS"
        )
        if shown is not None:
            shown = copy_bgr(shown)
            from vision.monitor_frames import draw_roi

            shown = draw_roi(shown, cam_id)
            self.last_vis[cam_id] = shown
        meta: Dict[str, Any] = {
            "ok": bool(ok),
            "message": str(message or ""),
            "ts": time.time(),
        }
        if isinstance(extra, dict):
            meta.update(extra)
        self.last_vis_meta[cam_id] = meta

    def _belt_toe_tcp_extra(
        self,
        toe_offset: Any,
        shoe_length_mm: float = 0.0,
    ) -> Dict[str, Any]:
        """把中心→鞋头距离换算成抓鞋前/后 TCP，写入监控 meta。"""
        from devices.toe_tcp import (
            describe_grasp_to_toe_tcp,
            grasp_tcp_from_robot_cfg,
        )

        if not (isinstance(toe_offset, (list, tuple)) and len(toe_offset) >= 2):
            return {}
        off = [
            float(toe_offset[0]),
            float(toe_offset[1]),
            float(toe_offset[2] if len(toe_offset) > 2 else 0.0),
        ]
        full_cfg = getattr(self, "root_cfg", None)
        if not isinstance(full_cfg, dict):
            full_cfg = self.cfg if isinstance(self.cfg, dict) else {}
        grasp = grasp_tcp_from_robot_cfg(full_cfg, "robot1")
        return describe_grasp_to_toe_tcp(
            grasp, off, shoe_length_mm=float(shoe_length_mm or 0.0)
        )

    def _publish_cam1_with_toe(
        self,
        vis: Any,
        message: str,
        ok: bool,
        *,
        raw: Any = None,
        toe_offset: Any = None,
        shoe_length_mm: float = 0.0,
        ascii_prefix: Optional[list] = None,
    ) -> Dict[str, Any]:
        """cam1 发布结果，并把中心→鞋头 / 抓鞋前后 TCP 写入 meta 与文案。"""
        extra = self._belt_toe_tcp_extra(toe_offset, shoe_length_mm)
        ascii_lines = list(ascii_prefix or [])
        ascii_lines.extend(list(extra.get("ascii_lines") or []))
        shown = vis
        if ascii_lines:
            base = shown if shown is not None else (raw if raw is not None else self.last_raw.get("cam1"))
            if base is not None:
                shown = annotate_bgr(
                    base,
                    ascii_lines[:7],
                    ok=ok,
                    cam_id="cam1",
                    kind="VIS",
                )
        elif shown is None and raw is not None:
            shown = annotate_bgr(
                raw,
                ["CAM1"],
                ok=ok,
                cam_id="cam1",
                kind="VIS",
            )
        label = str(message or "")
        zh = str(extra.get("label_zh") or "")
        if zh:
            label = f"{label}\n{zh}" if label else zh
        if extra:
            dbg = dict(self.last_belt_debug or {})
            dbg.update(
                {
                    "length_mm": extra.get("center_toe_dist_mm"),
                    "offset": extra.get("toe_offset_in_grasp_tcp"),
                    "tcp_before_grasp": extra.get("tcp_before_grasp"),
                    "tcp_after_grasp": extra.get("tcp_after_grasp"),
                }
            )
            self.last_belt_debug = dbg
            extra = dict(extra)
            extra["ascii_lines"] = ascii_lines
        self.publish_vis("cam1", shown, label, ok, raw=raw, extra=extra)
        return extra

    def peek_raw(self, cam_id: str) -> Any:
        """取最近一帧拷贝，不 grab（监控实时推演 / 叠加用）。"""
        img = self.last_raw.get(cam_id)
        if img is None:
            cam = self.cameras.get(cam_id)
            if cam is not None:
                img = getattr(cam, "last_color", None)
                if img is None:
                    img = getattr(cam, "_last_bgr", None)
        return copy_bgr(img) if img is not None else None

    def compute_monitor(self, cam_id: str, *, from_cache: bool = False) -> str:
        """监控页手动/定时推演：与自动流程同一套算法，并写入 last_vis。

        from_cache=True：只用已缓存帧，不抢相机 grab，避免监控原图掉帧。
        """
        if from_cache:
            return self._compute_monitor_cached(cam_id)
        mock_blk = self.cfg.get("belt_pick_mock") if isinstance(self.cfg, dict) else {}
        if not isinstance(mock_blk, dict):
            mock_blk = {}
        if cam_id == "cam1":
            r = self.photo_belt_pick(
                float(mock_blk.get("z", 120.0)),
                float(mock_blk.get("rx", 180.0)),
                float(mock_blk.get("ry", 0.0)),
            )
            return r.message
        if cam_id == "cam2":
            g = self.guide_place_edge()
            return g.message
        if cam_id == "cam3":
            r = self.photo_place_slot()
            return r.message
        if cam_id == "cam4":
            r = self.photo_pick_slot()
            return r.message
        return f"未知相机 {cam_id}"

    def _compute_monitor_cached(self, cam_id: str) -> str:
        """监控专用：缓存帧推演。"""
        mock_blk = self.cfg.get("belt_pick_mock") if isinstance(self.cfg, dict) else {}
        if not isinstance(mock_blk, dict):
            mock_blk = {}
        img = self.peek_raw(cam_id)

        if cam_id == "cam1":
            z = float(mock_blk.get("z", 120.0))
            rx = float(mock_blk.get("rx", 180.0))
            ry = float(mock_blk.get("ry", 0.0))
            if self.cam_is_mock("cam1"):
                shoes = algo.detect_belt_shoes_mock(self.cfg)
                if not shoes:
                    self.publish_vis("cam1", None, "屏蔽示教无鞋位", False, raw=img)
                    return "屏蔽示教无鞋位"
                # 与 photo 一致：取当前轮询鞋，但不推进 idx（监控只显示）
                seq = self._belt_mock_sequence(shoes)
                s = seq[int(self._belt_mock_idx) % len(seq)] if seq else shoes[0]
                side = "左鞋" if is_left_shoe_flag(s.is_left_shoe) else "右鞋"
                toe_off = getattr(s, "toe_offset_in_grasp_tcp", None)
                if not (isinstance(toe_off, (list, tuple)) and len(toe_off) >= 3):
                    toe_off = mock_blk.get("toe_offset_in_grasp_tcp") or [0.0, 120.0, 0.0]
                toe_list = [float(toe_off[0]), float(toe_off[1]), float(toe_off[2])]
                length_mm = (toe_list[0] ** 2 + toe_list[1] ** 2) ** 0.5
                msg = f"监控[缓存]屏蔽 {side} X={s.x:.1f} Y={s.y:.1f}"
                self._publish_cam1_with_toe(
                    None,
                    msg,
                    True,
                    raw=img,
                    toe_offset=toe_list,
                    shoe_length_mm=length_mm,
                    ascii_prefix=["SHIELD", f"{side}", f"XY=({s.x:.0f},{s.y:.0f})"],
                )
                return str((self.last_vis_meta.get("cam1") or {}).get("message") or msg)
            if img is None:
                self.publish_vis("cam1", None, "相机1无缓存图", False)
                return "相机1无缓存图"
            from vision.legacy_pipeline import FrameAdapter, get_shoe_vision

            sv = get_shoe_vision(self.cameras, self.cfg)
            ad = getattr(sv, "camera", None)
            old = bool(getattr(ad, "prefer_last", False)) if ad is not None else False
            if isinstance(ad, FrameAdapter):
                ad.prefer_last = True
            try:
                ar = algo.detect_belt_pick(self.cameras, self.cfg, z, rx, ry)
            finally:
                if isinstance(ad, FrameAdapter):
                    ad.prefer_last = old
            shown = ar.vis_bgr
            msg = f"监控[缓存]{ar.message}"
            prefix = ["YOLO", "OK" if ar.ok else "FAIL", f"XY=({ar.x:.0f},{ar.y:.0f})"]
            self._publish_cam1_with_toe(
                shown,
                msg,
                ar.ok,
                raw=img,
                toe_offset=ar.toe_offset_in_grasp_tcp,
                shoe_length_mm=float(ar.shoe_length_mm or 0.0),
                ascii_prefix=prefix if shown is None else None,
            )
            return str((self.last_vis_meta.get("cam1") or {}).get("message") or msg)

        if cam_id == "cam2":
            if self.cam_is_mock("cam2"):
                msg = "监控[缓存]cam2模拟"
                vis = annotate_bgr(img, ["MOCK", "TOE"], ok=True, cam_id="cam2", kind="VIS")
                self.publish_vis("cam2", vis, msg, True, raw=img)
                return msg
            if img is None:
                self.publish_vis("cam2", None, "相机2无缓存图", False)
                return "相机2无缓存图"
            toe = algo.classify_toe_align(img, self.cfg)
            if not toe.ok:
                vis = annotate_bgr(img, ["TOE", "FAIL"], ok=False, cam_id="cam2", kind="VIS")
                self.publish_vis("cam2", vis, toe.message, False, raw=img)
                return toe.message
            vis = annotate_bgr(
                img,
                ["TOE", f"L={toe.label or '-'}", "ALIGNED" if toe.aligned else "MOVE"],
                ok=True,
                cam_id="cam2",
                kind="VIS",
            )
            msg = f"监控[缓存]{toe.message}"
            self.publish_vis("cam2", vis, msg, True, raw=img)
            return msg

        if cam_id == "cam3":
            if self.cam_is_mock("cam3"):
                has = bool(self.mock_place_has_material)
                left = bool(self.mock_place_is_left)
                msg = f"监控[缓存]cam3模拟 {'有料' if has else '空'} {'左' if left else '右'}"
                vis = annotate_bgr(
                    img,
                    ["MOCK", "HAS" if has else "EMPTY", "LEFT" if left else "RIGHT"],
                    ok=True,
                    cam_id="cam3",
                    kind="VIS",
                )
                self.publish_vis("cam3", vis, msg, True, raw=img)
                return msg
            if img is None:
                self.publish_vis("cam3", None, "相机3无缓存图", False)
                return "相机3无缓存图"
            occ_r = algo.classify_slot_occupied(img, self.cfg)
            if not occ_r.ok:
                vis = annotate_bgr(img, ["SLOT", "FAIL"], ok=False, cam_id="cam3", kind="VIS")
                self.publish_vis("cam3", vis, occ_r.message, False, raw=img)
                return occ_r.message
            vis = annotate_bgr(
                img,
                ["SLOT", "HAS" if occ_r.has_material else "EMPTY"],
                ok=True,
                cam_id="cam3",
                kind="VIS",
            )
            msg = f"监控[缓存]{occ_r.message}"
            self.publish_vis("cam3", vis, msg, True, raw=img)
            return msg

        if cam_id == "cam4":
            if self.cam_is_mock("cam4"):
                has = bool(self.mock_pick_has_material)
                msg = f"监控[缓存]cam4模拟 {'有料' if has else '空'}"
                vis = annotate_bgr(
                    img,
                    ["MOCK", "HAS" if has else "EMPTY"],
                    ok=True,
                    cam_id="cam4",
                    kind="VIS",
                )
                self.publish_vis("cam4", vis, msg, True, raw=img)
                return msg
            if img is None:
                self.publish_vis("cam4", None, "相机4无缓存图", False)
                return "相机4无缓存图"
            occ_r = algo.classify_slot_occupied(img, self.cfg)
            if not occ_r.ok:
                vis = annotate_bgr(img, ["PICK", "FAIL"], ok=False, cam_id="cam4", kind="VIS")
                self.publish_vis("cam4", vis, occ_r.message, False, raw=img)
                return occ_r.message
            extra = ""
            shown = img
            if occ_r.has_material:
                rod = algo.measure_rod_offset_mm(self.cameras, self.cfg, image_bgr=img)
                if rod.ok:
                    if rod.vis_bgr is not None:
                        shown = rod.vis_bgr
                    extra = f" | {rod.message}"
                else:
                    extra = f" | 压杆未测到({rod.message})"
            vis = annotate_bgr(
                shown,
                ["PICK", "HAS" if occ_r.has_material else "EMPTY"],
                ok=True,
                cam_id="cam4",
                kind="VIS",
            )
            msg = f"监控[缓存]{occ_r.message}{extra}"
            self.publish_vis("cam4", vis, msg, True, raw=img)
            return msg

        return f"未知相机 {cam_id}"

    def _result_from_legacy(self, d: dict) -> BeltPickResult:
        toe = d.get("toe_offset_in_grasp_tcp")
        if isinstance(toe, (list, tuple)) and len(toe) >= 2:
            toe_list = [float(toe[0]), float(toe[1]), float(toe[2] if len(toe) > 2 else 0.0)]
        else:
            toe_list = None
        return BeltPickResult(
            ok=bool(d.get("ok")),
            x=float(d.get("x") or 0.0),
            y=float(d.get("y") or 0.0),
            z=float(d.get("z") or 0.0),
            rx=float(d.get("rx") or 0.0),
            ry=float(d.get("ry") or 0.0),
            rz=float(d.get("rz") or 0.0),
            is_left_shoe=bool(d.get("is_left_shoe", True)),
            message=str(d.get("message") or ""),
            source=str(d.get("source") or "legacy_yolo_handeye"),
            toe_offset_in_grasp_tcp=toe_list,
            shoe_length_mm=float(d.get("shoe_length_mm") or 0.0),
        )

    def photo_belt_pick(self, default_z: float, default_rx: float, default_ry: float) -> BeltPickResult:
        """
        皮带取料位姿：
        - cam1 Mock / 无相机：用 yaml vision.belt_pick_mock 示教的机器人基座 XYRz（+Z/Rx/Ry）
        - 真相机：YOLO OBB + 深度 + 手眼 → 基座毫米（模型/环境缺失则失败，保持 Mock）
        """
        mock_blk = self.cfg.get("belt_pick_mock") if isinstance(self.cfg, dict) else {}
        if not isinstance(mock_blk, dict):
            mock_blk = {}
        z = float(mock_blk.get("z", default_z))
        rx = float(mock_blk.get("rx", default_rx))
        ry = float(mock_blk.get("ry", default_ry))

        cam = self.cameras.get("cam1")
        if self.cam_is_mock("cam1"):
            shoes = algo.detect_belt_shoes_mock(self.cfg)
            if not shoes:
                return BeltPickResult(ok=False, message="未检测到鞋子(屏蔽示教点为空)")
            seq = self._belt_mock_sequence(shoes)
            alt = self._belt_alternate_enabled(mock_blk) and len(seq) > 1
            if alt:
                s = seq[int(self._belt_mock_idx) % len(seq)]
            else:
                s = sorted(shoes, key=lambda m: (m.x, m.y))[0]
            is_left = is_left_shoe_flag(s.is_left_shoe)
            side = "左鞋" if is_left else "右鞋"
            self._belt_mock_last_label = f"{side}:{s.label} Y={s.y:.1f}"
            if alt:
                nxt_s = seq[(int(self._belt_mock_idx) + 1) % len(seq)]
                nxt = "左鞋" if is_left_shoe_flag(nxt_s.is_left_shoe) else "右鞋"
            else:
                nxt = side
            n_l = sum(1 for x in shoes if is_left_shoe_flag(x.is_left_shoe))
            n_r = len(shoes) - n_l
            log.info(
                "[视觉] 屏蔽皮带选鞋 idx=%s/%s → %s「%s」Y=%.1f（示教左%d/右%d 交替=%s）",
                self._belt_mock_idx,
                len(seq),
                side,
                s.label,
                s.y,
                n_l,
                n_r,
                "开" if alt else "关",
            )
            toe_off = getattr(s, "toe_offset_in_grasp_tcp", None)
            if not (isinstance(toe_off, (list, tuple)) and len(toe_off) >= 3):
                toe_off = mock_blk.get("toe_offset_in_grasp_tcp") or [0.0, 120.0, 0.0]
            toe_list = [float(toe_off[0]), float(toe_off[1]), float(toe_off[2])]
            length_mm = (toe_list[0] ** 2 + toe_list[1] ** 2) ** 0.5
            self.last_belt_debug = {
                "ok": True,
                "source": "shield_mock",
                "length_mm": length_mm,
                "offset": list(toe_list),
            }
            msg = (
                f"屏蔽相机:本次{side}「{s.label}」X={s.x:.1f} Y={s.y:.1f}"
                f" | 鞋长={length_mm:.1f}mm 偏移={toe_list}"
                f" | 左{n_l}/右{n_r} | 交替={'开' if alt else '关'}"
                f" | idx={self._belt_mock_idx} | 确认后下次→{nxt}"
            )
            raw = self.grab_raw("cam1")
            self._publish_cam1_with_toe(
                None,
                msg,
                True,
                raw=raw,
                toe_offset=toe_list,
                shoe_length_mm=length_mm,
                ascii_prefix=["SHIELD MOCK", f"{side} X={s.x:.0f} Y={s.y:.0f}", f"L={length_mm:.0f}mm"],
            )
            return BeltPickResult(
                ok=True,
                x=s.x,
                y=s.y,
                z=z,
                rx=rx,
                ry=ry,
                rz=float(s.angle_deg),
                is_left_shoe=is_left,
                message=msg,
                source="shield_mock",
                toe_offset_in_grasp_tcp=toe_list,
                shoe_length_mm=length_mm,
            )

        img = cam.grab() if cam else None
        if img is None:
            self.publish_vis("cam1", None, "相机1无图", False)
            return BeltPickResult(ok=False, message="相机1无图")
        ar = algo.detect_belt_pick(self.cameras, self.cfg, z, rx, ry)
        d = {
            "ok": ar.ok,
            "x": ar.x,
            "y": ar.y,
            "z": ar.z,
            "rx": ar.rx,
            "ry": ar.ry,
            "rz": ar.rz,
            "is_left_shoe": ar.is_left_shoe,
            "message": ar.message,
            "source": ar.source,
            "toe_offset_in_grasp_tcp": ar.toe_offset_in_grasp_tcp,
            "shoe_length_mm": ar.shoe_length_mm,
        }
        vis = ar.vis_bgr
        self.last_belt_debug = {
            "ok": bool(d.get("ok")),
            "source": d.get("source") or "legacy_yolo_handeye",
            "length_mm": float(d.get("shoe_length_mm") or 0.0),
            "offset": d.get("toe_offset_in_grasp_tcp"),
            "message": d.get("message") or "",
            "vis": vis,
        }
        r = self._result_from_legacy(d)
        shown = vis
        self._publish_cam1_with_toe(
            shown,
            r.message,
            r.ok,
            raw=img,
            toe_offset=r.toe_offset_in_grasp_tcp,
            shoe_length_mm=float(r.shoe_length_mm or 0.0),
            ascii_prefix=(
                ["YOLO", "OK" if r.ok else "FAIL", f"XY=({r.x:.0f},{r.y:.0f})"]
                if shown is None
                else None
            ),
        )
        if not r.ok:
            log.warning("[视觉] YOLO皮带失败: %s", r.message)
        return r

    @staticmethod
    def _belt_alternate_enabled(mock_blk: dict) -> bool:
        """yaml vision.belt_pick_mock.alternate_lr，缺省 True。"""
        if "alternate_lr" not in mock_blk:
            return True
        v = mock_blk.get("alternate_lr")
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off")
        return bool(v)

    @staticmethod
    def _belt_mock_sequence(shoes: list) -> list:
        """
        生成轮询序列：左0、右0、左1、右1…
        若某一侧缺失，则只轮询有的一侧（并打日志）。
        """
        lefts = sorted(
            [x for x in shoes if is_left_shoe_flag(x.is_left_shoe)],
            key=lambda m: (m.x, m.y),
        )
        rights = sorted(
            [x for x in shoes if not is_left_shoe_flag(x.is_left_shoe)],
            key=lambda m: (m.x, m.y),
        )
        seq: list = []
        n = max(len(lefts), len(rights))
        for i in range(n):
            if i < len(lefts):
                seq.append(lefts[i])
            if i < len(rights):
                seq.append(rights[i])
        if not seq:
            seq = sorted(shoes, key=lambda m: (m.x, m.y))
        if not lefts or not rights:
            log.warning(
                "[视觉] 屏蔽示教左右不齐：左%d只 右%d只（请检查 is_left_shoe）。"
                "当前序列长度=%d",
                len(lefts),
                len(rights),
                len(seq),
            )
        return seq

    def commit_belt_mock_advance(self) -> None:
        """Station1 已采纳本次屏蔽取料结果后调用，轮到下一只。"""
        shoes = algo.detect_belt_shoes_mock(self.cfg)
        seq = self._belt_mock_sequence(shoes)
        if len(seq) <= 1:
            log.info("[视觉] 屏蔽皮带无需交替（序列长度=%s）上次[%s]", len(seq), self._belt_mock_last_label)
            return
        self._belt_mock_idx = int(self._belt_mock_idx) + 1
        nxt = seq[self._belt_mock_idx % len(seq)]
        log.info(
            "[视觉] 屏蔽皮带交替推进 idx=%s → 下次%s「%s」Y=%.1f（上次[%s]）",
            self._belt_mock_idx,
            "左鞋" if is_left_shoe_flag(nxt.is_left_shoe) else "右鞋",
            nxt.label,
            nxt.y,
            self._belt_mock_last_label or "-",
        )

    def belt_mock_status_text(self) -> str:
        """HMI：下次将给出的左右鞋。"""
        if not self.cam_is_mock("cam1"):
            return "皮带cam1:真机"
        mock_blk = self.cfg.get("belt_pick_mock") if isinstance(self.cfg, dict) else {}
        if not isinstance(mock_blk, dict):
            mock_blk = {}
        shoes = algo.detect_belt_shoes_mock(self.cfg)
        seq = self._belt_mock_sequence(shoes)
        alt = self._belt_alternate_enabled(mock_blk) and len(seq) > 1
        n_l = sum(1 for s in shoes if is_left_shoe_flag(s.is_left_shoe))
        n_r = len(shoes) - n_l
        last = self._belt_mock_last_label or "-"
        if not alt:
            return f"屏蔽皮带:交替关(取X最小) 左{n_l}/右{n_r} | 上次[{last}]"
        cur = seq[int(self._belt_mock_idx) % len(seq)]
        nxt_side = "左鞋" if is_left_shoe_flag(cur.is_left_shoe) else "右鞋"
        return (
            f"屏蔽皮带:交替开 左{n_l}/右{n_r} | idx={self._belt_mock_idx} "
            f"| 上次[{last}] | 下次→{nxt_side} Y={cur.y:.1f}"
        )

    def photo_place_slot(self) -> SlotPhotoResult:
        """
        放料槽拍照：
        - cam3 Mock：结果完全由 HMI「Mock放料槽有料 / Mock放料槽=左鞋槽」决定
        - cam3 真机：目前只判有无料；左右槽算法未接，暂默认左鞋槽（改 Mock 勾选无效）
        """
        cam = self.cameras.get("cam3")
        if self.cam_is_mock("cam3"):
            left = bool(self.mock_place_is_left)
            has = bool(self.mock_place_has_material)
            msg = (
                f"cam3【模拟】采用监视页Mock："
                f"{'有料' if has else '空槽'} / "
                f"{'左鞋槽' if left else '右鞋槽'}"
            )
            raw = self.grab_raw("cam3")
            vis = annotate_bgr(
                raw,
                ["MOCK", "HAS" if has else "EMPTY", "LEFT" if left else "RIGHT"],
                ok=True,
                cam_id="cam3",
                kind="VIS",
            )
            self.publish_vis("cam3", vis, msg, True, raw=raw)
            return SlotPhotoResult(
                ok=True,
                has_material=has,
                is_left_slot=left,
                message=msg,
            )
        img = cam.grab() if cam else None
        if img is None:
            self.publish_vis("cam3", None, "相机3无图（且非Mock）", False)
            return SlotPhotoResult(ok=False, message="相机3无图（且非Mock）")
        occ_r = algo.classify_slot_occupied(img, self.cfg)
        if not occ_r.ok:
            vis = annotate_bgr(img, ["SLOT", "FAIL"], ok=False, cam_id="cam3", kind="VIS")
            self.publish_vis("cam3", vis, f"YOLO槽分类失败: {occ_r.message}", False, raw=img)
            return SlotPhotoResult(ok=False, message=f"YOLO槽分类失败: {occ_r.message}")
        occ = occ_r.has_material
        msg = occ_r.message
        out_msg = f"cam3【真机/YOLO】{msg}；左右槽仍按流程记忆，不看监视页Mock"
        vis = annotate_bgr(
            img,
            ["SLOT YOLO", "HAS" if occ else "EMPTY"],
            ok=True,
            cam_id="cam3",
            kind="VIS",
        )
        self.publish_vis("cam3", vis, out_msg, True, raw=img)
        return SlotPhotoResult(
            ok=True,
            has_material=bool(occ),
            is_left_slot=True,
            message=out_msg,
        )

    def photo_pick_slot(self) -> SlotPhotoResult:
        cam = self.cameras.get("cam4")
        if self.cam_is_mock("cam4"):
            has = bool(self.mock_pick_has_material)
            msg = "cam4模拟 pick slot"
            raw = self.grab_raw("cam4")
            vis = annotate_bgr(
                raw,
                ["MOCK", "HAS" if has else "EMPTY"],
                ok=True,
                cam_id="cam4",
                kind="VIS",
            )
            self.publish_vis("cam4", vis, msg, True, raw=raw)
            return SlotPhotoResult(ok=True, has_material=has, message=msg)
        img = cam.grab() if cam else None
        if img is None:
            self.publish_vis("cam4", None, "相机4无图", False)
            return SlotPhotoResult(ok=False, message="相机4无图")
        occ_r = algo.classify_slot_occupied(img, self.cfg)
        if not occ_r.ok:
            self.last_pick_xy_offset_mm = None
            vis = annotate_bgr(img, ["PICK", "FAIL"], ok=False, cam_id="cam4", kind="VIS")
            self.publish_vis("cam4", vis, f"YOLO取槽分类失败: {occ_r.message}", False, raw=img)
            return SlotPhotoResult(ok=False, message=f"YOLO取槽分类失败: {occ_r.message}")
        occ = occ_r.has_material
        msg = occ_r.message
        extra = ""
        rod_vis = None
        self.last_pick_xy_offset_mm = None
        if occ:
            rod = algo.measure_rod_offset_mm(self.cameras, self.cfg)
            if rod.ok:
                self.last_pick_xy_offset_mm = [rod.dx, rod.dy, rod.dz]
                rod_vis = rod.vis_bgr
                extra = f" | {rod.message}"
            else:
                extra = f" | 压杆未测到({rod.message})，取料用示教点"
        out_msg = f"cam4【真机/YOLO】{msg}{extra}"
        shown = rod_vis if rod_vis is not None else img
        vis = annotate_bgr(
            shown,
            ["PICK YOLO", "HAS" if occ else "EMPTY"],
            ok=True,
            cam_id="cam4",
            kind="VIS",
        )
        self.publish_vis("cam4", vis, out_msg, True, raw=img)
        return SlotPhotoResult(ok=True, has_material=bool(occ), message=out_msg)

    def guide_place_edge(self) -> GuideResult:
        cam = self.cameras.get("cam2")
        if self.cam_is_mock("cam2"):
            raw = self.grab_raw("cam2")
            if self._guide_force_align_next:
                self._guide_force_align_next = False
                msg = "cam2模拟:已对齐"
                vis = annotate_bgr(raw, ["MOCK", "ALIGNED"], ok=True, cam_id="cam2", kind="VIS")
                self.publish_vis("cam2", vis, msg, True, raw=raw)
                return GuideResult(ok=True, aligned=True, message=msg)
            self._guide_force_align_next = True
            msg = "cam2模拟:需修正"
            vis = annotate_bgr(raw, ["MOCK", "OFFSET dx=2"], ok=True, cam_id="cam2", kind="VIS")
            self.publish_vis("cam2", vis, msg, True, raw=raw)
            return GuideResult(ok=True, aligned=False, dx=2.0, dy=0.0, message=msg)
        img = cam.grab() if cam else None
        if img is None:
            self.publish_vis("cam2", None, "相机2无图", False)
            return GuideResult(ok=False, message="相机2无图")
        toe = algo.classify_toe_align(img, self.cfg)
        if not toe.ok:
            vis = annotate_bgr(
                img,
                ["TOE", "FAIL"],
                ok=False,
                cam_id="cam2",
                kind="VIS",
            )
            self.publish_vis("cam2", vis, toe.message, False, raw=img)
            return GuideResult(ok=False, message=toe.message)
        label, msg = toe.label, toe.message
        aligned = toe.aligned
        adv = self.cfg.get("toe_align_advance_mm") or [0.0, 8.0, 0.0]
        dx = dy = 0.0
        lab = str(label).strip().lower()
        if label and not aligned:
            dx = float(adv[0]) if len(adv) > 0 else 0.0
            dy = float(adv[1]) if len(adv) > 1 else 8.0
            if lab in ("2", "right", "右"):
                dx, dy = -abs(dy), 0.0
            elif lab in ("left", "左"):
                dx, dy = abs(dy), 0.0
        vis = annotate_bgr(
            img,
            ["TOE", f"L={label or '-'}", "ALIGNED" if aligned else "MOVE"],
            ok=bool(label),
            cam_id="cam2",
            kind="VIS",
        )
        self.publish_vis("cam2", vis, msg, True, raw=img)
        return GuideResult(ok=True, aligned=aligned, dx=dx, dy=dy, message=msg)

    def test_slot_classify(self, cam_key: str = "cam3"):
        cam = self.cameras.get(cam_key)
        img = cam.grab() if cam else None
        if img is None:
            return None, f"{cam_key}无图", 0.0
        r = algo.classify_slot_occupied(img, self.cfg)
        if not r.ok:
            return None, r.message, r.confidence
        return r.has_material, r.message, r.confidence

    def test_toe_align_label(self, cam_key: str = "cam2"):
        cam = self.cameras.get(cam_key)
        img = cam.grab() if cam else None
        if img is None:
            return "", f"{cam_key}无图"
        r = algo.classify_toe_align(img, self.cfg)
        if not r.ok:
            return "", r.message
        return r.label, r.message

    def test_rod_offset(self):
        return algo.measure_rod_offset_tuple(self.cameras, self.cfg)
