"""投产算法：内参 / ROI / 手眼 / PickPose / 试走。

封装 vision.commission_actions，供 HMI「视觉采图」与脚本调用。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple


def write_intrinsics_from_calib(ctx, camera_id: str = "cam1") -> str:
    from vision import commission_actions as cact

    return cact.write_intrinsics_from_calib(ctx, camera_id)


def write_roi_ratio_from_file(ctx, camera_id: str = "cam1") -> str:
    from vision import commission_actions as cact

    return cact.write_roi_ratio_from_file(ctx, camera_id)


def record_handeye_sample(ctx, camera_id: str = "cam1", *, use_center: bool = False) -> str:
    from vision import commission_actions as cact

    return cact.record_handeye_sample(ctx, camera_id, use_center=use_center)


def solve_handeye_and_write(ctx, camera_id: str = "cam1", *, assumed_z_mm: float = 400.0) -> str:
    from vision import commission_actions as cact

    return cact.solve_handeye_and_write(ctx, camera_id, assumed_z_mm=assumed_z_mm)


def apply_belt_pick(ctx) -> Tuple[Any, str]:
    from vision import commission_actions as cact

    return cact.apply_belt_pick(ctx)


def pick_above_pose(ctx) -> dict:
    from vision import commission_actions as cact

    return cact.pick_above_pose(ctx)


def move_robot1_to_pick(ctx, *, above: bool = False) -> str:
    from vision import commission_actions as cact

    return cact.move_robot1_to_pick(ctx, above=above)


def checklist_lines(ctx) -> list:
    from vision import commission_actions as cact

    return list(cact.checklist_lines(ctx))


def find_chessboard(image_bgr, cols: int, rows: int):
    from vision import calib

    return calib.find_chessboard(image_bgr, cols, rows)


def calibrate_intrinsics(corners_list, image_size, cols: int, rows: int, square_mm: float):
    from vision import calib

    return calib.calibrate_intrinsics(corners_list, image_size, cols, rows, square_mm)


def save_calib(camera_id: str, data: dict) -> Any:
    from vision import calib

    return calib.save_calib(camera_id, data)


def load_calib(camera_id: str) -> Optional[dict]:
    from vision import calib

    return calib.load_calib(camera_id)


def save_roi(camera_id: str, roi_dict: dict) -> Any:
    from vision import roi

    return roi.save_roi(camera_id, roi_dict)


def load_roi(camera_id: str) -> Optional[dict]:
    from vision import roi

    return roi.load_roi(camera_id)
