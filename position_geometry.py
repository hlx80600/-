"""定位计算中的几何与可视化辅助模块。

除图像叠加绘制外，本模块中的函数尽量保持为纯计算逻辑。它们从
``position.py`` 中抽离出来，用于隔离测量数学与主流程控制代码。
"""

import cv2
import numpy as np


def get_obb_center_x(obb_points):
    """返回四点 OBB 的中心 X 坐标。"""
    return (obb_points[0] + obb_points[2] + obb_points[4] + obb_points[6]) / 4.0


def get_obb_center(obb_points):
    """返回四点 OBB 的中心像素坐标。"""
    return (
        int(
            round((obb_points[0] + obb_points[2] + obb_points[4] + obb_points[6]) / 4.0)
        ),
        int(
            round((obb_points[1] + obb_points[3] + obb_points[5] + obb_points[7]) / 4.0)
        ),
    )


def draw_reference_point_overlay(
    image, shoe_obb, ref_point_x: float, ref_point_y: float
):
    """在图像上绘制鞋中心、参考点和像素差标记。

    Args:
        image: 检测器返回的可视化图像。
        shoe_obb: 鞋目标的 OBB，格式为 ``[x1, y1, ..., x4, y4]``。
        ref_point_x: 参考点的 X 像素坐标。
        ref_point_y: 参考点的 Y 像素坐标。

    Returns:
        绘制完成后的新图像；如果输入图像无效，则原样返回。
    """
    if image is None or not isinstance(image, np.ndarray):
        return image

    if image.ndim < 2:
        return image

    vis = image.copy()
    img_h, img_w = vis.shape[:2]
    shoe_center = get_obb_center(shoe_obb)
    ref_x = int(round(ref_point_x))
    ref_y = int(round(ref_point_y))

    ref_x = max(0, min(img_w - 1, ref_x))
    ref_y = max(0, min(img_h - 1, ref_y))

    cv2.circle(vis, shoe_center, 6, (0, 255, 255), thickness=-1)
    cv2.putText(
        vis,
        "shoe_center",
        (shoe_center[0] + 8, max(20, shoe_center[1] - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 255),
        2,
        lineType=cv2.LINE_AA,
    )

    cv2.circle(vis, (ref_x, ref_y), 6, (0, 0, 255), thickness=-1)
    cv2.drawMarker(
        vis,
        (ref_x, ref_y),
        (0, 0, 255),
        markerType=cv2.MARKER_CROSS,
        markerSize=20,
        thickness=2,
    )
    cv2.putText(
        vis,
        f"ref_point({ref_x},{ref_y})",
        (ref_x + 8, max(20, ref_y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 255),
        2,
        lineType=cv2.LINE_AA,
    )

    line_y = shoe_center[1]
    cv2.line(vis, shoe_center, (ref_x, line_y), (255, 200, 0), 2, lineType=cv2.LINE_AA)
    cv2.line(vis, (ref_x, 0), (ref_x, img_h - 1), (0, 0, 255), 1, lineType=cv2.LINE_AA)
    cv2.putText(
        vis,
        f"dx_px={ref_x - shoe_center[0]}",
        (min(shoe_center[0], ref_x) + 8, max(25, line_y - 12)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 200, 0),
        2,
        lineType=cv2.LINE_AA,
    )
    return vis


def build_robot_xyz_offset(camera_id: int, distance: float, cam1_cov, cam2_cov):
    """根据相机方向向量，把标量距离展开成机器人 XYZ 偏移。

    Args:
        camera_id: 相机编号，目前支持 ``1`` 或 ``2``。
        distance: 标量位移，单位米。
        cam1_cov: 相机 1 在机器人基坐标系下的方向向量。
        cam2_cov: 相机 2 在机器人基坐标系下的方向向量。

    Returns:
        机器人基坐标系下的 ``[dx, dy, dz]`` 位移列表。

    Raises:
        ValueError: 相机编号不支持。
        RuntimeError: 对应相机的方向向量为空。
    """
    if camera_id == 1:
        cov = cam1_cov
    elif camera_id == 2:
        cov = cam2_cov
    else:
        raise ValueError(f"不支持的相机id: {camera_id}")

    if cov is None:
        raise RuntimeError(f"相机{camera_id}对应的机械臂方向向量未初始化")

    return [distance * axis_scale for axis_scale in cov]


def compute_slot_2d_distance(
    shoe_item,
    slot_item,
    detect_result_image,
    camera_id: int,
    pixel_to_meter_k: float,
    cam1_cov,
    cam2_cov,
):
    """用鞋和单个鞋槽的边缘差估算二维位移。

    Args:
        shoe_item: 鞋目标元组，格式为 ``(x_3d, y_3d, z_3d, obb)``。
        slot_item: 鞋槽目标元组，格式为 ``(x_3d, y_3d, z_3d, obb)``。
        detect_result_image: 需要继续向上传递的可视化图像。
        camera_id: 相机编号。
        pixel_to_meter_k: 像素到米的换算系数。
        cam1_cov: 相机 1 的方向向量。
        cam2_cov: 相机 2 的方向向量。

    Returns:
        与原 ``Position`` 流程兼容的四元组：
        ``(find_flag, robot_xyz_offset, detect_result_image, mode)``。
    """
    _, _, _, shoe_obb = shoe_item
    _, _, _, slot_obb = slot_item

    def edge_xs(obb_points):
        x1, _, x2, _, x3, _, x4, _ = obb_points
        sorted_x = sorted([x1, x2, x3, x4])
        left_x = (sorted_x[0] + sorted_x[1]) / 2.0
        right_x = (sorted_x[2] + sorted_x[3]) / 2.0
        return left_x, right_x

    shoe_center_x = get_obb_center_x(shoe_obb)
    slot_center_x = get_obb_center_x(slot_obb)
    shoe_left_x, shoe_right_x = edge_xs(shoe_obb)
    slot_left_x, slot_right_x = edge_xs(slot_obb)

    if shoe_center_x > slot_center_x:
        slot_edge_x = slot_right_x
        shoe_edge_x = shoe_left_x
    else:
        slot_edge_x = slot_left_x
        shoe_edge_x = shoe_right_x

    delta_u = slot_edge_x - shoe_edge_x
    distance = pixel_to_meter_k * delta_u
    print(
        f"[二维单槽] 像素边缘差: {delta_u:.2f} px, 近似距离: {distance:.4f} m "
        f"(K={pixel_to_meter_k:.6f} m/px)"
    )
    return (
        True,
        build_robot_xyz_offset(camera_id, distance, cam1_cov, cam2_cov),
        detect_result_image,
        "二维单槽",
    )


def compute_slot_reference_point_2d_distance(
    shoe_item,
    ref_point_x: float,
    ref_point_y: float,
    detect_result_image,
    camera_id: int,
    pixel_to_meter_k: float,
    cam1_cov,
    cam2_cov,
):
    """用鞋中心与预设参考点之间的像素差估算二维位移。

    Args:
        shoe_item: 鞋目标元组，格式为 ``(x_3d, y_3d, z_3d, obb)``。
        ref_point_x: 参考点 X 像素坐标。
        ref_point_y: 参考点 Y 像素坐标。
        detect_result_image: 需要绘制并继续返回的可视化图像。
        camera_id: 相机编号。
        pixel_to_meter_k: 像素到米的换算系数。
        cam1_cov: 相机 1 的方向向量。
        cam2_cov: 相机 2 的方向向量。

    Returns:
        与原 ``Position`` 流程兼容的四元组：
        ``(find_flag, robot_xyz_offset, detect_result_image, mode)``。
    """
    _, _, _, shoe_obb = shoe_item
    shoe_center_x = get_obb_center_x(shoe_obb)
    delta_u = ref_point_x - shoe_center_x
    distance = pixel_to_meter_k * delta_u
    detect_result_image = draw_reference_point_overlay(
        detect_result_image, shoe_obb, ref_point_x, ref_point_y
    )
    print(
        f"[二维参考点] 参考点与鞋中心像素差: {delta_u:.2f} px, 近似距离: {distance:.4f} m "
        f"(K={pixel_to_meter_k:.6f} m/px, ref_x={ref_point_x:.2f}, shoe_x={shoe_center_x:.2f})"
    )
    return (
        True,
        build_robot_xyz_offset(camera_id, distance, cam1_cov, cam2_cov),
        detect_result_image,
        "二维参考点",
    )
