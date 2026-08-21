#!/usr/bin/env python3
"""示教抓取相关位姿，并更新左右槽 YAML。

使用方式:
    python3 press_shoes/scripts/teach_gripper_refer_pose.py --ip 192.168.57.2

流程:
    1) 先选择要更新 left_slot.yaml 还是 right_slot.yaml
    2) 选择要示教 gripper_refer_pose、target_point，或沿 1->2 方向平移 target_point
    3) 连接机械臂（选项 3 不需要）
    4) 按所选模式采集 TCP 位姿或计算偏移，并写回槽配置
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shoe_seg.slot_config import SlotConfig
from press_shoes.robot_arm.fairino_robot_arm_inherit import FairinoRobotArm

DEFAULT_ARM_IP = "192.168.57.2"
DEFAULT_LEFT_SLOT_CONFIG = REPO_ROOT / "press_shoes" / "config" / "left_slot.yaml"
DEFAULT_RIGHT_SLOT_CONFIG = REPO_ROOT / "press_shoes" / "config" / "right_slot.yaml"
GRIPPER_REFER_POSE_COUNT = 2
TEACH_MODE_GRIPPER = "gripper_refer_pose"
TEACH_MODE_TARGET_POINT = "target_point"
TEACH_MODE_SHIFT_TARGET = "shift_target_point"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="示教抓取 TCP 位姿并写入左右槽配置 YAML")
    parser.add_argument("--ip", default=DEFAULT_ARM_IP, help=f"机械臂 IP (默认 {DEFAULT_ARM_IP})")
    parser.add_argument(
        "--slot-config",
        dest="slot_config",
        type=str,
        default=None,
        help="槽配置路径（默认启动后交互选择 left_slot.yaml / right_slot.yaml）",
    )
    return parser.parse_args()


def choose_slot_config_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path)

    options = {
        "1": ("left_slot", DEFAULT_LEFT_SLOT_CONFIG),
        "2": ("right_slot", DEFAULT_RIGHT_SLOT_CONFIG),
    }
    print("请选择要更新的槽配置:")
    print(f"  1) left_slot  -> {DEFAULT_LEFT_SLOT_CONFIG}")
    print(f"  2) right_slot -> {DEFAULT_RIGHT_SLOT_CONFIG}")

    while True:
        choice = input("请输入 1 或 2: ").strip()
        selected = options.get(choice)
        if selected is not None:
            slot_name, slot_path = selected
            print(f"已选择 {slot_name}: {slot_path}")
            return slot_path
        print("输入无效，请重新输入 1 或 2。")


def choose_teach_mode() -> str:
    print("请选择要示教的字段:")
    print("  1) 鞋槽夹爪位置 -> 固定采集 2 个抓取位姿")
    print("  2) 鞋头对齐目标点位置 -> 采集 1 个 TCP 位姿")
    print("  3) 沿 gripper_refer_pose 第1->第2点方向平移 target_point")

    while True:
        choice = input("请输入 1、2 或 3: ").strip()
        if choice == "1":
            print(f"已选择 {TEACH_MODE_GRIPPER}")
            return TEACH_MODE_GRIPPER
        if choice == "2":
            print(f"已选择 {TEACH_MODE_TARGET_POINT}")
            return TEACH_MODE_TARGET_POINT
        if choice == "3":
            print(f"已选择 {TEACH_MODE_SHIFT_TARGET}")
            return TEACH_MODE_SHIFT_TARGET
        print("输入无效，请重新输入 1、2 或 3。")


def get_tcp_pose6(arm: FairinoRobotArm) -> list[float]:
    ret = arm.GetActualTCPPose()
    if not isinstance(ret, tuple) or len(ret) < 2:
        raise RuntimeError(f"读取 TCP 位姿返回格式异常: {ret}")
    if int(ret[0]) != 0:
        raise RuntimeError(f"读取 TCP 位姿失败, ret={ret[0]}")

    pose = [float(v) for v in ret[1]]
    if len(pose) < 6:
        raise RuntimeError(f"TCP 位姿长度不足 6: {pose}")
    return [round(v, 6) for v in pose[:6]]


def teach_gripper_refer_pose(arm: FairinoRobotArm) -> list[list[float]]:
    print("\n采集说明:")
    print(f"  - gripper_refer_pose 固定采集 {GRIPPER_REFER_POSE_COUNT} 个点")
    print("  - 第 1 个点必须是后束起点")
    print("  - 第 2 个点必须是前束终点")
    print("  - 如果前 2 个点顺序反了，当前流程会把引导线方向用反")

    labels = [
        "第1个抓取位姿（后束起点）",
        "第2个抓取位姿（前束终点）",
    ]
    poses: list[list[float]] = []
    for label in labels:
        input(f"\n请将机械臂移动到{label}，然后按回车采集 ...")
        pose = get_tcp_pose6(arm)
        print(f"  已采集 {label}: {[round(v, 4) for v in pose]}")
        poses.append(pose)
    return poses


def teach_target_point_pose(arm: FairinoRobotArm) -> list[float]:
    print("\n采集说明:")
    print("  - 请将 TCP 移动到鞋头对齐目标点位置")
    print("  - 当前主流程只使用 target_point 的 XY")
    print("  - 脚本会把采集到的 XYZ 额外记录到 target_point_xyz 便于追溯")

    input("\n请将机械臂移动到 target_point 位置，然后按回车采集 ...")
    pose = get_tcp_pose6(arm)
    print(f"  已采集 target_point TCP pose: {[round(v, 4) for v in pose]}")
    return [round(v, 6) for v in pose[:3]]


def prompt_shift_distance_mm() -> float:
    while True:
        raw = input("请输入沿 gripper_refer_pose 第1->第2点方向的平移量 (mm，正数为正向): ").strip()
        try:
            return float(raw)
        except ValueError:
            print("输入无效，请输入数字。")


def shift_target_point_along_gripper(
    slot_config: SlotConfig,
    distance_mm: float,
) -> tuple[list[float], list[float], tuple[float, float]]:
    """沿 gripper_refer_pose[0]->[1] 的 XY 方向平移 target_point。"""
    limits = slot_config.gripper_xy_rz_limits()
    line_dir = np.asarray(limits["line_dir"], dtype=float)
    line_dir_xy = (float(line_dir[0]), float(line_dir[1]))

    old_xy = slot_config.target_point_xy()
    new_xy_arr = old_xy + float(distance_mm) * line_dir
    new_xy = [round(float(v), 6) for v in new_xy_arr.tolist()]

    old_xyz_raw = slot_config.extra_fields.get("target_point_xyz")
    if isinstance(old_xyz_raw, (list, tuple)) and len(old_xyz_raw) >= 3:
        new_xyz = [
            new_xy[0],
            new_xy[1],
            round(float(old_xyz_raw[2]), 6),
        ]
    else:
        new_xyz = [new_xy[0], new_xy[1], 0.0]

    return new_xy, new_xyz, line_dir_xy


def apply_shift_target_point(
    slot_config: SlotConfig,
    slot_config_path: Path,
    distance_mm: float,
) -> int:
    old_xy = slot_config.target_point_xy().tolist()
    old_xyz = slot_config.extra_fields.get("target_point_xyz")
    new_xy, new_xyz, line_dir_xy = shift_target_point_along_gripper(slot_config, distance_mm)

    print("\n平移说明:")
    print("  - 方向取自 gripper_refer_pose 第1点 -> 第2点")
    print(f"  - 单位方向向量 = [{line_dir_xy[0]:.6f}, {line_dir_xy[1]:.6f}]")
    print(f"  - 平移量 = {distance_mm} mm")
    print(f"\n将写入 {slot_config_path}:")
    print(f"  target_point: {old_xy} -> {new_xy}")
    if old_xyz is not None:
        print(f"  target_point_xyz: {old_xyz} -> {new_xyz}")
    else:
        print(f"  target_point_xyz: (新建) -> {new_xyz}")

    confirm = input("确认写入槽配置 YAML 吗？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消写入。")
        return 0

    slot_config.target_point = new_xy
    slot_config.extra_fields["target_point_xyz"] = new_xyz
    slot_config.save_yaml(slot_config_path)
    print(f"[OK] 已更新: {slot_config_path}")
    return 0


def main() -> int:
    args = parse_args()
    slot_config_path = choose_slot_config_path(args.slot_config)
    teach_mode = choose_teach_mode()

    if not slot_config_path.exists():
        raise FileNotFoundError(f"槽配置不存在: {slot_config_path}")

    slot_config = SlotConfig.load_yaml(slot_config_path)

    if teach_mode == TEACH_MODE_SHIFT_TARGET:
        distance_mm = prompt_shift_distance_mm()
        return apply_shift_target_point(slot_config, slot_config_path, distance_mm)

    arm = FairinoRobotArm("teach_gripper_refer_pose", robot_ip=args.ip)
    print(f"正在连接机械臂 {args.ip} ...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        return 1
    print(f"[OK] 已连接机械臂 @ {args.ip}")
    slot_config = SlotConfig.load_yaml(slot_config_path)

    if teach_mode == TEACH_MODE_GRIPPER:
        poses = teach_gripper_refer_pose(arm)
        print(f"\n将写入 {slot_config_path} 的 gripper_refer_pose:")
        for idx, pose in enumerate(poses, start=1):
            print(f"  {idx}: {pose}")

        confirm = input("确认写入槽配置 YAML 吗？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消写入。")
            return 0

        slot_config.gripper_refer_pose = poses
    else:
        target_point_xyz = teach_target_point_pose(arm)
        print(f"\n将写入 {slot_config_path}:")
        print(f"  target_point = {target_point_xyz[:2]}")
        print(f"  target_point_xyz = {target_point_xyz}")

        confirm = input("确认写入槽配置 YAML 吗？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消写入。")
            return 0

        slot_config.target_point = target_point_xyz[:2]
        slot_config.extra_fields["target_point_xyz"] = target_point_xyz

    slot_config.save_yaml(slot_config_path)

    print(f"[OK] 已更新: {slot_config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
