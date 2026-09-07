"""视觉参数读写：皮带 json、压杆 yaml、default.yaml 的 vision 段。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POSITION = ROOT / "position_config.yaml"


def position_path(vis_cfg: Optional[dict] = None) -> Path:
    """压杆配置文件路径（默认仓库根 position_config.yaml）。"""
    raw = None
    blk = (vis_cfg or {}).get("position") if isinstance(vis_cfg, dict) else None
    if isinstance(blk, dict):
        raw = blk.get("config")
    p = Path(str(raw)) if raw else DEFAULT_POSITION
    if not p.is_absolute():
        p = ROOT / p
    return p


def load_position(vis_cfg: Optional[dict] = None) -> dict[str, Any]:
    """读压杆 yaml；缺文件返回空 dict。"""
    path = position_path(vis_cfg)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_position(data: dict[str, Any], vis_cfg: Optional[dict] = None) -> Path:
    """整表写回压杆 yaml（HMI 保存用）。"""
    if not data:
        raise ValueError("压杆配置为空，拒绝覆盖")
    path = position_path(vis_cfg)
    header = "# 本文件可由 HMI「视觉 → 视觉参数」保存覆盖。\n"
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(header + body, encoding="utf-8")
    return path


def rod_camera_id(vis_cfg: Optional[dict] = None) -> int:
    """1=左口 cam_left_*，2=右口 cam_right_*。"""
    blk = (vis_cfg or {}).get("position") if isinstance(vis_cfg, dict) else None
    if isinstance(blk, dict):
        try:
            return 2 if int(blk.get("camera_id") or 1) == 2 else 1
        except (TypeError, ValueError):
            return 1
    return 1


def parse_rod_roi(data: dict[str, Any], camera_id: int) -> tuple[int, int, int, int]:
    """压杆 ROI → (x, y, w, h)。"""
    key = "cam_right_rod_roi" if camera_id == 2 else "cam_left_rod_roi"
    raw = data.get(key)
    x, y = 0, 0
    if isinstance(raw, list) and raw:
        pt = raw[0] if isinstance(raw[0], (list, tuple)) else raw
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            x, y = int(pt[0]), int(pt[1])
    w = int(data.get("roi_width") or 600)
    h = int(data.get("roi_height") or 300)
    return x, y, w, h


def parse_k(data: dict[str, Any], camera_id: int) -> tuple[float, float, float, float]:
    """内参 [fx, fy, cx, cy]。"""
    key = "cam_right_K" if camera_id == 2 else "cam_left_K"
    raw = data.get(key) or [600.0, 600.0, 640.0, 360.0]
    vals = [float(v) for v in raw[:4]]
    while len(vals) < 4:
        vals.append(0.0)
    return vals[0], vals[1], vals[2], vals[3]


def parse_gripper_x(data: dict[str, Any], camera_id: int) -> float:
    """夹爪示教 X（米）。"""
    key = "cam_right_gripper_preset_xyz" if camera_id == 2 else "cam_left_gripper_preset_xyz"
    raw = data.get(key) or [0.0, 0.0, 0.0]
    if isinstance(raw, (list, tuple)) and raw:
        return float(raw[0])
    return 0.0


def write_rod_fields(
    data: dict[str, Any],
    *,
    camera_id: int,
    conf: float,
    imgsz: int,
    gripper_x: float,
    roi_xywh: tuple[int, int, int, int],
    k: tuple[float, float, float, float],
) -> None:
    """把压杆常用项写进已加载的 dict（原地改）。"""
    data["rod_obb_detection_conf"] = float(conf)
    data["rod_obb_img_size"] = int(imgsz)
    x, y, w, h = roi_xywh
    data["roi_width"] = int(w)
    data["roi_height"] = int(h)
    roi_key = "cam_right_rod_roi" if camera_id == 2 else "cam_left_rod_roi"
    k_key = "cam_right_K" if camera_id == 2 else "cam_left_K"
    g_key = "cam_right_gripper_preset_xyz" if camera_id == 2 else "cam_left_gripper_preset_xyz"
    data[roi_key] = [[int(x), int(y)]]
    data[k_key] = [float(v) for v in k]
    xyz = list(data.get(g_key) or [0.0, 0.0, 0.0])
    while len(xyz) < 3:
        xyz.append(0.0)
    xyz[0] = float(gripper_x)
    data[g_key] = xyz
