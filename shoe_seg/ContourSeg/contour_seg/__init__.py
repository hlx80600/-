"""
ContourSeg — 基于 SAM (Segment Anything Model) 的目标外轮廓提取库。

公开 API
--------
>>> from contour_seg import load_sam, pick_mask_by_box, pick_mask_auto
>>> from contour_seg import extract_outer_contour, process_image
"""

from contour_seg.model import load_sam
from contour_seg.core import (
    pick_mask_by_box,
    pick_mask_auto,
    extract_outer_contour,
    process_image,
    obb_to_xyxy,
    _mask_border_touch_count,
)
from contour_seg.visualize import visualize, visualize_multi, show_preview
from contour_seg.interactive import draw_boxes_interactively, click_quad_points_interactively

__all__ = [
    "load_sam",
    "pick_mask_by_box",
    "pick_mask_auto",
    "extract_outer_contour",
    "process_image",
    "obb_to_xyxy",
    "visualize",
    "visualize_multi",
    "show_preview",
    "draw_boxes_interactively",
    "click_quad_points_interactively",
    "_mask_border_touch_count",
]
