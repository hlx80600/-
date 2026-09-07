"""主界面整体比例：按窗口相对设计分辨率缩放间距/字号。

用矢量字号而不是把整窗画成位图再拉伸，指引和汉字保持清晰。
字号有下限，窗口缩得很小时文字不会细到看不清。
"""

from __future__ import annotations

# 设计稿：侧栏+内容在这个尺寸下最舒服；更大窗口等比放大，更小则缩小
DESIGN_W = 1280
DESIGN_H = 800
MIN_SCALE = 0.78
MAX_SCALE = 1.35
_STEP = 0.05

_scale: float = 1.0


def scale() -> float:
    """当前界面比例，1.0 = 设计尺寸。"""
    return float(_scale)


def compute(width: int, height: int) -> float:
    """由窗口客户区算出比例，并量化，避免拖拽时每像素都刷新样式。"""
    w = max(1, int(width))
    h = max(1, int(height))
    raw = min(float(w) / float(DESIGN_W), float(h) / float(DESIGN_H))
    raw = max(MIN_SCALE, min(MAX_SCALE, raw))
    stepped = round(raw / _STEP) * _STEP
    return float(max(MIN_SCALE, min(MAX_SCALE, round(stepped, 2))))


def set_scale(value: float) -> bool:
    """写入新比例。有变化返回 True。"""
    global _scale
    nxt = float(max(MIN_SCALE, min(MAX_SCALE, value)))
    if abs(nxt - _scale) < 0.001:
        return False
    _scale = nxt
    return True


def px(design: float, *, min_v: int = 1) -> int:
    """设计像素 → 当前像素；min_v 防止箭头/描边被压没。"""
    return max(int(min_v), int(round(float(design) * _scale)))


def font_px(design: float, *, min_v: int = 12) -> int:
    """界面字号（px）。下限保证缩小后仍能读。"""
    return max(int(min_v), int(round(float(design) * _scale)))


def font_pt(design: float, *, min_pt: float = 10.0) -> float:
    """QFont 点数。CJK 建议 min_pt=10。"""
    return max(float(min_pt), float(design) * _scale)
