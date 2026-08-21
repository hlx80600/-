"""鞋子视觉推理与相机/手眼集成模块。

本文件提供：
1) 一组与具体业务无关的工具函数（JSON 读取、裁剪/填充、坐标变换、结果渲染等）。
2) `ShoeVision` 业务类：
    - 负责加载 YOLO 模型与 `CasbotYoloP3D`
    - 根据配置文件初始化 Orbbec 相机（含单例复用）
    - 加载手眼矩阵并在实时循环中输出相机坐标/基座坐标

约定：
- 配置文件里已有的参数（相机 id/sn、分辨率、fps、曝光/增益、handeye）以配置为准。
- 工具函数以 `_` 前缀开头，表示仅供本模块内部使用。

使用示例：返回左右脚鞋楦中心点+角度（基坐标系）
```python
from shoe_vision import ShoeVision
shoe_vision = ShoeVision.from_config_file()
left_points, right_points = shoe_vision.get_all_shoe_points()
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import json
import math
import os
import time
from pathlib import Path

import torch

import cv2
import numpy as np

# CasbotYoloP3D / yoloOBB360 会把 ultralytics.* 别名到 ultralytics_obb360；
# 必须先加载该栈，再用其 YOLO，否则 site-packages AutoBackend 与 obb360 export_formats 不匹配
# 会触发 ValueError: too many values to unpack (expected 19)。
from casbot_yolo_point4d.casbot_yolo_point4d import CasbotYoloP3D
from ultralytics_obb360 import YOLO
from RSDT_Simple_Automation.automation_machine import automationMachine
try:
    # 该目录是一个namespace package（无__init__.py），需要显式导入driver里的类
    from RSDT_Simple_Automation.hardware_module.orbbec_camera.orbbec_camera_driver import orbbec_camera
except Exception:  # 允许在无相机依赖环境下仍可加载本脚本（例如只跑离线图片）
    orbbec_camera = object

CAMERA_COLOR_RES = [1280, 720]
CAMERA_DEPTH_RES = [848, 480]
CAMERA_FPS = 30
ORIGINAL_IMAGE_SAVE_DIR = Path("/home/casbot/robot/Casbot_Press_Shoes/runs/original")

# 在模型推理前添加
def print_gpu_memory():
    if torch.cuda.is_available():
        print(f"GPU Memory allocated: {torch.cuda.memory_allocated()/1024**2:.2f} MB")
        print(f"GPU Memory cached: {torch.cuda.memory_reserved()/1024**2:.2f} MB")
        print(f"GPU Memory max allocated: {torch.cuda.max_memory_allocated()/1024**2:.2f} MB")


def get_roi_xyxy(image_shape, roi_ratio):
    h, w = image_shape[:2]
    x1 = int(w * roi_ratio[0])
    y1 = int(h * roi_ratio[1])
    x2 = int(w * roi_ratio[2])
    y2 = int(h * roi_ratio[3])
    # clamp
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w - 1, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h - 1, y2))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def point_in_roi(x, y, roi_xyxy):
    x1, y1, x2, y2 = roi_xyxy
    return (x1 <= x <= x2) and (y1 <= y <= y2)


def _default_config_path() -> Path:
    """获取默认配置文件路径。

    优先级：
    1) 环境变量 `SHOE_VISION_CONFIG` 指定的路径
    2) 与本脚本同目录下的 `shoe_vision_config.json`

    Returns:
        默认配置文件的 Path。
    """
    env_p = os.getenv("SHOE_VISION_CONFIG")
    if env_p:
        return Path(env_p)
    return Path(__file__).with_name("shoe_vision_config.json")


def _load_json_config(path: Path) -> Dict[str, object]:
    """从磁盘读取 JSON 配置并返回 dict。

    Args:
        path: JSON 文件路径。

    Returns:
        解析后的配置字典。

    Raises:
        FileNotFoundError: 文件不存在。
        json.JSONDecodeError: JSON 不合法。
        ValueError: JSON 根节点不是对象（dict）。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object/dict, got {type(data)}")
    return data


def _try_float(v: object) -> Optional[float]:
    """把输入转换为 float。

    Args:
        v: 任意对象。

    Returns:
        - 转换成功返回 float
        - 输入为 None 或转换失败返回 None
    """
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None



def _normalize_deg_360(deg: float) -> float:
    """把角度归一化到 [0, 360) 区间。

    Args:
        deg: 任意角度（度）。

    Returns:
        归一化后的角度（度）。
    """
    return (float(deg) % 360.0 + 360.0) % 360.0


def _normalize_deg_180(deg: float) -> float:
    """把角度归一化到 [-180, 180) 区间。"""
    out = _normalize_deg_360(float(deg))
    if out >= 180.0:
        out -= 360.0
    return out


def _angle_diff_deg(a: float, b: float) -> float:
    """计算两个角度在圆上的最小夹角（度），范围 [0, 180]。"""
    da = _normalize_deg_360(float(a))
    db = _normalize_deg_360(float(b))
    d = abs(da - db) % 360.0
    return float(min(d, 360.0 - d))

def debug_visualize_crop(crop, shoe_center_crop, tree_center_crop, 
     toe_line_angle, toe_dir_cls, save_path):
    """可视化抠图中的关键点"""
    vis = crop.copy()
    
    # 画鞋子中心（绿色）
    cv2.circle(vis, 
               (int(shoe_center_crop[0]), int(shoe_center_crop[1])), 
               5, (0, 255, 0), -1)
    
    # 画鞋楦中心（红色）  
    cv2.circle(vis,
               (int(tree_center_crop[0]), int(tree_center_crop[1])),
               5, (0, 0, 255), -1)
    
    # 画连线（黄色）
    cv2.line(vis,
             (int(shoe_center_crop[0]), int(shoe_center_crop[1])),
             (int(tree_center_crop[0]), int(tree_center_crop[1])),
             (0, 255, 255), 2)
    
    # 标注角度
    cv2.putText(vis, f"line_angle: {toe_line_angle:.1f}", 
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                0.7, (255, 255, 255), 2)
    cv2.putText(vis, f"cls_dir: {toe_dir_cls}", 
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 
                0.7, (255, 255, 255), 2)
    
    cv2.imwrite(save_path, vis)


def _depth_value_at(depth: Optional[np.ndarray], x_color: float, y_color: float, color_shape: Tuple[int, int], kernel_size: int = 4) -> Optional[float]:
    """从深度图中取邻域中值，提高鲁棒性。
    """
    if depth is None:
        return None
    if depth.ndim < 2:
        return None
    dh, dw = int(depth.shape[0]), int(depth.shape[1])
    if dh <= 0 or dw <= 0:
        return None

    xd = int(round(float(x_color)))
    yd = int(round(float(y_color)))

    if xd < 0 or xd >= dw or yd < 0 or yd >= dh:
        return None
    
    # 取邻域中值
    half_k = kernel_size // 2
    x1 = max(0, xd - half_k)
    x2 = min(dw, xd + half_k + 1)
    y1 = max(0, yd - half_k)
    y2 = min(dh, yd + half_k + 1)
    
    if x2 <= x1 or y2 <= y1:
        return None
        
    neighborhood = depth[y1:y2, x1:x2]
    valid_values = neighborhood[(neighborhood > 0) & np.isfinite(neighborhood)]
    
    if len(valid_values) == 0:
        return None
        
    return float(np.median(valid_values))


def _pixel_to_cam_xyz(x: float, y: float, z: float, fx: float, fy: float, cx: float, cy: float) -> Tuple[float, float, float]:
    """针孔相机模型下，将像素点反投影到相机坐标系。

    Args:
        x, y: 像素坐标。
        z: 深度（与内参单位一致，通常是 mm）。
        fx, fy, cx, cy: 相机内参。

    Returns:
        (X, Y, Z) 相机坐标系下的三维点。
    """
    X = (float(x) - float(cx)) * float(z) / float(fx)
    Y = (float(y) - float(cy)) * float(z) / float(fy)
    Z = float(z)
    return X, Y, Z


def _transform_point(T: np.ndarray, p: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """使用 4x4 齐次变换矩阵变换一个三维点。

    Args:
        T: 4x4 变换矩阵。
        p: (x, y, z) 三维点。

    Returns:
        变换后的三维点。
    """
    v = np.array([p[0], p[1], p[2], 1.0], dtype=np.float64)
    o = (T @ v)[:3]
    return float(o[0]), float(o[1]), float(o[2])


def _transform_dir(T: np.ndarray, v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """使用 4x4 变换矩阵的旋转部分变换一个方向向量。

    Args:
        T: 4x4 变换矩阵。
        v: (vx, vy, vz) 方向向量。

    Returns:
        旋转后的方向向量（不含平移）。
    """
    R = np.asarray(T, dtype=np.float64)[:3, :3]
    o = R @ np.array([v[0], v[1], v[2]], dtype=np.float64)
    return float(o[0]), float(o[1]), float(o[2])


def _compute_base_pose_from_image(
    hand_eye_T: np.ndarray,
    center_xy: Tuple[float, float],
    angle_deg_image: float,
    depth: Optional[np.ndarray],
    color_shape_hw: Tuple[int, int],
    fx: float,
    fy: float,
    cx: float,
    cy: float,
) -> Tuple[
    Optional[Tuple[float, float, float]],
    Optional[Tuple[float, float, float]],
    Optional[float],
]:
    """结合手眼矩阵，把图像上的中心点+方向角转换到基座坐标系。

    流程：
    1) 从 depth 取该像素深度 z
    2) 由内参将像素反投影得到相机坐标系点
    3) 用 hand-eye 变换到基座坐标系
    4) 将图像平面方向（角度）映射为相机系方向向量，再用旋转变换到基座系

    Args:
        hand_eye_T: 4x4 手眼矩阵（cam->base）。
        center_xy: 图像像素中心点 (x, y)。
        angle_deg_image: 图像平面角度（度），0 度指向 +x。
        depth: 深度图。
        color_shape_hw: 彩色图像形状 (H, W)。
        fx, fy, cx, cy: 相机内参。

    Returns:
        (cam_xyz, base_xyz, base_yaw_deg)
        - cam_xyz: 相机坐标系点 (X,Y,Z)
        - base_xyz: 基座坐标系点 (X,Y,Z)
        - base_yaw_deg: 基座系平面 yaw（度，归一化到 [-180,180)）

    Notes:
        若深度无效或矩阵形状不对，会返回 (None, None, None)。
    """
    if hand_eye_T is None:
        return None, None, None
    T = np.asarray(hand_eye_T, dtype=np.float64)
    if T.shape != (4, 4):
        return None, None, None

    x, y = float(center_xy[0]), float(center_xy[1])
    z = _depth_value_at(depth, x, y, color_shape_hw)
    if z is None:
        return None, None, None

    cam_xyz = _pixel_to_cam_xyz(x, y, z, fx=fx, fy=fy, cx=cx, cy=cy)
    base_xyz = _transform_point(T, cam_xyz)

    th = math.radians(float(angle_deg_image))
    v_cam = (math.cos(th), math.sin(th), 0.0)
    v_base = _transform_dir(T, v_cam)
    base_yaw = _normalize_deg_180(math.degrees(math.atan2(v_base[1], v_base[0])))
    return cam_xyz, base_xyz, base_yaw


def _resolve_local_offset_xy(
    offset_cfg: object,
    class_id: Optional[int] = None,
) -> Tuple[float, float]:
    """解析局部偏移配置。

    支持两种格式：
    1) `[x, y]`：全局统一偏移
    2) `[[x0, y0], [x1, y1], ...]`：按类别配置偏移
    """
    if isinstance(offset_cfg, (list, tuple)):
        if len(offset_cfg) >= 2 and all(isinstance(v, (int, float)) for v in offset_cfg[:2]):
            return float(offset_cfg[0]), float(offset_cfg[1])

        if (
            class_id is not None
            and 0 <= int(class_id) < len(offset_cfg)
            and isinstance(offset_cfg[int(class_id)], (list, tuple))
            and len(offset_cfg[int(class_id)]) >= 2
        ):
            item = offset_cfg[int(class_id)]
            if all(isinstance(v, (int, float)) for v in item[:2]):
                return float(item[0]), float(item[1])
    return 0.0, 0.0


def _apply_local_xy_offset(
    center_xy: Tuple[float, float],
    angle_deg: float,
    offset_xy: Tuple[float, float],
    local_scale_xy: Tuple[float, float] = (1.0, 1.0),
) -> Tuple[float, float]:
    """按局部坐标系偏移中心点。

    定义：
    - 局部 X：鞋楦修正后的正方向
    - 局部 Y：局部 X 顺时针旋转 90° 的垂直方向（与当前图像角度定义一致）

    说明：
    - `offset_xy` 默认按“局部尺寸比例”解释；实际像素偏移量为
      `(offset_x * scale_x, offset_y * scale_y)`。
    - 当 `local_scale_xy=(1,1)` 时，可退化为直接按像素偏移。
    """
    ox = float(offset_xy[0]) * float(local_scale_xy[0])
    oy = float(offset_xy[1]) * float(local_scale_xy[1])
    if abs(ox) + abs(oy) <= 1e-12:
        return float(center_xy[0]), float(center_xy[1])

    th = math.radians(float(angle_deg))
    dir_x = math.cos(th)
    dir_y = math.sin(th)
    perp_x = -math.sin(th)
    perp_y = math.cos(th)

    dx = ox * dir_x + oy * perp_x
    dy = ox * dir_y + oy * perp_y
    return float(center_xy[0] + dx), float(center_xy[1] + dy)


def _obb_local_scale_from_points(
    obb_pts_xy: Union[List[float], Tuple[float, ...], np.ndarray],
    quarter_turns: int = 0,
) -> Tuple[float, float]:
    """根据 OBB 四点计算局部 X/Y 的尺寸。

    约定与 `shift_center_by_obb_scale` 一致：
    - 原始局部 X 对应 p3->p2
    - 原始局部 Y 对应 p2->p1

    当方向修正发生 90°/270° 旋转时，局部 X/Y 尺寸需要交换。
    """
    pts = np.asarray(obb_pts_xy, dtype=np.float32).reshape(4, 2)
    p1, p2, p3, _p4 = pts

    x_scale = float(np.hypot(*(p2 - p3)))
    y_scale = float(np.hypot(*(p1 - p2)))

    if int(quarter_turns) % 2 == 1:
        x_scale, y_scale = y_scale, x_scale
    return x_scale, y_scale


def _to_numpy(x):
    """将输入安全转换为 numpy.ndarray。

    Args:
        x: 可能是 numpy、list、或 torch.Tensor。

    Returns:
        - torch.Tensor: detach/cpu 后转 numpy
        - 其他: 使用 np.asarray

    Notes:
        这里用 try/except 避免在无 torch 环境下导入失败。
    """
    if x is None:
        return None
    try:
        import torch

        if isinstance(x, torch.Tensor):
            return x.detach().cpu().numpy()
    except Exception:
        pass
    return np.asarray(x)


def _expand_polygon(pts: np.ndarray, expand_ratio: float) -> np.ndarray:
    """按比例围绕多边形中心点做等比例放缩。

    Args:
        pts: 形状 (4,2) 或可 reshape 成 (4,2) 的四边形顶点。
        expand_ratio: 扩张比例，例如 0.1 表示扩大 10%。

    Returns:
        放缩后的四边形点集 (4,2)。
    """
    if expand_ratio == 0:
        return pts
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    center = pts.mean(axis=0, keepdims=True)
    factor = 1.0 + float(expand_ratio)
    return center + (pts - center) * factor


def _pad_to_square(
    image_bgr: np.ndarray,
    target_size: int,
    border_type: str = "constant",
    pad_value: int = 0,
) -> np.ndarray:
    """把任意长宽比图像缩放并填充到正方形尺寸。

    Args:
        image_bgr: 输入图像。
        target_size: 输出正方形边长。
        border_type: 填充方式，支持 constant/replicate/reflect/reflect101/wrap。
        pad_value: constant 模式下的填充值（灰度）。

    Returns:
        (target_size, target_size, 3) 的 BGR 图像。

    Raises:
        ValueError: 输入图像 shape 非法或 border_type 不支持。
    """
    if image_bgr is None:
        raise ValueError("image_bgr is None")
    h, w = image_bgr.shape[:2]
    if h <= 0 or w <= 0:
        raise ValueError(f"Invalid image shape: {image_bgr.shape}")

    target_size = int(target_size)
    scale = min(target_size / w, target_size / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_w = target_size - new_w
    pad_h = target_size - new_h
    pad_left = pad_w // 2
    pad_right = pad_w - pad_left
    pad_top = pad_h // 2
    pad_bottom = pad_h - pad_top

    bt = (border_type or "constant").lower()
    if bt == "constant":
        border_mode = cv2.BORDER_CONSTANT
        value = (int(pad_value), int(pad_value), int(pad_value))
        return cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right, border_mode, value=value)
    if bt == "replicate":
        border_mode = cv2.BORDER_REPLICATE
    elif bt == "reflect":
        border_mode = cv2.BORDER_REFLECT
    elif bt == "reflect101" or bt == "reflect_101":
        border_mode = cv2.BORDER_REFLECT_101
    elif bt == "wrap":
        border_mode = cv2.BORDER_WRAP
    else:
        raise ValueError(f"Unknown border_type: {border_type}")

    return cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right, border_mode)


def _obb360_style_crop_square(
    image_bgr: np.ndarray,
    pts_xy: np.ndarray,
    target_size: int,
    expand_ratio: float,
    clockwise: bool,
    pad_mode: str,
    pad_value: int,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[Tuple[int, int]], Optional[Tuple[float, float]]]:
    """按 yoloOBB360 风格抠图并输出正方形输入。

    步骤：
    1) 取四边形的外接 bbox 裁剪
    2) 对裁剪后的局部坐标做透视变换，得到正交矩形
    3) 缩放+padding 到正方形 `target_size`

    Args:
        image_bgr: 原图。
        pts_xy: 四边形点。
        target_size: 输出正方形大小。
        expand_ratio: 扩张比例。
        clockwise: 点序方向选择。
        pad_mode: padding 模式。
        pad_value: constant padding 的值。

    Returns:
        (crop_square, h_orig_to_rect, rect_wh, shoe_center_crop)
        - crop_square: 输出正方形图
        - h_orig_to_rect: 3x3 单应矩阵（原图坐标->rectified 坐标）
        - rect_wh: 透视矫正后矩形的 (w, h)
        - shoe_center_crop: 裁剪后图像中的鞋楦中心点 (x, y)
    """
    if image_bgr is None:
        return None, None, None, None

    pts_in = np.asarray(pts_xy, dtype=np.float32).reshape(4, 2)
    pts_in = _expand_polygon(pts_in, expand_ratio)
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = pts_in

    rect_w = int(math.sqrt(float((x2 - x1) ** 2 + (y2 - y1) ** 2)))
    rect_h = int(math.sqrt(float((x3 - x2) ** 2 + (y3 - y2) ** 2)))
    if rect_w <= 1 or rect_h <= 1:
        return None, None, None, None

    min_x = int(min(x1, x2, x3, x4))
    max_x = int(max(x1, x2, x3, x4))
    min_y = int(min(y1, y2, y3, y4))
    max_y = int(max(y1, y2, y3, y4))
    h_img, w_img = image_bgr.shape[:2]
    min_x = max(0, min_x)
    min_y = max(0, min_y)
    max_x = min(w_img, max_x)
    max_y = min(h_img, max_y)
    if max_x - min_x <= 1 or max_y - min_y <= 1:
        return None, None, None, None

    cropped = image_bgr[min_y:max_y, min_x:max_x]

    ax1, ay1 = float(x1 - min_x), float(y1 - min_y)
    ax2, ay2 = float(x2 - min_x), float(y2 - min_y)
    ax3, ay3 = float(x3 - min_x), float(y3 - min_y)
    ax4, ay4 = float(x4 - min_x), float(y4 - min_y)

    if clockwise:
        src_points = np.float32([[ax1, ay1], [ax2, ay2], [ax3, ay3], [ax4, ay4]])
    else:
        src_points = np.float32([[ax2, ay2], [ax1, ay1], [ax4, ay4], [ax3, ay3]])

    # 注意：warpPerspective 输出大小是 (rect_w, rect_h)，像素坐标范围应为
    # x∈[0, rect_w-1], y∈[0, rect_h-1]。
    # 若这里使用 (rect_w, rect_h) 作为角点，会引入尺度偏差，导致后续回投坐标偏移。
    dst_points = np.float32([[0, 0], [rect_w - 1, 0], [rect_w - 1, rect_h - 1], [0, rect_h - 1]])
    p_mat = cv2.getPerspectiveTransform(src_points, dst_points)
    rectified = cv2.warpPerspective(cropped, p_mat, (rect_w, rect_h), flags=cv2.INTER_NEAREST)

    crop_square = _pad_to_square(rectified, target_size=int(target_size), border_type=pad_mode, pad_value=pad_value)

    a = np.array([[1.0, 0.0, -float(min_x)], [0.0, 1.0, -float(min_y)], [0.0, 0.0, 1.0]], dtype=np.float32)
    h_orig_to_rect = (p_mat @ a).astype(np.float32)
    # 计算扩充后图片中鞋子的中心点
    # 先将原始鞋中心点（透视矫正后中心）映射回扩充后图片坐标
    # rectified 中心点 (rect_w/2, rect_h/2) -> crop_square 坐标
    # 由于 _pad_to_square 可能有 padding，需考虑偏移
    pad_top, pad_left = 0, 0
    h, w = rectified.shape[:2]
    target = int(target_size)
    if h < target:
        pad_top = (target - h) // 2
    if w < target:
        pad_left = (target - w) // 2
    shoe_center_crop = (pad_left + float(rect_w) / 2.0, pad_top + float(rect_h) / 2.0)
    return crop_square, h_orig_to_rect, (rect_w, rect_h), shoe_center_crop


def _iter_image_results(results: Iterable) -> Iterable[Tuple[int, object]]:
    """统一遍历 Ultralytics 的 results 结构。

    Args:
        results: `YOLO.predict` 或 `YOLO.__call__` 的返回。

    Yields:
        (idx, result)
    """
    for idx, r in enumerate(results):
        yield idx, r


def _load_bgr_from_result(result, fallback_path: str) -> Optional[np.ndarray]:
    """从 Ultralytics 结果对象中尽力取到原始 BGR 图。

    Args:
        result: 单个 result 对象。
        fallback_path: result 内没有图像时，尝试用该路径 `cv2.imread`。

    Returns:
        BGR 图像或 None。
    """
    img = getattr(result, "orig_img", None)
    if img is not None:
        return img
    p = getattr(result, "path", None) or fallback_path
    if p:
        return cv2.imread(str(p))
    return None


def _classify_one(cls_model: YOLO, image_bgr: np.ndarray, imgsz: int) -> Tuple[int, str, float]:
    """对单张图做分类推理并返回 top1。

    Args:
        cls_model: Ultralytics YOLO 分类模型。
        image_bgr: 输入图像。
        imgsz: 推理输入大小。

    Returns:
        (top1_id, top1_name, top1_conf)
    """
    cls_results = cls_model.predict(image_bgr, imgsz=imgsz, verbose=False)
    if not cls_results:
        return -1, "", float("nan")
    r0 = cls_results[0]
    probs = getattr(r0, "probs", None)
    names_map = getattr(r0, "names", {}) or getattr(cls_model, "names", {}) or {}

    top1 = int(getattr(probs, "top1", -1)) if probs is not None else -1
    top1conf = float(getattr(probs, "top1conf", float("nan"))) if probs is not None else float("nan")
    name = names_map.get(top1, str(top1)) if top1 >= 0 else ""
    return top1, name, top1conf


def _classify_toe_direction_4cls(cls_model: YOLO, image_bgr: np.ndarray, imgsz: int) -> Tuple[int, str, float]:
    """鞋头方向四分类：返回 top1 的方向类别。

    约定（与 yoloOBB360 的 generate_rotated_images 一致）：
    - 0: 鞋头朝上（无需旋转）
    - 1: 鞋头朝右（相当于把“朝上”图顺时针旋转了 90°）
    - 2: 鞋头朝下（旋转 180°）
    - 3: 鞋头朝左（旋转 270°/逆时针 90°）

    Returns:
        (dir_id, dir_name, conf)
        - dir_id: 0..3；失败为 -1
        - dir_name: names 映射得到的名称（通常也是 "0".."3"）
        - conf: top1 置信度
    """
    if cls_model is None or image_bgr is None:
        return -1, "", float("nan")

    results = cls_model.predict(image_bgr, imgsz=int(imgsz), verbose=False)
    if not results:
        return -1, "", float("nan")
    r0 = results[0]
    probs = getattr(r0, "probs", None)
    names_map = getattr(r0, "names", {}) or getattr(cls_model, "names", {}) or {}

    top1 = int(getattr(probs, "top1", -1)) if probs is not None else -1
    top1conf = float(getattr(probs, "top1conf", float("nan"))) if probs is not None else float("nan")
    name = names_map.get(top1, str(top1)) if top1 >= 0 else ""

    # 更稳健：如果是分类模型且 probs.data 可用（类别数可能 >4），
    # 则只在前 4 个方向类里取 argmax 作为方向输出，避免 top1 落在 4 类以外时返回 -1。
    data = getattr(probs, "data", None) if probs is not None else None
    if data is not None:
        try:
            arr = _to_numpy(data)
            if arr is not None:
                arr = np.asarray(arr).reshape(-1)
                if arr.size >= 4:
                    k = int(np.argmax(arr[:4]))
                    conf_k = float(arr[k]) if np.isfinite(arr[k]) else float("nan")
                    name_k = names_map.get(k, str(k))
                    return k, str(name_k), conf_k
        except Exception:
            pass

    # 兼容 names 里直接存的是字符串数字（"0".."3"）的情况
    try:
        if isinstance(name, str) and name.strip().isdigit():
            top1 = int(name.strip())
    except Exception:
        pass

    if top1 not in (0, 1, 2, 3):
        return -1, str(name), float(top1conf)
    return int(top1), str(name), float(top1conf)


def _toe_dir_to_angle_deg_from_x(toe_dir: int) -> Optional[float]:
    """把鞋头方向类别转换成角度（度），以图像 +x 方向为 0°，逆时针为正。

    图像坐标：x 向右，y 向下。
    - toe_dir=1 (朝右) -> 0°
    - toe_dir=2 (朝下) -> 90°
    - toe_dir=3 (朝左) -> 180°
    - toe_dir=0 (朝上) -> 270°
    """
    if toe_dir == 1:
        return 0.0
    if toe_dir == 2:
        return 90.0
    if toe_dir == 3:
        return 180.0
    if toe_dir == 0:
        return 270.0
    return None


def _angle_deg_from_x_to_toe_dir(angle_deg: float) -> int:
    """把连续角度映射到最近的四方向鞋头类别。"""
    candidates = (
        (1, 0.0),
        (2, 90.0),
        (3, 180.0),
        (0, 270.0),
    )
    best_dir, _best_angle = min(
        candidates,
        key=lambda item: _angle_diff_deg(float(angle_deg), float(item[1])),
    )
    return int(best_dir)


def _rotate_crop_to_toe_up(crop_square_bgr: np.ndarray, toe_dir: int) -> np.ndarray:
    """将正方形抠图旋转到“鞋头朝上”。

    Notes:
        约定与 yoloOBB360 的 generate_rotated_images 一致：
        toe_dir == i 表示输入 crop 相对于“朝上”被顺时针旋转了 i*90°。
        因此要恢复“朝上”，应旋转 -i*90°。
    """
    img = crop_square_bgr
    k = int(toe_dir) % 4
    if k == 0:
        return img
    if k == 1:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if k == 2:
        return cv2.rotate(img, cv2.ROTATE_180)
    # k == 3
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)


def _rotate_vec_to_toe_up(vx: float, vy: float, toe_dir: int) -> Tuple[float, float]:
    """将 2D 向量按 `_rotate_crop_to_toe_up` 的规则做同样旋转（不含平移）。"""
    k = int(toe_dir) % 4
    vxx = float(vx)
    vyy = float(vy)
    if k == 0:
        return vxx, vyy
    if k == 1:
        # 90° CCW: (dx,dy) -> (dy,-dx)
        return vyy, -vxx
    if k == 2:
        return -vxx, -vyy
    # k == 3
    return -vyy, vxx


def _toe_up_score_4cls(cls_model: YOLO, image_bgr: np.ndarray, imgsz: int) -> Tuple[float, int, float, str]:
    """计算“鞋头朝上(类0)”得分。

    优先从 `probs.data` 取第 0 类概率；若不可用，则退化为：
    - top1==0 时用 top1conf 作为得分
    - 否则得分为 0

    Returns:
        (score_up, top1_id, top1_conf, top1_name)
    """
    if cls_model is None or image_bgr is None:
        return 0.0, -1, float("nan"), ""

    results = cls_model.predict(image_bgr, imgsz=int(imgsz), verbose=False)
    if not results:
        return 0.0, -1, float("nan"), ""
    r0 = results[0]
    probs = getattr(r0, "probs", None)
    names_map = getattr(r0, "names", {}) or getattr(cls_model, "names", {}) or {}

    top1 = int(getattr(probs, "top1", -1)) if probs is not None else -1
    top1conf = float(getattr(probs, "top1conf", float("nan"))) if probs is not None else float("nan")
    top1name = names_map.get(top1, str(top1)) if top1 >= 0 else ""

    score_up: Optional[float] = None
    data = getattr(probs, "data", None) if probs is not None else None
    if data is not None:
        try:
            arr = _to_numpy(data)
            if arr is not None:
                arr = np.asarray(arr).reshape(-1)
                if arr.size >= 1 and np.isfinite(arr[0]):
                    score_up = float(arr[0])
        except Exception:
            score_up = None

    if score_up is None:
        if top1 == 0 and np.isfinite(top1conf):
            score_up = float(top1conf)
        else:
            score_up = 0.0

    return float(score_up), int(top1), float(top1conf), str(top1name)


def _ensure_crop_toe_up(
    cls_model: YOLO,
    crop_square_bgr: np.ndarray,
    imgsz: int,
    *,
    verify: bool = True,
    bruteforce_on_fail: bool = True,
) -> Tuple[np.ndarray, Dict[str, object]]:
    """把输入 crop 尽力旋转到“鞋头朝上”。

    逻辑：
    1) 先按一次方向四分类得到 toe_dir_raw
    2) 按 toe_dir_raw 旋转成 crop_toe_up
    3) 可选：再跑一次分类做自校验；若仍非 0，则穷举 4 个旋转，选“类0概率”最高者

    Returns:
        (crop_toe_up, info)
        info 包含：toe_dir_raw/toe_conf_raw/toe_dir_verify/toe_conf_verify/toe_rot_k/toe_up_score
    """
    info: Dict[str, object] = {}

    toe_dir_raw, toe_name_raw, toe_conf_raw = _classify_toe_direction_4cls(cls_model, crop_square_bgr, imgsz=int(imgsz))
    info["toe_dir_raw"] = toe_dir_raw
    info["toe_dir_name_raw"] = toe_name_raw
    info["toe_dir_conf_raw"] = toe_conf_raw

    crop_toe_up = crop_square_bgr
    toe_rot_k: Optional[int] = None
    if toe_dir_raw in (0, 1, 2, 3):
        toe_rot_k = int(toe_dir_raw)
        crop_toe_up = _rotate_crop_to_toe_up(crop_square_bgr, toe_dir_raw)

    toe_dir_verify: Optional[int] = None
    toe_conf_verify: Optional[float] = None
    if verify:
        d2, n2, c2 = _classify_toe_direction_4cls(cls_model, crop_toe_up, imgsz=int(imgsz))
        toe_dir_verify, toe_conf_verify = int(d2), float(c2)
        info["toe_dir_verify"] = toe_dir_verify
        info["toe_dir_name_verify"] = n2
        info["toe_dir_conf_verify"] = toe_conf_verify

    # 若自校验失败或 raw 无效，则穷举 4 个旋转，选“朝上类(0)”得分最高的版本。
    need_bruteforce = bool(bruteforce_on_fail) and (
        (toe_rot_k is None) or (verify and toe_dir_verify is not None and toe_dir_verify != 0)
    )
    if need_bruteforce:
        best_score = -1.0
        best_k = 0
        best_img = crop_toe_up
        best_pred = -1
        best_conf = float("nan")
        best_name = ""

        for k in (0, 1, 2, 3):
            cand = _rotate_crop_to_toe_up(crop_square_bgr, k)
            score_up, pred, pred_conf, pred_name = _toe_up_score_4cls(cls_model, cand, imgsz=int(imgsz))
            if score_up > best_score:
                best_score = float(score_up)
                best_k = int(k)
                best_img = cand
                best_pred = int(pred)
                best_conf = float(pred_conf)
                best_name = str(pred_name)

        crop_toe_up = best_img
        toe_rot_k = best_k
        info["toe_up_score"] = best_score
        info["toe_dir_bruteforce_pred"] = best_pred
        info["toe_dir_bruteforce_name"] = best_name
        info["toe_dir_bruteforce_conf"] = best_conf

    # 最终结果再跑一次方向预测，方便记录/排查（此字段才代表最终 crop_toe_up 的方向）
    try:
        d3, n3, c3 = _classify_toe_direction_4cls(cls_model, crop_toe_up, imgsz=int(imgsz))
        info["toe_dir_final"] = int(d3)
        info["toe_dir_name_final"] = str(n3)
        info["toe_dir_conf_final"] = float(c3)
    except Exception:
        info["toe_dir_final"] = -1

    info["toe_rot_k"] = toe_rot_k
    return crop_toe_up, info


def _infer_side_from_cls_name(name: str) -> str:
    """根据分类名称推断左右脚。

    Args:
        name: 分类名称（可能包含中文“左/右”或英文 left/right）。

    Returns:
        'left' | 'right' | 'unknown'
    """
    if not name:
        return "unknown"
    s = str(name).strip()
    s_low = s.lower()
    is_left = (
        ("左" in s)
        or ("left" in s_low)
        or s_low.startswith("left")
        or s_low in {"l", "lf", "leftfoot", "left_foot"}
    )
    is_right = (
        ("右" in s)
        or ("right" in s_low)
        or s_low.startswith("right")
        or s_low in {"r", "rt", "rightfoot", "right_foot"}
    )
    if is_left and not is_right:
        return "left"
    if is_right and not is_left:
        return "right"
    return "unknown"


def _draw_shoe_tree_overlay(
    frame_bgr: np.ndarray,
    items: List[Dict[str, object]],
    *,
    side_color: Tuple[int, int, int],
    arrow_len: int = 80,
) -> np.ndarray:
    """在图像上绘制鞋楦 OBB 框、中心点、方向箭头与文字信息。

    Args:
        frame_bgr: 输入 BGR 图（会原地绘制）。
        items: `run_obb_demo()` 输出的记录列表（dict）。
        side_color: 该侧（left/right）绘制颜色（B,G,R）。
        arrow_len: 方向箭头长度（像素）。

    Returns:
        绘制后的 frame_bgr（同一个对象）。
    """
    if frame_bgr is None:
        return frame_bgr
    h, w = frame_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return frame_bgr

    for rec in items:
        center_xy = rec.get("shoe_tree_center")
        deg = rec.get("shoe_tree_degree")
        if not (isinstance(center_xy, (list, tuple)) and len(center_xy) >= 2):
            continue
        if deg is None:
            continue

        try:
            cx = int(round(float(center_xy[0])))
            cy = int(round(float(center_xy[1])))
        except Exception:
            continue
        if cx < 0 or cx >= w or cy < 0 or cy >= h:
            continue

        try:
            theta = math.radians(float(deg))
        except Exception:
            theta = 0.0

        ex = int(round(cx + float(arrow_len) * math.cos(theta)))
        ey = int(round(cy + float(arrow_len) * math.sin(theta)))

        # 鞋楦 OBB 框（原图像素坐标）
        tree_obb = rec.get("shoe_tree_obb_pts")
        if isinstance(tree_obb, (list, tuple)) and len(tree_obb) >= 4:
            try:
                poly = np.asarray(tree_obb, dtype=np.float32).reshape(-1, 2)
                if poly.shape[0] >= 4:
                    cv2.polylines(
                        frame_bgr,
                        [np.round(poly).astype(np.int32)],
                        isClosed=True,
                        color=side_color,
                        thickness=2,
                        lineType=cv2.LINE_AA,
                    )
            except Exception:
                pass

        cv2.circle(frame_bgr, (cx, cy), 5, side_color, thickness=-1)
        cv2.arrowedLine(frame_bgr, (cx, cy), (ex, ey), side_color, thickness=2, tipLength=0.25)

        side = str(rec.get("side", ""))
        tree_conf = rec.get("shoe_tree_conf")
        base_center = rec.get("base_center")
        base_yaw = rec.get("base_yaw_deg")

        label = side
        try:
            if tree_conf is not None:
                label = f"{label} conf={float(tree_conf):.2f}"
        except Exception:
            pass
        try:
            label = f"{label} deg={float(deg):.1f}°"
        except Exception:
            pass

        y0 = max(0, cy - 10)
        cv2.putText(
            frame_bgr,
            label,
            (min(w - 1, cx + 8), y0),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            side_color,
            2,
            lineType=cv2.LINE_AA,
        )

        # 基座坐标信息（可选）
        if isinstance(base_center, (list, tuple)) and len(base_center) >= 3 and base_yaw is not None:
            try:
                bx, by, bz = float(base_center[0]), float(base_center[1]), float(base_center[2])
                yaw = float(base_yaw)
                txt = f"base=({bx:.1f},{by:.1f},{bz:.1f}) yaw={yaw:.1f}°"
                cv2.putText(
                    frame_bgr,
                    txt,
                    (min(w - 1, cx + 8), min(h - 1, cy + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    side_color,
                    2,
                    lineType=cv2.LINE_AA,
                )
            except Exception:
                pass

    return frame_bgr


def _draw_frame_visualization(
    frame_bgr: np.ndarray,
    left_items: List[Dict[str, object]],
    right_items: List[Dict[str, object]],
) -> np.ndarray:
    """绘制整帧可视化：左右脚的中心点、方向、文字。"""
    if frame_bgr is None:
        return frame_bgr
    out = frame_bgr

    # 颜色：left=绿色，right=红色
    out = _draw_shoe_tree_overlay(out, left_items, side_color=(0, 255, 0))
    out = _draw_shoe_tree_overlay(out, right_items, side_color=(0, 0, 255))

    # 顶部统计
    try:
        txt = f"left={len(left_items)} right={len(right_items)}  (press q to quit)"
        cv2.putText(out, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, lineType=cv2.LINE_AA)
    except Exception:
        pass
    return out


class ShoeVision:
    """鞋子视觉：加载模型、连接相机、处理手眼矩阵，并提供基于图像的鞋子点位/方向推理接口。
    典型用法：
        vision = ShoeVision.from_config_file()
        left, right = vision.get_all_shoe_points(hand_eye_mat=handEye)
    """

    _CAMERA_SINGLETON: Dict[Tuple[str, str], Any] = {}
    _CAMERA_SINGLETON_PARAMS: Dict[
        Tuple[str, str],
        Tuple[Tuple[int, int], Tuple[int, int], int, Optional[int], Optional[int]],
    ] = {}
    _MACHINE_SINGLETON: Optional[Any] = None

    def __init__(
        self,
        shoe_model_path: str,
        shoe_tree_model_path: str,
        shoe_tree_cls_model_path: str,
        shoe_cls_direction_path:str,
        fx: float,
        fy: float,
        cx: float,
        cy: float,
        shoe_cls_model_path: Optional[str] = None,
        conf: float = 0.25,
        iou: float = 0.7,
        cls_imgsz: int = 256,
        crop_expand: float = 0.0,
        pad_size: int = 256,
        pad_mode: str = "constant",
        pad_value: int = 0,
        obb_img_size: int = 256,
        obb_detection_conf: float = 0.6,
        machine: Optional[Any] = None,
        cam_id: Optional[str] = None,
        cam_serial: Optional[str] = None,
        color_res: Optional[Union[List[int], Tuple[int, int]]] = None,
        depth_res: Optional[Union[List[int], Tuple[int, int]]] = None,
        fps: Optional[int] = None,
        color_exposure: Optional[int] = None,
        color_gain: Optional[int] = None,
        color_auto_exposure: bool = False,
        handeye_mat: Optional[np.ndarray] = None,
        handeye_path: Optional[str] = None,
        save_crop_toe_up: bool = True,
        crop_toe_up_dir: Optional[Union[str, Path]] = None,
        crop_toe_up_save_every: int = 1,
        save_shoe_tree_crop: bool = False,
        shoe_tree_crop_dir: Optional[Union[str, Path]] = None,
        shift: list[list[float]] = None,
        shoe_tree_center_offset: Optional[Union[List[float], List[List[float]]]] = None,
        roi_ratio: list[list[float]] = None,
    ):
        """创建 ShoeVision 实例并完成模型加载/相机初始化/手眼矩阵加载。

        说明：
        - 本类会在实例级缓存模型对象，适用于实时循环反复调用。
        - 当配置提供 `cam_id/cam_serial` 且未传入 `machine` 时，会懒加载创建 `automationMachine()`，
          并在类级缓存 `_MACHINE_SINGLETON`，实现复用。
        - 相机对象按 (cam_id, cam_serial) 做单例复用：避免多次 connect。

        Args:
            shoe_model_path: 鞋子 OBB 检测 YOLO 模型路径。
            shoe_tree_model_path: 鞋楦点位/方向 OBB 模型路径（CasbotYoloP3D 使用）。
            shoe_tree_cls_model_path: 鞋楦分类模型路径（CasbotYoloP3D 使用）。
            fx, fy, cx, cy: 彩色相机内参。
            shoe_cls_model_path: 可选，鞋子分类 YOLO 模型路径。
            conf, iou: OBB 检测阈值。
            cls_imgsz: 分类推理输入尺寸。
            crop_expand: 抠图扩张比例。
            pad_size: 抠图后 padding 到的正方形尺寸。
            pad_mode/pad_value: padding 方式与常量填充值。
            obb_img_size/obb_detection_conf: CasbotYoloP3D 的推理参数。
            machine: 可选，外部提供的 automationMachine 实例。
            cam_id/cam_serial: Orbbec 相机标识（来自配置文件）。
            color_res/depth_res/fps: 相机连接参数（来自配置文件）。
            color_exposure/color_gain: 彩色手动曝光/增益（来自配置文件，None 表示不设置）。
            color_auto_exposure: 彩色自动曝光开关，True 时忽略 color_exposure/color_gain。
            handeye_mat/handeye_path: 手眼矩阵（直接矩阵或文件路径）。

        Raises:
            RuntimeError: 相机未找到或连接失败。
            ValueError: 传入的分类模型不是 classify 任务。
        """

        torch.cuda.empty_cache()  # 清理GPU缓存

        self.shoe_model_path = str(shoe_model_path)
        self.shoe_tree_model_path = str(shoe_tree_model_path)
        self.shoe_tree_cls_model_path = str(shoe_tree_cls_model_path)
        self.shoe_cls_model_path = str(shoe_cls_model_path) if shoe_cls_model_path else None
        self.shoe_cls_direction_path = str(shoe_cls_direction_path) if shoe_cls_direction_path else None

        self.fx = float(fx)
        self.fy = float(fy)
        self.cx = float(cx)
        self.cy = float(cy)

        self.conf = float(conf)
        self.iou = float(iou)
        self.cls_imgsz = int(cls_imgsz)
        self.crop_expand = float(crop_expand)
        self.pad_size = int(pad_size)
        self.pad_mode = str(pad_mode)
        self.pad_value = int(pad_value)

        self.shift = shift
        self.shoe_tree_center_offset = shoe_tree_center_offset if shoe_tree_center_offset is not None else [0.0, 0.0]
        self.roi_ratio = roi_ratio

        self.save_crop_toe_up = bool(save_crop_toe_up)
        self.crop_toe_up_dir = Path(crop_toe_up_dir) if crop_toe_up_dir is not None else Path(__file__).with_name("runs") / "crop_toe_up"
        self.crop_toe_up_save_every = max(1, int(crop_toe_up_save_every))
        self._crop_toe_up_save_count = 0
        self._warned_crop_toe_up_disabled = False
        if self.save_crop_toe_up:
            try:
                self.crop_toe_up_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"⚠️ 无法创建 crop_toe_up_dir={self.crop_toe_up_dir}: {e}")
                self.save_crop_toe_up = False

        # 检测到鞋楦时：保存抠图 + 带鞋楦 OBB 框的可视化图
        self.save_shoe_tree_crop = bool(save_shoe_tree_crop)
        self.shoe_tree_crop_dir = (
            Path(shoe_tree_crop_dir)
            if shoe_tree_crop_dir is not None
            else Path(__file__).with_name("runs") / "shoe_tree_crop"
        )
        if self.save_shoe_tree_crop:
            try:
                self.shoe_tree_crop_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"⚠️ 无法创建 shoe_tree_crop_dir={self.shoe_tree_crop_dir}: {e}")
                self.save_shoe_tree_crop = False

        self._shoe_model = None
        self.shoe_cls_direction_model = None

        self._yolo4d = CasbotYoloP3D(
            obb_model_path=self.shoe_model_path,
            classify_model_path=self.shoe_tree_cls_model_path,
        )
        self._yolo4d.set_parameters(
            obb_img_size=960,
            obb_detection_conf=float(conf),
            obb_iou_thres=float(iou),
        )

        self._shoe_tree_yolo4d = CasbotYoloP3D(
            obb_model_path=self.shoe_tree_model_path,
            classify_model_path=self.shoe_tree_cls_model_path,
        )
        self._shoe_tree_yolo4d.set_parameters(obb_img_size=int(obb_img_size), obb_detection_conf=float(obb_detection_conf))

        self._shoe_cls_model: Optional[YOLO] = None
        if self.shoe_cls_model_path:
            self._shoe_cls_model = YOLO(self.shoe_cls_model_path)
            if getattr(self._shoe_cls_model, "task", None) != "classify":
                raise ValueError(
                    f"shoe_cls_model_path {self.shoe_cls_model_path} is not a classify model "
                    f"(task={getattr(self._shoe_cls_model, 'task', None)})"
                )


        self.color_res = (int(color_res[0]), int(color_res[1]))
        self.depth_res = (int(depth_res[0]), int(depth_res[1]))
        self.fps = int(fps)
        self.color_exposure = int(color_exposure) if color_exposure is not None else None
        self.color_gain = int(color_gain) if color_gain is not None else None
        self.color_auto_exposure = bool(color_auto_exposure)

        # 相机初始化
        self.camera = None
        self.machine = machine
        if cam_id is not None and cam_serial is not None:
            if self.machine is None:
                if self.__class__._MACHINE_SINGLETON is None:
                    self.__class__._MACHINE_SINGLETON = automationMachine()
                self.machine = self.__class__._MACHINE_SINGLETON

        if self.machine is not None and cam_id is not None and cam_serial is not None:
            key = (str(cam_id), str(cam_serial))
            if key in self._CAMERA_SINGLETON:
                cached_params = self._CAMERA_SINGLETON_PARAMS.get(key)
                want_params = (self.color_res, self.depth_res, self.fps, self.color_auto_exposure, self.color_exposure, self.color_gain)
                if cached_params is not None and cached_params != want_params:
                    print(
                        f"[ShoeVision] 警告：相机已单例复用，但请求参数不同。"
                        f" cached={cached_params} want={want_params}"
                    )
                self.camera = self._CAMERA_SINGLETON[key]
            else:
                self.machine.hardwareModule.activate_orbbec_camera(cam_id, cam_serial)
                cam_obj = self.machine.hardwareModule.orbbec_camera_dict.get(cam_id)
                if cam_obj is None:
                    raise RuntimeError(f"相机对象未找到: cam_id={cam_id}")

                want_params = (self.color_res, self.depth_res, self.fps, self.color_auto_exposure, self.color_exposure, self.color_gain)
                flag = cam_obj.connect_camera(
                    [want_params[0][0], want_params[0][1]],
                    [want_params[1][0], want_params[1][1]],
                    want_params[2],
                    color_auto_exposure=want_params[3],
                    color_exposure=want_params[4],
                    color_gain=want_params[5],
                )
                if not flag:
                    raise RuntimeError("相机连接失败")

                self._CAMERA_SINGLETON[key] = cam_obj
                self._CAMERA_SINGLETON_PARAMS[key] = want_params
                self.camera = cam_obj
        elif self.machine is not None:
            # 自动取第一个可用相机
            cam_dict = getattr(self.machine.hardwareModule, "orbbec_camera_dict", {})
            if cam_dict:
                self.camera = list(cam_dict.values())[0]

        # 手眼矩阵加载
        self.handeye_mat = None
        if handeye_mat is not None:
            self.handeye_mat = np.array(handeye_mat)
        elif handeye_path is not None:
            try:
                arr = np.loadtxt(handeye_path)
                if arr.shape == (4, 4):
                    self.handeye_mat = arr
            except Exception as e:
                print(f"手眼矩阵加载失败: {e}")

    def close(self):
        """释放所有资源"""
        # 释放相机资源
        if getattr(self, "camera", None) is not None:
            try:
                self.camera.stop()
            except Exception as e:
                print(f"关闭相机时出错: {e}")
            self.camera = None

        # 释放模型资源（可选）
        self._shoe_model = None
        self._yolo4d = None
        self._shoe_tree_yolo4d = None
        self._shoe_cls_model = None
        self.shoe_cls_direction_model = None

        # 其他资源
        self.machine = None

    def __del__(self):
        """析构函数，确保资源释放"""
        self.close()

    @classmethod
    def from_config_file(
        cls,
        path: Optional[Union[str, Path]] = None,
        *,
        connect_camera: bool = True,
    ) -> "ShoeVision":
        """从配置文件创建 `ShoeVision` 实例。

        本方法会读取：
        - `camera`: 相机内参 fx/fy/cx/cy
        - `orbbec`: cam_id/cam_serial/color_res/depth_res/fps/color_auto_exposure/color_exposure/color_gain
        - `handeye`: mat 或 path
        - 各种模型路径与推理参数
        - connect_camera=False：不打开 RSDT 相机，由本程序 cam1 FrameAdapter 喂图

        Args:
            path: 配置文件路径。None 时使用 `_default_config_path()`。

        Returns:
            初始化完成的 `ShoeVision` 实例。

        Raises:
            ValueError: 配置字段缺失或类型不符合预期。
        """
        p = Path(path) if path is not None else _default_config_path()
        cfg = _load_json_config(p)
        cfg_dir = p.parent

        def _resolve_cfg_path(value: object) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            path_obj = Path(text).expanduser()
            if not path_obj.is_absolute():
                path_obj = (cfg_dir / path_obj).resolve()
            return str(path_obj)

        if not isinstance(cfg.get("camera", {}), dict):
            raise ValueError("camera field must be a dict")
        cam: Dict[str, object] = cfg.get("camera", {})  # type: ignore[assignment]

        orbbec_cfg: Dict[str, object] = cfg.get("orbbec", {}) if isinstance(cfg.get("orbbec", {}), dict) else {}
        handeye_cfg: Dict[str, object] = cfg.get("handeye", {}) if isinstance(cfg.get("handeye", {}), dict) else {}

        # 先解析相机连接参数，确保在 __init__ connect 前生效
        color_res = orbbec_cfg.get("color_res")
        depth_res = orbbec_cfg.get("depth_res")
        fps = orbbec_cfg.get("fps")
        color_exposure_raw = orbbec_cfg.get("color_exposure")
        color_gain_raw = orbbec_cfg.get("color_gain")
        color_auto_exposure = bool(orbbec_cfg.get("color_auto_exposure", False))
        color_exposure = int(color_exposure_raw) if color_exposure_raw is not None else None
        color_gain = int(color_gain_raw) if color_gain_raw is not None else None

        shift=cfg.get("shift")
        shoe_tree_center_offset = cfg.get("shoe_tree_center_offset")
        # ROI的比例
        roi_ratio=cfg.get("roi_ratio") 

        if not (isinstance(color_res, (list, tuple)) and len(color_res) == 2):
            raise ValueError("配置缺失/非法: orbbec.color_res，必须是 [w, h]")
        if not (isinstance(depth_res, (list, tuple)) and len(depth_res) == 2):
            raise ValueError("配置缺失/非法: orbbec.depth_res，必须是 [w, h]")
        if fps is None:
            raise ValueError("配置缺失: orbbec.fps，必须是整数")

        cam_id = str(orbbec_cfg.get("cam_id")) if orbbec_cfg.get("cam_id") else None
        cam_serial = str(orbbec_cfg.get("cam_serial")) if orbbec_cfg.get("cam_serial") else None
        if not connect_camera:
            cam_id = None
            cam_serial = None

        handeye_mat: Optional[np.ndarray] = None
        handeye_path: Optional[str] = None
        if handeye_cfg.get("mat") is not None:
            handeye_mat = np.array(handeye_cfg.get("mat"))
        elif handeye_cfg.get("path") is not None:
            handeye_path = _resolve_cfg_path(handeye_cfg.get("path"))

        fx = _try_float(cam.get("fx"))
        fy = _try_float(cam.get("fy"))
        cx = _try_float(cam.get("cx"))
        cy = _try_float(cam.get("cy"))
        if fx is None or fy is None or cx is None or cy is None:
            raise ValueError(f"camera intrinsics missing/invalid in config: fx={fx}, fy={fy}, cx={cx}, cy={cy}")

        vision = cls(
            shoe_model_path=_resolve_cfg_path(cfg.get("shoe_model_path")) or str(cfg.get("shoe_model_path", "")),
            shoe_tree_model_path=_resolve_cfg_path(cfg.get("shoe_tree_model_path")) or str(cfg.get("shoe_tree_model_path", "")),
            shoe_tree_cls_model_path=_resolve_cfg_path(cfg.get("shoe_tree_cls_model_path")) or str(cfg.get("shoe_tree_cls_model_path", "")),
            shoe_cls_model_path=_resolve_cfg_path(cfg.get("shoe_cls_model_path")) if cfg.get("shoe_cls_model_path") else None,
            shoe_cls_direction_path=_resolve_cfg_path(cfg.get("shoe_cls_direction_path")) if cfg.get("shoe_cls_direction_path") else None,
            fx=float(fx),
            fy=float(fy),
            cx=float(cx),
            cy=float(cy),
            conf=float(cfg.get("conf", 0.25)),
            iou=float(cfg.get("iou", 0.7)),
            cls_imgsz=int(cfg.get("cls_imgsz", 256)),
            crop_expand=float(cfg.get("crop_expand", 0.0)),
            pad_size=int(cfg.get("pad_size", 256)),
            pad_mode=str(cfg.get("pad_mode", "constant")),
            pad_value=int(cfg.get("pad_value", 0)),
            cam_id=cam_id,
            cam_serial=cam_serial,
            color_res=color_res,
            depth_res=depth_res,
            fps=int(fps),
            color_exposure=color_exposure,
            color_gain=color_gain,
            color_auto_exposure=color_auto_exposure,
            handeye_mat=handeye_mat,
            handeye_path=handeye_path,
            save_crop_toe_up=bool(cfg.get("save_crop_toe_up", False)),
            crop_toe_up_dir=(_resolve_cfg_path(cfg.get("crop_toe_up_dir")) if cfg.get("crop_toe_up_dir") else None),
            crop_toe_up_save_every=int(cfg.get("crop_toe_up_save_every", 1)),
            save_shoe_tree_crop=bool(cfg.get("save_shoe_tree_crop", False)),
            shoe_tree_crop_dir=(_resolve_cfg_path(cfg.get("shoe_tree_crop_dir")) if cfg.get("shoe_tree_crop_dir") else None),
            shift=shift if shift is not None else [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]],
            shoe_tree_center_offset=(shoe_tree_center_offset if shoe_tree_center_offset is not None else [0.0, 0.0]),
            roi_ratio=roi_ratio if roi_ratio is not None else [0.29, 0.20, 0.72, 0.95],
        )
        return vision

    def run_obb_demo(
        self,
        image: Union[str, np.ndarray] = "",
        depth: Optional[np.ndarray] = None,
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        """对单帧图像执行完整推理链路。

        流程：
        1) 鞋子 OBB 检测（Ultralytics YOLO）
        2) 对每个 OBB 做透视抠图并 pad 成正方形
        3) 可选：对抠图做分类，用于判断左右脚
        4) 调用 CasbotYoloP3D 在抠图上检测鞋楦点位/方向
        5) 将鞋楦中心点与方向回投到原图坐标系，输出 left/right 列表

        Args:
            image: 输入图像（路径或 numpy BGR）。
            depth: 可选深度图，用于后续坐标计算（本函数主要负责 2D 输出）。

        Returns:
            (left_shoes, right_shoes)
            每个元素是 dict，常见字段：
            - side: 'left'/'right'
            - shoe_obb_pts: 鞋子 OBB 四顶点（原图像素坐标）
            - shoe_tree_obb_pts: 鞋楦 OBB 四顶点（原图像素坐标）
            - shoe_tree_center: (x, y) 原图像素坐标
            - shoe_tree_degree: 原图坐标系角度（度）
            - shoe_tree_conf: 鞋楦检测置信度
            - det_conf/cls_name/cls_conf 等调试信息

        Notes:
            - 若分类无法判断左右或 CasbotYoloP3D 未输出有效结果，会跳过该候选。
            - 本函数不会做 GUI 显示；显示在 `get_all_shoe_points` 中处理。
        """
        left_shoes: List[Dict[str, object]] = []
        right_shoes: List[Dict[str, object]] = []

        conf_v = self.conf
        iou_v = self.iou    
        cls_imgsz_v = int(self.cls_imgsz)
        crop_expand_v = float(self.crop_expand)
        pad_size_v = int(self.pad_size)
        pad_mode_v = str(self.pad_mode)
        pad_value_v = int(self.pad_value)
        fallback_path = image if isinstance(image, str) else ""

        img_bgr = cv2.imread(fallback_path) if fallback_path else image
        if img_bgr is None:
            print("无法获取原图，跳过鞋子检测")
            return left_shoes, right_shoes

        # 直接使用 360 OBB detect 输出整鞋四点和方向角，避免再走 Ultralytics OBB 的 0-90 角度。
        shoe_shift = [[0.0, 0.0] for _ in range(64)]
        shoe_det_results, _shoe_img_obb_show = self._yolo4d.detect(
            img_bgr,
            depth,
            self.fx,
            self.fy,
            self.cx,
            self.cy,
            shoe_shift,
            draw_result=False,
        )

        try:
            for idx, shoe_results in ((0, shoe_det_results),):
                if not shoe_results:
                    print("yolo4d 未检测到鞋子，跳过该候选")
                    continue

                names_map = getattr(getattr(self._yolo4d, "yolo_obb360_model", None), "obb_model", None)
                names_map = getattr(names_map, "names", {}) or {}

                for i, shoe_res in enumerate(shoe_results):
                    # yolo4d 格式: [has_depth, class_id, conf, shift_x, shift_y, x_3d, y_3d, z_3d, obb_pts, degree, vector_x, vector_y]
                    if shoe_res is None or len(shoe_res) < 12:
                        print(f"yolo4d 鞋子结果无效，跳过该候选: idx={idx} det={i} shoe_res={shoe_res}")
                        continue
                    class_id = int(shoe_res[1])
                    _class_name = names_map.get(class_id, str(class_id))
                    score = float(shoe_res[2])
                    shoe_degree_orig = _normalize_deg_360(float(shoe_res[9]))

                    cls_top1, cls_name, cls_conf = -1, "", float("nan")
                    side = "unknown"
                    crop_toe_up: Optional[np.ndarray] = None
                    toe_method: str = ""
                    toe_dir_used: int = -1
                    toe_debug: Dict[str, object] = {}
                    toe_line_angle_crop: Optional[float] = None
                    toe_flip_180 = False
                    toe_diff_deg: Optional[float] = None

                    pts = np.asarray(shoe_res[8], dtype=np.float32).reshape(-1, 2)

                    shoe_center_orig= (float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1])))
                    crop, hmat, rect_wh, shoe_center_crop = _obb360_style_crop_square(
                        img_bgr,
                        pts,
                        target_size=pad_size_v,
                        expand_ratio=crop_expand_v,
                        clockwise=False,
                        pad_mode=pad_mode_v,
                        pad_value=pad_value_v,
                    )
                    if crop is None or rect_wh is None or hmat is None:
                        print(f"抠图失败，跳过该候选: idx={idx} det={i} class_id={class_id} cls_name={cls_name} conf={score:.2f}")
                        continue
                    #膨胀后的中心点坐标
                    shoe_center_crop_pad = (float(rect_wh[0] / 2), float(rect_wh[1] / 2))
                    
                    warped_w, warped_h = rect_wh

                    def _shoe_angle_orig_to_crop_angle(degree_orig: float) -> Optional[float]:
                        """将整鞋原图角度投影到当前 crop 坐标系。"""
                        try:
                            if hmat is None:
                                return None
                            th = math.radians(float(degree_orig))
                            base_x, base_y = shoe_center_orig
                            tip_x = base_x + 80.0 * math.cos(th)
                            tip_y = base_y + 80.0 * math.sin(th)

                            def _project(px: float, py: float) -> Tuple[float, float]:
                                p = np.array([[float(px)], [float(py)], [1.0]], dtype=np.float32)
                                q = (hmat @ p).reshape(3)
                                w = float(q[2].item())
                                if abs(w) < 1e-9:
                                    raise ValueError("homography w=0")
                                return float(q[0].item() / w), float(q[1].item() / w)

                            base_crop_x, base_crop_y = _project(base_x, base_y)
                            tip_crop_x, tip_crop_y = _project(tip_x, tip_y)
                            vx = tip_crop_x - base_crop_x
                            vy = tip_crop_y - base_crop_y
                            if abs(vx) + abs(vy) < 1e-6:
                                return None
                            return _normalize_deg_360(math.degrees(math.atan2(vy, vx)))
                        except Exception:
                            return None

                    # 先做鞋楦检测（基于未旋转的 crop），拿到鞋楦中心点
                    shift = self.shift
                    yolo_p3d_results, _img_obb_show = self._shoe_tree_yolo4d.detect(crop, depth, self.fx, self.fy, self.cx, self.cy, shift)
                    if not yolo_p3d_results:
                        try:
                            no_tree_dir = Path(__file__).with_name("runs") / "no_shoe_tree"
                            no_tree_dir.mkdir(parents=True, exist_ok=True)
                            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                            ms = int(round((time.time() % 1.0) * 1000.0))
                            conf_tag = f"{score:.2f}" if np.isfinite(score) else "nan"
                            class_tag = str(_class_name).replace("/", "_").replace("\\", "_").replace(" ", "_")
                            fname = (
                                f"{ts}_{ms:03d}_img{idx}_det{i}"
                                f"_class{class_tag}_shoeconf{conf_tag}_no_shoe_tree.jpg"
                            )
                            save_img = _img_obb_show if isinstance(_img_obb_show, np.ndarray) else crop
                            if isinstance(save_img, np.ndarray):
                                cv2.imwrite(str(no_tree_dir / fname), save_img)
                        except Exception as e:
                            print(f"[no_shoe_tree] 保存检测图片失败: {e}")
                        print(f"yolo4d 未检测到鞋楦，跳过该候选: idx={idx} det={i} class_id={class_id} cls_name={cls_name} conf={score:.2f}")   
                        continue

                    # 可选：检测到鞋楦后保存抠图 + 带鞋楦 OBB 的可视化图
                    if self.save_shoe_tree_crop and isinstance(crop, np.ndarray):
                        try:
                            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                            ms = int(round((time.time() % 1.0) * 1000.0))
                            conf_tag = f"{score:.2f}" if np.isfinite(score) else "nan"
                            class_tag = (
                                str(_class_name)
                                .replace("/", "_")
                                .replace("\\", "_")
                                .replace(" ", "_")
                            )
                            stem = (
                                f"{ts}_{ms:03d}_img{idx}_det{i}"
                                f"_class{class_tag}_shoeconf{conf_tag}_n{len(yolo_p3d_results)}"
                            )
                            crop_path = self.shoe_tree_crop_dir / f"{stem}_crop.jpg"
                            obb_path = self.shoe_tree_crop_dir / f"{stem}_obb.jpg"

                            ok_crop = cv2.imwrite(str(crop_path), crop)

                            # 优先用检测返回的可视化图；否则在抠图上自绘鞋楦 OBB
                            if isinstance(_img_obb_show, np.ndarray):
                                obb_vis = _img_obb_show
                            else:
                                obb_vis = crop.copy()
                                for _tr in yolo_p3d_results:
                                    if _tr is None or len(_tr) < 12:
                                        continue
                                    try:
                                        poly = np.asarray(_tr[8], dtype=np.float32).reshape(-1, 2)
                                        if poly.shape[0] < 4:
                                            continue
                                        cv2.polylines(
                                            obb_vis,
                                            [np.round(poly).astype(np.int32)],
                                            isClosed=True,
                                            color=(0, 255, 255),
                                            thickness=2,
                                            lineType=cv2.LINE_AA,
                                        )
                                        tcx = int(round(float(poly[:, 0].mean())))
                                        tcy = int(round(float(poly[:, 1].mean())))
                                        cv2.circle(obb_vis, (tcx, tcy), 4, (0, 0, 255), -1)
                                        cv2.putText(
                                            obb_vis,
                                            f"{float(_tr[2]):.2f}",
                                            (tcx + 6, max(0, tcy - 6)),
                                            cv2.FONT_HERSHEY_SIMPLEX,
                                            0.5,
                                            (0, 255, 255),
                                            1,
                                            lineType=cv2.LINE_AA,
                                        )
                                    except Exception:
                                        continue
                            ok_obb = cv2.imwrite(str(obb_path), obb_vis)
                            # print(
                            #     f"[shoe_tree_crop] crop={'OK' if ok_crop else 'FAIL'} {crop_path} | "
                            #     f"obb={'OK' if ok_obb else 'FAIL'} {obb_path}"
                            # )
                        except Exception as e:
                            print(f"[shoe_tree_crop] 保存失败: {e}")

                    # 在本 shoe OBB 内，用“鞋楦中心->鞋子中心(OBB中心)”连线方向来辅助鞋头摆正，
                    # 然后再用摆正后的抠图做左右脚分类。
                    # 这里取第一个有效 tree_res 来决定该 shoe 的摆正方式。
                    # 注：yolo4d 的坐标系基于未旋转 crop，因此摆正只影响保存/分类，不影响回投计算。

                    # 找到第一个有效 tree_res，算出 shoe_line_angle_crop
                    tree_center_crop: Optional[Tuple[float, float]] = None
                    for _tree_res in yolo_p3d_results:
                        if _tree_res is None or len(_tree_res) < 12:
                            print(f"yolo4d 结果无效，继续寻找下一个: {_tree_res}")
                            continue
                        try:
                            pts2d0 = _tree_res[8]
                            tcx = float(sum(pts2d0[::2]) / 4)
                            tcy = float(sum(pts2d0[1::2]) / 4)
                            tree_center_crop = (tcx, tcy)
                            break
                        except Exception:
                            continue

                    # 鞋头摆正：直接使用整鞋 360 OBB 角度，投影到 crop 坐标后映射到最近四方向。
                    if tree_center_crop is not None and shoe_center_crop is not None:
                        vx_line = float(float(shoe_center_crop[0]-tree_center_crop[0]))
                        vy_line = float(float(shoe_center_crop[1]-tree_center_crop[1]))
                        if abs(vx_line) + abs(vy_line) > 1e-6:
                            toe_line_angle_crop = _normalize_deg_360(math.degrees(math.atan2(vy_line, vx_line)))

                            toe_angle_crop = _shoe_angle_orig_to_crop_angle(shoe_degree_orig)
                            if toe_angle_crop is not None:
                                toe_dir_used = _angle_deg_from_x_to_toe_dir(toe_angle_crop)
                                toe_ang_used = _toe_dir_to_angle_deg_from_x(int(toe_dir_used))
                                if toe_ang_used is not None:
                                    toe_diff_deg = _angle_diff_deg(float(toe_ang_used), float(toe_line_angle_crop))

                            if toe_dir_used in (0, 1, 2, 3):
                                toe_method = "yolo4d"
                                crop_toe_up = _rotate_crop_to_toe_up(crop, toe_dir_used)
                                toe_debug = {
                                    "toe_method": toe_method,
                                    "toe_dir_used": int(toe_dir_used),
                                    "shoe_degree_orig": float(shoe_degree_orig),
                                    "shoe_angle_crop": float(toe_angle_crop) if toe_angle_crop is not None else None,
                                    "toe_diff_deg": toe_diff_deg,
                                    "toe_line_angle_crop": toe_line_angle_crop,
                                    "tree_center_crop": tree_center_crop,
                                    "shoe_center_orig": shoe_center_orig,
                                    "shoe_center_crop": shoe_center_crop,
                                }

                    # 可视化鞋和鞋楦中心点、连线方向等信息，验证摆正/分类是否合理。

                    # 若整鞋 360 角度不可用，则不再调用鞋头方向分类兜底。
                    if crop_toe_up is None:
                        print(
                            f"无法根据 yolo4d 鞋子角度摆正 crop，跳过该候选: "
                            f"idx={idx} det={i} class_id={class_id} cls_name={cls_name} conf={score:.2f}"
                        )
                        continue

                    # 左右脚分类（基于摆正后的抠图）
                    if self._shoe_cls_model is not None and crop_toe_up is not None:
                        cls_top1, cls_name, cls_conf = _classify_one(self._shoe_cls_model, crop_toe_up, imgsz=cls_imgsz_v)
                    side = _infer_side_from_cls_name(cls_name)
                    if side not in {"left", "right"}:
                        print(f"无法判断左右脚，跳过该候选: idx={idx} det={i} class_id={class_id} cls_name={cls_name} conf={score:.2f}")    
                        continue

                    # 可选：保存鞋头朝上 crop 便于可视化（此时已摆正且已分类）
                    if self.save_crop_toe_up and crop_toe_up is not None:
                        try:
                            self._crop_toe_up_save_count += 1
                            if (self._crop_toe_up_save_count % self.crop_toe_up_save_every) == 0:
                                ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                                ms = int(round((time.time() % 1.0) * 1000.0))
                                side_tag = cls_name if cls_name else side
                                conf_tag = f"{cls_conf:.2f}" if np.isfinite(cls_conf) else "nan"

                                if toe_method == "cls":
                                    toe_v = toe_debug.get("toe_dir_verify", None) if isinstance(toe_debug, dict) else None
                                    toe_f = toe_debug.get("toe_dir_final", None) if isinstance(toe_debug, dict) else None
                                    toe_vk = toe_debug.get("toe_rot_k", None) if isinstance(toe_debug, dict) else None
                                    toe_vk = toe_vk if toe_vk is not None else "n"
                                    fname = (
                                        f"{ts}_{ms:03d}_img{idx}_det{i}"
                                        f"_toem{toe_method}_toeraw{toe_dir_used}_tok{toe_vk}_tov{toe_v}_tof{toe_f}"
                                        f"_{side_tag}_{conf_tag}.jpg"
                                    )
                                else:
                                    ang_tag = f"{toe_line_angle_crop:.0f}" if toe_line_angle_crop is not None else "nan"
                                    fname = (
                                        f"{ts}_{ms:03d}_img{idx}_det{i}"
                                        f"_toem{toe_method}_toedir{toe_dir_used}_ang{ang_tag}"
                                        f"_{side_tag}_{conf_tag}_toe_flip_180{toe_flip_180}_toe_diff_deg{toe_diff_deg}.jpg"
                                    )

                                out_path = self.crop_toe_up_dir / fname
                                ok = cv2.imwrite(str(out_path), crop_toe_up)
                                # if ok:
                                #     print(f"[crop_toe_up] saved: {out_path}")
                                # else:
                                #     print(f"[crop_toe_up] FAILED to save: {out_path}")
                        except Exception as e:
                            print(f"[crop_toe_up] exception while saving: {e}")
                    else:
                        if not self._warned_crop_toe_up_disabled and (not self.save_crop_toe_up):
                            self._warned_crop_toe_up_disabled = True
                            print(
                                "[crop_toe_up] saving is disabled. "
                                "Enable it in shoe_vision_config.json with save_crop_toe_up=true "
                                "(and optional crop_toe_up_dir/crop_toe_up_save_every)."
                            )

                    for tree_i, tree_res in enumerate(yolo_p3d_results):
                        # 新格式: [has_depth, class_id, conf, shift_x, shift_y, x_3d, y_3d, z_3d, obb_pts, degree, vector_x, vector_y]
                        if tree_res is None or len(tree_res) < 12:
                            print(f"yolo4d 结果无效，跳过该鞋楦结果: idx={idx} det={i} tree_i={tree_i} tree_res={tree_res}")
                            continue
                        try:
                            tree_class_id = int(tree_res[1])
                            tree_conf = float(tree_res[2])
                            pts2d = tree_res[8]
                            vector_x = float(tree_res[10])
                            vector_y = float(tree_res[11])

                            shift_x=float(tree_res[3])
                            shift_y=float(tree_res[4])

                            c_x = float(sum(pts2d[::2]) / 4)
                            c_y = float(sum(pts2d[1::2]) / 4)

                            target_size = pad_size_v
                            if warped_w <= 0 or warped_h <= 0:
                                raise ValueError(f"invalid warped size: warped_w={warped_w}, warped_h={warped_h}")
                            scale = min(target_size / warped_w, target_size / warped_h)
                            new_w = max(1, int(round(warped_w * scale)))
                            new_h = max(1, int(round(warped_h * scale)))
                            pad_left = (target_size - new_w) // 2
                            pad_top = (target_size - new_h) // 2

                            hmat_inv = np.linalg.inv(hmat)

                            def _crop_to_original(pt_x: float, pt_y: float) -> Tuple[float, float]:
                                """将抠图坐标系点反算回原图坐标系。"""
                                pt_x_adj = pt_x - pad_left
                                pt_y_adj = pt_y - pad_top
                                if scale <= 0:
                                    raise ValueError(f"invalid scale: {scale}")
                                pt_x_adj /= scale
                                pt_y_adj /= scale
                                pt_crop = np.array([[pt_x_adj], [pt_y_adj], [1.0]], dtype=np.float32)
                                pt_orig_homo = (hmat_inv @ pt_crop).reshape(3)
                                w = float(pt_orig_homo[2].item())
                                if w == 0.0:
                                    raise ValueError("homography w=0")
                                pt_orig = pt_orig_homo[:2] / w
                                return float(pt_orig[0].item()), float(pt_orig[1].item())

                            orig_shift_x,orig_shift_y=_crop_to_original(shift_x, shift_y)
                            orig_x, orig_y = _crop_to_original(c_x, c_y)
                            # 鞋楦 OBB 四顶点：抠图坐标 -> 原图坐标
                            pts2d_arr = np.asarray(pts2d, dtype=np.float32).reshape(-1)
                            shoe_tree_obb_pts: List[Tuple[float, float]] = []
                            if pts2d_arr.size >= 8:
                                for k in range(4):
                                    ox, oy = _crop_to_original(float(pts2d_arr[2 * k]), float(pts2d_arr[2 * k + 1]))
                                    shoe_tree_obb_pts.append((ox, oy))
                            tip_len = 60.0
                            tip_crop_x = shift_x + tip_len * vector_x
                            tip_crop_y = shift_y + tip_len * vector_y
                            tip_orig_x, tip_orig_y = _crop_to_original(tip_crop_x, tip_crop_y)
                            vxo = tip_orig_x - orig_shift_x
                            vyo = tip_orig_y - orig_shift_y
                            if abs(vxo) + abs(vyo) < 1e-6:
                                raise ValueError("orig vector too small")
                            degree_orig = (math.degrees(math.atan2(vyo, vxo)) + 360.0) % 360.0
                            # print(f"orig_shift_x: {orig_shift_x}, orig_shift_y: {orig_shift_y}, orig_x: {orig_x}, orig_y: {orig_y} ")
                            # -----------------------------
                            # 方向校正（新版）：用“鞋头方向 toe_dir_used”辅助修正鞋楦方向。
                            # 思路：把 toe_dir_used 对应的角度（crop坐标系）投影回原图得到 toe_angle_orig，
                            # 然后在 tree_deg_raw + {0,90,180,270} 中找与 toe_angle_orig 最接近的那个。
                            # 若最小夹角 <= 20°，则采用该候选作为 tree_deg_fixed。
                            # -----------------------------
                            TOE_ASSIST_MAX_DIFF_DEG = 20.0

                            tree_deg_raw = float(degree_orig)
                            tree_deg_fixed = float(degree_orig)
                            tree_deg_flipped = False  # 历史字段名：此处表示“发生了90°/180°/270°旋转修正”

                            shoe_center_xy: Optional[Tuple[float, float]] = None
                            shoe_line_angle: Optional[float] = None  # 仍保留用于日志/排查（tree_center -> shoe_center）
                            shoe_diff_raw: Optional[float] = None
                            shoe_diff_flip: Optional[float] = None  # 仍沿用字段名，记录“修正后最小夹角”

                            # 仍计算 shoe_center 与连线角度，方便对比排查（不再作为修正依据）
                            try:
                                pts_np = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
                                if pts_np.shape[0] >= 4:
                                    shoe_cx = float(pts_np[:, 0].mean())
                                    shoe_cy = float(pts_np[:, 1].mean())
                                    shoe_center_xy = (shoe_cx, shoe_cy)
                                    vx = shoe_cx - float(orig_x)
                                    vy = shoe_cy - float(orig_y)
                                    if abs(vx) + abs(vy) > 1e-6:
                                        shoe_line_angle = _normalize_deg_360(math.degrees(math.atan2(vy, vx)))
                            except Exception:
                                shoe_center_xy = None

                            # 1) 计算鞋头方向在原图坐标系下的角度 toe_angle_orig
                            toe_angle_orig: Optional[float] = None
                            try:
                                if toe_dir_used in (0, 1, 2, 3) and shoe_center_crop is not None:
                                    toe_ang_crop = _toe_dir_to_angle_deg_from_x(int(toe_dir_used))
                                    if toe_ang_crop is not None:
                                        th = math.radians(float(toe_ang_crop))
                                        base_crop_x = float(shoe_center_crop_pad[0])
                                        base_crop_y = float(shoe_center_crop_pad[1])
                                        tip_len = 80.0
                                        tip_crop_x = base_crop_x + tip_len * math.cos(th)
                                        tip_crop_y = base_crop_y + tip_len * math.sin(th)

                                        base_orig_x, base_orig_y = _crop_to_original(base_crop_x, base_crop_y)
                                        tip_orig_x, tip_orig_y = _crop_to_original(tip_crop_x, tip_crop_y)
                                        vxo = float(tip_orig_x - base_orig_x)
                                        vyo = float(tip_orig_y - base_orig_y)
                                        if abs(vxo) + abs(vyo) > 1e-6:
                                            toe_angle_orig = _normalize_deg_360(math.degrees(math.atan2(vyo, vxo)))
                            except Exception:
                                toe_angle_orig = None

                            # 2) 若 toe_angle_orig 可用，尝试 {0,90,180,270} 旋转修正鞋楦方向
                            if toe_angle_orig is not None:
                                shoe_diff_raw = _angle_diff_deg(tree_deg_raw, toe_angle_orig)

                                best_deg = tree_deg_raw
                                best_diff = float(shoe_diff_raw)
                                best_k = 0
                                for k, add_deg in ((1, 90.0), (2, 180.0), (3, 270.0)):
                                    cand = _normalize_deg_360(tree_deg_raw + float(add_deg))
                                    d = _angle_diff_deg(cand, toe_angle_orig)
                                    if d + 1e-6 < best_diff:
                                        best_diff = float(d)
                                        best_deg = float(cand)
                                        best_k = int(k)

                                # 只有当“存在一个候选能进 20°”才采用修正
                                if best_diff <= TOE_ASSIST_MAX_DIFF_DEG and best_k != 0:
                                    tree_deg_fixed = float(best_deg)
                                    degree_orig = float(tree_deg_fixed)
                                    tree_deg_flipped = True
                                    shoe_diff_flip = float(best_diff)
                                else:
                                    # 不修正时，把 flip 字段保持为“翻转180后的差值”不再有意义，置为 None
                                    shoe_diff_flip = None

                            local_offset_xy = _resolve_local_offset_xy(self.shoe_tree_center_offset, tree_class_id)
                            raw_tree_center_xy = (float(orig_shift_x), float(orig_shift_y))
                            quarter_turns = int(round((_normalize_deg_360(tree_deg_fixed - tree_deg_raw)) / 90.0)) % 4
                            local_scale_xy = _obb_local_scale_from_points(pts2d, quarter_turns=quarter_turns)
                            final_tree_center_x, final_tree_center_y = _apply_local_xy_offset(
                                raw_tree_center_xy,
                                float(degree_orig),
                                local_offset_xy,
                                local_scale_xy=local_scale_xy,
                            )
                            applied_offset_px = (
                                float(final_tree_center_x - raw_tree_center_xy[0]),
                                float(final_tree_center_y - raw_tree_center_xy[1]),
                            )

                            # ROI检测
                            roi_xyxy = get_roi_xyxy(img_bgr.shape, self.roi_ratio)
                            in_roi = point_in_roi(final_tree_center_x, final_tree_center_y, roi_xyxy)
                            if not in_roi:
                                # print(
                                #     f"鞋楦中心点不在ROI内，跳过该候选: idx={idx} det={i} "
                                #     f"orig_x={final_tree_center_x:.1f} orig_y={final_tree_center_y:.1f} roi_xyxy={roi_xyxy}"
                                # )
                                continue

                            record: Dict[str, object] = {
                                "side": side,
                                "shoe_obb_pts": pts.astype(float).tolist(),
                                "shoe_tree_obb_pts": shoe_tree_obb_pts,
                                "shoe_tree_center": (final_tree_center_x, final_tree_center_y),
                                "shoe_tree_degree": degree_orig,
                                "shoe_tree_conf": tree_conf,
                                "shoe_tree_center_raw": raw_tree_center_xy,
                                "shoe_tree_center_offset": local_offset_xy,
                                "shoe_tree_center_offset_scale": local_scale_xy,
                                "shoe_tree_center_offset_px": applied_offset_px,
                                "tree_index": tree_i,
                                "det_index": i,
                                "det_conf": score,
                                "cls_name": cls_name,
                                "cls_conf": cls_conf,
                                "cls_top1": cls_top1,
                                "det_class": _class_name,
                                # 鞋头摆正信息（调试字段）
                                "toe_method": toe_method,
                                "toe_dir_used": toe_dir_used,
                                "toe_line_angle_crop": toe_line_angle_crop,
                                "toe_debug": toe_debug,
                                # 鞋子中心连线辅助校正的调试信息
                                "shoe_center_xy": shoe_center_xy,
                                "shoe_line_angle": shoe_line_angle,
                                "tree_degree_raw": tree_deg_raw,
                                "tree_degree_fixed": tree_deg_fixed,
                                "tree_degree_flipped": tree_deg_flipped,
                                "shoe_tree_diff_raw": shoe_diff_raw,
                                "shoe_tree_diff_flip": shoe_diff_flip,
                                "shoe_center_orig": shoe_center_orig,
                                "shoe_angle_orig": toe_angle_orig,
                            }
                            if side == "left":
                                left_shoes.append(record)
                            else:
                                right_shoes.append(record)
                        except Exception as e:
                            print(f"[ERROR] 回投原图坐标失败，已跳过该点: result={idx} det={i} tree={tree_i}, cls='{cls_name}', err={e}")
        finally:
            pass

        return left_shoes, right_shoes

    def _populate_base_pose_fields(
        self,
        items: List[Dict[str, object]],
        depth: np.ndarray,
        color_shape_hw: Tuple[int, int],
        hand_eye_mat: np.ndarray,
    ) -> None:
        """为鞋记录补充相机/基座坐标字段。"""
        for rec in items:
            center_xy = rec.get("shoe_tree_center")
            ang = rec.get("shoe_tree_degree")
            if not center_xy or ang is None:
                print(f"缺少鞋中心点或角度，无法计算基坐标系位置: center_xy={center_xy}, ang={ang}")
                continue
            cam_xyz, base_xyz, base_yaw = _compute_base_pose_from_image(
                hand_eye_T=hand_eye_mat,
                center_xy=(float(center_xy[0]), float(center_xy[1])),
                angle_deg_image=float(ang),
                depth=depth,
                color_shape_hw=color_shape_hw,
                fx=self.fx,
                fy=self.fy,
                cx=self.cx,
                cy=self.cy,
            )
            if cam_xyz is not None:
                rec["cam_center"] = cam_xyz
            if base_xyz is not None:
                rec["base_center"] = base_xyz
            if base_yaw is not None:
                rec["base_yaw_deg"] = base_yaw

    def _extract_base_poses(self, items: List[Dict[str, object]]) -> List[Tuple[float, float, float, float]]:
        """提取基座坐标系下的鞋位姿列表。"""
        out: List[Tuple[float, float, float, float]] = []
        for rec in items:
            bc = rec.get("base_center")
            by = rec.get("base_yaw_deg")

            if bc is None or by is None:
                print(f"缺少基坐标系信息，跳过该候选: base_center={bc}, base_yaw_deg={by}")
                continue
            if isinstance(bc, (list, tuple)) and len(bc) >= 3:
                out.append((float(bc[0]), float(bc[1]), float(bc[2]), float(by)))
        return out

    @staticmethod
    def _reorder_obb_pts_toe_first(
        obb_pts: List[Tuple[float, float]],
        shoe_tree_degree_deg: float,
    ) -> List[Tuple[float, float]]:
        """重排 OBB 四点，使 pts[0]-pts[1] 是最靠近鞋头方向的那条边。

        shoe_tree_degree_deg 为图像坐标系下的鞋头朝向角（+x 为 0°，逆时针为正）。
        """
        pts = np.array(obb_pts, dtype=np.float32)   # (4, 2)
        center = pts.mean(axis=0)
        rad = math.radians(float(shoe_tree_degree_deg))
        toe_vec = np.array([math.cos(rad), math.sin(rad)], dtype=np.float32)

        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        best_k, best_dot = 0, -float("inf")
        for k, (i, j) in enumerate(edges):
            mid = (pts[i] + pts[j]) / 2.0
            v = mid - center
            norm = float(np.linalg.norm(v))
            if norm < 1e-6:
                continue
            dot = float(np.dot(v / norm, toe_vec))
            if dot > best_dot:
                best_dot, best_k = dot, k

        i0, i1 = edges[best_k]
        i2, i3 = (i0 + 2) % 4, (i1 + 2) % 4
        return [(float(pts[r][0]), float(pts[r][1])) for r in (i0, i1, i2, i3)]

    def _extract_base_poses_with_obb(
        self,
        items: List[Dict[str, object]],
    ) -> Tuple[List[Tuple[float, float, float, float]], List[List[Tuple[float, float]]]]:
        """提取与位姿严格索引对应的 OBB 顶点列表。"""
        base_poses: List[Tuple[float, float, float, float]] = []
        shoe_obb_pts: List[List[Tuple[float, float]]] = []
        for rec in items:
            bc = rec.get("base_center")
            by = rec.get("base_yaw_deg")
            obb_pts = rec.get("shoe_obb_pts")

            if bc is None or by is None:
                print(f"缺少基坐标系信息，跳过该候选: base_center={bc}, base_yaw_deg={by}")
                continue
            if not (isinstance(bc, (list, tuple)) and len(bc) >= 3):
                print(f"基坐标系信息格式无效，跳过该候选: base_center={bc}")
                continue
            if not isinstance(obb_pts, (list, tuple)) or len(obb_pts) != 4:
                print(f"缺少 OBB 顶点信息，跳过该候选: shoe_obb_pts={obb_pts}")
                continue

            try:
                norm_obb_pts = [
                    (float(pt[0]), float(pt[1]))
                    for pt in obb_pts
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2
                ]
            except Exception:
                print(f"OBB 顶点信息格式无效，跳过该候选: shoe_obb_pts={obb_pts}")
                continue

            if len(norm_obb_pts) != 4:
                print(f"OBB 顶点数量无效，跳过该候选: shoe_obb_pts={obb_pts}")
                continue

            # 用图像坐标系的鞋头角度重排 OBB 点，使 [0]-[1] 为鞋头边
            tree_deg = rec.get("shoe_tree_degree")
            if tree_deg is not None:
                try:
                    norm_obb_pts = self._reorder_obb_pts_toe_first(norm_obb_pts, float(tree_deg))
                except Exception as e:
                    print(f"OBB 顶点重排失败，保留原始顺序: {e}")

            base_poses.append((float(bc[0]), float(bc[1]), float(bc[2]), float(by)))
            shoe_obb_pts.append(norm_obb_pts)
        return base_poses, shoe_obb_pts

    def get_all_shoe_points(
        self,
        save_original_image: bool = False,
    ) -> Tuple[List[Tuple[float, float, float, float]], List[Tuple[float, float, float, float]]]:
        """实时循环取帧并持续输出左右脚结果（返回基坐标系下鞋楦中心点+角度）。

        若还需要返回每只鞋对应的 OBB 顶点，请使用 `get_all_shoe_points_with_obb()`。

        Args:
            save_original_image: 为 True 时将当前 RGB 帧保存到 ``ORIGINAL_IMAGE_SAVE_DIR``。
        Returns:
            (left_base_poses, right_base_poses)
            - left_base_poses: 左脚鞋楦中心点+角度列表（基坐标系），元素为 (x, y, z, yaw_deg)
            - right_base_poses: 右脚鞋楦中心点+角度列表（基坐标系），元素为 (x, y, z, yaw_deg)

        Raises:
            RuntimeError: 缺少手眼矩阵，无法计算基坐标系中心点。

        Behavior:
            - 循环中按 `q` 键退出。
            - 若环境无 GUI（imshow 不可用），会捕获异常并打印提示，仍返回最后结果。
        """
        left_shoe_points: List[Dict[str, object]] = []
        right_shoe_points: List[Dict[str, object]] = []

        camera = self.camera
        if camera is None:
            raise RuntimeError("未找到可用相机：请检查 orbbec 配置或相机连接")


        hand_eye_mat = self.handeye_mat
        if hand_eye_mat is None:
            raise RuntimeError("缺少 handeye 矩阵：当前返回值要求包含基坐标系中心点与角度")

        while True:
            img, depth, _color = camera.get_one_frame()
            if img is None or depth is None:
                print("无法获取相机帧，正在重试...")
                continue
            img_out = img.copy() if isinstance(img, np.ndarray) else img
            if save_original_image:
                save_dir = ORIGINAL_IMAGE_SAVE_DIR
                save_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                save_path = save_dir / f"original_{ts}.jpg"
                try:
                    if not cv2.imwrite(str(save_path), img_out):
                        print(f"保存原图失败: {save_path}")
                    else:
                        print(f"已保存原图: {save_path}")
                except Exception as e:
                    print(f"保存原图异常: {save_path}, {e}")
            left_shoe_points, right_shoe_points = self.run_obb_demo(
                image=img,
                depth=depth,
            )
            color_shape_hw = (int(img.shape[0]), int(img.shape[1]))
            self._populate_base_pose_fields(left_shoe_points + right_shoe_points, depth, color_shape_hw, hand_eye_mat)
            if left_shoe_points or right_shoe_points:
                break

        left_base_poses = self._extract_base_poses(left_shoe_points)
        right_base_poses = self._extract_base_poses(right_shoe_points)
        return left_base_poses, right_base_poses

    def get_all_shoe_points_with_obb(
        self,
        save_original_image: bool = True,
    ) -> Tuple[
        List[Tuple[float, float, float, float]],
        List[Tuple[float, float, float, float]],
        List[List[Tuple[float, float]]],
        List[List[Tuple[float, float]]],
        np.ndarray,
        np.ndarray,
    ]:
        """实时循环取帧并返回左右脚位姿与对应 OBB 顶点。


        Returns:
            (left_base_poses, right_base_poses, left_shoe_obb_pts, right_shoe_obb_pts, img, depth)
            - left_base_poses: 左脚鞋楦中心点+角度列表（基坐标系），元素为 (x, y, z, yaw_deg)
            - right_base_poses: 右脚鞋楦中心点+角度列表（基坐标系），元素为 (x, y, z, yaw_deg)
            - left_shoe_obb_pts: 左脚 OBB 顶点列表（原图像素坐标），元素为 [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
            - right_shoe_obb_pts: 右脚 OBB 顶点列表（原图像素坐标），元素为 [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
            - img: 当前推理使用的 RGB 帧（BGR 格式 np.ndarray）
            - depth: 当前推理使用的 depth 帧（np.ndarray）

        Notes:
            - 每一侧的 base_poses 与 shoe_obb_pts 按索引严格对应。
            - 若某只鞋未得到有效 base pose 或 OBB 顶点，则该鞋不会进入返回列表。
        """
        left_shoe_points: List[Dict[str, object]] = []
        right_shoe_points: List[Dict[str, object]] = []

        camera = self.camera
        if camera is None:
            raise RuntimeError("未找到可用相机：请检查 orbbec 配置或相机连接")

        hand_eye_mat = self.handeye_mat
        if hand_eye_mat is None:
            raise RuntimeError("缺少 handeye 矩阵：当前返回值要求包含基坐标系中心点与角度")

        img: Optional[np.ndarray] = None
        depth: Optional[np.ndarray] = None
        while True:
            img, depth, _color = camera.get_one_frame()
            if img is None or depth is None:
                print("无法获取相机帧，正在重试...")
                continue

            img_out = img.copy() if isinstance(img, np.ndarray) else img
            if save_original_image:
                save_dir = ORIGINAL_IMAGE_SAVE_DIR
                save_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                save_path = save_dir / f"original_{ts}.jpg"
                try:
                    if not cv2.imwrite(str(save_path), img_out):
                        print(f"保存原图失败: {save_path}")
                    else:
                        print(f"已保存原图: {save_path}")
                except Exception as e:
                    print(f"保存原图异常: {save_path}, {e}")
                    
            left_shoe_points, right_shoe_points = self.run_obb_demo(
                image=img,
                depth=depth,
            )
            color_shape_hw = (int(img.shape[0]), int(img.shape[1]))
            self._populate_base_pose_fields(left_shoe_points + right_shoe_points, depth, color_shape_hw, hand_eye_mat)
            if left_shoe_points or right_shoe_points:
                break

        left_base_poses, left_shoe_obb_pts = self._extract_base_poses_with_obb(left_shoe_points)
        right_base_poses, right_shoe_obb_pts = self._extract_base_poses_with_obb(right_shoe_points)
        if img is None or depth is None:
            raise RuntimeError("未获取到有效图像帧")
        img_out = img.copy() if isinstance(img, np.ndarray) else img
        depth_out = depth.copy() if isinstance(depth, np.ndarray) else depth

        return left_base_poses, right_base_poses, left_shoe_obb_pts, right_shoe_obb_pts, img_out, depth_out

    def run_live_visualization(
        self,
        window_name: str = "ShoeVision",
    ) -> None:
        """多窗口实时显示推理结果：原图、鞋子识别+鞋头朝向、鞋楦识别+鞋楦朝向。

        - 按 `q` 退出。
        - 若环境无 GUI（imshow 不可用），会捕获异常并打印提示后退出。
        """
        camera = self.camera
        if camera is None:
            raise RuntimeError("未找到可用相机：请检查 orbbec 配置或相机连接")

        hand_eye_mat = self.handeye_mat
        if hand_eye_mat is None:
            raise RuntimeError("缺少 handeye 矩阵：当前可视化会同时显示 base_center/base_yaw")

        try:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        except Exception as e:
            print(f"[ShoeVision] GUI 不可用，无法可视化显示: {e}")
            return

        while True:
            img, depth, _color = camera.get_one_frame()
            if img is None or depth is None:
                print(f"无法获取相机帧，img={img} depth={depth}，正在重试...")
                continue
            cv2.imshow("img_raw", img)
            cv2.imshow("depth_raw", depth)  
            roi_xyxy = get_roi_xyxy(img.shape,self.roi_ratio)
            img_roi=img.copy()
            

            if img is not None:
                cv2.rectangle(
                    img_roi,
                    (roi_xyxy[0], roi_xyxy[1]),
                    (roi_xyxy[2], roi_xyxy[3]),
                    (0, 255, 0),
                    2,
                )
                cv2.putText(
                    img_roi,
                    "ROI",
                    (roi_xyxy[0], max(0, roi_xyxy[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                )
            cv2.imshow("img_roi", img_roi)
            left_shoe_points, right_shoe_points = self.run_obb_demo(image=img, depth=depth)

            color_shape_hw = (int(img.shape[0]), int(img.shape[1]))
            for rec in left_shoe_points + right_shoe_points:
                
                center_xy = rec.get("shoe_tree_center")
                ang = rec.get("shoe_tree_degree")
                if not center_xy or ang is None:
                    print(f"缺少鞋中心点或角度，无法计算基坐标系位置: center_xy={center_xy}, ang={ang}")
                    continue
                cam_xyz, base_xyz, base_yaw = _compute_base_pose_from_image(
                    hand_eye_T=hand_eye_mat,
                    center_xy=(float(center_xy[0]), float(center_xy[1])),
                    angle_deg_image=float(ang),
                    depth=depth,
                    color_shape_hw=color_shape_hw,
                    fx=self.fx,
                    fy=self.fy,
                    cx=self.cx,
                    cy=self.cy,
                )
                if cam_xyz is not None:
                    rec["cam_center"] = cam_xyz
                if base_xyz is not None:
                    rec["base_center"] = base_xyz
                if base_yaw is not None:
                    rec["base_yaw_deg"] = base_yaw

            # 主窗口（整帧可视化）
            vis = _draw_frame_visualization(img.copy(), left_shoe_points, right_shoe_points)
            try:
                cv2.imshow(window_name, vis)
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    break
            except Exception as e:
                print(f"[ShoeVision] imshow/waitKey 失败，可能无 GUI: {e}")
                break

        try:
            cv2.destroyWindow(window_name)
        except Exception:
            pass


if __name__ == "__main__":
    vision = ShoeVision.from_config_file()
    if vision.camera is None:
        raise RuntimeError("未找到可用相机：请检查 orbbec 配置或相机连接")

    vision.run_live_visualization()

    left_list, right_list = vision.get_all_shoe_points(save_original_image=True)
    
    print(f"最后一帧返回列表: left={len(left_list)} right={len(right_list)}")
    print("Left base poses (x,y,z,yaw_deg):")
    for x, y, z, yaw in left_list:
        print((x, y, z, yaw))
    print("Right base poses (x,y,z,yaw_deg):")
    for x, y, z, yaw in right_list:
        print((x, y, z, yaw))