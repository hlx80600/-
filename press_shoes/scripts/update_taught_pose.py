from __future__ import annotations

import re
import sys
from numbers import Real
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


# 允许直接运行本脚本: python press_shoes/scripts/update_take_arm_pose.py
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


class _FlowSequence(list):
    pass


class _TaughtPoseDumper(yaml.SafeDumper):
    pass


def _represent_flow_sequence(dumper: yaml.SafeDumper, data: _FlowSequence) -> yaml.nodes.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(data), flow_style=True)


_TaughtPoseDumper.add_representer(_FlowSequence, _represent_flow_sequence)


def _wrap_inline_numeric_sequences(value: object) -> object:
    if isinstance(value, dict):
        return {key: _wrap_inline_numeric_sequences(item) for key, item in value.items()}
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        wrapped_items = [_wrap_inline_numeric_sequences(item) for item in value]
        if wrapped_items and all(isinstance(item, Real) for item in wrapped_items):
            return _FlowSequence(wrapped_items)
        return wrapped_items
    return value


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


def save_config(filename: str, data: Dict) -> None:
    cfg_path = _get_config_path(filename)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.dump(
            _wrap_inline_numeric_sequences(data),
            f,
            Dumper=_TaughtPoseDumper,
            allow_unicode=True,
            sort_keys=False,
            width=200,
        )
    print(f"已更新配置文件: {cfg_path}")


def _is_6d_list(value: object) -> bool:
    if not isinstance(value, list) or len(value) < 6:
        return False
    try:
        [float(v) for v in value[:6]]
        return True
    except (TypeError, ValueError):
        return False


def build_pose_options(cfg: Dict) -> Dict[str, Tuple[str, str, str]]:
    """自动识别所有 pose / pose_j 配对项并按 YAML 顺序编号。"""
    options: List[Tuple[str, str, str]] = []
    seen_pairs = set()
    pattern = re.compile(r"^(.*?)(\d*)$")

    for key in cfg.keys():
        match = pattern.match(key)
        if not match:
            continue

        base, num = match.group(1), match.group(2)
        joint_key = f"{base}_j{num}"
        if joint_key not in cfg:
            continue

        pair = (key, joint_key)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        desc = str(cfg.get(f"{key}_desc", key))
        options.append((key, joint_key, desc))

    return {str(i + 1): item for i, item in enumerate(options)}


def choose_arm() -> Dict[str, str]:
    print("请选择要修改的机械臂配置:")
    for key, info in ARM_CONFIGS.items():
        print(f"  {key}. {info['desc']} ({info['filename']})")
    print("  q. 退出")

    while True:
        choice = input("请输入机械臂编号: ").strip().lower()
        if choice == "q":
            print("已退出，不做任何修改。")
            sys.exit(0)
        if choice in ARM_CONFIGS:
            return ARM_CONFIGS[choice]
        print("无效选项，请重新输入。")


def choose_pose(pose_options: Dict[str, Tuple[str, str, str]]) -> Optional[Tuple[str, str, str]]:
    print("\n请选择要更新的位姿对 (TCP + 关节角度):")
    for key, (pose_key, joint_key, desc) in pose_options.items():
        print(f"  {key}. {desc} ({pose_key} / {joint_key})")
    print("  q. 返回/退出")

    while True:
        choice = input("请输入位姿编号: ").strip().lower()
        if choice == "q":
            return None
        if choice in pose_options:
            return pose_options[choice]
        print("无效选项，请重新输入。")


def get_current_pose_and_joint(arm: FairinoRobotArm) -> Tuple[List[float], List[float]]:
    pose_ret = arm.GetActualTCPPose()
    joint_ret = arm.GetActualJointPosDegree()

    try:
        tcp_pose = [float(v) for v in pose_ret[1]][:6]
        joint = [float(v) for v in joint_ret[1]][:6]
    except Exception as exc:
        print(f"读取机械臂位姿/关节角度失败: {exc}")
        sys.exit(1)

    if len(tcp_pose) < 6 or len(joint) < 6:
        print("读取数据长度不足 6，退出。")
        sys.exit(1)

    return tcp_pose, joint


def main() -> None:
    arm_cfg = choose_arm()
    filename = arm_cfg["filename"]
    print(f"\n已选择机械臂配置: {arm_cfg['desc']} ({filename})")

    cfg = load_config(filename)
    pose_options = build_pose_options(cfg)
    if not pose_options:
        print("未找到可更新的 pose / pose_j 配对项。")
        sys.exit(1)

    arm_ip = cfg.get("arm_ip", "fake")
    if arm_ip == "fake":
        print("当前配置 arm_ip 为 'fake'，无法从真实机械臂读取位姿，请先在 YAML 中配置真实 IP。")
        sys.exit(1)

    arm = FairinoRobotArm(arm_cfg["arm_name"], robot_ip=arm_ip)
    print("正在连接机械臂...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)

    while True:
        selected = choose_pose(pose_options)
        if selected is None:
            print("已退出本次示教，程序结束。")
            break

        pose_key, joint_key, desc = selected
        input(f"\n请将机械臂移动到目标位置: {desc}，然后按回车采集位姿...")

        tcp_pose, joint = get_current_pose_and_joint(arm)
        print("\n采集到的 TCP 位姿:")
        print([round(v, 4) for v in tcp_pose])
        print("采集到的关节角度:")
        print([round(v, 4) for v in joint])

        confirm = input(
            f"\n确认写入 {filename} 的 {pose_key} / {joint_key} 吗？(y/N): "
        ).strip().lower()
        if confirm != "y":
            print("已取消写入。")
            continue

        cfg[pose_key] = tcp_pose
        cfg[joint_key] = joint
        save_config(filename, cfg)


if __name__ == "__main__":
    main()
