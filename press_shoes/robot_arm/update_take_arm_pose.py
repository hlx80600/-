from pathlib import Path
import sys
import yaml

from fairino_robot_arm_inherit import FairinoRobotArm


# 两种配置文件（自动识别所有 *_pose 和 *_pose_j 项）
ARM_CONFIGS = {
    "1": {
        "filename": "put_arm_config.yaml",
        "desc": "投鞋机械臂 (put_workflow_manager)",
    },
    "2": {
        "filename": "take_arm_config.yaml",
        "desc": "取鞋机械臂 (take_workflow_manager)",
    },
}


def _get_config_path(filename: str) -> Path:
    """定位 YAML 配置路径。"""
    base_dir = Path(__file__).resolve().parents[1]  # press_shoes 目录
    return base_dir / "config" / filename


def load_config(filename: str) -> dict:
    cfg_path = _get_config_path(filename)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}")
        sys.exit(1)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def save_config(filename: str, data: dict) -> None:
    cfg_path = _get_config_path(filename)
    with cfg_path.open("w", encoding="utf-8") as f:
        # 保留中文，按原顺序输出
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"已更新配置文件: {cfg_path}")


def choose_arm_and_pose():
    # 先选择要修改的机械臂/配置文件
    print("请选择要修改的机械臂配置:")
    for key, info in ARM_CONFIGS.items():
        print(f"  {key}. {info['desc']} ({info['filename']})")
    print("  q. 退出")

    while True:
        arm_choice = input("请输入机械臂编号: ").strip()
        if arm_choice.lower() == "q":
            print("已退出，不做任何修改。")
            sys.exit(0)
        if arm_choice in ARM_CONFIGS:
            break
        print("无效选项，请重新输入。")

    arm_cfg = ARM_CONFIGS[arm_choice]
    filename = arm_cfg["filename"]
    # 动态读取配置文件，自动识别所有 *_pose 和 *_pose_j 配对项
    cfg = load_config(filename)
    pose_options = build_pose_options(cfg)
    return filename, pose_options, arm_cfg["desc"]


def choose_pose(pose_options):
    """选择要更新的位姿对，返回 (pose_key, joint_key, desc) 或 None 表示退出。"""
    print("\n请选择要更新的位姿对 (TCP + 关节角度):")
    for key, (pose_key, joint_key, desc) in pose_options.items():
        print(f"  {key}. {desc} ({pose_key} / {joint_key})")
    print("  q. 返回/退出")

    while True:
        choice = input("请输入位姿编号: ").strip()
        if choice.lower() == "q":
            return None
        if choice in pose_options:
            pose_key, joint_key, desc = pose_options[choice]
            return pose_key, joint_key, desc
        print("无效选项，请重新输入。")


# 新增：自动识别所有 *_pose 和 *_pose_j 配对项，并生成编号和描述
def build_pose_options(cfg):
    """
    自动识别所有 *_pose 和 *_pose_j 配对项，顺序与YAML一致，生成编号、(pose_key, joint_key, desc)。
    无论内容是否为空都显示所有有配对的项。
    desc优先用YAML中的注释（如无则用key名），如需更好描述可在YAML中用注释或额外字段。
    """
    import re
    # 新正则：匹配所有 xxx 或 xxxN，且有对应 xxx_j 或 xxx_jN
    pose_keys = []
    joint_keys = set()
    for k in cfg.keys():
        # 匹配 xxx 或 xxxN
        m = re.match(r"^(.*?)(\d*)$", k)
        if m:
            base = m.group(1)
            num = m.group(2)
            joint_key = f"{base}_j{num}"
            if joint_key in cfg:
                pose_keys.append((k, joint_key))
                joint_keys.add(joint_key)
    # 保持YAML顺序
    options = []
    for pose_key, joint_key in pose_keys:
        desc = cfg.get(pose_key + '_desc', pose_key)
        options.append((pose_key, joint_key, desc))
    pose_options = {str(i+1): v for i, v in enumerate(options)}
    return pose_options


def get_current_pose_and_joint(arm: FairinoRobotArm):
    """从机械臂读取当前 TCP 位姿和关节角度。"""
    pose_ret = arm.GetActualTCPPose()
    joint_ret = arm.GetActualJointPosDegree()

    try:
        tcp_pose = list(map(float, pose_ret[1]))
        joint = list(map(float, joint_ret[1]))
    except Exception as exc:
        print(f"读取机械臂位姿/关节角度失败: {exc}")
        sys.exit(1)

    if len(tcp_pose) < 6:
        print(f"TCP 位姿长度不足 6: {tcp_pose}")
        sys.exit(1)
    if len(joint) < 6:
        print(f"关节角度长度不足 6: {joint}")
        sys.exit(1)

    return tcp_pose[:6], joint[:6]


def main():
    # 1. 选择要修改的配置
    filename, pose_options, arm_desc = choose_arm_and_pose()
    print(f"\n已选择机械臂配置: {arm_desc} ({filename})")

    # 2. 读取对应配置，获取机械臂 IP
    cfg = load_config(filename)
    arm_ip = cfg.get("arm_ip", "fake")
    if arm_ip == "fake":
        print("当前配置 arm_ip 为 'fake'，无法从真实机械臂读取位姿，请先在 YAML 中配置真实 IP。")
        sys.exit(1)

    # 3. 连接机械臂
    arm = FairinoRobotArm("示教机械臂", robot_ip=arm_ip)
    print("正在连接机械臂...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)

    # 4. 循环选择并更新多个位姿，直到用户选择退出
    while True:
        pose_choice = choose_pose(pose_options)
        if pose_choice is None:
            print("已退出本次示教，配置文件不再修改。")
            break

        pose_key, joint_key, desc = pose_choice
        input(f"\n请将机械臂移动到目标位置: {desc}，然后按回车采集一次位姿...")

        tcp_pose, joint = get_current_pose_and_joint(arm)

        print("\n采集到的 TCP 位姿: ")
        print(tcp_pose)
        print("采集到的关节角度: ")
        print(joint)

        confirm = input(
            f"\n确认将以上数值写入配置文件 {filename} 的配置项 {pose_key} / {joint_key} 吗？(y/N): "
        ).strip().lower()
        if confirm != "y":
            print("已取消写入，本次不修改配置。")
            continue

        # 写回 YAML（保留内存中的 cfg，重复更新同一文件）
        cfg[pose_key] = tcp_pose
        cfg[joint_key] = joint
        save_config(filename, cfg)
        print("该位姿已更新，可继续选择其他位姿或输入 q 退出。")


if __name__ == "__main__":
    main()
