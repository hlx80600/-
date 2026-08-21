"""鞋子分割与可视化脚本。

用法：
1) 实时显示（默认）
    python shoes_seg.py

2) 仅处理一帧并退出
    python shoes_seg.py --once

3) 打印鞋头中心在基座坐标系下的坐标（mm）
    python shoes_seg.py --print-toe-center

4) 只打印不弹窗
    python shoes_seg.py --no-show --print-toe-center

5) 保存当前可视化结果
    python shoes_seg.py --output logs/shoes_seg_result.jpg --once

get_shoe_base_pose_toe_and_arc_points
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from shoe_vision_seg import ShoeVision, _depth_value_at, _pixel_to_cam_xyz, _transform_point


CURRENT_DIR = Path(__file__).resolve().parent
CONTOUR_SEG_ROOT = CURRENT_DIR / "ContourSeg"
DEFAULT_SAM_CHECKPOINT = CONTOUR_SEG_ROOT / "weights" / "sam_vit_b_01ec64.pth"
VALID_SAM_MODEL_TYPES = {"vit_b", "vit_l", "vit_h"}


def _most_likely_depth_value_at(
    depth_frame: np.ndarray,
    u: float,
    v: float,
    shape_hw: Tuple[int, int],
    mask_bool: Optional[np.ndarray] = None,
    window_radius: int = 4,
    depth_bin_mm: float = 5.0,
    min_samples: int = 8,
) -> Optional[float]:
    """在像素邻域内估计最可能深度（近似众数），可限制在目标 mask 区域。"""
    height, width = int(shape_hw[0]), int(shape_hw[1])
    if height <= 0 or width <= 0:
        return None

    ui = int(round(u))
    vi = int(round(v))
    x0 = max(0, ui - window_radius)
    x1 = min(width, ui + window_radius + 1)
    y0 = max(0, vi - window_radius)
    y1 = min(height, vi + window_radius + 1)
    if x0 >= x1 or y0 >= y1:
        return None

    patch = depth_frame[y0:y1, x0:x1]
    if patch.size == 0:
        return None

    if mask_bool is not None:
        mask_patch = mask_bool[y0:y1, x0:x1]
        if mask_patch.shape == patch.shape:
            patch = patch[mask_patch]
        else:
            patch = patch.reshape(-1)

    values = patch.astype(np.float32).reshape(-1)
    values = values[np.isfinite(values) & (values > 0.0)]
    if values.size == 0:
        return None
    if values.size < min_samples:
        return float(np.median(values))

    quantized = np.round(values / depth_bin_mm).astype(np.int32)
    bins, counts = np.unique(quantized, return_counts=True)
    best_bin = int(bins[int(np.argmax(counts))])
    best_center = float(best_bin) * depth_bin_mm
    in_best_bin = np.abs(values - best_center) <= (depth_bin_mm * 0.5)
    if np.any(in_best_bin):
        return float(np.median(values[in_best_bin]))
    return float(np.median(values))


def _pixel_to_base_xyz(
    pixel_xy: Sequence[float],
    depth_frame: np.ndarray,
    vision: ShoeVision,
    fallback_z: float,
    mask_bool: Optional[np.ndarray] = None,
    rgb_frame: Optional[np.ndarray] = None,
) -> Tuple[float, float, float]:
    u, v = float(pixel_xy[0]), float(pixel_xy[1])
    shape_hw = rgb_frame.shape[:2] if rgb_frame is not None else depth_frame.shape[:2]
    z_depth = _most_likely_depth_value_at(depth_frame, u, v, shape_hw, mask_bool=mask_bool)
    if z_depth is None:
        z_depth = _depth_value_at(depth_frame, u, v, shape_hw)
    if z_depth is None:
        z_depth = float(fallback_z)

    cam_xyz = _pixel_to_cam_xyz(u, v, z_depth, vision.fx, vision.fy, vision.cx, vision.cy)
    base_xyz = _transform_point(vision.handeye_mat, cam_xyz)
    return float(base_xyz[0]), float(base_xyz[1]), float(base_xyz[2])


def _base_xyz_to_pixel_xy(
    base_xyz: Sequence[float],
    vision: ShoeVision,
) -> Optional[Tuple[float, float]]:
    """将基座坐标点反投影到图像像素坐标。"""
    if vision.handeye_mat is None:
        return None

    try:
        handeye_inv = np.linalg.inv(np.asarray(vision.handeye_mat, dtype=np.float64))
        base_h = np.array([float(base_xyz[0]), float(base_xyz[1]), float(base_xyz[2]), 1.0], dtype=np.float64)
        cam_h = handeye_inv @ base_h
        cam_z = float(cam_h[2])
        if abs(cam_z) < 1e-6:
            return None
        u = float((float(vision.fx) * float(cam_h[0]) / cam_z) + float(vision.cx))
        v = float((float(vision.fy) * float(cam_h[1]) / cam_z) + float(vision.cy))
        if not (np.isfinite(u) and np.isfinite(v)):
            return None
        return u, v
    except Exception:
        return None


def _draw_obb_items(
    image: np.ndarray,
    items: Sequence[Sequence[Tuple[float, float]]],
    poses: Sequence[Tuple[float, float, float, float]],
    color: Tuple[int, int, int],
    side_name: str,
    vision: Optional[ShoeVision] = None,
) -> None:
    for index, (obb_pts, pose) in enumerate(zip(items, poses)):
        pts = np.asarray(obb_pts, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(image, [pts], isClosed=True, color=color, thickness=2)

        center_pixel_xy = _base_xyz_to_pixel_xy((pose[0], pose[1], pose[2]), vision) if vision is not None else None
        if center_pixel_xy is not None:
            cx = int(round(center_pixel_xy[0]))
            cy = int(round(center_pixel_xy[1]))
        else:
            cx = int(round(float(np.mean([point[0] for point in obb_pts]))))
            cy = int(round(float(np.mean([point[1] for point in obb_pts]))))
        cv2.circle(image, (cx, cy), 4, color, thickness=-1)

        x, y, z, yaw = pose
        label = f"{side_name}[{index}] yaw={yaw:.1f}"
        base_txt = f"({x:.1f},{y:.1f},{z:.1f})"
        cv2.putText(image, label, (cx + 6, max(20, cy - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, lineType=cv2.LINE_AA)
        cv2.putText(image, base_txt, (cx + 6, cy + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2, lineType=cv2.LINE_AA)


def _overlay_mask_and_contour(
    image: np.ndarray,
    mask_bool: np.ndarray,
    contour: np.ndarray,
    color: Tuple[int, int, int],
) -> None:
    overlay = image.copy()
    overlay[mask_bool] = color
    cv2.addWeighted(overlay, 0.35, image, 0.65, 0, image)


def _find_nearest_contour_to_segment(
    contour: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
) -> Tuple[int, int]:
    """返回轮廓上距线段 p0-p1 最近的点（图像像素坐标）。"""
    seg = p1 - p0
    seg_len_sq = float(np.dot(seg, seg))
    ctour = contour.reshape(-1, 2).astype(np.float32)
    if seg_len_sq > 1e-12:
        t = np.clip(((ctour - p0) @ seg) / seg_len_sq, 0.0, 1.0)
        proj = p0 + t[:, None] * seg
        dists = np.linalg.norm(ctour - proj, axis=1)
    else:
        dists = np.linalg.norm(ctour - p0, axis=1)
    return tuple(ctour[int(np.argmin(dists))].astype(int))


def _extract_mask_contours(
    mask_bool: np.ndarray,
    smooth_eps: float = 0.0008,
    min_area: float = 120.0,
) -> List[np.ndarray]:
    """保留 mask 的内外轮廓，避免鞋头开口被闭运算和外轮廓提取抹掉。"""
    mask_u8 = mask_bool.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel, iterations=1)
    contours, hierarchy = cv2.findContours(mask_u8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return []

    contour_list: List[np.ndarray] = []
    for contour in contours:
        area = abs(float(cv2.contourArea(contour)))
        if area < min_area:
            continue
        epsilon = smooth_eps * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(np.int32)
        if approx.shape[0] >= 3:
            contour_list.append(approx)
    return contour_list


def _find_toe_tip_from_arc(
    toe_arc: Sequence[Tuple[int, int]],
    obb_pts: np.ndarray,
) -> Tuple[int, int]:
    center = obb_pts.mean(axis=0)
    mid_toe = (obb_pts[0] + obb_pts[1]) / 2.0
    toe_outward = mid_toe - center
    norm = float(np.linalg.norm(toe_outward))
    if norm < 1e-6 or not toe_arc:
        return int(round(float(mid_toe[0]))), int(round(float(mid_toe[1])))
    toe_outward = toe_outward / norm
    arc_points = np.asarray(toe_arc, dtype=np.float32).reshape(-1, 2)
    projections = (arc_points - center) @ toe_outward
    return tuple(arc_points[int(np.argmax(projections))].astype(int))


def _select_toe_contour_and_arc(
    contours: Sequence[np.ndarray],
    obb_pts: np.ndarray,
) -> Tuple[Optional[np.ndarray], List[Tuple[int, int]]]:
    """从内外轮廓中挑出真正的鞋头开口弧线。

    优先满足：
    1) 靠近鞋头边两点中点；
    2) 弧线主体位于 OBB 内侧，朝鞋楦开口；
    3) 不明显超出 OBB 鞋头边。
    """
    if not contours:
        return None, []

    toe_mid = (obb_pts[0] + obb_pts[1]) / 2.0
    heel_mid = (obb_pts[2] + obb_pts[3]) / 2.0
    toe_outward = toe_mid - obb_pts.mean(axis=0)
    outward_norm = float(np.linalg.norm(toe_outward))
    if outward_norm < 1e-6:
        contour = np.asarray(contours[0], dtype=np.int32)
        return contour, _find_toe_arc_on_contour(contour, obb_pts)
    toe_outward = toe_outward / outward_norm

    toe_tangent = obb_pts[1] - obb_pts[0]
    tangent_norm = float(np.linalg.norm(toe_tangent))
    if tangent_norm < 1e-6:
        contour = np.asarray(contours[0], dtype=np.int32)
        return contour, _find_toe_arc_on_contour(contour, obb_pts)
    toe_tangent = toe_tangent / tangent_norm

    toe_depth = abs(float(np.dot(heel_mid - toe_mid, toe_outward)))
    target_inside = -max(toe_depth * 0.18, 8.0)
    outside_allow = max(toe_depth * 0.12, 6.0)

    best_contour: Optional[np.ndarray] = None
    best_arc: List[Tuple[int, int]] = []
    best_score: Optional[float] = None

    for contour in contours:
        toe_arc = _find_toe_arc_on_contour(contour, obb_pts)
        if not toe_arc:
            continue

        arc_np = np.asarray(toe_arc, dtype=np.float32).reshape(-1, 2)
        local = arc_np - toe_mid
        tangent_proj = local @ toe_tangent
        normal_proj = local @ toe_outward
        center_bias = float(np.mean(np.abs(tangent_proj)))
        depth_bias = abs(float(np.median(normal_proj)) - target_inside)
        outside_penalty = float(np.mean(np.maximum(normal_proj - outside_allow, 0.0)))
        inside_bonus = 0.0 if float(np.median(normal_proj)) <= 0.0 else 20.0
        score = center_bias * 2.0 + depth_bias * 1.5 + outside_penalty * 6.0 + inside_bonus

        if best_score is None or score < best_score:
            best_score = score
            best_contour = np.asarray(contour, dtype=np.int32)
            best_arc = [(int(point[0]), int(point[1])) for point in arc_np.astype(int)]

    return best_contour, best_arc


def _find_toe_tip_on_contour(
    contour: np.ndarray,
    obb_pts: np.ndarray,
) -> Tuple[int, int]:
    """返回过滤后鞋头弧线上的极值点，避免把非弧线点误当成鞋头。

    先用与可视化一致的规则筛出鞋头连续弧线段，再仅在该弧线上取
    OBB 鞋头外法线方向投影最大的点。对光滑凸弧线，该点处切线与
    鞋头边近似平行。
    """
    center = obb_pts.mean(axis=0)
    mid_toe = (obb_pts[0] + obb_pts[1]) / 2.0
    toe_outward = mid_toe - center
    norm = float(np.linalg.norm(toe_outward))
    if norm < 1e-6:
        return _find_nearest_contour_to_segment(contour, obb_pts[0], obb_pts[1])
    toe_outward = toe_outward / norm

    toe_arc = _find_toe_arc_on_contour(contour, obb_pts)
    if not toe_arc:
        return _find_nearest_contour_to_segment(contour, obb_pts[0], obb_pts[1])
    return _find_toe_tip_from_arc(toe_arc, obb_pts)


def _find_toe_arc_on_contour(
    contour: np.ndarray,
    obb_pts: np.ndarray,
    arc_half_angle_deg: float = 30.0,
    max_gap: int = 1,
) -> List[Tuple[int, int]]:
    """返回鞋头附近的连续弧线点集（按轮廓顺序）。

    思路：
    1) 只保留靠近鞋头边中点的一小段轮廓，避免把鞋侧/鞋帮带进来；
    2) 候选点需要位于鞋头方向的窄扇区内，且只允许少量超出 OBB；
    3) 以最靠近鞋头边中点的候选点为锚点，沿轮廓两侧扩展得到连续弧线段。
    """
    ctour = contour.reshape(-1, 2).astype(np.float32)
    point_count = ctour.shape[0]
    if point_count == 0:
        return []

    center = obb_pts.mean(axis=0)
    toe_mid = (obb_pts[0] + obb_pts[1]) / 2.0
    heel_mid = (obb_pts[2] + obb_pts[3]) / 2.0
    mid_toe = (obb_pts[0] + obb_pts[1]) / 2.0
    toe_outward = mid_toe - center
    norm = float(np.linalg.norm(toe_outward))
    if norm < 1e-6:
        # OBB 退化时返回单点降级结果
        return [_find_nearest_contour_to_segment(contour, obb_pts[0], obb_pts[1])]
    toe_outward = toe_outward / norm
    toe_tangent = obb_pts[1] - obb_pts[0]
    tangent_norm = float(np.linalg.norm(toe_tangent))
    if tangent_norm < 1e-6:
        return [_find_nearest_contour_to_segment(contour, obb_pts[0], obb_pts[1])]
    toe_tangent = toe_tangent / tangent_norm

    toe_half_width = tangent_norm * 0.5
    toe_depth = abs(float(np.dot(heel_mid - toe_mid, toe_outward)))
    if toe_depth < 1e-6:
        toe_depth = max(float(np.linalg.norm(center - toe_mid)), 1.0)

    vectors_from_center = ctour - center
    lengths = np.linalg.norm(vectors_from_center, axis=1)
    safe_lengths = np.maximum(lengths, 1e-6)
    forward_proj = vectors_from_center @ toe_outward
    cos_sim = forward_proj / safe_lengths

    vectors_from_toe_mid = ctour - toe_mid
    normal_proj = vectors_from_toe_mid @ toe_outward
    tangent_proj = vectors_from_toe_mid @ toe_tangent
    distance_to_toe_mid = np.linalg.norm(vectors_from_toe_mid, axis=1)

    center_strip_limit = max(toe_half_width * 0.6, 8.0)
    outward_allow = max(toe_depth * 0.12, 6.0)
    inward_allow = max(toe_depth * 0.28, 12.0)
    radial_limit = max(toe_half_width * 0.9, toe_depth * 0.35, 16.0)

    cos_threshold = float(np.cos(np.deg2rad(arc_half_angle_deg)))
    valid = (
        (forward_proj > -inward_allow * 0.25)
        & (cos_sim >= cos_threshold)
        & (np.abs(tangent_proj) <= center_strip_limit)
        & (normal_proj >= -inward_allow)
        & (normal_proj <= outward_allow)
        & (distance_to_toe_mid <= radial_limit)
    )

    if not np.any(valid):
        return [_find_nearest_contour_to_segment(contour, obb_pts[0], obb_pts[1])]

    anchor_candidates = np.where(valid)[0]
    anchor_idx = int(anchor_candidates[int(np.argmin(distance_to_toe_mid[anchor_candidates]))])

    def _walk(direction: int) -> List[int]:
        indices = [anchor_idx]
        idx = anchor_idx
        gap_count = 0
        for _ in range(point_count - 1):
            idx = (idx + direction) % point_count
            if bool(valid[idx]):
                indices.append(idx)
                gap_count = 0
                continue
            gap_count += 1
            if gap_count > max_gap:
                break
        return indices

    left = _walk(-1)
    right = _walk(1)

    arc_indices = list(reversed(left)) + right[1:]
    return [tuple(ctour[i].astype(int)) for i in arc_indices]


def _flatten_obb_to_8(obb_pts: Sequence[Tuple[float, float]]) -> List[float]:
    out: List[float] = []
    for x, y in obb_pts:
        out.extend([float(x), float(y)])
    return out


def _resolve_sam_model_type(checkpoint: str, model_type: str) -> str:
    checkpoint_name = Path(checkpoint).name.lower()

    inferred = None
    for candidate in ("vit_b", "vit_l", "vit_h"):
        if candidate in checkpoint_name:
            inferred = candidate
            break

    model_type = model_type.strip().lower()
    if model_type == "auto":
        if inferred is not None:
            return inferred
        raise ValueError("无法从 checkpoint 文件名推断 SAM 模型类型，请显式指定 --sam-model-type")

    if model_type not in VALID_SAM_MODEL_TYPES:
        raise ValueError(f"不支持的 --sam-model-type: {model_type}，可选: vit_b/vit_l/vit_h/auto")

    if inferred is not None and inferred != model_type:
        print(
            f"[WARN] checkpoint 看起来是 {inferred}，但 --sam-model-type={model_type}，可能导致加载失败或效果异常。"
        )

    return model_type


def _segment_by_obb(
    source_bgr: np.ndarray,
    vis_bgr: np.ndarray,
    left_obb_pts: Sequence[Sequence[Tuple[float, float]]],
    right_obb_pts: Sequence[Sequence[Tuple[float, float]]],
    predictor,
    pick_mask_by_box,
    extract_outer_contour,
) -> Tuple[int, int]:
    """用 OBB 作为 box prompt、传完整原图给 SAM（参考 run_quad.py）。
    整帧只做一次 set_image，对每只鞋单独 predict，把 mask/轮廓叠加到 vis_bgr。
    """
    image_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
    predictor.set_image(image_rgb)

    left_count = 0
    right_count = 0

    def _process(obb_pts: Sequence[Tuple[float, float]], color: Tuple[int, int, int]) -> bool:
        pts = np.array(obb_pts, dtype=np.float32)          # (4, 2) 全图像素坐标
        box = np.array([pts[:, 0].min(), pts[:, 1].min(),  # AABB xyxy，与 run_quad 一致
                        pts[:, 0].max(), pts[:, 1].max()], dtype=np.float32)
        masks, scores, _ = predictor.predict(box=box, multimask_output=True)
        if masks is None or len(masks) == 0:
            return False
        mask = masks[int(np.argmax(scores))]
        
        obb_mask = np.zeros_like(mask, dtype=np.uint8)
        cv2.fillPoly(obb_mask, [pts.astype(np.int32).reshape(-1, 1, 2)], 1)
        mask = mask & obb_mask.astype(bool)

        if not np.any(mask):
            return False
        contours = _extract_mask_contours(np.asarray(mask, dtype=bool))
        contour, toe_arc = _select_toe_contour_and_arc(contours, pts)
        if contour is None:
            contour = extract_outer_contour(mask)
            if contour is not None:
                toe_arc = _find_toe_arc_on_contour(contour, pts)
        if contour is None:
            return False
        _overlay_mask_and_contour(vis_bgr, mask, contour, color=color)

        # 找鞋头弧线极值点（切线与鞋头边平行）并标记
        nearest = _find_toe_tip_from_arc(toe_arc, pts) if toe_arc else _find_toe_tip_on_contour(contour, pts)
        cv2.circle(vis_bgr, nearest, 3, (0, 0, 255), -1)   # 红色小实心圆

        # 额外标记鞋头附近弧线点集
        if len(toe_arc) >= 2:
            arc_np = np.asarray(toe_arc, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(vis_bgr, [arc_np], isClosed=False, color=(255, 120, 0), thickness=2)
        elif len(toe_arc) == 1:
            cv2.circle(vis_bgr, toe_arc[0], 2, (255, 120, 0), -1)

        return True

    for obb in left_obb_pts:
        if _process(obb, color=(0, 255, 0)):
            left_count += 1

    for obb in right_obb_pts:
        if _process(obb, color=(0, 255, 255)):
            right_count += 1

    return left_count, right_count


def get_toe_tip_xyz(
    base_pose: Tuple[float, float, float, float],
    shoe_obb_pts: Sequence[Tuple[float, float]],
    rgb_frame: Optional[np.ndarray],
    depth_frame: np.ndarray,
    vision: ShoeVision,
    predictor=None,
    extract_outer_contour: Optional[Callable] = None,
) -> Optional[Tuple[float, float, float]]:
    """返回单只鞋“红点”对应的机器人基座坐标 (x, y, z) mm。

    OBB 须已由 _reorder_obb_pts_toe_first 重排：pts[0]-pts[1] 为鞋头边。

    计算方式：
    - 使用 OBB 作为提示从 SAM 得到鞋子轮廓；
    - 在轮廓上按与可视化一致的规则取鞋头极值点（即图像红点）；
    - 将该像素点通过深度+手眼标定转换到基座坐标系。

    Args:
        base_pose:             (x, y, z, yaw_deg) 抓取中心基座坐标（mm）。
        shoe_obb_pts:          4 个像素坐标，鞋头边在前。
        rgb_frame:             BGR 图像（仅用于获取尺寸及 SAM set_image）；可为 None。
        depth_frame:           深度图（与彩色图对齐）。
        vision:                ShoeVision 实例，提供相机内参与手眼矩阵。
        predictor:             SAM SamPredictor 实例；传 None 则用 OBB-only 模式。
        extract_outer_contour: ContourSeg 中的同名函数；predictor 非 None 时必须同时传入。

    Returns:
        (x, y, z) mm 基座坐标；无法计算时返回 None。
    """
    if depth_frame is None or vision.handeye_mat is None:
        return None

    pts = np.asarray(shoe_obb_pts, dtype=np.float32)  # (4, 2)，鞋头边 = pts[0]-pts[1]
    if pts.shape[0] < 2:
        return None

    shoe_mask: Optional[np.ndarray] = None
    toe_pixel_xy: Optional[Tuple[float, float]] = None
    if predictor is not None and rgb_frame is not None:
        try:
            image_rgb = cv2.cvtColor(rgb_frame, cv2.COLOR_BGR2RGB)
            predictor.set_image(image_rgb)
            box = np.array(
                [pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()],
                dtype=np.float32,
            )
            masks, scores, _ = predictor.predict(box=box, multimask_output=True)
            if masks is not None and len(masks) > 0:
                shoe_mask = np.asarray(masks[int(np.argmax(scores))], dtype=bool)

                obb_mask = np.zeros_like(shoe_mask, dtype=np.uint8)
                cv2.fillPoly(obb_mask, [pts.astype(np.int32).reshape(-1, 1, 2)], 1)
                shoe_mask = shoe_mask & obb_mask.astype(bool)

                contour_candidates = _extract_mask_contours(shoe_mask)
                contour, toe_arc = _select_toe_contour_and_arc(contour_candidates, pts)
                if contour is None and extract_outer_contour is not None:
                    contour = extract_outer_contour(shoe_mask)
                    if contour is not None:
                        toe_arc = _find_toe_arc_on_contour(contour, pts)
                if contour is not None:
                    toe_px = _find_toe_tip_from_arc(toe_arc, pts) if toe_arc else _find_toe_tip_on_contour(contour, pts)
                    toe_pixel_xy = (float(toe_px[0]), float(toe_px[1]))
        except Exception:
            shoe_mask = None
            toe_pixel_xy = None

    if toe_pixel_xy is None:
        # SAM/轮廓不可用时退化到鞋头边中点，避免直接失败。
        toe_mid = (pts[0] + pts[1]) / 2.0
        toe_pixel_xy = (float(toe_mid[0]), float(toe_mid[1]))

    toe_base = _pixel_to_base_xyz(
        toe_pixel_xy,
        depth_frame,
        vision,
        fallback_z=base_pose[2],
        mask_bool=shoe_mask,
        rgb_frame=rgb_frame,
    )
    return float(toe_base[0]), float(toe_base[1]), float(toe_base[2])


def _pick_available_side(
    left_base_poses: Sequence[Tuple[float, float, float, float]],
    right_base_poses: Sequence[Tuple[float, float, float, float]],
    requested_side: str,
) -> Optional[str]:
    has_left = bool(left_base_poses)
    has_right = bool(right_base_poses)

    if requested_side == "left":
        return "left" if has_left else None
    return "right" if has_right else None


def _pick_fallback_side(
    left_base_poses: Sequence[Tuple[float, float, float, float]],
    right_base_poses: Sequence[Tuple[float, float, float, float]],
) -> Optional[str]:
    has_left = bool(left_base_poses)
    has_right = bool(right_base_poses)

    if has_left and has_right:
        return "left" if len(left_base_poses) >= len(right_base_poses) else "right"
    if has_left:
        return "left"
    if has_right:
        return "right"
    return None


def get_shoe_base_pose_toe_and_arc_points(
    vision: ShoeVision,
    predictor=None,
    extract_outer_contour: Optional[Callable] = None,
    side: str = "left",
    max_retries: int = 5,
    retry_interval: float = 0.05,
) -> dict:
    """单次检测并返回鞋楦中心、鞋头点、鞋头弧线点（均为机器人基坐标系）。

    输入:
        vision: ShoeVision 实例。

    输出字典字段:
        left_base_poses/right_base_poses:
            由 `vision.get_all_shoe_points_with_obb()` 返回的鞋楦中心位姿列表 (x, y, z, yaw)。
        left_toe_base_points/right_toe_base_points:
            每只鞋鞋头红点对应的基坐标 (x, y, z)。
        left_toe_arc_base_points/right_toe_arc_base_points:
            每只鞋鞋头弧线点对应的基坐标列表；若弧线不可用则为空列表。
        left_shoe_obb_pts/right_shoe_obb_pts/rgb_frame/depth_frame:
            原始检测附带结果，便于调用方继续可视化。
    """
    requested_side = side
    attempts = max(1, int(max_retries))

    capture = None
    current_capture = None
    any_available_side = ""
    selected_side = ""
    for attempt in range(1, attempts + 1):
        left_base_poses, right_base_poses, left_shoe_obb_pts, right_shoe_obb_pts, rgb_frame, depth_frame = (
            vision.get_all_shoe_points_with_obb()
        )
        current_capture = {
            "left_base_poses": left_base_poses,
            "right_base_poses": right_base_poses,
            "left_shoe_obb_pts": left_shoe_obb_pts,
            "right_shoe_obb_pts": right_shoe_obb_pts,
            "rgb_frame": rgb_frame,
            "depth_frame": depth_frame,
        }
        available_side = _pick_available_side(left_base_poses, right_base_poses, requested_side)
        any_available_side = _pick_fallback_side(left_base_poses, right_base_poses) or ""

        if available_side is not None:
            capture = current_capture
            selected_side = available_side
            break

        if attempt < attempts:
            print(f"[get_shoe_base_pose_toe_and_arc_points] 未检测到{requested_side}鞋，第{attempt}/{attempts}次重试")
            time.sleep(max(0.0, float(retry_interval)))

    if capture is None:
        capture = current_capture
        selected_side = any_available_side
        if requested_side in {"left", "right"} and selected_side:
            print(f"[get_shoe_base_pose_toe_and_arc_points] 重试后仍未检测到{requested_side}鞋，回退使用{selected_side}鞋")

    left_base_poses = capture["left_base_poses"]
    right_base_poses = capture["right_base_poses"]
    left_shoe_obb_pts = capture["left_shoe_obb_pts"]
    right_shoe_obb_pts = capture["right_shoe_obb_pts"]
    rgb_frame = capture["rgb_frame"]
    depth_frame = capture["depth_frame"]

    vis_frame = rgb_frame.copy() if rgb_frame is not None else None

    process_left = selected_side == "left"
    process_right = selected_side == "right"

    result = {
        "left_base_poses": left_base_poses,
        "right_base_poses": right_base_poses,
        "left_toe_base_points": [],
        "right_toe_base_points": [],
        "left_toe_arc_base_points": [],
        "right_toe_arc_base_points": [],
        "left_shoe_obb_pts": left_shoe_obb_pts,
        "right_shoe_obb_pts": right_shoe_obb_pts,
        "rgb_frame": rgb_frame,
        "depth_frame": depth_frame,
        "vis_frame": vis_frame,
        "requested_side": requested_side,
        "selected_side": selected_side,
        "fallback_used": bool(selected_side) and selected_side != requested_side,
    }

    if depth_frame is None or vision.handeye_mat is None:
        return result

    if predictor is not None and rgb_frame is not None:
        image_rgb = cv2.cvtColor(rgb_frame, cv2.COLOR_BGR2RGB)
        predictor.set_image(image_rgb)

    def _compute_side_points(
        base_poses: Sequence[Tuple[float, float, float, float]],
        shoe_obb_pts: Sequence[Sequence[Tuple[float, float]]],
        toe_key: str,
        arc_key: str,
        side_color: Tuple[int, int, int] = (0, 255, 0),
    ) -> None:
        for base_pose, obb_pts in zip(base_poses, shoe_obb_pts):
            pts = np.asarray(obb_pts, dtype=np.float32)
            if pts.shape[0] < 2:
                result[toe_key].append(None)
                result[arc_key].append([])
                continue

            shoe_mask: Optional[np.ndarray] = None
            contour = None
            toe_arc: List[Tuple[int, int]] = []

            if predictor is not None and rgb_frame is not None:
                try:
                    box = np.array(
                        [pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()],
                        dtype=np.float32,
                    )
                    masks, scores, _ = predictor.predict(box=box, multimask_output=True)
                    if masks is not None and len(masks) > 0:
                        shoe_mask = np.asarray(masks[int(np.argmax(scores))], dtype=bool)

                        obb_mask = np.zeros_like(shoe_mask, dtype=np.uint8)
                        cv2.fillPoly(obb_mask, [pts.astype(np.int32).reshape(-1, 1, 2)], 1)
                        shoe_mask = shoe_mask & obb_mask.astype(bool)

                        contour_candidates = _extract_mask_contours(shoe_mask)
                        contour, toe_arc = _select_toe_contour_and_arc(contour_candidates, pts)
                        if contour is None and extract_outer_contour is not None:
                            contour = extract_outer_contour(shoe_mask)
                            if contour is not None:
                                toe_arc = _find_toe_arc_on_contour(contour, pts)
                except Exception:
                    shoe_mask = None
                    contour = None
                    toe_arc = []

            if contour is not None:
                toe_px = _find_toe_tip_from_arc(toe_arc, pts) if toe_arc else _find_toe_tip_on_contour(contour, pts)
                toe_pixel_xy = (float(toe_px[0]), float(toe_px[1]))
                # 在 vis_frame 上绘制 mask、弧线、鞋头点
                if vis_frame is not None and shoe_mask is not None:
                    _overlay_mask_and_contour(vis_frame, shoe_mask, contour, color=side_color)
                if vis_frame is not None:
                    if len(toe_arc) >= 2:
                        arc_np = np.asarray(toe_arc, dtype=np.int32).reshape(-1, 1, 2)
                        cv2.polylines(vis_frame, [arc_np], isClosed=False, color=(255, 120, 0), thickness=2)
                    elif len(toe_arc) == 1:
                        cv2.circle(vis_frame, toe_arc[0], 2, (255, 120, 0), -1)
                    cv2.circle(vis_frame, (int(toe_px[0]), int(toe_px[1])), 3, (0, 0, 255), -1)
            else:
                # 分割不可用时退化到鞋头边中点。
                toe_mid = (pts[0] + pts[1]) / 2.0
                toe_pixel_xy = (float(toe_mid[0]), float(toe_mid[1]))

            toe_base = _pixel_to_base_xyz(
                toe_pixel_xy,
                depth_frame,
                vision,
                fallback_z=base_pose[2],
                mask_bool=shoe_mask,
                rgb_frame=rgb_frame,
            )
            result[toe_key].append((float(toe_base[0]), float(toe_base[1]), float(toe_base[2])))

            arc_base_points: List[Tuple[float, float, float]] = []
            for arc_px in toe_arc:
                arc_base = _pixel_to_base_xyz(
                    arc_px,
                    depth_frame,
                    vision,
                    fallback_z=base_pose[2],
                    mask_bool=shoe_mask,
                    rgb_frame=rgb_frame,
                )
                arc_base_points.append((float(arc_base[0]), float(arc_base[1]), float(arc_base[2])))
            result[arc_key].append(arc_base_points)

    if process_left:
        _compute_side_points(left_base_poses, left_shoe_obb_pts, "left_toe_base_points", "left_toe_arc_base_points", side_color=(0, 255, 0))
    else:
        result["left_base_poses"] = []
        result["left_shoe_obb_pts"] = []

    if process_right:
        _compute_side_points(right_base_poses, right_shoe_obb_pts, "right_toe_base_points", "right_toe_arc_base_points", side_color=(0, 255, 255))
    else:
        result["right_base_poses"] = []
        result["right_shoe_obb_pts"] = []

    # 在 vis_frame 上叠加 OBB 框
    if vis_frame is not None:
        if process_left:
            _draw_obb_items(vis_frame, left_shoe_obb_pts, left_base_poses, color=(0, 255, 0), side_name="left", vision=vision)
        if process_right:
            _draw_obb_items(vis_frame, right_shoe_obb_pts, right_base_poses, color=(0, 255, 255), side_name="right", vision=vision)

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试 get_all_shoe_points_with_obb 并把 OBB 画到图像上")
    parser.add_argument("--config", type=str, default=None, help="shoe_vision_config.json 路径")
    parser.add_argument("--output", type=str, default=None, help="输出图片路径；未传时不保存")
    parser.add_argument("--once", action="store_true", help="只处理一帧后退出")
    parser.add_argument("--no-show", action="store_true", help="不弹窗显示，仅打印结果")
    parser.add_argument(
        "--print-toe-center",
        action="store_true",
        help="打印每只鞋图像红点（鞋头极值点）在基座坐标系下的坐标（mm）",
    )
    return parser.parse_args()


import threading


def main() -> int:
    args = parse_args()
    output_path = Path(args.output) if args.output else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    if str(CONTOUR_SEG_ROOT) not in sys.path:
        sys.path.insert(0, str(CONTOUR_SEG_ROOT))

    try:
        contour_seg_module = importlib.import_module("contour_seg")
        segment_anything_module = importlib.import_module("segment_anything")

        load_sam = getattr(contour_seg_module, "load_sam")
        pick_mask_by_box = getattr(contour_seg_module, "pick_mask_by_box")
        extract_outer_contour = getattr(contour_seg_module, "extract_outer_contour")
        SamPredictor = getattr(segment_anything_module, "SamPredictor")
    except Exception as exc:
        raise RuntimeError(
            "导入 ContourSeg 或 segment-anything 失败，请先确认依赖安装与路径可用。"
        ) from exc

    sam = load_sam(str(DEFAULT_SAM_CHECKPOINT), "vit_b")
    predictor = SamPredictor(sam)

    vision = ShoeVision.from_config_file(args.config)
    if vision.camera is None:
        raise RuntimeError("未找到可用相机：请检查配置与相机连接")

    vision.save_crop_toe_up = False

    latest_frame: dict[str, np.ndarray | None] = {"vis": None}
    frame_lock = threading.Lock()
    stop_event = threading.Event()

    def detection_loop() -> None:
        frame_index = 0
        while not stop_event.is_set():
            left_base_toe_arc = get_shoe_base_pose_toe_and_arc_points(
                vision=vision,
                predictor=predictor,
                extract_outer_contour=extract_outer_contour,
                side="left",
            )
            right_base_toe_arc = get_shoe_base_pose_toe_and_arc_points(
                vision=vision,
                predictor=predictor,
                extract_outer_contour=extract_outer_contour,
                side="right",
            )

            left_base_poses = left_base_toe_arc["left_base_poses"]
            right_base_poses = right_base_toe_arc["right_base_poses"]
            left_shoe_obb_pts = left_base_toe_arc["left_shoe_obb_pts"]
            right_shoe_obb_pts = right_base_toe_arc["right_shoe_obb_pts"]
            rgb_frame = left_base_toe_arc["rgb_frame"]
            if rgb_frame is None:
                rgb_frame = right_base_toe_arc["rgb_frame"]
            _depth_frame = left_base_toe_arc["depth_frame"]
            if _depth_frame is None:
                _depth_frame = right_base_toe_arc["depth_frame"]
            if rgb_frame is None:
                continue

            raw = rgb_frame.copy()
            vis = raw.copy()

            seg_left, seg_right = _segment_by_obb(
                raw, vis,
                left_shoe_obb_pts, right_shoe_obb_pts,
                predictor, pick_mask_by_box, extract_outer_contour,
            )

            # 在 mask 上叠加 OBB 框
            _draw_obb_items(vis, left_shoe_obb_pts, left_base_poses, color=(0, 255, 0), side_name="left", vision=vision)
            _draw_obb_items(vis, right_shoe_obb_pts, right_base_poses, color=(0, 255, 255), side_name="right", vision=vision)

            summary = (
                f"frame={frame_index}  "
                f"L={len(left_base_poses)}({seg_left})  "
                f"R={len(right_base_poses)}({seg_right})"
            )
            cv2.putText(vis, summary, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (255, 255, 255), 2, lineType=cv2.LINE_AA)
            print(summary)

            if args.print_toe_center:
                for idx, toe_xyz in enumerate(left_base_toe_arc["left_toe_base_points"]):
                    if toe_xyz is None:
                        print(f"  [toe-center][left][{idx}] unavailable")
                    else:
                        print(
                            "  [toe-center][left][{}] base=({:.1f}, {:.1f}, {:.1f}) mm".format(
                                idx, toe_xyz[0], toe_xyz[1], toe_xyz[2]
                            )
                        )

                for idx, toe_xyz in enumerate(right_base_toe_arc["right_toe_base_points"]):
                    if toe_xyz is None:
                        print(f"  [toe-center][right][{idx}] unavailable")
                    else:
                        print(
                            "  [toe-center][right][{}] base=({:.1f}, {:.1f}, {:.1f}) mm".format(
                                idx, toe_xyz[0], toe_xyz[1], toe_xyz[2]
                            )
                        )

            with frame_lock:
                latest_frame["vis"] = vis

            if args.once:
                stop_event.set()
                break
            frame_index += 1

    try:
        det_thread = threading.Thread(target=detection_loop, daemon=True)
        det_thread.start()

        if not args.no_show:
            save_index = 0
            while not stop_event.is_set():
                with frame_lock:
                    frame = latest_frame["vis"]

                if frame is not None:
                    cv2.imshow("shoes_seg_realtime", frame)

                key = cv2.waitKey(30) & 0xFF
                if key == ord("q"):
                    stop_event.set()
                    break
                elif key == ord("s") and frame is not None:
                    save_path = (
                        output_path if output_path is not None
                        else Path(f"logs/shoes_seg_{save_index:05d}.jpg")
                    )
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    if cv2.imwrite(str(save_path), frame):
                        print(f"[s] Saved → {save_path}")
                    save_index += 1

            cv2.destroyAllWindows()
        else:
            det_thread.join()

        if output_path is not None:
            with frame_lock:
                last = latest_frame["vis"]
            if last is not None:
                if not cv2.imwrite(str(output_path), last):
                    raise RuntimeError(f"保存图片失败: {output_path}")
                print(f"Saved to: {output_path}")
        return 0
    finally:
        stop_event.set()
        vision.close()


if __name__ == "__main__":
    raise SystemExit(main())