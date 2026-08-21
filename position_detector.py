"""定位流程中的 ROI 过滤辅助模块。

本模块集中处理与图像空间相关的逻辑，包括 ROI 规范化、全图检测执行，
以及基于 ROI 的检测结果过滤和可视化。
"""

import cv2
import numpy as np


ROI_BOX_COLOR = (255, 0, 255)
ROI_TEXT_COLOR = (255, 0, 255)


def normalize_roi(roi, image_shape):
    """规范化两点式 ROI，并裁剪到图像边界范围内。

    Args:
        roi: 以 ``[[x1, y1], [x2, y2]]`` 形式定义的 ROI。
        image_shape: 原始图像的 shape。

    Returns:
        合法时返回标准化后的 ``(x, y, w, h)``；非法时返回 ``None``。
    """
    if roi is None or image_shape is None or len(image_shape) < 2:
        return None

    if not isinstance(roi, (list, tuple)) or len(roi) != 2:
        return None
    if not isinstance(roi[0], (list, tuple)) or not isinstance(roi[1], (list, tuple)):
        return None
    if len(roi[0]) != 2 or len(roi[1]) != 2:
        return None

    image_h, image_w = image_shape[:2]
    x1, y1 = (int(round(value)) for value in roi[0])
    x2, y2 = (int(round(value)) for value in roi[1])

    left = min(x1, x2)
    right = max(x1, x2)
    top = min(y1, y2)
    bottom = max(y1, y2)

    if left == right or top == bottom:
        return None

    x1 = max(0, min(image_w, left))
    y1 = max(0, min(image_h, top))
    x2 = max(0, min(image_w, right))
    y2 = max(0, min(image_h, bottom))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def detect_with_roi_filter(
    detector,
    rgb_image,
    depth_image,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    shift,
    roi=None,
):
    """执行全图检测，并在可选 ROI 下过滤结果。

    Args:
        detector: 提供 ``detect(...)`` 方法的检测器对象，接口与
            ``CasbotYoloP3D`` 兼容。
        rgb_image: 完整 RGB 图像。
        depth_image: 与 ``rgb_image`` 对齐的完整深度图。
        fx: 相机 x 方向焦距。
        fy: 相机 y 方向焦距。
        cx: 完整图坐标系下的主点 x 坐标。
        cy: 完整图坐标系下的主点 y 坐标。
        shift: 透传给检测器的类别偏移配置。
        roi: 可选 ROI，格式为 ``[[x1, y1], [x2, y2]]``。

    Returns:
        与检测器返回格式一致的二元组 ``(result, detect_result_image)``。
        当启用 ROI 时，仅保留中心点位于 ROI 内的目标。
    """
    normalized_roi = normalize_roi(
        roi,
        rgb_image.shape if isinstance(rgb_image, np.ndarray) else None,
    )
    result, detect_result_image = detector.detect(
        rgb_image,
        depth_image,
        fx,
        fy,
        cx,
        cy,
        shift,
    )
    if normalized_roi is None:
        return result, detect_result_image

    x0, y0, w, h = normalized_roi
    filtered_result = []
    roi_x1 = x0 + w
    roi_y1 = y0 + h

    for detection in result or []:
        center_x = None
        center_y = None
        if (
            len(detection) > 8
            and isinstance(detection[8], (list, tuple))
            and len(detection[8]) == 8
        ):
            obb = [float(value) for value in detection[8]]
            center_x = sum(obb[::2]) / 4.0
            center_y = sum(obb[1::2]) / 4.0
        elif len(detection) > 4:
            center_x = float(detection[3])
            center_y = float(detection[4])

        if center_x is None or center_y is None:
            continue
        if x0 <= center_x < roi_x1 and y0 <= center_y < roi_y1:
            filtered_result.append(detection)

    full_vis = (
        detect_result_image.copy()
        if isinstance(detect_result_image, np.ndarray) and detect_result_image.ndim >= 2
        else rgb_image.copy()
    )
    cv2.rectangle(
        full_vis,
        (x0, y0),
        (x0 + w - 1, y0 + h - 1),
        ROI_BOX_COLOR,
        2,
    )
    cv2.putText(
        full_vis,
        f"ROI({x0},{y0},{w},{h})",
        (x0 + 6, max(20, y0 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        ROI_TEXT_COLOR,
        2,
        lineType=cv2.LINE_AA,
    )
    return filtered_result, full_vis
