"""兼容转发：实现已迁到 visualize_module.frames。"""

from visualize_module.frames import (  # noqa: F401
    CAM_IDS,
    CAM_TITLES,
    annotate_bgr,
    copy_bgr,
    draw_point_pair,
    draw_roi,
)

__all__ = [
    "CAM_IDS",
    "CAM_TITLES",
    "annotate_bgr",
    "copy_bgr",
    "draw_point_pair",
    "draw_roi",
]
