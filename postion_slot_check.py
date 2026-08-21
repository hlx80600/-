
from ultralytics import YOLO
import cv2
import numpy as np

_models: dict[str, YOLO] = {}


def get_slot_check_model(model_path: str):
    if model_path not in _models:
        _models[model_path] = YOLO(model_path)
    return _models[model_path]


def slot_check(
    depth_color_img,
    model_path: str,
    img_conf: float,
    img_size: int,
    roi_start,
    roi_size,
):
    model = get_slot_check_model(model_path)
    results = model(
        crop_img_to_roi(depth_color_img, roi_start=roi_start, roi_size=roi_size),
        conf=img_conf,
        imgsz=img_size,
    )

    slot_probs = {}
    for result in results:
        for cls_id, prob in enumerate(result.probs.data):
            cls_name = result.names[cls_id]
            slot_probs[cls_name] = float(prob)
            # print(cls_name, f"{float(prob):.2f}")

    return slot_probs


def crop_img_to_roi(image, roi_start, roi_size):
    height, width = image.shape[:2]
    x1, y1 = roi_start
    roi_width, roi_height = roi_size
    x2 = x1 + roi_width
    y2 = y1 + roi_height

    if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
        raise ValueError(
            f'ROI 超出图片范围: image_size=({width}, {height}), '
            f'roi_start={roi_start}, roi_size={roi_size}'
        )

    return image[y1:y2, x1:x2]


def build_depth_color(
    rgb_image,
    depth_data,
    alpha,
    beta,
    color_start_depth_mm,
    color_range_mm,
    min_depth_mm,
    max_depth_mm,
):
    """按 orbbec_camera_driver.get_one_frame(True) 的方式合成 depth_color。"""
    invalid_mask = (depth_data < min_depth_mm) | (depth_data > max_depth_mm)
    filtered_depth = depth_data.copy()
    filtered_depth[invalid_mask] = 0

    valid_mask = filtered_depth > 0
    if np.any(valid_mask):
        color_start = color_start_depth_mm
        if color_start is None:
            color_start = float(filtered_depth[valid_mask].min())
        color_end = color_start + color_range_mm
        clipped_depth = np.clip(filtered_depth, color_start, color_end)
        depth_gray = np.zeros(filtered_depth.shape, dtype=np.uint8)
        depth_gray[valid_mask] = (
            (clipped_depth[valid_mask] - color_start) * 255.0 / color_range_mm
        ).astype(np.uint8)
    else:
        depth_gray = np.zeros(filtered_depth.shape, dtype=np.uint8)

    depth_image = cv2.applyColorMap(depth_gray, cv2.COLORMAP_JET)

    gray_rgb = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2GRAY)
    rgb_image = cv2.cvtColor(gray_rgb, cv2.COLOR_GRAY2BGR)

    return cv2.addWeighted(rgb_image, alpha, depth_image, beta, 0)


def is_slot_position_good(
    rgb_img,
    depth_img,
    model_path: str,
    img_conf: float,
    img_size: int,
    alpha,
    beta,
    color_start_depth_mm,
    color_range_mm,
    min_depth_mm,
    max_depth_mm,
    roi_start,
    roi_size,
):
    colored_depth = build_depth_color(
        rgb_img,
        depth_img,
        alpha=alpha,
        beta=beta,
        color_start_depth_mm=color_start_depth_mm,
        color_range_mm=color_range_mm,
        min_depth_mm=min_depth_mm,
        max_depth_mm=max_depth_mm,
    )
    slot_result = slot_check(
        colored_depth,
        model_path=model_path,
        img_conf=img_conf,
        img_size=img_size,
        roi_start=roi_start,
        roi_size=roi_size,
    )
    if not slot_result:
        return False

    cls_name = 'unkown'
    good_prob = slot_result.get('good', 0.0)
    bad_prob = slot_result.get('bad', 0.0)

    if good_prob >= img_conf or bad_prob >= img_conf:
        if good_prob >= bad_prob:
            cls_name = 'good'
        else:
            cls_name = 'bad'

    if cls_name == 'good':
        return True

    if cls_name == 'bad':
        return False

    if cls_name in ('unkown', 'unknown'):
        return False

    return False
