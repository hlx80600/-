#!/usr/bin/env python3
"""计算使 TCP 刚体连线与鞋头-鞋楦中心连线在 XY 投影对齐时的 TCP rz。

流程：
1) 读取当前机械臂法兰与 TCP 位姿，建立 flange->tcp 刚体连线（基坐标系）。
2) 调用 get_shoe_base_pose_toe_and_arc_points 获取鞋楦中心与鞋头点（基坐标系）。
3) 在 XY 平面比较两条连线方向，计算需要补偿到 TCP rz 的角度。

说明：
- 本脚本只计算与打印，不下发运动命令。
- rz 计算公式：target_rz = wrap(current_tcp_rz + wrap(theta_shoe - theta_rigid))

使用方法：
1) 基础用法（自动选择一只可用鞋）
    python /home/casbotskill/ct/Casbot_Press_Shoes/shoe_pose_computer.py --ip 192.168.57.2

2) 指定左/右脚
    python /home/casbotskill/ct/Casbot_Press_Shoes/shoe_pose_computer.py --ip 192.168.57.2 --side left

3) 启用 SAM 获取更稳鞋头点（更慢）
   python /home/casbotskill/ct/Casbot_Press_Shoes/shoe_pose_computer.py --ip 192.168.57.2 --use-sam

4) 自定义输出 JSON 路径
    python /home/casbotskill/ct/Casbot_Press_Shoes/shoe_pose_computer.py \
       --ip 192.168.57.2 --save logs/tcp_rz_result.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent
RSDT_PATH = WORKSPACE_ROOT / "RSDT_Simple_Automation"

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from shoe_vision_seg import ShoeVision
from shoe_seg.shoes_seg import (
    DEFAULT_SAM_CHECKPOINT,
    get_shoe_base_pose_toe_and_arc_points,
)
from press_shoes.robot_arm.tcp_redirect import pose_to_matrix


def _norm_deg_180(deg: float) -> float:
    out = (float(deg) + 180.0) % 360.0 - 180.0
    if out == -180.0:
        return 180.0
    return out


def _angle_deg_from_xy(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(float(dy), float(dx)))


def _normalize_side(side: str) -> str:
    side_norm = str(side).strip().lower()
    if side_norm not in {"left", "right"}:
        raise ValueError(f"side 必须是 left/right，实际收到: {side}")
    return side_norm


def _rz_in_range(rz: float, rz_min: Optional[float], rz_max: Optional[float]) -> bool:
    rz_norm = _norm_deg_180(rz)
    rz_min_norm = None if rz_min is None else _norm_deg_180(float(rz_min))
    rz_max_norm = None if rz_max is None else _norm_deg_180(float(rz_max))

    if rz_min_norm is None and rz_max_norm is None:
        return True
    if rz_min_norm is None:
        return rz_norm <= rz_max_norm
    if rz_max_norm is None:
        return rz_norm >= rz_min_norm
    if rz_min_norm <= rz_max_norm:
        return rz_min_norm <= rz_norm <= rz_max_norm
    return rz_norm >= rz_min_norm or rz_norm <= rz_max_norm


def _read_pose6_with_check(ret_pose: Any, label: str) -> np.ndarray:
    if not isinstance(ret_pose, tuple) or len(ret_pose) < 2:
        raise RuntimeError(f"读取{label}失败：返回格式异常={ret_pose}")
    ret, pose = ret_pose[0], ret_pose[1]
    if int(ret) != 0:
        raise RuntimeError(f"读取{label}失败：ret={ret}")
    arr = np.asarray(pose, dtype=float).reshape(-1)
    if arr.size < 6:
        raise RuntimeError(f"读取{label}失败：位姿长度不足={arr}")
    return arr[:6]


def _connect_fairino(ip: str):
    if str(RSDT_PATH) not in sys.path:
        sys.path.insert(0, str(RSDT_PATH))
    automation_machine = importlib.import_module("automation_machine")
    automationMachine = getattr(automation_machine, "automationMachine")

    machine = automationMachine()
    machine.hardwareModule.activate_fairino_arm(
        dict_key_name="shoe_pose_computer_arm",
        name="shoe_pose_computer_arm",
        robot_ip=ip,
    )
    arm = machine.hardwareModule.get_fairino_robot_arm("shoe_pose_computer_arm")
    if not arm.ConnectRobotArm():
        raise RuntimeError(f"Fairino 连接失败: {ip}")
    print(f"[OK] 已连接 Fairino @ {ip}")
    return arm


def _disconnect_fairino(arm) -> None:
    try:
        arm.CloseRPC()
    except Exception:
        pass


def _get_tcp_pose(arm) -> np.ndarray:
    return _read_pose6_with_check(arm.GetActualTCPPose(), "TCP位姿")


def _get_flange_pose(arm) -> np.ndarray:
    # 优先法兰接口，失败时回退到 TCP 接口（兼容部分控制器）
    if hasattr(arm, "GetActualToolFlangePose"):
        try:
            return _read_pose6_with_check(arm.GetActualToolFlangePose(), "法兰位姿")
        except Exception as exc:
            print(f"[WARN] GetActualToolFlangePose 失败，回退 GetActualTCPPose: {exc}")
    return _read_pose6_with_check(arm.GetActualTCPPose(), "法兰位姿(回退TCP接口)")


def _load_sam_predictor_if_needed(use_sam: bool, sam_checkpoint: str = DEFAULT_SAM_CHECKPOINT ):
    if not use_sam:
        return None, None

    contour_seg_root = CURRENT_DIR / "ContourSeg"
    if str(contour_seg_root) not in sys.path:
        sys.path.insert(0, str(contour_seg_root))

    contour_seg_module = importlib.import_module("contour_seg")
    segment_anything_module = importlib.import_module("segment_anything")

    load_sam = getattr(contour_seg_module, "load_sam")
    extract_outer_contour = getattr(contour_seg_module, "extract_outer_contour")
    SamPredictor = getattr(segment_anything_module, "SamPredictor")

    sam = load_sam(str(DEFAULT_SAM_CHECKPOINT), "vit_b")
    predictor = SamPredictor(sam)
    return predictor, extract_outer_contour


class ShoePoseComputer:
    """纯计算类：根据机械臂当前位姿和视觉检测，计算目标鞋的鞋楦中心 xyz、rz、鞋头 xyz 与鞋头弧线点。"""

    def __init__(self, arm, vision: ShoeVision, *, predictor=None, extract_outer_contour=None) -> None:
        self.arm = arm
        self.vision = vision
        self.predictor = predictor
        self.extract_outer_contour = extract_outer_contour

        self.last_rgb_frame: Optional[np.ndarray] = None
        self.last_target_obb_points = None
        self.last_flange_pose: Optional[list[float]] = None
        self.last_tcp_pose: Optional[list[float]] = None
        self.last_delta_rz_deg: Optional[float] = None
        self.last_current_tcp_rz_deg: Optional[float] = None
        self.last_target_tcp_rz_deg: Optional[float] = None

    def get_shoe_data(
        self,
        side: str,
        *,
        rz_min: Optional[float] = None,
        rz_max: Optional[float] = None,
        max_tries: int = 5,
        retry_interval: float = 0.05,
        rz_offset: float = 0.0,
    ) -> dict[str, Any]:
        """单次返回目标鞋的鞋楦中心 xyz、目标 rz、鞋头 xyz 与鞋头弧线点列表。

        返回字典字段:
            side:             "left" 或 "right"
            index:            选中的目标索引
            shoe_center_xyz:  鞋楦中心基坐标 [x, y, z]
            rz:               目标 TCP rz (度)
            toe_xyz:          鞋头基坐标 [x, y, z]
            toe_arc_xyz_list: 鞋头弧线点基坐标列表 [[x, y, z], ...]
            vis_frame:        带可视化标注的 BGR 图像 (np.ndarray 或 None)

        参数说明:
            rz_min/rz_max:    可选的目标 TCP rz 约束，只有满足范围的目标才会被选中。
            rz_offset:        base_pose rz 到目标 TCP rz 的偏移量（度），默认 0。
        """
        flange_pose = _get_flange_pose(self.arm)
        tcp_pose = _get_tcp_pose(self.arm)
        expect_side = _normalize_side(side)
        current_side = expect_side

        find_target = False
        chosen_idx = -1
        base_pose = None
        toe_point = None
        toe_arc_xyz_list = None
        obb_points = None
        rgb_frame = None
        vis_frame = None
        while not find_target:
            chosen = get_shoe_base_pose_toe_and_arc_points(
                vision=self.vision,
                predictor=self.predictor,
                extract_outer_contour=self.extract_outer_contour,
                side=current_side,
                max_retries=max(1, int(max_tries)),
                retry_interval=retry_interval,
            )

            requested_side = str(chosen.get("requested_side")).strip().lower()
            chosen_side = str(chosen.get("selected_side", "")).strip().lower()
            if chosen_side not in {"left", "right"}:
                raise RuntimeError("未选择到可用鞋目标")
            if chosen_side != expect_side:
                print(f"[获取鞋子] 预期[{expect_side}]鞋，实际选择[{chosen_side}]鞋")

            side_key_prefix = "left" if chosen_side == "left" else "right"
            base_poses = chosen[f"{side_key_prefix}_base_poses"]
            toe_points = chosen[f"{side_key_prefix}_toe_base_points"]
            toe_arc_points = chosen[f"{side_key_prefix}_toe_arc_base_points"]
            obb_points = chosen[f"{side_key_prefix}_shoe_obb_pts"]
            rgb_frame = chosen.get("rgb_frame")
            vis_frame = chosen.get("vis_frame")

            chosen_idx = -1
            base_pose = None
            toe_point = None
            toe_arc_xyz_list = None
            for idx, (base_pose_candidate, toe_point_candidate, toe_arc_points_candidate) in enumerate(
                zip(base_poses, toe_points, toe_arc_points)
            ):
                if toe_point_candidate is None:
                    continue
                target_tcp_rz_candidate = _norm_deg_180(float(base_pose_candidate[3]) + rz_offset)
                if not _rz_in_range(target_tcp_rz_candidate, rz_min, rz_max):
                    continue
                chosen_idx = idx
                base_pose = base_pose_candidate
                toe_point = toe_point_candidate
                toe_arc_xyz_list = toe_arc_points_candidate
                break

            if chosen_idx < 0:
                if rz_min is None and rz_max is None:
                    raise RuntimeError(f"未检测到可用 {chosen_side} 鞋目标")
                other_side = "right" if current_side == "left" else "left"
                print(f"[换侧] {chosen_side} 鞋 rz 不满足范围[{rz_min}, {rz_max}]，改为检测 {other_side} 鞋 ...")
                current_side = other_side
                time.sleep(retry_interval)
                continue
            if base_pose is None or toe_point is None or toe_arc_xyz_list is None:
                raise RuntimeError(f"{chosen_side}[{chosen_idx}] 目标数据不完整")
            find_target = True
            
        center_xyz = np.asarray(base_pose[:3], dtype=float)
        toe_xyz = np.asarray(toe_point[:3], dtype=float)

        current_tcp_rz = float(tcp_pose[5])
        target_tcp_rz = _norm_deg_180(float(base_pose[3]) + rz_offset)
        delta_deg = _norm_deg_180(target_tcp_rz - current_tcp_rz)

        self.last_rgb_frame = rgb_frame if isinstance(rgb_frame, np.ndarray) else None
        self.last_target_obb_points = obb_points[chosen_idx] if chosen_idx < len(obb_points) else None
        self.last_flange_pose = flange_pose.tolist()
        self.last_tcp_pose = tcp_pose.tolist()
        self.last_delta_rz_deg = float(delta_deg)
        self.last_current_tcp_rz_deg = float(current_tcp_rz)
        self.last_target_tcp_rz_deg = float(target_tcp_rz)

        return {
            "side": chosen_side,
            "index": int(chosen_idx),
            "shoe_center_xyz": center_xyz.tolist(),
            "rz": float(target_tcp_rz),
            "toe_xyz": toe_xyz.tolist(),
            "toe_arc_xyz_list": [list(map(float, arc_point[:3])) for arc_point in toe_arc_xyz_list],
            "vis_frame": vis_frame,
        }

