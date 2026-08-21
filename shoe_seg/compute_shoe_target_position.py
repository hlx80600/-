#!/usr/bin/env python3
"""计算鞋子放入鞋槽的目标 TCP 位姿。

流程：
1) 连接机械臂和相机，调用 get_shoe_data 获取鞋子数据
2) 提取鞋槽中心线与引导线约束，计算刚性关系和过渡 TCP
3) 将 toe_arc_in_tcp / toe_lower_in_tcp 存入槽配置文件
4) 可视化结果（单独线程）

环境：需要在 yolo conda 环境中运行
    conda activate yolo

使用方法：
    # 基本用法（是否启用 SAM 由 slot 配置中的 use_sam 控制）
    python3 /home/casbotskill/ct/Casbot_Press_Shoes/shoe_seg/compute_shoe_target_position.py --ip 192.168.57.2

    # 指定左/右脚
    python3 /home/casbotskill/ct/Casbot_Press_Shoes/shoe_seg/compute_shoe_target_position.py --ip 192.168.57.2 --side left

    # 跳过 3D 可视化窗口
    python3 /home/casbotskill/ct/Casbot_Press_Shoes/shoe_seg/compute_shoe_target_position.py --ip 192.168.57.2 --no-vis

    # 自定义 slot 配置路径 / shoe_vision 配置
    python3 /home/casbotskill/ct/Casbot_Press_Shoes/shoe_seg/compute_shoe_target_position.py --ip 192.168.57.2 \
        --slot-config path/to/slot.yaml --config path/to/shoe_vision_config.json

参数说明：
    --ip            机械臂 IP 地址（默认 192.168.57.2）
    --side          指定检测哪只脚: left / right / auto / any（默认 auto）
    --no-vis        不弹出 matplotlib 3D 可视化窗口
    --slot-config   槽配置文件路径（YAML 读取，默认自动查找 slot.yaml/slot.json）
    --config        shoe_vision_config.json 路径（默认自动查找）

slot 配置字段：
    use_sam         是否启用 SAM 分割以提取鞋头弧线点
    toe_z_offset    鞋头最低点高于鞋槽最低 z 的距离，单位 mm

输出：
    - 终端打印目标 TCP 位姿 [x, y, z, rx, ry, rz]
    - 更新槽配置中的 toe_arc_in_tcp / toe_lower_in_tcp 字段
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation as Rot

CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent
SLOT_CONFIG_CANDIDATES = (
    WORKSPACE_ROOT / "press_shoes" / "config" / "slot.yaml",
    WORKSPACE_ROOT / "press_shoes" / "config" / "slot.json",
)
SLOT_CONFIG_PATH = next((path for path in SLOT_CONFIG_CANDIDATES if path.exists()), SLOT_CONFIG_CANDIDATES[0])

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from shoe_seg.slot_config import SlotConfig
from press_shoes.robot_arm.tcp_redirect import pose_to_matrix
from shoe_seg.shoe_pose_computer import (
    ShoePoseComputer,
    _connect_fairino,
    _disconnect_fairino,
    _load_sam_predictor_if_needed,
)
from shoe_vision_seg import ShoeVision
from shoe_seg.visualize_compute_slot_target_tcp import run_visualization, start_cv2_vis_thread


# ─────────────────────────── arg parsing ───────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算鞋子放入鞋槽的目标 TCP 位姿")
    parser.add_argument("--ip", default="192.168.57.2", help="Fairino IP")
    parser.add_argument("--config", type=str, default=None, help="shoe_vision_config.json 路径")
    parser.add_argument("--side", choices=["left", "right"], required=True)
    parser.add_argument("--gripper-host", default="192.168.57.100", help="夹爪 TCP 服务 IP")
    parser.add_argument("--gripper-port", type=int, default=15001, help="夹爪 TCP 服务端口")
    parser.add_argument("--gripper-timeout", type=float, default=5.0, help="夹爪 TCP socket 超时时间")
    parser.add_argument(
        "--slot-config",
        "--slot-json",
        dest="slot_config",
        type=str,
        default=str(SLOT_CONFIG_PATH),
        help="槽配置文件路径（按 YAML 格式读取，默认自动查找 slot.yaml/slot.json）",
    )
    parser.add_argument("--no-vis", action="store_true", help="不显示 3D 可视化")
    return parser.parse_args()


# ─────────────────────────── rigid relationship ───────────────────────────


def compute_rigid_relationship(
    grab_pose: list[float],
    toe_arc_xyz_list: list[list[float]],
) -> list[list[float]]:
    """计算 toe_arc 在 TCP 局部坐标系下的表示。

    Args:
        grab_pose: 抓取 TCP 位姿 [x, y, z, rx, ry, rz]
        toe_arc_xyz_list: 鞋头弧线基坐标 [[x,y,z], ...]

    Returns:
        toe_arc_in_tcp
    """
    grab_pose_arr = np.asarray(grab_pose, dtype=float).reshape(-1)
    if grab_pose_arr.size < 6:
        raise ValueError("grab_pose must contain 6 values")

    R_tcp = pose_to_matrix(grab_pose_arr[:6].tolist())[:3, :3]
    tcp_xyz = grab_pose_arr[:3]

    toe_arc_in_tcp = []
    for p in toe_arc_xyz_list:
        p_in_tcp = R_tcp.T @ (np.array(p) - tcp_xyz)
        toe_arc_in_tcp.append([round(float(v), 6) for v in p_in_tcp])

    return toe_arc_in_tcp


def send_gripper_command(host: str, port: int, command: str, timeout: float) -> dict[str, Any]:
    request = json.dumps({"command": command}, ensure_ascii=False) + "\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request.encode("utf-8"))
        sock_file = sock.makefile("r", encoding="utf-8")
        response_line = sock_file.readline()
        if not response_line:
            raise RuntimeError("夹爪服务端未返回数据")
        return json.loads(response_line)


def close_gripper(host: str, port: int, timeout: float) -> None:
    response = send_gripper_command(host, port, "close", timeout)
    if not bool(response.get("success")):
        raise RuntimeError(f"夹爪关闭失败: {response}")

# ─────────────────────────── solve full target TCP ───────────────────────────

def solve_target_tcp(
    toe_arc_in_tcp: np.ndarray,
    slot_z_min: float,
    target_point_xy: np.ndarray,
    toe_z_offset: float = 20.0,
    rx: float = 179.5,
    ry: float = 0.5,
    toe_lower_in_tcp: np.ndarray | None = None,
    xy_rz_limits: dict[str, object] | None = None,
) -> np.ndarray:
    """求解满足所有约束的目标 TCP 位姿 [x, y, z, rx, ry, rz]。

    约束:
        C0 (第一硬约束): xy 在线段投影范围内，rz 在最短弧范围内；离线距离作为软约束尽量小
        C2 (交点逼近):   鞋头弧线与引导线的交点尽量靠近 target_point_xy
        C4 (高度):       鞋头帘面最低 z = slot_z_min + toe_z_offset（解析计算）

    Args:
        xy_rz_limits: xy/rz 的约束参数，格式:
            {
                "line_point": (x0, y0),
                "line_dir": (dx, dy),
                "line_len": line_len,
                "line_tol_mm": tol,
                "rz_anchor": rz_anchor,
                "rz_delta": (delta_min, delta_max),
                "rz_display": (rz_start, rz_end),
            }
    """
    if xy_rz_limits is None:
        raise ValueError("xy_rz_limits is required for C0 hard constraint")
    slot_z_min = float(slot_z_min)

    line_point = np.asarray(xy_rz_limits["line_point"], dtype=float)
    line_dir = np.asarray(xy_rz_limits["line_dir"], dtype=float)
    line_len = float(xy_rz_limits["line_len"])
    line_tol_mm = float(xy_rz_limits["line_tol_mm"])
    rz_anchor = float(xy_rz_limits["rz_anchor"])
    rz_delta_min, rz_delta_max = xy_rz_limits["rz_delta"]
    rz_start, rz_end = xy_rz_limits["rz_display"]
    line_p0 = np.asarray(xy_rz_limits["line_p0"], dtype=float)
    line_p1 = np.asarray(xy_rz_limits["line_p1"], dtype=float)
    print(
        f"  [C0] xy projected on gripper segment, prefer line proximity (tol={line_tol_mm:.3f} mm), "
        f"segment=({line_p0.tolist()} -> {line_p1.tolist()}), "
        f"rz on shortest arc [{rz_start:.4f} -> {rz_end:.4f}]"
    )
    print(f"  [C2] target point on slot center line = {target_point_xy.tolist()}")

    # ── C2 + C4: 求 tcp_x, tcp_y, rz（z 由 C4 解析给出） ──

    def _offsets_for_rz(rz_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
        rot = Rot.from_euler("xyz", [rx, ry, float(rz_deg)], degrees=True).as_matrix()
        off_upper = (rot @ toe_arc_in_tcp.T).T
        if toe_lower_in_tcp is not None:
            off_lower = (rot @ toe_lower_in_tcp.T).T
            off_xy = np.vstack([off_upper[:, :2], off_lower[:, :2]])
            off_z = np.concatenate([off_upper[:, 2], off_lower[:, 2]])
        else:
            off_lower = None
            off_xy = off_upper[:, :2]
            off_z = off_upper[:, 2]
        return off_upper, off_lower, off_xy, off_z

    def _tcp_z_for_offsets(offsets_z: np.ndarray) -> float:
        min_offset_z = float(np.min(offsets_z))
        return slot_z_min + toe_z_offset - min_offset_z

    def _normalize_deg(angle_deg: float) -> float:
        return ((float(angle_deg) + 180.0) % 360.0) - 180.0

    def _intersect_toe_arc_with_guide_line(
        tcp_xy: np.ndarray,
        rz_deg: float,
    ) -> tuple[np.ndarray, float]:
        off_upper, _, _, _ = _offsets_for_rz(rz_deg)
        toe_arc_xy = tcp_xy + off_upper[:, :2]
        perp = np.array([-line_dir[1], line_dir[0]], dtype=float)
        signed_dist = (toe_arc_xy - line_point) @ perp

        crossings = np.where(signed_dist[:-1] * signed_dist[1:] <= 0.0)[0]
        if len(crossings) > 0:
            best_xy = None
            best_cost = None
            for idx in crossings:
                idx = int(idx)
                d0 = float(signed_dist[idx])
                d1 = float(signed_dist[idx + 1])
                if abs(d0 - d1) < 1e-9:
                    candidate_xy = toe_arc_xy[idx]
                else:
                    ratio = d0 / (d0 - d1)
                    candidate_xy = toe_arc_xy[idx] + ratio * (toe_arc_xy[idx + 1] - toe_arc_xy[idx])
                candidate_cost = float(np.sum((candidate_xy - target_point_xy) ** 2))
                if best_cost is None or candidate_cost < best_cost:
                    best_xy = candidate_xy
                    best_cost = candidate_cost
            intersection_xy = np.asarray(best_xy, dtype=float)
        else:
            idx = int(np.argmin(np.abs(signed_dist)))
            intersection_xy = toe_arc_xy[idx]

        return np.asarray(intersection_xy, dtype=float), float(np.min(np.abs(signed_dist)))

    rz_init_delta = 0.5 * (float(rz_delta_min) + float(rz_delta_max))
    rz_init = _normalize_deg(rz_anchor + rz_init_delta)
    offsets_upper_init, _, _, _ = _offsets_for_rz(rz_init)
    centroid_offset_init = np.mean(offsets_upper_init[:, :2], axis=0)
    tcp_xy_init = target_point_xy - centroid_offset_init

    def _project_t_and_signed_dist(pt_xy: np.ndarray) -> tuple[float, float]:
        rel = pt_xy - line_point
        t = float(np.dot(rel, line_dir) / max(line_len, 1e-9))
        perp = np.array([-line_dir[1], line_dir[0]])
        signed_dist = float(np.dot(rel, perp))
        return t, signed_dist

    t_init, _ = _project_t_and_signed_dist(tcp_xy_init)
    t_init = float(np.clip(t_init, 0.0, 1.0))
    xy_on_segment = line_point + (t_init * line_len) * line_dir
    x0 = np.array([
        float(xy_on_segment[0]),
        float(xy_on_segment[1]),
        float(np.clip(rz_init_delta, float(rz_delta_min), float(rz_delta_max))),
    ])

    def objective(var: np.ndarray) -> float:
        tcp_xy = var[:2]
        rz_val = _normalize_deg(rz_anchor + float(var[2]))
        intersection_xy, toe_line_gap = _intersect_toe_arc_with_guide_line(tcp_xy, rz_val)

        c2_pull = float(np.sum((intersection_xy - target_point_xy) ** 2))
        t, signed_dist = _project_t_and_signed_dist(tcp_xy)
        line_pull = 100.0 * float(signed_dist ** 2)
        toe_line_pull = 10.0 * float(toe_line_gap ** 2)

        return c2_pull + line_pull + toe_line_pull

    def c0_seg_low(var: np.ndarray) -> float:
        t, _ = _project_t_and_signed_dist(var[:2])
        return float(t * line_len)

    def c0_seg_high(var: np.ndarray) -> float:
        t, _ = _project_t_and_signed_dist(var[:2])
        return float((1.0 - t) * line_len)

    def c0_rz_low(var: np.ndarray) -> float:
        return float(var[2] - float(rz_delta_min))

    def c0_rz_high(var: np.ndarray) -> float:
        return float(float(rz_delta_max) - var[2])

    constraints = [
        # C0: 第一硬约束（xy 在线段投影范围内 + rz 最短弧）
        {"type": "ineq", "fun": c0_seg_low},
        {"type": "ineq", "fun": c0_seg_high},
        {"type": "ineq", "fun": c0_rz_low},
        {"type": "ineq", "fun": c0_rz_high},
    ]

    def _is_feasible(var: np.ndarray, tol: float = 1.0) -> bool:
        return all(float(c["fun"](var)) >= -tol for c in constraints)

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        constraints=constraints,
    )

    if (not bool(result.success)) or (not _is_feasible(np.asarray(result.x, dtype=float))):
        retry_best = None
        rz_mid = 0.5 * (float(rz_delta_min) + float(rz_delta_max))
        for t_seed in np.linspace(0.0, 1.0, 7):
            xy_seed = line_point + (float(t_seed) * line_len) * line_dir
            for rz_seed in (float(rz_delta_min), rz_mid, float(rz_delta_max)):
                seed = np.array([float(xy_seed[0]), float(xy_seed[1]), float(rz_seed)], dtype=float)
                cand = minimize(
                    objective,
                    seed,
                    method="SLSQP",
                    constraints=constraints,
                )
                if bool(cand.success) and _is_feasible(np.asarray(cand.x, dtype=float)):
                    if retry_best is None or float(cand.fun) < float(retry_best.fun):
                        retry_best = cand

        if retry_best is None:
            vals = [float(c["fun"](np.asarray(result.x, dtype=float))) for c in constraints]
            raise RuntimeError(
                "No feasible solution for hard constraints C0. "
                f"min_constraint={min(vals):.6f}, line_tol_mm={line_tol_mm:.3f}."
            )
        result = retry_best
    tcp_x, tcp_y, rz_delta_target = float(result.x[0]), float(result.x[1]), float(result.x[2])
    rz_target = _normalize_deg(rz_anchor + rz_delta_target)

    R_target = Rot.from_euler("xyz", [rx, ry, rz_target], degrees=True).as_matrix()
    offsets_upper, offsets_lower, offsets_xy, offsets_z = _offsets_for_rz(rz_target)
    tcp_z = _tcp_z_for_offsets(offsets_z)
    print(f"  [C4] tcp_z = {tcp_z:.4f} mm  (slot_z_min={slot_z_min}, offset={toe_z_offset})")

    # ── 验证约束 ──
    intersection_xy, toe_line_gap = _intersect_toe_arc_with_guide_line(
        np.array([tcp_x, tcp_y], dtype=float),
        rz_target,
    )
    c2_dist = float(np.linalg.norm(intersection_xy - target_point_xy))
    print(
        f"  [C2] toe_arc_line_intersection={intersection_xy.tolist()}, "
        f"target_dist={c2_dist:.4f} mm, toe_line_gap={toe_line_gap:.4f} mm"
    )

    c0_t, c0_signed_dist = _project_t_and_signed_dist(np.array([tcp_x, tcp_y]))
    in_seg = -1e-6 <= c0_t <= 1.0 + 1e-6
    in_rz = float(rz_delta_min) - 1e-6 <= rz_delta_target <= float(rz_delta_max) + 1e-6
    print(
        f"  [C0] line_dist={abs(c0_signed_dist):.4f}mm (soft), "
        f"seg_t={c0_t:.4f} in_seg={in_seg}, rz={rz_target:.4f} in_range={in_rz}"
    )

    toe_z_all = offsets_z + tcp_z
    print(f"  [C4] toe_z_min={float(np.min(toe_z_all)):.4f}, target={slot_z_min + toe_z_offset:.4f}")
    upper_z_all = offsets_upper[:, 2] + tcp_z
    print(f"  toe_arc (upper) z_mean = {float(np.mean(upper_z_all)):.4f} mm")
    if toe_lower_in_tcp is not None:
        lower_z_all = offsets_lower[:, 2] + tcp_z
        print(f"  toe_lower (lower) z_mean = {float(np.mean(lower_z_all)):.4f} mm")

    target_tcp = np.array([tcp_x, tcp_y, tcp_z, rx, ry, rz_target])
    return target_tcp


# ─────────────────────────── solve transition TCP ───────────────────────────
def solve_transition_tcp(
    toe_arc_in_tcp: np.ndarray,
    slot_z_min: float,
    target_point_xy: np.ndarray,
    rx: float,
    ry: float,
    toe_z_offset: float = 20.0,
    toe_lower_in_tcp: np.ndarray | None = None,
    xy_rz_limits: dict[str, object] | None = None,
) -> np.ndarray:
    """求解过渡 TCP 位姿。

    委托 solve_target_tcp 完成。
    """
    return solve_target_tcp(
        toe_arc_in_tcp=toe_arc_in_tcp,
        slot_z_min=slot_z_min,
        target_point_xy=target_point_xy,
        toe_z_offset=toe_z_offset,
        rx=rx,
        ry=ry,
        toe_lower_in_tcp=toe_lower_in_tcp,
        xy_rz_limits=xy_rz_limits,
    )


@dataclass(slots=True)
class TransitionTcpResult:
    transition_tcp: np.ndarray
    toe_arc_in_tcp: np.ndarray
    toe_lower_in_tcp: np.ndarray | None = None
    toe_point_in_tcp: np.ndarray | None = None


def compute_transition_tcp(
    grab_pose: list[float],
    toe_arc_xyz_list: list[list[float]],
    slot_z_min: float,
    shoe_origin_z_min: float | None,
    rx: float,
    ry: float,
    toe_z_offset: float,
    xy_rz_limits: dict[str, object],
    toe_point: list[float] | None = None
) -> TransitionTcpResult:
    """根据抓取位姿与鞋头弧线，计算过渡 TCP 与刚性关系结果。"""
    if "target_point_xy" not in xy_rz_limits:
        raise ValueError("xy_rz_limits must include target_point_xy for compute_transition_tcp")

    grab_pose_arr = np.asarray(grab_pose, dtype=float).reshape(-1)
    if grab_pose_arr.size < 6:
        raise ValueError("grab_pose must contain 6 values")

    toe_arc_in_tcp_np = np.asarray(
        compute_rigid_relationship(grab_pose_arr[:6].tolist(), toe_arc_xyz_list),
        dtype=float,
    )

    toe_lower_in_tcp_np = None
    if shoe_origin_z_min is not None:
        R_tcp = pose_to_matrix(grab_pose_arr[:6].tolist())[:3, :3]
        tcp_xyz = grab_pose_arr[:3]
        toe_lower_in_tcp = []
        for point_xyz in toe_arc_xyz_list:
            lower_point_xyz = np.array([point_xyz[0], point_xyz[1], shoe_origin_z_min], dtype=float)
            point_in_tcp = R_tcp.T @ (lower_point_xyz - tcp_xyz)
            toe_lower_in_tcp.append([round(float(value), 6) for value in point_in_tcp])
        toe_lower_in_tcp_np = np.asarray(toe_lower_in_tcp, dtype=float)

    toe_point_in_tcp_np = None
    if toe_point is not None:
        toe_point_in_tcp_np = np.asarray(
            compute_rigid_relationship(grab_pose_arr[:6].tolist(), [toe_point])[0],
            dtype=float,
        )

    transition_tcp = solve_transition_tcp(
        toe_arc_in_tcp=toe_arc_in_tcp_np,
        slot_z_min=slot_z_min,
        target_point_xy=np.asarray(xy_rz_limits["target_point_xy"], dtype=float),
        rx=rx,
        ry=ry,
        toe_z_offset=toe_z_offset,
        toe_lower_in_tcp=toe_lower_in_tcp_np,
        xy_rz_limits=xy_rz_limits,
    )

    return TransitionTcpResult(
        transition_tcp=transition_tcp,
        toe_arc_in_tcp=toe_arc_in_tcp_np,
        toe_lower_in_tcp=toe_lower_in_tcp_np,
        toe_point_in_tcp=toe_point_in_tcp_np,
    )


def main() -> int:
    args = parse_args()
    slot_config = SlotConfig.load_yaml(args.slot_config)
    toe_z_offset = slot_config.toe_z_offset
    use_sam = slot_config.use_sam

    arm = None
    vision: Optional[ShoeVision] = None
    vis_stop_event = threading.Event()
    vis_thread: Optional[threading.Thread] = None

    try:
        print(f"[config] slot_config={args.slot_config}, use_sam={use_sam}, toe_z_offset={toe_z_offset} mm")
        if not slot_config.z_heights:
            raise RuntimeError("slot config missing z_heights; current workflow needs slot_z_min for C4")
        slot_z_min = float(min(slot_config.z_heights))

        # ══ Phase 1: 连接 + 获取鞋子数据 ══
        arm = _connect_fairino(args.ip)
        predictor, extract_outer_contour = _load_sam_predictor_if_needed(use_sam)

        vision = ShoeVision.from_config_file(args.config)
        if vision.camera is None:
            raise RuntimeError("No camera found: check config and camera connection")
        vision.save_crop_toe_up = False

        computer = ShoePoseComputer(
            arm, vision, predictor=predictor, extract_outer_contour=extract_outer_contour
        )

        print("\n[1/6] Getting shoe data ...")
        max_arc_retries = 10
        shoe_data = None
        for attempt in range(1, max_arc_retries + 1):
            shoe_data = computer.get_shoe_data(args.side, rz_offset=-89.5)
            if len(shoe_data["toe_arc_xyz_list"]) > 0:
                break
            print(f"  [retry {attempt}/{max_arc_retries}] toe_arc empty, re-detecting ...")
            import time
            time.sleep(0.3)

        shoe_center_xyz = shoe_data["shoe_center_xyz"]
        # shoe_center_xyz[0] += -50  # 应用用户指定的 X 轴偏移
        rz = shoe_data["rz"]
        toe_arc_xyz_list = shoe_data["toe_arc_xyz_list"]

        # 在独立线程中显示 vis_frame
        vis_frame = shoe_data.get("vis_frame")
        vis_thread = start_cv2_vis_thread(vis_frame, vis_stop_event)

        print(f"  selected_target = {shoe_data['side']}")
        print(f"  shoe_center_xyz = {shoe_center_xyz}")
        print(f"  rz = {rz:.4f}°")
        print(f"  toe_arc count = {len(toe_arc_xyz_list)}")
        if len(toe_arc_xyz_list) > 0:
            arc_z_vals = [p[2] for p in toe_arc_xyz_list]
            print(f"  toe_arc z_mean (detected) = {sum(arc_z_vals)/len(arc_z_vals):.4f} mm")

        if len(toe_arc_xyz_list) == 0:
            raise RuntimeError(
                f"After {max_arc_retries} retries, toe_arc_xyz_list is still empty. "
                "Make sure the shoe is visible to the camera, or enable use_sam in the slot config."
            )

        # ══ Phase 1b: 准备抓取位姿与鞋头几何 ══
        print("\n[2/6] Preparing grab pose and toe geometry ...")

        # 抓取位姿：z = shoe_center_xyz[2] - 50
        grab_z = shoe_center_xyz[2] - 55.0
        grab_pose = [shoe_center_xyz[0], shoe_center_xyz[1], grab_z, 179.5, 0.5, rz]
        print(f"  grab_pose = {[round(v, 3) for v in grab_pose]}")

        # 构造下线：与 toe_arc 相同 XY，Z = shoe_origin_z_min
        shoe_origin_z_min = slot_config.shoe_origin_z_min
        toe_lower_xyz_list = None
        if shoe_origin_z_min is not None:
            toe_lower_xyz_list = [[p[0], p[1], shoe_origin_z_min] for p in toe_arc_xyz_list]
            print(f"  shoe_origin_z_min = {shoe_origin_z_min} mm (from slot config)")
        else:
            print("  [WARN] shoe_origin_z_min not found in slot config, skipping lower line")

        grab_center_xyz = [shoe_center_xyz[0], shoe_center_xyz[1], grab_z]

        # ══ 准备机械臂运动（由可视化按钮或用户确认触发） ══
        approach_z = grab_z + 80.0
        approach_pose = [shoe_center_xyz[0], shoe_center_xyz[1], approach_z, 179.5, 0.5, rz]
        print(f"\n  grab_pose = {[round(v, 3) for v in grab_pose]}")
        print(f"  approach_pose (z+50) = {[round(v, 3) for v in approach_pose]}")

        move_thread_holder: list[threading.Thread] = []
        move_error: list[Exception] = []

        def _arm_move_worker() -> None:
            try:
                print("  [ARM] moving to approach point ...")
                ret = arm.MoveCart(
                    desc_pos=approach_pose, tool=1, user=0,
                    vel=20.0, acc=0.0, ovl=20.0, blendT=-1.0, config=-1,
                )
                if ret != 0:
                    raise RuntimeError(f"MoveL to approach failed, ret={ret}")
                print("  [OK] reached approach point")

                ret = arm.MoveL(
                    desc_pos=grab_pose, tool=1, user=0,
                    vel=20.0, acc=0.0, ovl=20.0, blendR=-1.0, config=-1,
                )
                if ret != 0:
                    raise RuntimeError(f"MoveL to grab point failed, ret={ret}")
                print("  [OK] reached grab point")

                print("  [GRIPPER] closing gripper ...")
                close_gripper(args.gripper_host, args.gripper_port, args.gripper_timeout)
                print("  [OK] gripper closed")
            except Exception as exc:
                move_error.append(exc)
                print(f"  [ERR] grab sequence failed: {exc}")

        def _start_arm_move() -> None:
            t = threading.Thread(target=_arm_move_worker, daemon=True)
            t.start()
            move_thread_holder.append(t)

        # ══ Phase 2: 提取目标点与引导线参考 ══
        print("\n[3/6] Extracting target-point and guide-line references ...")
        xy_rz_limits = slot_config.gripper_xy_rz_limits()
        _l_dir = np.asarray(xy_rz_limits["line_dir"], dtype=float)

        target_point_xy = slot_config.target_point_xy()
        center_line_dir = slot_config.center_line_direction(reference_direction=_l_dir)
        print(f"  center_line_dir (from gripper reference): {center_line_dir.tolist()}")
        print(f"  slot_z_min = {slot_z_min:.4f} mm")

        xy_rz_limits_with_target = dict(xy_rz_limits)
        xy_rz_limits_with_target["target_point_xy"] = target_point_xy.tolist()
        print(f"  target_point_xy = {target_point_xy.tolist()}")

        print(
            "  gripper_refer_pose limits: "
            f"line=({xy_rz_limits['line_p0']} -> {xy_rz_limits['line_p1']}), "
            f"line_tol_mm={xy_rz_limits['line_tol_mm']}, "
            f"rz_shortest_arc={xy_rz_limits['rz_display']}"
        )

        # ══ Phase 3: 计算刚性关系并求解过渡 TCP ══
        print("\n[4/6] Computing transition TCP and rigid relationship ...")
        slot_up_pose = [210.391, 256.074, 183.338, 165.447, 3.495, -27.288]
        trans_rx, trans_ry = slot_up_pose[3], slot_up_pose[4]
        print(f"\n  Computing transition TCP (rx={trans_rx}, ry={trans_ry}) ...")
        transition_result = compute_transition_tcp(
            grab_pose=grab_pose,
            toe_arc_xyz_list=toe_arc_xyz_list,
            slot_z_min=slot_z_min,
            shoe_origin_z_min=shoe_origin_z_min,
            rx=trans_rx,
            ry=trans_ry,
            toe_z_offset=toe_z_offset,
            xy_rz_limits=xy_rz_limits_with_target,
        )
        transition_tcp = transition_result.transition_tcp
        toe_arc_in_tcp_np = transition_result.toe_arc_in_tcp
        toe_lower_in_tcp_np = transition_result.toe_lower_in_tcp
        toe_arc_in_tcp = toe_arc_in_tcp_np.tolist()
        toe_lower_in_tcp = toe_lower_in_tcp_np.tolist() if toe_lower_in_tcp_np is not None else None

        print(f"  toe_arc_in_tcp count = {len(toe_arc_in_tcp)}")
        if toe_lower_in_tcp is not None:
            print(f"  toe_lower_in_tcp count = {len(toe_lower_in_tcp)}")

        # 往返验证
        grab_tcp_full = [*grab_center_xyz, 179.5, 0.5, rz]
        R_grab = pose_to_matrix(grab_tcp_full)[:3, :3]
        tcp_xyz_np = np.array(grab_center_xyz)
        max_err = 0.0
        for i, p_orig in enumerate(toe_arc_xyz_list):
            p_reconstructed = R_grab @ toe_arc_in_tcp_np[i] + tcp_xyz_np
            err = float(np.linalg.norm(np.array(p_orig) - p_reconstructed))
            max_err = max(max_err, err)
        print(f"  round-trip max error = {max_err:.6f} mm")

        # ══ 保存到槽配置 ══
        print("\n[5/6] Saving rigid relationship to slot config ...")
        slot_config.set_rigid_relationship(toe_arc_in_tcp, toe_lower_in_tcp)
        slot_config.save_yaml(args.slot_config)
        print(f"  [OK] updated {args.slot_config}")

        trans_list = [round(float(v), 4) for v in transition_tcp]
        print(f"  Transition TCP pose: {trans_list}")

        R_trans = Rot.from_euler("xyz", transition_tcp[3:], degrees=True).as_matrix()
        transition_toe_base = (R_trans @ toe_arc_in_tcp_np.T).T + transition_tcp[:3]
        transition_toe_lower_base = None
        if toe_lower_in_tcp_np is not None:
            transition_toe_lower_base = (R_trans @ toe_lower_in_tcp_np.T).T + transition_tcp[:3]

        # ── slot_up 和 transition 运动回调 ──
        def _slot_up_move_worker() -> None:
            try:
                print("  [ARM] moving to approach_pose ...")
                ret = arm.MoveL(
                    desc_pos=approach_pose, tool=1, user=0,
                    vel=20.0, acc=0.0, ovl=20.0, blendR=-1.0, config=-1,
                )
                if ret != 0:
                    raise RuntimeError(f"MoveL to approach_pose failed, ret={ret}")
                print("  [OK] reached approach_pose")

                print("  [ARM] moving to slot_up_pose ...")
                ret = arm.MoveL(
                    desc_pos=slot_up_pose, tool=1, user=0,
                    vel=20.0, acc=0.0, ovl=20.0, blendR=-1.0, config=-1,
                )
                if ret != 0:
                    raise RuntimeError(f"MoveL to slot_up_pose failed, ret={ret}")
                print("  [OK] reached slot_up_pose")
            except Exception as exc:
                move_error.append(exc)
                print(f"  [ERR] slot_up move failed: {exc}")

        def _start_slot_up_move() -> None:
            t = threading.Thread(target=_slot_up_move_worker, daemon=True)
            t.start()
            move_thread_holder.append(t)

        def _transition_move_worker() -> None:
            try:
                print("  [ARM] moving to transition pose ...")
                ret = arm.MoveL(
                    desc_pos=transition_tcp.tolist(), tool=1, user=0,
                    vel=20.0, acc=0.0, ovl=20.0, blendR=-1.0, config=-1,
                )
                if ret != 0:
                    raise RuntimeError(f"MoveL to transition failed, ret={ret}")
                print("  [OK] reached transition pose")
            except Exception as exc:
                move_error.append(exc)
                print(f"  [ERR] transition move failed: {exc}")

        def _start_transition_move() -> None:
            t = threading.Thread(target=_transition_move_worker, daemon=True)
            t.start()
            move_thread_holder.append(t)

        # ══ Phase 4: 可视化 + 可选运动 ══
        if not args.no_vis:
            print("\n[6/6] Starting 3D visualization ...")
            print("  (click buttons on figure to trigger arm movements)")
            vis_ok = run_visualization(
                center_line_dir=center_line_dir,
                toe_arc_base=transition_toe_base,
                target_tcp=transition_tcp,
                flange_vec_in_tcp=None,
                grab_tcp_xyz=np.array(grab_center_xyz),
                toe_arc_orig=np.array(toe_arc_xyz_list),
                toe_lower_base=transition_toe_lower_base,
                toe_lower_orig=np.array(toe_lower_xyz_list) if toe_lower_xyz_list is not None else None,
                move_buttons=[
                    ("1. Grab", _start_arm_move),
                    ("2. Slot Up", _start_slot_up_move),
                    ("3. Transition", _start_transition_move),
                ],
                transition_tcp=transition_tcp,
                transition_toe_base=transition_toe_base,
                transition_toe_lower_base=transition_toe_lower_base,
                trajectory=None,
                traj_toe_arc_in_tcp=toe_arc_in_tcp_np,
                traj_toe_lower_in_tcp=toe_lower_in_tcp_np,
            )
            if not vis_ok:
                print("  [WARN] Visualization popup not available, fallback to terminal confirmation.")
                ans = input("  Press Enter to move arm to grab pose, or 'n' to skip: ").strip().lower()
                if ans != 'n':
                    _arm_move_worker()
        else:
            print("\n[6/6] Skipping visualization (--no-vis)")
            ans = input("  Press Enter to move arm, or 'n' to skip: ").strip().lower()
            if ans != 'n':
                _arm_move_worker()

        # 等待机械臂运动线程完成
        for t in move_thread_holder:
            print("\nWaiting for arm movement to finish ...")
            t.join()
        if move_error:
            print(f"  [WARN] arm movement error: {move_error[0]}")

        return 0

    finally:
        vis_stop_event.set()
        if vis_thread is not None:
            vis_thread.join(timeout=1.0)
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        if vision is not None:
            try:
                vision.close()
            except Exception:
                pass
        if arm is not None:
            _disconnect_fairino(arm)


if __name__ == "__main__":
    raise SystemExit(main())
