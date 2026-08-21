#!/usr/bin/env python3
"""读取当前 TCP 位姿，并让 TCP 绕固定点旋转到目标 rx/ry。

默认行为：
1. 通过 GetActualTCPPose 读取当前位姿。
2. 以基坐标固定点为旋转中心，计算“姿态变化导致的目标 TCP 平移”。
3. 将 rx/ry 旋转到目标值（rz 默认保持当前值），并用 MoveCart 执行。
4. 目标 rx/ry、速度、分段步数等参数在脚本内常量中配置。

用法：
    1) 默认执行（使用脚本内默认 pivot 和目标 rx/ry）
         python shoe_align/rotate_tcp_about_point.py --ip 192.168.57.2

    2) 仅计算不运动（查看目标位姿）
         python shoe_align/rotate_tcp_about_point.py --ip 192.168.57.2 --dry-run

    3) 自定义旋转中心
         python shoe_align/rotate_tcp_about_point.py --ip 192.168.57.2 \
             --pivot 462.371 651.096 147.274
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R


CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

DEFAULT_IP = "192.168.57.2"
RSDT_PATH = "/home/casbotskill/RSDT_Simple_Automation"

# 基坐标系下旋转中心（mm），通常取鞋面/夹具上的固定接触点
DEFAULT_PIVOT = np.array([459.0, 702.1, 36.17], dtype=float)
DEFAULT_TARGET_RX = 178.88
DEFAULT_TARGET_RY = 3.17

# Fairino MoveCart 参数（与 shoe_allign 等脚本保持一致时可复用）
MOVE_CART_TOOL = 1
MOVE_CART_USER = 0
MOVE_CART_VEL = 20.0
MOVE_CART_ACC = 0.0
MOVE_CART_OVL = 20.0
MOVE_CART_BLEND_T = -1.0  # -1 表示不启用轨迹交融
DEFAULT_STEPS = 30  # 1=单步直达；>1 时分段插补并在每步前等待确认


def connect_fairino(ip: str):
    if RSDT_PATH not in sys.path:
        sys.path.insert(0, RSDT_PATH)
    automation_machine = importlib.import_module("automation_machine")
    automationMachine = getattr(automation_machine, "automationMachine")

    machine = automationMachine()
    machine.hardwareModule.activate_fairino_arm(
        dict_key_name="rotate_tcp_about_point_arm",
        name="rotate_tcp_about_point_arm",
        robot_ip=ip,
    )
    arm = machine.hardwareModule.get_fairino_robot_arm("rotate_tcp_about_point_arm")
    if not arm.ConnectRobotArm():
        raise RuntimeError(f"Fairino 连接失败: {ip}")
    print(f"[OK] 已连接 Fairino @ {ip}")
    return arm


def disconnect_fairino(arm) -> None:
    try:
        arm.CloseRPC()
        print("[OK] 已断开 Fairino RPC")
    except Exception:
        pass


def get_tcp_pose(arm) -> np.ndarray:
    ret, pose = arm.GetActualTCPPose()
    if ret != 0:
        raise RuntimeError(f"读取当前位姿失败(GetActualTCPPose), ret={ret}")
    pose_array = np.asarray(pose, dtype=float)
    if pose_array.shape[0] < 6:
        raise RuntimeError(f"当前位姿长度异常: {pose_array.shape}")
    return pose_array[:6]


class TcpRotateAboutPivot:
    """绕基坐标固定点规划 TCP 位姿，使工具姿态到达目标 rx/ry。

    几何约定：姿态按欧拉 xyz（度）变化时，TCP 位置需同步平移，才能保证
    TCP 绕 pivot 做刚体旋转，而不是在原地只转姿态。每步位姿均相对「规划
    起点 current_pose」计算，分段时不在中间点之间递推。

    供其他模块使用时传入 ``log``（需有 ``.info(msg)``），或 ``log_plan`` /
    ``set_logger``；本文件 ``main()`` 仅用 ``print``。
    """

    @staticmethod
    def emit_log(log: Any | None, msg: str) -> None:
        """优先 ``log.info``，无 log 或失败时 ``print``。"""
        if log is None:
            print(msg)
            return
        try:
            log.info(msg)
        except Exception:
            print(msg)

    def __init__(
        self,
        pivot_point: np.ndarray,
        target_rx: float,
        target_ry: float,
        target_rz: float | None = None,
        steps: int = 1,
        log: Any | None = None,
    ) -> None:
        self.pivot = np.asarray(pivot_point, dtype=float)  # 基坐标，mm
        self.target_rx = float(target_rx)
        self.target_ry = float(target_ry)
        self.target_rz = target_rz  # None → 保持当前 rz
        self.steps = int(steps)
        self.log = log
        if self.steps < 1:
            raise ValueError("steps 必须 >= 1")

    def set_logger(self, log: Any | None) -> None:
        self.log = log

    def _log(self, msg: str) -> None:
        self.emit_log(self.log, msg)

    @staticmethod
    def format_pose(pose: np.ndarray) -> str:
        values = [float(v) for v in pose[:6]]
        return (
            f"x={values[0]:.3f}, y={values[1]:.3f}, z={values[2]:.3f}, "
            f"rx={values[3]:.3f}, ry={values[4]:.3f}, rz={values[5]:.3f}"
        )

    @staticmethod
    def interpolate_angle(start_deg: float, end_deg: float, ratio: float) -> float:
        """沿最短角度路径插值，避免跨越 ±180 时大幅回转。"""
        delta = ((end_deg - start_deg + 180.0) % 360.0) - 180.0
        return start_deg + ratio * delta

    def resolve_target_rz(self, current_pose: np.ndarray) -> float:
        """rz 未指定时沿用当前姿态，避免绕 pivot 时额外扭转工具 Z 轴。"""
        return float(current_pose[5] if self.target_rz is None else self.target_rz)

    def compute_target_pose(
        self,
        current_pose: np.ndarray,
        target_rx: float | None = None,
        target_ry: float | None = None,
        target_rz: float | None = None,
    ) -> np.ndarray:
        """在 pivot 处做刚体旋转：姿态变为目标 rx/ry/rz，并反算 TCP 平移。

        可选 target_rx/ry/rz 用于分段中间步；缺省时用实例上的目标角。
        """
        rx = self.target_rx if target_rx is None else target_rx
        ry = self.target_ry if target_ry is None else target_ry
        rz = self.resolve_target_rz(current_pose) if target_rz is None else target_rz

        current_position = current_pose[:3]
        current_rotation = R.from_euler("xyz", current_pose[3:], degrees=True).as_matrix()
        target_rotation = R.from_euler("xyz", [rx, ry, rz], degrees=True).as_matrix()

        # 从当前姿态到目标姿态的旋转增量（在基坐标系下）
        rotation_delta = target_rotation @ current_rotation.T
        # 位置随同一刚体变换：p' = pivot + R_delta @ (p - pivot)
        target_position = self.pivot + rotation_delta @ (current_position - self.pivot)

        target_pose = np.empty(6, dtype=float)
        target_pose[:3] = target_position
        target_pose[3:] = [rx, ry, rz]
        return target_pose

    def build_segmented_poses(self, current_pose: np.ndarray) -> list[list[float]]:
        """从当前位姿到目标姿态，按 steps 分段生成 MoveCart 目标点。

        每一步的 rx/ry/rz 在起点与终点之间线性插值（最短角路径），再对
        同一 current_pose 调用 compute_target_pose。注意：不以上一分段点为
        新起点，避免累积误差，也与「绕固定 pivot 旋转」的定义一致。
        """
        resolved_target_rz = self.resolve_target_rz(current_pose)
        segmented_poses: list[list[float]] = []

        for step_idx in range(1, self.steps + 1):
            ratio = step_idx / float(self.steps)  # (0, 1]，最后一步 ratio=1
            step_rx = self.interpolate_angle(float(current_pose[3]), self.target_rx, ratio)
            step_ry = self.interpolate_angle(float(current_pose[4]), self.target_ry, ratio)
            step_rz = self.interpolate_angle(float(current_pose[5]), resolved_target_rz, ratio)
            step_pose = self.compute_target_pose(
                current_pose=current_pose,
                target_rx=step_rx,
                target_ry=step_ry,
                target_rz=step_rz,
            )
            segmented_poses.append(step_pose.tolist())

        return segmented_poses

    def plan(self, current_pose: np.ndarray) -> tuple[np.ndarray, list[list[float]]]:
        """计算终点位姿与分段轨迹（不访问机械臂、不打印）。"""
        target_pose = self.compute_target_pose(current_pose)
        segmented_poses = self.build_segmented_poses(current_pose)
        return target_pose, segmented_poses

    def log_plan(self, current_pose: np.ndarray) -> tuple[np.ndarray, list[list[float]]]:
        """打印当前/目标位姿摘要，并返回与 plan() 相同的结果。"""
        target_pose, segmented_poses = self.plan(current_pose)

        self._log("\n[INFO] 当前 TCP 位姿:")
        self._log(self.format_pose(current_pose))
        self._log(f"[INFO] 旋转中心点: {[round(float(v), 3) for v in self.pivot.tolist()]}")
        self._log("[INFO] 目标 TCP 位姿:")
        self._log(self.format_pose(target_pose))

        return target_pose, segmented_poses


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP 绕固定点旋转到目标 rx/ry")
    parser.add_argument("--ip", default=DEFAULT_IP, help=f"Fairino IP（默认 {DEFAULT_IP}）")
    parser.add_argument(
        "--pivot",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        default=DEFAULT_PIVOT.tolist(),
        help="旋转中心点（基坐标系，mm）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅计算目标位姿，不执行 MoveCart")
    args = parser.parse_args()

    pivot = np.asarray(args.pivot, dtype=float)
    # 位姿规划与执行分离：planner 只做几何，MoveCart 在下方循环调用
    planner = TcpRotateAboutPivot(
        pivot_point=pivot,
        target_rx=DEFAULT_TARGET_RX,
        target_ry=DEFAULT_TARGET_RY,
        target_rz=None,
        steps=DEFAULT_STEPS,
    )
    steps = planner.steps

    arm = connect_fairino(args.ip)
    try:
        current_pose = get_tcp_pose(arm)
        target_pose, segmented_poses = planner.plan(current_pose)

        print("\n[INFO] 当前 TCP 位姿:")
        print(planner.format_pose(current_pose))
        print(f"[INFO] 旋转中心点: {[round(float(v), 3) for v in planner.pivot.tolist()]}")
        print("[INFO] 目标 TCP 位姿:")
        print(planner.format_pose(target_pose))

        if args.dry_run:
            print(f"[INFO] dry-run 模式，分段数: {steps}")
            for idx, pose in enumerate(segmented_poses, start=1):
                print(
                    f"  step {idx:03d}/{steps}: "
                    f"{planner.format_pose(np.asarray(pose, dtype=float))}"
                )
            with open("poses.txt", "w") as f:
                for pose in segmented_poses:
                    f.write(",".join(f"{v:.6f}" for v in pose) + "\n")
            print("[INFO] 已保存所有分段点到 poses.txt，可用于轨迹可视化。")
            return

        print(f"[RUN] 分段插补执行，共 {steps} 步")
        for idx, pose in enumerate(segmented_poses, start=1):
            print(f"  step {idx:03d}/{steps}: {pose}")
        if steps > 1:
            print("[INFO] 分步模式：每一步输入一个空格后回车执行，输入 q 回车可中止。")

        aborted = False
        for idx, pose in enumerate(segmented_poses, start=1):
            if steps > 1:
                while True:
                    command = input(f"[WAIT] step {idx:03d}/{steps}，输入空格执行（q 退出）: ")
                    if command.lower() == "q":
                        aborted = True
                        break
                    if command == " ":
                        break
                    print("[WARN] 请输入单个空格后回车，或输入 q 回车退出。")

                if aborted:
                    print("[INFO] 已按用户请求中止后续步骤。")
                    break

            ret = arm.MoveCart(
                desc_pos=pose,
                tool=MOVE_CART_TOOL,
                user=MOVE_CART_USER,
                vel=float(MOVE_CART_VEL),
                acc=MOVE_CART_ACC,
                ovl=float(MOVE_CART_OVL),
                blendT=MOVE_CART_BLEND_T,
                config=-1,
            )
            if ret != 0:
                raise RuntimeError(f"MoveCart 第 {idx}/{steps} 步失败, ret={ret}")
            print(f"[OK] step {idx:03d}/{steps}")

        if not aborted:
            print("[OK] 分段插补执行完成。")

    finally:
        disconnect_fairino(arm)


if __name__ == "__main__":
    main()
