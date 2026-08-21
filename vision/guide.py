"""相机2：眼在手上，引导鞋头贴紧槽边（改 XYR）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from vision.numpy_compat import np


@dataclass
class GuideResult:
    ok: bool
    dx: float = 0.0
    dy: float = 0.0
    drz: float = 0.0
    aligned: bool = False
    message: str = ""


def compute_edge_offset(image_bgr: Optional[Any], threshold_px: float = 8.0) -> GuideResult:
    """
    简易边缘偏移估计算法占位。
    真机：用槽边框特征/模板得到像素偏差，再经手眼标定转为基座 XYR。
    Mock：第一次给偏移，第二次认为已贴紧。
    """
    if image_bgr is None:
        return GuideResult(ok=False, message="无图像")
    mean = float(np.mean(image_bgr))
    dx = 2.0 if mean % 2 < 1 else 0.0
    dy = 1.0 if mean % 3 < 1 else 0.0
    drz = 0.5 if mean % 5 < 1 else 0.0
    aligned = abs(dx) < 0.1 and abs(dy) < 0.1 and abs(drz) < 0.1
    if abs(dx) + abs(dy) + abs(drz) < threshold_px:
        aligned = True
        dx = dy = drz = 0.0
    return GuideResult(ok=True, dx=dx, dy=dy, drz=drz, aligned=aligned)
