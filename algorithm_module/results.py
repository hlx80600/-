"""算法结果类型（与 VisionService 字段对齐，便于工位/HMI 直接用）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


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
    source: str = ""
    toe_offset_in_grasp_tcp: Optional[list] = None
    shoe_length_mm: float = 0.0
    vis_bgr: Any = None


@dataclass
class SlotResult:
    ok: bool
    has_material: bool = False
    is_left_slot: Optional[bool] = None
    message: str = ""
    confidence: float = 0.0
    vis_bgr: Any = None


@dataclass
class ToeAlignResult:
    ok: bool
    aligned: bool = False
    label: str = ""
    message: str = ""
    vis_bgr: Any = None


@dataclass
class RodOffsetResult:
    ok: bool
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    message: str = ""
    vis_bgr: Any = None
