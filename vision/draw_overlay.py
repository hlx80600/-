"""检测结果叠图：细框 + 描边字，不铺实心底，少挡画面。"""

from __future__ import annotations

from typing import Any, Sequence

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore


def put_text_outline(
    image: Any,
    text: str,
    org: tuple[int, int],
    color: tuple[int, int, int],
    *,
    scale: float = 0.45,
    thickness: int = 1,
) -> None:
    """黑边描边字，不用色块底。"""
    if image is None or cv2 is None or not str(text):
        return
    x, y = int(org[0]), int(org[1])
    x = max(2, x)
    y = max(12, y)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        image,
        str(text),
        (x, y),
        font,
        float(scale),
        (0, 0, 0),
        int(thickness) + 2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        str(text),
        (x, y),
        font,
        float(scale),
        color,
        int(thickness),
        cv2.LINE_AA,
    )


def draw_obb_poly(
    image: Any,
    pts8: Sequence[float],
    color: tuple[int, int, int] = (255, 180, 40),
    *,
    thickness: int = 1,
) -> None:
    """画旋转框，只描边。"""
    if image is None or cv2 is None or len(pts8) < 8:
        return
    pts = np.array(
        [
            [float(pts8[0]), float(pts8[1])],
            [float(pts8[2]), float(pts8[3])],
            [float(pts8[4]), float(pts8[5])],
            [float(pts8[6]), float(pts8[7])],
        ],
        dtype=np.int32,
    )
    cv2.polylines(image, [pts], True, color, int(thickness), cv2.LINE_AA)


def draw_hud_lines(
    image: Any,
    lines: Sequence[str],
    *,
    ok: bool = True,
    anchor: str = "tl",
) -> None:
    """边角半透明条，状态不压在工件上。

    Args:
        image: BGR 图，原地绘制。
        lines: 文本行。
        ok: True 绿色 / False 红色。
        anchor: ``tl`` 左上，``bl`` 左下（避免和别的抬头叠在一起）。
    """
    if image is None or cv2 is None:
        return
    texts = [str(line) for line in lines if str(line).strip()]
    if not texts:
        return
    texts = texts[:6]
    h, w = int(image.shape[0]), int(image.shape[1])
    pad = 6
    line_h = 18
    bar_h = min(h, pad * 2 + line_h * len(texts))
    max_chars = max(len(t) for t in texts)
    bar_w = min(w, max(80, pad * 2 + max_chars * 9))
    x0 = 0
    y0 = max(0, h - bar_h) if str(anchor).lower() == "bl" else 0
    overlay = image.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + bar_w, y0 + bar_h), (16, 16, 16), -1)
    cv2.addWeighted(overlay, 0.42, image, 0.58, 0, dst=image)
    color = (80, 220, 80) if ok else (60, 60, 230)
    for i, line in enumerate(texts):
        put_text_outline(
            image,
            line[:80],
            (x0 + pad, y0 + pad + 12 + i * line_h),
            color,
            scale=0.42,
            thickness=1,
        )
