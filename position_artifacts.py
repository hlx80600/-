"""定位流程中的检测产物保存辅助模块。"""

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def save_detection_artifacts(
    enabled: bool,
    root: Path,
    task_name: str,
    camera_id: int,
    rgb_image,
    detect_result_image,
    result,
    extra_info: dict | None = None,
    depth_image=None,
):
    """保存原图和深度图。

    Args:
        enabled: 是否启用检测产物保存。
        root: 检测产物根目录。
        task_name: 逻辑任务名，例如 ``rod_distance``。
        camera_id: 相机编号。
        rgb_image: 原始 RGB 图像。
        detect_result_image: 兼容旧调用，当前不落盘。
        result: 兼容旧调用，当前不落盘。
        extra_info: 额外信息，用于判断 OK/NG。
        depth_image: 可选的深度图像（uint16，单位毫米），保存为 16-bit PNG。

    Returns:
        ``None``。函数通过写文件的副作用完成保存。"""
    if not enabled:
        return

    if rgb_image is None or not isinstance(rgb_image, np.ndarray) or rgb_image.ndim < 2:
        return

    extra_info = extra_info or {}
    outcome_label = "ok" if bool(extra_info.get("success")) else "ng"
    task_short_name_map = {
        "rod_distance": "rod",
        "slot_distance": "slot",
        "slot_reference_point_distance": "ref",
    }
    task_short_name = task_short_name_map.get(task_name, task_name)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    camera_name_map = {1: "cam_left", 2: "cam_right"}
    camera_name = camera_name_map.get(camera_id, f"cam{camera_id}")
    sample_id = f"{task_short_name}_{camera_name}_{outcome_label}_{timestamp}"
    root_dir = root / task_name / camera_name
    raw_dir = root_dir / "raw"
    depth_dir = root_dir / "depth"

    for directory in (raw_dir, depth_dir):
        directory.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{sample_id}.jpg"
    depth_path = depth_dir / f"{sample_id}.npy"

    cv2.imwrite(str(raw_path), rgb_image)

    if isinstance(depth_image, np.ndarray) and depth_image.ndim >= 2:
        np.save(str(depth_path), depth_image)
