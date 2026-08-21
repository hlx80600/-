"""把旧压鞋机视觉接到本程序：YOLO OBB + 分类 + 深度 + 手眼 → 机器人毫米。

旧链路：
  皮带：ShoeVision（鞋OBB / 左右脚 / 鞋楦 / 手眼）
  放槽：槽相机鞋头分类伺服 + 有无鞋分类
  取槽：Position 压杆测距微调取鞋 XY

本模块可在未装 YOLO/RSDT 时返回明确错误，由 VisionService 走 Mock。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from vision.numpy_compat import np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHOE_CFG = ROOT / "shoe_vision_config.json"


class FrameAdapter:
    """把本程序 OrbbecCamera 伪装成旧程序 camera.get_one_frame()。"""

    def __init__(self, cam, fallback_z_mm: float = 400.0):
        self._cam = cam
        self.fallback_z_mm = float(fallback_z_mm)
        # True：监控实时推演用 last_color，不抢 grab（避免原图掉帧）
        self.prefer_last = False

    def get_one_frame(self):
        img = None
        if self.prefer_last:
            img = getattr(self._cam, "last_color", None)
            if img is None:
                img = getattr(self._cam, "_last_bgr", None)
        if img is None:
            try:
                img = self._cam.grab()
            except Exception as e:
                log.warning("取帧失败: %s", e)
        depth = getattr(self._cam, "last_depth", None)
        if img is not None and depth is None:
            h, w = img.shape[:2]
            depth = np.full((h, w), self.fallback_z_mm, dtype=np.float32)
        return img, depth, None

    def connect_camera(self, *a, **k) -> bool:
        return bool(getattr(self._cam, "opened", False) or getattr(self._cam, "use_mock", False))

    def stop(self) -> None:
        pass


class SnapshotAdapter:
    """用已缓存的一帧做推演，完全不访问相机。"""

    def __init__(self, img, depth=None, fallback_z_mm: float = 400.0):
        self._img = img
        self._depth = depth
        self.fallback_z_mm = float(fallback_z_mm)

    def get_one_frame(self):
        img = self._img
        depth = self._depth
        if img is not None and depth is None:
            h, w = img.shape[:2]
            depth = np.full((h, w), self.fallback_z_mm, dtype=np.float32)
        return img, depth, None

    def connect_camera(self, *a, **k) -> bool:
        return self._img is not None

    def stop(self) -> None:
        pass


def stack_status() -> Dict[str, Any]:
    """检查旧视觉依赖是否能 import。"""
    out: Dict[str, Any] = {
        "shoe_vision": False,
        "ultralytics": False,
        "slot_check": False,
        "position": False,
        "message": "",
    }
    errs = []
    try:
        import shoe_vision_seg  # noqa: F401

        out["shoe_vision"] = True
    except Exception as e:
        errs.append(f"ShoeVision: {e}")
    try:
        import ultralytics  # noqa: F401

        out["ultralytics"] = True
    except Exception as e:
        errs.append(f"ultralytics: {e}")
    try:
        import slot_check  # noqa: F401

        out["slot_check"] = True
    except Exception as e:
        errs.append(f"slot_check: {e}")
    try:
        import position  # noqa: F401

        out["position"] = True
    except Exception as e:
        errs.append(f"Position: {e}")
    out["message"] = " | ".join(errs) if errs else "旧视觉栈可加载"
    return out


def vision_method(vis_cfg: Optional[dict]) -> str:
    """检测一律 YOLO。yaml 可写 yolo / legacy，其它值也按 YOLO 跑。"""
    return "yolo"


def listed_model_paths(vis_cfg: Optional[dict] = None) -> list[tuple[str, Path]]:
    """HMI 检查清单用：名称 + 路径。"""
    vis = vis_cfg if isinstance(vis_cfg, dict) else {}
    shoe = vis.get("shoe_vision") if isinstance(vis.get("shoe_vision"), dict) else {}
    toe = vis.get("toe_align") if isinstance(vis.get("toe_align"), dict) else {}
    slot = vis.get("slot_check") if isinstance(vis.get("slot_check"), dict) else {}
    pos = vis.get("position") if isinstance(vis.get("position"), dict) else {}

    def _p(raw, fallback: str) -> Path:
        text = str(raw or fallback or "").strip()
        p = Path(text) if text else Path(fallback)
        if not p.is_absolute():
            p = ROOT / p
        return p

    cfg_json = _shoe_cfg_path(vis)
    shoe_obb = last_cls = last_obb = ""
    try:
        import json

        data = json.loads(cfg_json.read_text(encoding="utf-8")) if cfg_json.exists() else {}
        if isinstance(data, dict):
            shoe_obb = str(data.get("shoe_model_path") or "")
            last_cls = str(data.get("shoe_cls_model_path") or "")
            last_obb = str(data.get("shoe_tree_model_path") or "")
    except Exception:
        pass
    return [
        ("皮带-鞋OBB", _p(shoe.get("shoe_model_path"), shoe_obb)),
        ("皮带-左右脚", _p(shoe.get("cls_model_path"), last_cls)),
        ("皮带-鞋楦OBB", _p(shoe.get("tree_model_path"), last_obb)),
        ("鞋头对位", _p(toe.get("model_path"), "models/toe_align/0722best.pt")),
        ("槽有无鞋", _p(slot.get("model_path"), "models/slot_check/7.10slot_check.pt")),
        ("取槽压杆", _p(pos.get("rod_model_path"), "models/position/rod/obb.pt")),
        ("ShoeVision配置", cfg_json),
    ]


def model_status_text(vis_cfg: Optional[dict] = None) -> str:
    bits = []
    for name, path in listed_model_paths(vis_cfg):
        mark = "✓" if path.exists() else "✗"
        bits.append(f"{mark}{name}")
    return " ".join(bits)


_sv = None
_sv_err = ""


def reset_shoe_vision() -> None:
    """json/模型改完后丢掉缓存，下次检测重新 from_config_file。"""
    global _sv, _sv_err
    _sv = None
    _sv_err = ""


def _shoe_cfg_path(vis_cfg: Dict[str, Any]) -> Path:
    raw = (vis_cfg.get("shoe_vision") or {}).get("config") if isinstance(vis_cfg.get("shoe_vision"), dict) else None
    p = Path(str(raw)) if raw else DEFAULT_SHOE_CFG
    if not p.is_absolute():
        p = ROOT / p
    return p


def get_shoe_vision(cameras: Optional[dict] = None, vis_cfg: Optional[dict] = None):
    """懒加载 ShoeVision，并把 cam1 接到 get_one_frame。"""
    global _sv, _sv_err
    if _sv is not None:
        if cameras and cameras.get("cam1") is not None:
            z = 400.0
            mock = vis_cfg.get("belt_pick_mock") if isinstance(vis_cfg, dict) else {}
            if isinstance(mock, dict):
                z = float(mock.get("z", z))
            _sv.camera = FrameAdapter(cameras["cam1"], fallback_z_mm=z)
        return _sv
    try:
        from shoe_vision_seg import ShoeVision

        path = _shoe_cfg_path(vis_cfg or {})
        _sv = ShoeVision.from_config_file(str(path), connect_camera=False)
        _sv_err = ""
        if cameras and cameras.get("cam1") is not None:
            z = 400.0
            mock = vis_cfg.get("belt_pick_mock") if isinstance(vis_cfg, dict) else {}
            if isinstance(mock, dict):
                z = float(mock.get("z", z))
            _sv.camera = FrameAdapter(cameras["cam1"], fallback_z_mm=z)
        return _sv
    except Exception as e:
        _sv_err = str(e)
        log.warning("ShoeVision 未能启动: %s", e)
        return None


def last_shoe_vision_error() -> str:
    return _sv_err


def detect_belt_legacy(cameras, vis_cfg, default_z, default_rx, default_ry):
    """
    皮带抓鞋：旧 ShoeVision → 基座 XYZ + yaw + 左右脚 + 鞋头偏移（机器人毫米）。
    返回 (result_dict, vis_bgr)
    """
    fail = {"ok": False, "message": "", "source": "legacy_yolo_handeye"}
    sv = get_shoe_vision(cameras, vis_cfg)
    if sv is None:
        fail["message"] = f"旧视觉未就绪（YOLO/手眼/模型）: {_sv_err or stack_status()['message']}"
        return fail, None

    try:
        from shoe_seg.shoes_seg import get_shoe_base_pose_toe_and_arc_points
    except Exception:
        left, right = sv.get_all_shoe_points()
        chosen_side = "left" if left else "right"
        poses = left if left else right
        if not poses:
            fail["message"] = "YOLO未检出鞋子"
            return fail, None
        x, y, z, yaw = poses[0]
        L = 120.0
        return {
            "ok": True,
            "x": float(x),
            "y": float(y),
            "z": float(z if z else default_z),
            "rx": float(default_rx),
            "ry": float(default_ry),
            "rz": float(yaw),
            "is_left_shoe": chosen_side == "left",
            "message": f"旧视觉抓鞋 {chosen_side} 楦心=({x:.1f},{y:.1f},{z:.1f}) yaw={yaw:.1f}（无鞋头分割）",
            "source": "legacy_yolo_handeye",
            "toe_offset_in_grasp_tcp": [0.0, L, 0.0],
            "shoe_length_mm": L,
        }, None

    chosen = get_shoe_base_pose_toe_and_arc_points(vision=sv, side="left", max_retries=3)
    vis = chosen.get("vis_frame")
    side = str(chosen.get("selected_side") or "")
    if side not in ("left", "right"):
        fail["message"] = "旧视觉未选到左右脚"
        return fail, vis
    prefix = "left" if side == "left" else "right"
    poses = chosen.get(f"{prefix}_base_poses") or []
    toes = chosen.get(f"{prefix}_toe_base_points") or []
    if not poses:
        fail["message"] = "旧视觉无楦心位姿"
        return fail, vis
    x, y, z, yaw = poses[0]
    toe = toes[0] if toes else None
    if toe is not None and len(toe) >= 2:
        length = float(np.hypot(float(toe[0]) - float(x), float(toe[1]) - float(y)))
        dz = float(toe[2]) - float(z) if len(toe) >= 3 else 0.0
    else:
        length = 120.0
        dz = 0.0
    msg = (
        f"旧视觉抓鞋 {('左' if side=='left' else '右')} "
        f"楦心XY=({x:.1f},{y:.1f}) Z={z:.1f} yaw={yaw:.1f} "
        f"鞋头距={length:.1f}mm（示教器）"
    )
    return {
        "ok": True,
        "x": float(x),
        "y": float(y),
        "z": float(z if abs(float(z)) > 1e-6 else default_z),
        "rx": float(default_rx),
        "ry": float(default_ry),
        "rz": float(yaw),
        "is_left_shoe": side == "left",
        "message": msg,
        "source": "legacy_yolo_handeye",
        "toe_offset_in_grasp_tcp": [0.0, float(length), float(dz)],
        "shoe_length_mm": float(length),
    }, vis


def classify_slot_occupied(image_bgr, vis_cfg: Optional[dict] = None) -> Tuple[Optional[bool], str, float]:
    """有鞋=True 没鞋=False；失败 (None, msg, 0)。"""
    try:
        from slot_check import SlotChecker
    except Exception as e:
        return None, f"slot_check 不可用: {e}", 0.0
    blk = (vis_cfg or {}).get("slot_check") if isinstance(vis_cfg, dict) else {}
    path = None
    if isinstance(blk, dict):
        path = blk.get("model_path")
    try:
        kw = {}
        resolved = _resolve_model_path(path) if path else None
        if resolved is not None:
            kw["model_path"] = resolved
        checker = SlotChecker(**kw) if kw else SlotChecker()
        r = checker.classify(image_bgr)
        cid = int(getattr(r, "class_id", -1))
        conf = float(getattr(r, "confidence", 0.0) or 0.0)
        if cid == 1:
            return True, f"有鞋 conf={conf:.2f}", conf
        if cid == 0:
            return False, f"空槽 conf={conf:.2f}", conf
        return None, f"未知类别 id={cid}", conf
    except Exception as e:
        return None, str(e), 0.0


def classify_toe_align(image_bgr, vis_cfg: Optional[dict] = None) -> Tuple[str, str]:
    """鞋头对位分类（旧 YOLO classify）。返回 (label, message)。"""
    blk = (vis_cfg or {}).get("toe_align") if isinstance(vis_cfg, dict) else {}
    raw = (blk or {}).get("model_path") if isinstance(blk, dict) else None
    model = _resolve_model_path(raw)
    if model is None:
        return "", "未配置 vision.toe_align.model_path"
    if not model.exists():
        return "", f"鞋头对位模型不存在: {model}"
    try:
        from ultralytics import YOLO

        m = YOLO(str(model))
        imgsz = int((blk or {}).get("imgsz", 256) or 256)
        res = m.predict(source=image_bgr, imgsz=imgsz, verbose=False)[0]
        names = getattr(res, "names", None) or getattr(m, "names", {}) or {}
        if hasattr(res, "probs") and res.probs is not None:
            idx = int(res.probs.top1)
            conf = float(res.probs.top1conf)
            label = str(names.get(idx, idx))
            return label, f"鞋头对位 {label}  conf={conf:.2f}"
        return "", "模型无分类输出"
    except Exception as e:
        return "", f"鞋头对位失败: {e}"


def _resolve_model_path(raw) -> Optional[Path]:
    if raw is None or str(raw).strip() == "":
        return None
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return p


def _cov_from_rotation_deg(degrees) -> list:
    """把 [1,0,0] 转到机器人基座（与旧 Position 相同）。"""
    vals = [float(v) for v in (degrees or [0, 0, 0])]
    while len(vals) < 3:
        vals.append(0.0)
    x_d, y_d, z_d = vals[:3]
    vec = np.array([[1.0], [0.0], [0.0]])
    rx = np.array(
        [
            [1, 0, 0],
            [0, np.cos(np.radians(x_d)), -np.sin(np.radians(x_d))],
            [0, np.sin(np.radians(x_d)), np.cos(np.radians(x_d))],
        ]
    )
    ry = np.array(
        [
            [np.cos(np.radians(y_d)), 0, np.sin(np.radians(y_d))],
            [0, 1, 0],
            [-np.sin(np.radians(y_d)), 0, np.cos(np.radians(y_d))],
        ]
    )
    rz = np.array(
        [
            [np.cos(np.radians(z_d)), -np.sin(np.radians(z_d)), 0],
            [np.sin(np.radians(z_d)), np.cos(np.radians(z_d)), 0],
            [0, 0, 1],
        ]
    )
    out = (rz @ ry @ rx @ vec).reshape(-1)
    n = float(np.linalg.norm(out))
    if n < 1e-9:
        return [1.0, 0.0, 0.0]
    return [float(out[0] / n), float(out[1] / n), float(out[2] / n)]


def measure_rod_offset_mm(
    cameras: Optional[dict],
    vis_cfg: Optional[dict],
    image_bgr: Any = None,
) -> Tuple[bool, float, float, float, Any, str]:
    """
    取料槽压杆/夹爪 X 距 → 机器人基座 XY 毫米（旧 Position 算法）。
    用本程序 cam4（可在 yaml 改），不另开 RSDT 相机。
    image_bgr 若传入则不再 grab（监控实时推演用）。
    返回 (ok, dx_mm, dy_mm, dz_mm, vis_bgr, message)
    """
    vis = vis_cfg if isinstance(vis_cfg, dict) else {}
    blk = vis.get("position") if isinstance(vis.get("position"), dict) else {}
    cam_key = str((blk or {}).get("camera") or "cam4")
    camera_id = int((blk or {}).get("camera_id") or 1)
    cam = (cameras or {}).get(cam_key) if cameras else None
    if image_bgr is not None:
        img = image_bgr
    else:
        if cam is None:
            return False, 0.0, 0.0, 0.0, None, f"没有相机 {cam_key}"
        try:
            img = cam.grab()
        except Exception as e:
            return False, 0.0, 0.0, 0.0, None, f"取图失败: {e}"
    if img is None:
        return False, 0.0, 0.0, 0.0, None, f"{cam_key} 无图"
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    if depth is None:
        h, w = img.shape[:2]
        z = float((blk or {}).get("fallback_z_mm") or 400.0)
        depth = np.full((h, w), z, dtype=np.float32)

    cfg_path = (blk or {}).get("config") or "position_config.yaml"
    p = _resolve_model_path(cfg_path) or (ROOT / "position_config.yaml")
    try:
        import yaml

        pos_cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"读 position_config 失败: {e}"

    model = (blk or {}).get("rod_model_path") or pos_cfg.get("rod_obb_model_path")
    model_p = _resolve_model_path(model)
    if model_p is None or not model_p.exists():
        return False, 0.0, 0.0, 0.0, img, f"压杆模型不存在: {model_p}"

    try:
        from position_detector import detect_with_roi_filter
        from position_geometry import build_robot_xyz_offset
        from position_obb import OBBOnlyDetector
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"Position 栈不可用: {e}"

    if camera_id == 2:
        k = pos_cfg.get("cam_right_K") or [600, 600, 640, 360]
        preset = pos_cfg.get("cam_right_gripper_preset_xyz") or [-0.08, 0.05, 0.37]
        rot = pos_cfg.get("cam_right_robot_base_rotation_degrees") or [0, 0, -43]
        roi_lt = pos_cfg.get("cam_right_rod_roi")
    else:
        k = pos_cfg.get("cam_left_K") or [600, 600, 640, 360]
        preset = pos_cfg.get("cam_left_gripper_preset_xyz") or [0.079, 0.037, 0.38]
        rot = pos_cfg.get("cam_left_robot_base_rotation_degrees") or [0, 0, 137]
        roi_lt = pos_cfg.get("cam_left_rod_roi")
        camera_id = 1

    cov = _cov_from_rotation_deg(rot)
    det = OBBOnlyDetector(str(model_p))
    det.set_parameters(
        obb_img_size=int(pos_cfg.get("rod_obb_img_size") or 640),
        obb_detection_conf=float(pos_cfg.get("rod_obb_detection_conf") or 0.4),
    )
    roi = None
    rw = int(pos_cfg.get("roi_width") or 600)
    rh = int(pos_cfg.get("roi_height") or 300)
    if isinstance(roi_lt, list) and roi_lt and isinstance(roi_lt[0], (list, tuple)):
        x1, y1 = int(roi_lt[0][0]), int(roi_lt[0][1])
        roi = [[x1, y1], [x1 + rw, y1 + rh]]
    shift = pos_cfg.get("rod_default_shift") or [[0, 0]]
    try:
        result, vis = detect_with_roi_filter(
            detector=det,
            rgb_image=img,
            depth_image=depth,
            fx=float(k[0]),
            fy=float(k[1]),
            cx=float(k[2]),
            cy=float(k[3]),
            shift=shift,
            roi=roi,
        )
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"压杆检测失败: {e}"
    xie_x = None
    for detection in result or []:
        if not detection or len(detection) < 6:
            continue
        if not detection[0]:
            continue
        if int(detection[1]) == 0:
            xie_x = float(detection[5])
            break
    if xie_x is None:
        return False, 0.0, 0.0, 0.0, vis if vis is not None else img, "未检出鞋或缺少深度"
    distance = xie_x - float(preset[0])
    xyz_m = build_robot_xyz_offset(camera_id, distance, cov, cov)
    dx, dy, dz = float(xyz_m[0]) * 1000.0, float(xyz_m[1]) * 1000.0, float(xyz_m[2]) * 1000.0
    msg = f"压杆偏移 cam{camera_id} dx={dx:.1f} dy={dy:.1f} mm（示教器） dist={distance:.4f}m"
    return True, dx, dy, dz, vis if vis is not None else img, msg
