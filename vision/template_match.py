"""模板匹配：左右脚 / 有无料；皮带 Mock 取料点从 yaml 读取。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "config" / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MatchResult:
    ok: bool
    x: float = 0.0
    y: float = 0.0
    angle_deg: float = 0.0
    score: float = 0.0
    label: str = ""
    is_left_shoe: Optional[bool] = None
    has_material: Optional[bool] = None
    toe_offset_in_grasp_tcp: Optional[list] = None
    ncc: float = 0.0
    edge: float = 0.0
    contour: Optional[list] = None
    tmpl_w: int = 0
    tmpl_h: int = 0
    box: Optional[tuple] = None


def template_path(name: str) -> Path:
    return TEMPLATE_DIR / f"{name}.png"


def save_template(name: str, image_bgr) -> Path:
    path = template_path(name)
    if cv2 is not None:
        cv2.imwrite(str(path), image_bgr)
    return path


def delete_template(name: str) -> bool:
    from vision.shape_match import delete_shape_model

    return delete_shape_model(name)


def match_template(
    image,
    template_name: str,
    threshold: float | None = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> MatchResult:
    """形状匹配：掩膜+旋转+边缘复核。无模型文件时退回旧灰度相关。"""
    from vision.shape_match import match_shape_model

    return match_shape_model(image, template_name, threshold=threshold, cfg=cfg)


def detect_belt_shoes_mock(cfg: Optional[Dict[str, Any]] = None) -> List[MatchResult]:
    """
    无相机 / cam1 Mock：返回 yaml vision.belt_pick_mock.shoes 中的机器人基座 XY + Rz。
    真机手眼标定后不再走本函数，改由像素→基座转换。
    """
    shoes_cfg: List[Dict[str, Any]] = []
    if isinstance(cfg, dict):
        mock = cfg.get("belt_pick_mock") or {}
        raw = mock.get("shoes") if isinstance(mock, dict) else None
        if isinstance(raw, list):
            shoes_cfg = [s for s in raw if isinstance(s, dict)]

    if not shoes_cfg:
        # 兜底：靠近 pick_entry，避免不可达
        shoes_cfg = [
            {"x": -274.0, "y": -120.0, "rz": 77.9, "is_left_shoe": True, "name": "左鞋"},
            {"x": -240.0, "y": -100.0, "rz": 77.9, "is_left_shoe": False, "name": "右鞋"},
        ]

    from devices.pose_utils import is_left_shoe_flag

    shoes: List[MatchResult] = []
    for s in shoes_cfg:
        toe_off = s.get("toe_offset_in_grasp_tcp")
        if isinstance(toe_off, (list, tuple)) and len(toe_off) >= 3:
            toe_list = [float(toe_off[0]), float(toe_off[1]), float(toe_off[2])]
        else:
            # 默认：鞋头在抓取系 +Y 前方约 120mm（可按示教改 yaml）
            toe_list = [0.0, 120.0, 0.0]
        shoes.append(
            MatchResult(
                ok=True,
                x=float(s.get("x", 0)),
                y=float(s.get("y", 0)),
                angle_deg=float(s.get("rz", s.get("angle_deg", 0))),
                label=str(s.get("name", "shoe")),
                is_left_shoe=is_left_shoe_flag(s.get("is_left_shoe", True)),
                has_material=True,
                toe_offset_in_grasp_tcp=toe_list,
            )
        )
    # 操作工视角先取左侧：按基座 X 从小到大（可按现场再改排序规则）
    return sorted(shoes, key=lambda m: m.x)
