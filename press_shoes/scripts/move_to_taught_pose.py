from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# 允许直接运行本脚本: python press_shoes/scripts/move_to_taught_pose.py
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from press_shoes.robot_arm.fairino_robot_arm_inherit import FairinoRobotArm


ARM_CONFIGS = {
    "1": {
        "filename": "put_arm_config.yaml",
        "desc": "投鞋机械臂 (put_workflow_manager)",
        "arm_name": "投鞋机械臂",
    },
    "2": {
        "filename": "take_arm_config.yaml",
        "desc": "取鞋机械臂 (take_workflow_manager)",
        "arm_name": "取鞋机械臂",
    },
}


def _get_config_path(filename: str) -> Path:
    press_shoes_dir = Path(__file__).resolve().parents[1]
    return press_shoes_dir / "config" / filename


def load_config(filename: str) -> Dict:
    cfg_path = _get_config_path(filename)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}")
        sys.exit(1)
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _is_6d_pose(value: object) -> bool:
    if not isinstance(value, list) or len(value) < 6:
        return False
    try:
        [float(v) for v in value[:6]]
        return True
    except (TypeError, ValueError):
        return False


def _is_name_route(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) for v in value)


def _is_joint_key(key: str) -> bool:
    return bool(re.search(r"_j\d*$", key))


def choose_arm() -> Dict[str, str]:
    print("请选择要控制的机械臂配置:")
    for key, info in ARM_CONFIGS.items():
        print(f"  {key}. {info['desc']} ({info['filename']})")
    print("  q. 退出")

    while True:
        choice = input("请输入机械臂编号: ").strip().lower()
        if choice == "q":
            print("已退出，不做任何操作。")
            sys.exit(0)
        if choice in ARM_CONFIGS:
            return ARM_CONFIGS[choice]
        print("无效选项，请重新输入。")


def validate_config_pairs(cfg: Dict) -> None:
    print("\n=== 配置完整性检查 ===")
    pattern = re.compile(r"^(.*?)(\d*)$")
    all_ok = True

    for key, val in cfg.items():
        if _is_joint_key(key):
            continue
        if not _is_6d_pose(val):
            continue

        match = pattern.match(key)
        if not match:
            continue

        base, num = match.group(1), match.group(2)
        joint_key = f"{base}_j{num}"
        pose_ok = _is_6d_pose(cfg.get(key))
        joint_ok = _is_6d_pose(cfg.get(joint_key))
        status = "✓" if (pose_ok and joint_ok) else "✗"
        if not (pose_ok and joint_ok):
            all_ok = False
        print(f"  {status} 位姿对: {key} <-> {joint_key}")

    for key, val in cfg.items():
        if not key.startswith("move") or _is_joint_key(key):
            continue
        if not _is_name_route(val):
            continue
        route_joint = f"{key}_j"
        route_joint_ok = _is_name_route(cfg.get(route_joint))
        status = "✓" if route_joint_ok else "✗"
        if not route_joint_ok:
            all_ok = False
        print(f"  {status} 路由对: {key} <-> {route_joint}")

    print("=== 检查通过 ===\n" if all_ok else "=== 存在未配对项，请检查配置 ===\n")


def build_options(cfg: Dict) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = []
    idx = 1
    pattern = re.compile(r"^(.*?)(\d*)$")
    seen_pairs = set()

    for key in cfg.keys():
        if _is_joint_key(key):
            continue
        match = pattern.match(key)
        if not match:
            continue
        base, num = match.group(1), match.group(2)
        joint_key = f"{base}_j{num}"
        if not _is_6d_pose(cfg.get(key)):
            continue
        pair = (key, joint_key)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        options.append({
            "idx": str(idx),
            "kind": "pose",
            "key": key,
            "joint_key": joint_key,
            "desc": key,
        })
        idx += 1

    for key, val in cfg.items():
        if not key.startswith("move") or _is_joint_key(key):
            continue
        if not _is_name_route(val):
            continue
        options.append({"idx": str(idx), "kind": "move", "key": key, "desc": key})
        idx += 1

    return options


def choose_option(options: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    print("\n请选择要执行的操作:")
    for opt in options:
        tag = "[样条路由]" if opt["kind"] == "move" else "[位姿]"
        print(f"  {opt['idx']}. {tag}{opt['desc']}")
    print("  q. 退出")

    while True:
        choice = input("请输入编号: ").strip().lower()
        if choice == "q":
            return None
        for opt in options:
            if choice == opt["idx"]:
                return opt
        print("无效选项，请重新输入。")


def choose_motion_mode() -> str:
    print("\n请选择位姿运动模式 (move*** 路由固定使用样条):")
    print("  1. 笛卡尔 MoveCart")
    print("  2. 关节 MoveJ")
    print("  q. 退出")

    while True:
        choice = input("请输入模式编号: ").strip().lower()
        if choice == "q":
            print("已退出，不做任何操作。")
            sys.exit(0)
        if choice == "1":
            return "cart"
        if choice == "2":
            return "joint"
        print("无效选项，请重新输入。")


def resolve_move_points(cfg: Dict, move_key: str) -> List[List[float]]:
    points: List[List[float]] = []
    for name in cfg.get(move_key, []):
        value = cfg.get(name)
        if not _is_6d_pose(value):
            print(f"  警告: 位姿键 '{name}' 未配置或非有效6D位姿，已跳过")
            continue
        points.append([float(v) for v in value[:6]])
    return points


def main() -> None:
    arm_cfg = choose_arm()
    filename = arm_cfg["filename"]
    arm_name = arm_cfg["arm_name"]
    print(f"\n已选择机械臂: {arm_cfg['desc']} ({filename})")

    cfg = load_config(filename)
    arm_ip = cfg.get("arm_ip", "fake")
    tool = int(cfg.get("tool", 0))
    spline_speed = float(cfg.get("spline_speed", 100.0) or 100.0)

    if arm_ip == "fake":
        print("当前配置 arm_ip 为 'fake'，无法控制真实机械臂，请先在 YAML 中配置真实 IP。")
        sys.exit(1)

    validate_config_pairs(cfg)
    options = build_options(cfg)
    if not options:
        print("未找到可执行位姿，请检查配置。")
        sys.exit(1)

    motion_mode = choose_motion_mode()

    arm = FairinoRobotArm(arm_name, robot_ip=arm_ip)
    print("正在连接机械臂...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)

    while True:
        opt = choose_option(options)
        if opt is None:
            print("已退出，程序结束。")
            break

        if opt["kind"] == "pose":
            pose_key = opt["key"]
            joint_key = opt["joint_key"]
            pose = cfg.get(pose_key, [])
            if not _is_6d_pose(pose):
                print(f"配置项 {pose_key} 未配置或无效，无法移动。")
                continue
            target_pose = [float(v) for v in pose[:6]]

            if motion_mode == "cart":
                input(f"\n即将 MoveCart 到 {pose_key}，按回车开始，Ctrl+C 可中断...")
                ret = arm.MoveCart(desc_pos=target_pose, tool=tool, user=0, vel=50, ovl=100.0, blendT=-1.0)
            else:
                joint = cfg.get(joint_key, [])
                if not _is_6d_pose(joint):
                    print(f"配置项 {joint_key} 未配置或无效，无法 MoveJ。")
                    continue
                target_joint = [float(v) for v in joint[:6]]
                input(f"\n即将 MoveJ 到 {pose_key}，按回车开始，Ctrl+C 可中断...")
                ret = arm.MoveJ(
                    joint_pos=target_joint,
                    desc_pos=target_pose,
                    tool=tool,
                    user=0,
                    vel=100,
                    ovl=100.0,
                    blendT=-1.0,
                )

            if ret != 0:
                print(f"移动失败，返回值: {ret}")
            else:
                print("移动成功！")
            continue

        move_key = opt["key"]
        points = resolve_move_points(cfg, move_key)
        if len(points) < 2:
            print(f"路由 {move_key} 有效位姿点不足 2 个（当前 {len(points)} 个），无法执行样条运动。")
            continue

        print(f"\n路由 {move_key}，共 {len(points)} 个位姿点，速度 {spline_speed} mm/s:")
        for i, p in enumerate(points):
            print(f"  [{i}] {[round(v, 3) for v in p]}")

        input("按回车开始 RobotServoSpline 样条运动，Ctrl+C 可中断...")
        ret = arm.RobotServoSpline(
            points=points,
            speed_mm_s=spline_speed,
            max_ori_step_deg=0.4,
            tool=tool,
            user=0,
        )
        if ret != 0:
            print(f"样条运动失败，返回值: {ret}")
        else:
            print("样条运动成功！")


if __name__ == "__main__":
    main()
