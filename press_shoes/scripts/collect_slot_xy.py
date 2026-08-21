#!/usr/bin/env python3
"""采集鞋槽轮廓点 [x, y] 及槽底/槽顶 z 高度，写入槽配置 YAML。

功能说明：
    1. 连接 Fairino 机械臂，读取 TCP 位姿。
    2. 用户手动将机械臂 TCP 依次移到鞋槽弧形边缘各点，
       每按一次回车采集一个 [x, y]，存入 slot_xy_points。
       采集顺序应沿弧形连续排列（如从一端到另一端），
       至少采集 5 个点（--min 可调整）。
    3. xy 采集完成后，再分别采集槽底和槽顶位置的 z 值，
       存入 z_heights = [z_min, z_max]。
    4. 结果以增量方式写入槽配置文件（保留已有字段）。

输出字段：
    slot_xy_points : list[[x, y]]   — 鞋槽弧形边缘 xy 坐标（mm）
    z_heights      : list[float]    — [槽底 z, 槽顶 z]（mm）

使用方法：
    python press_shoes/scripts/collect_slot_xy.py --ip 192.168.57.2
    python press_shoes/scripts/collect_slot_xy.py --ip 192.168.57.2 --min 8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shoe_seg.slot_config import SlotConfig
from press_shoes.robot_arm.fairino_robot_arm_inherit import FairinoRobotArm

OUTPUT_PATH = Path("/home/casbotskill/ct/Casbot_Press_Shoes/press_shoes/config/slot.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 TCP [x, y] 写入槽配置 YAML")
    parser.add_argument("--ip", required=True, help="机械臂 IP")
    parser.add_argument("--min", type=int, default=5, help="最少采集点数（默认 5）")
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH), help="输出 YAML 路径")
    return parser.parse_args()


def get_tcp_xy(arm: FairinoRobotArm) -> list[float]:
    ret = arm.GetActualTCPPose()
    if not isinstance(ret, tuple) or len(ret) < 2 or int(ret[0]) != 0:
        raise RuntimeError(f"读取 TCP 位姿失败: {ret}")
    pose = [float(v) for v in ret[1]]
    if len(pose) < 2:
        raise RuntimeError(f"TCP 位姿长度不足: {pose}")
    return [round(pose[0], 4), round(pose[1], 4)]


def main() -> None:
    args = parse_args()
    min_points = max(5, int(args.min))

    arm = FairinoRobotArm("collect_slot_xy_arm", robot_ip=args.ip)
    print(f"正在连接机械臂 {args.ip} ...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)
    print(f"[OK] 已连接机械臂 @ {args.ip}")

    points: list[list[float]] = []
    print(f"\n请依次将机械臂移动到各个 slot 位置，按回车采集 [x, y]。")
    print(f"最少需要采集 {min_points} 个点。输入 q 结束采集（满足最少点数后）。\n")

    while True:
        prompt = f"[{len(points) + 1}] 按回车采集当前点"
        if len(points) >= min_points:
            prompt += "（或输入 q 结束）"
        prompt += ": "

        user_input = input(prompt).strip().lower()
        if user_input == "q":
            if len(points) < min_points:
                print(f"  还需采集至少 {min_points - len(points)} 个点，不能退出。")
                continue
            break

        try:
            xy = get_tcp_xy(arm)
        except RuntimeError as exc:
            print(f"  采集失败: {exc}，请重试。")
            continue

        points.append(xy)
        print(f"  已采集第 {len(points)} 个点: {xy}")

    print(f"\n共采集 {len(points)} 个点。")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    slot_config = SlotConfig.load_yaml_or_default(output_path)
    slot_config.slot_xy_points = points
    slot_config.save_yaml(output_path)
    print(f"[OK] 已写入: {output_path}")


if __name__ == "__main__":
    main()
