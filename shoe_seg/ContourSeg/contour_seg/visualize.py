"""可视化工具：mask 叠加、轮廓绘制、预览窗口。"""

import cv2
import numpy as np
from pathlib import Path

# 多目标时循环使用的调色板（BGR）
_PALETTE: list[tuple[int, int, int]] = [
    (0,   200,  80),   # 绿
    (0,   120, 255),   # 橙
    (220,  50,  50),   # 蓝
    (180,   0, 220),   # 紫
    (0,   220, 220),   # 黄
    (255, 100,   0),   # 青
    (50,  180, 255),   # 浅橙
    (120, 255,  50),   # 黄绿
]


def visualize(
    image_bgr: np.ndarray,
    mask_bool: np.ndarray,
    contour: np.ndarray,
    save_path: str | Path,
    color: tuple[int, int, int] = (0, 200, 80),
) -> None:
    """在图像上叠加单个 mask 和轮廓，并保存结果图。

    Parameters
    ----------
    image_bgr : np.ndarray
        原始 BGR 图像。
    mask_bool : np.ndarray
        布尔型 mask，形状与图像 H×W 一致。
    contour : np.ndarray
        轮廓点数组，形状 (N, 2)，int32。
    save_path : str or Path
        结果图保存路径。
    color : tuple
        mask 填充颜色（BGR）。
    """
    vis = _draw_mask_contour(image_bgr.copy(), mask_bool, contour, color)
    cv2.imwrite(str(save_path), vis)
    print(f"  \u2192 可视化已保存: {save_path}")


def visualize_multi(
    image_bgr: np.ndarray,
    items: list[tuple[np.ndarray, np.ndarray]],
    save_path: str | Path,
    labels: list[str] | None = None,
) -> None:
    """将多个 mask + 轮廓叠加在同一张图上，每个目标使用不同颜色。

    Parameters
    ----------
    image_bgr : np.ndarray
        原始 BGR 图像。
    items : list of (mask_bool, contour)
        每个元素是一个 ``(布尔 mask, 轮廓数组)`` 元组。
    save_path : str or Path
        结果图保存路径。
    labels : list of str, optional
        每个目标的标签文字（如 "Box 1"），长度须与 items 一致。
    """
    vis = image_bgr.copy()
    for i, (mask_bool, contour) in enumerate(items):
        color = _PALETTE[i % len(_PALETTE)]
        vis = _draw_mask_contour(vis, mask_bool, contour, color)
        # 在轮廓包围盒左上角绘制标签
        if labels and i < len(labels):
            x, y, w, h = cv2.boundingRect(contour.reshape(-1, 1, 2))
            cv2.putText(vis, labels[i], (x, max(y - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 3)
            cv2.putText(vis, labels[i], (x, max(y - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.imwrite(str(save_path), vis)
    print(f"  \u2192 合并可视化已保存: {save_path}")


def show_preview(
    image_bgr: np.ndarray,
    items: list[tuple[np.ndarray, np.ndarray]] | None = None,
    mask_bool: np.ndarray | None = None,
    contour: np.ndarray | None = None,
    window_name: str = "Segmentation Preview  [any key to close]",
) -> None:
    """弹出预览窗口，按任意键关闭。

    支持两种调用方式：

    * 多目标：``show_preview(image_bgr, items=[(mask, contour), ...])``
    * 单目标：``show_preview(image_bgr, mask_bool=mask, contour=contour)``
    """
    vis = image_bgr.copy()
    if items is not None:
        for i, (m, c) in enumerate(items):
            vis = _draw_mask_contour(vis, m, c, _PALETTE[i % len(_PALETTE)])
    elif mask_bool is not None and contour is not None:
        vis = _draw_mask_contour(vis, mask_bool, contour, _PALETTE[0])
    cv2.imshow(window_name, vis)
    cv2.waitKey(0)
    cv2.destroyWindow(window_name)


# ─────────────────────────── 内部工具 ───────────────────────────

def _draw_mask_contour(
    canvas: np.ndarray,
    mask_bool: np.ndarray,
    contour: np.ndarray,
    color: tuple[int, int, int],
) -> np.ndarray:
    """在 canvas 上叠加单个 mask 填充和轮廓线，原地修改并返回。"""
    overlay = canvas.copy()
    overlay[mask_bool] = color
    cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, canvas)
    cv2.drawContours(canvas, [contour.reshape(-1, 1, 2)], -1, color, 3)
    step = max(1, len(contour) // 60)
    dot_color = tuple(min(c + 40, 255) for c in color)
    for pt in contour[::step]:
        cv2.circle(canvas, tuple(pt), 5, dot_color, -1)
    return canvas
