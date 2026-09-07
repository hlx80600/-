"""深度图可视化：毫米数组 → BGR 伪彩，供预览 / 监控 / 快照。"""

from __future__ import annotations

from typing import Any

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

# 工位常见工作距（毫米）；伪彩拉伸范围。过窄会把有效点滤成全黑。
_DEPTH_MM_LO = 20.0
_DEPTH_MM_HI = 8000.0
# HMI 伪彩最长边（保持原图宽高比）。只缩小着色，避免全分辨率卡住界面。
_HMI_DEPTH_MAX_SIDE = 480
_MOCK_DEPTH_CACHE: dict[tuple[int, int, int], Any] = {}
_MOCK_VIS_CACHE: dict[tuple[int, int, int], Any] = {}


def mock_depth_mm(height: int, width: int, *, center_mm: float = 700.0) -> Any:
    """模拟碗状深度，方便无真机时也能看到伪彩。按尺寸缓存，避免每次重建。"""
    h = max(1, int(height))
    w = max(1, int(width))
    key = (h, w, int(center_mm))
    cached = _MOCK_DEPTH_CACHE.get(key)
    if cached is not None:
        return cached
    yy, xx = np.ogrid[0:h, 0:w]
    cx = (w - 1) * 0.5
    cy = (h - 1) * 0.5
    radius = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    arr = (float(center_mm) + radius * 0.85).astype(np.float32)
    _MOCK_DEPTH_CACHE[key] = arr
    return arr


def mock_depth_vis_bgr(height: int, width: int, *, center_mm: float = 700.0) -> Any:
    """模拟深度伪彩（已缩小）。供 Mock 相机直接用，避免勾选时现场算。"""
    h = max(1, int(height))
    w = max(1, int(width))
    key = (h, w, int(center_mm))
    cached = _MOCK_VIS_CACHE.get(key)
    if cached is not None:
        return cached
    vis = colorize_depth_mm(mock_depth_mm(h, w, center_mm=center_mm))
    if vis is not None:
        _MOCK_VIS_CACHE[key] = vis
    return vis


def depth_stats_text(depth: Any) -> str:
    """有效深度范围文案；抽样计算，不对整幅做 median。"""
    arr = _as_mm(depth)
    if arr is None:
        return ""
    step = max(1, int(max(arr.shape[0], arr.shape[1]) // 64) or 1)
    sample = arr[::step, ::step]
    valid = sample[(sample > _DEPTH_MM_LO) & (sample < _DEPTH_MM_HI)]
    if valid.size < 1:
        return "深度:无有效点"
    return f"深度:{float(valid.min()):.0f}~{float(valid.max()):.0f}mm"


def _downscale_depth(arr: Any) -> Any:
    """把深度缩到 HMI 伪彩尺寸，再做 colormap。"""
    if cv2 is None or arr is None:
        return arr
    h, w = int(arr.shape[0]), int(arr.shape[1])
    m = max(h, w)
    if m <= _HMI_DEPTH_MAX_SIDE:
        return arr
    scale = float(_HMI_DEPTH_MAX_SIDE) / float(m)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    return cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_NEAREST)


def colorize_depth_mm(
    depth: Any,
    *,
    color_hw: tuple[int, int] | None = None,
    z_min_mm: float = _DEPTH_MM_LO,
    z_max_mm: float = _DEPTH_MM_HI,
) -> Any:
    """深度毫米图转 JET 伪彩 BGR；无效像素为黑。

    先缩小再着色，避免全分辨率 Fancy index / colormap 卡住界面。
    color_hw 只允许再缩小，不允许放大回彩色全图。
    """
    if cv2 is None:
        return None
    try:
        arr = np.asarray(depth)
    except Exception:
        return None
    if arr.ndim < 2 or arr.size < 16:
        return None
    if arr.ndim > 2:
        arr = arr[:, :, 0]
    arr = _downscale_depth(arr)
    arr = arr.astype(np.float32, copy=False)
    lo = float(z_min_mm)
    hi = float(z_max_mm)
    if hi <= lo:
        hi = lo + 1.0
    valid = (arr > lo) & (arr < hi)
    if not np.any(valid):
        nz = arr[arr > 0]
        if nz.size >= 16:
            lo = float(np.percentile(nz, 2))
            hi = float(np.percentile(nz, 98))
            if hi <= lo:
                hi = lo + 1.0
            valid = (arr >= lo) & (arr <= hi)
    gray = np.zeros(arr.shape, dtype=np.uint8)
    if np.any(valid):
        clipped = np.clip(arr, lo, hi)
        gray[valid] = ((clipped[valid] - lo) * (255.0 / (hi - lo))).astype(np.uint8)
    vis = cv2.applyColorMap(gray, cv2.COLORMAP_JET)
    vis[~valid] = 0
    if color_hw is not None:
        th, tw = int(color_hw[0]), int(color_hw[1])
        if 0 < th <= vis.shape[0] and 0 < tw <= vis.shape[1]:
            if th != vis.shape[0] or tw != vis.shape[1]:
                vis = cv2.resize(vis, (tw, th), interpolation=cv2.INTER_NEAREST)
    cv2.putText(
        vis,
        "DEPTH",
        (8, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )
    return vis


def depth_placeholder_bgr(height: int, width: int, text: str = "NO DEPTH") -> Any:
    """无深度时的灰底提示图。"""
    h = max(8, int(height))
    w = max(8, int(width))
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (42, 42, 42)
    if cv2 is None:
        return img
    cv2.putText(
        img,
        text,
        (12, max(28, h // 2)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (180, 180, 180),
        2,
    )
    return img


def _letterbox_bgr(img: Any, height: int, width: int, *, fill: tuple[int, int, int] = (42, 42, 42)) -> Any:
    """把图放进 height×width，保持宽高比，不足处填色（不拉伸）。"""
    th = max(1, int(height))
    tw = max(1, int(width))
    src = np.asarray(img)
    if src.ndim == 2 and cv2 is not None:
        src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
    if src.ndim != 3 or src.shape[2] < 3:
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        canvas[:] = fill
        return canvas
    sh, sw = int(src.shape[0]), int(src.shape[1])
    if sh < 1 or sw < 1:
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        canvas[:] = fill
        return canvas
    if sh == th and sw == tw:
        return src[:, :, :3]
    scale = min(float(tw) / float(sw), float(th) / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    if cv2 is None:
        return src[:, :, :3]
    resized = cv2.resize(src[:, :, :3], (nw, nh), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    canvas[:] = fill
    x0 = (tw - nw) // 2
    y0 = (th - nh) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas


def hstack_color_depth(color_bgr: Any, depth_bgr: Any) -> Any:
    """彩色 | 深度：两格同高同宽，深度按自身比例信箱缩放，不压成窄条。"""
    if color_bgr is None:
        return depth_bgr
    if cv2 is None:
        return color_bgr
    color = np.asarray(color_bgr)
    h = int(color.shape[0])
    w = int(color.shape[1])
    if h < 1 or w < 1:
        return color
    if depth_bgr is None:
        right = depth_placeholder_bgr(h, w)
    else:
        right = _letterbox_bgr(depth_bgr, h, w)
    return np.hstack((color, right))


def _as_mm(depth: Any) -> Any:
    if depth is None:
        return None
    try:
        arr = np.asarray(depth)
    except Exception:
        return None
    if arr.ndim < 2 or arr.size < 16:
        return None
    if arr.ndim > 2:
        arr = arr[:, :, 0]
    return arr.astype(np.float32, copy=False)
