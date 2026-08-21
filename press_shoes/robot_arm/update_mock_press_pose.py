"""
更新 mock_press.yaml 中的压机位姿脚本。
类似于 update_take_arm_pose.py，通过实时采集机械臂位姿来更新压机配置。
"""
from pathlib import Path
import sys
import yaml

from fairino_robot_arm_inherit import FairinoRobotArm


# 压机位姿配置项定义
PRESS_POSES = {
    "1": ("left_lever_down_pose", "左杆下压位"),
    "2": ("left_lever_up_pose", "左杆复位位"),
    "3": ("right_lever_down_pose", "右杆下压位"),
    "4": ("right_lever_up_pose", "右杆复位位"),
    "5": ("home_pose", "压机 home 位"),
}


def _get_config_path() -> Path:
    """定位 mock_press.yaml 配置路径。"""
    return Path(__file__).resolve().parent / "mock_press.yaml"


def load_config() -> dict:
    """加载 mock_press.yaml 配置。"""
    cfg_path = _get_config_path()
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}")
        sys.exit(1)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def save_config(data: dict) -> None:
    """保存 mock_press.yaml 配置。"""
    cfg_path = _get_config_path()
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"已更新配置文件: {cfg_path}")


def choose_pose():
    """选择要更新的位姿，返回 (pose_key, desc) 或 None 表示退出。"""
    print("\n请选择要更新的压机位姿:")
    for key, (pose_key, desc) in PRESS_POSES.items():
        print(f"  {key}. {desc} ({pose_key})")
    print("  q. 返回/退出")

    while True:
        choice = input("请输入位姿编号: ").strip()
        if choice.lower() == "q":
            return None
        if choice in PRESS_POSES:
            pose_key, desc = PRESS_POSES[choice]
            return pose_key, desc
        print("无效选项，请重新输入。")


def get_current_pose(arm: FairinoRobotArm):
    """从机械臂读取当前 TCP 位姿。"""
    pose_ret = arm.GetActualTCPPose()
    try:
        tcp_pose = list(map(float, pose_ret[1]))
    except Exception as exc:
        print(f"读取机械臂 TCP 位姿失败: {exc}")
        return None

    if len(tcp_pose) < 6:
        print(f"TCP 位姿长度不足 6: {tcp_pose}")
        return None

    return tcp_pose[:6]


def main():
    # 1. 读取配置，获取机械臂 IP
    cfg = load_config()
    arm_ip = cfg.get("arm_ip", "192.168.57.4")
    
    print(f"使用机械臂 IP: {arm_ip}")
    if arm_ip == "fake":
        print("当前 arm_ip 为 'fake'，无法从真实机械臂读取位姿，请修改 mock_press.yaml 中的 arm_ip。")
        sys.exit(1)

    # 2. 连接机械臂
    arm = FairinoRobotArm("右机械臂", robot_ip=arm_ip)
    print("正在连接机械臂...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)

    # 3. 循环选择并更新多个位姿，直到用户选择退出
    while True:
        pose_choice = choose_pose()
        if pose_choice is None:
            print("已退出本次示教，配置文件不再修改。")
            break

        pose_key, desc = pose_choice
        input(f"\n请将机械臂移动到目标位置: {desc}，然后按回车采集一次位姿...")

        tcp_pose = get_current_pose(arm)
        if tcp_pose is None:
            print("采集位姿失败，请重试。")
            continue

        print("\n采集到的 TCP 位姿 (x, y, z, rx, ry, rz):")
        print(tcp_pose)

        confirm = input(
            f"\n确认将以上数值写入配置文件 mock_press.yaml 的配置项 {pose_key} 吗？(y/N): "
        ).strip().lower()
        if confirm != "y":
            print("已取消写入，本次不修改配置。")
            continue

        # 写回 YAML（保留内存中的 cfg，重复更新同一文件）
        cfg[pose_key] = tcp_pose
        save_config(cfg)
        print("该位姿已更新，可继续选择其他位姿或输入 q 退出。")


if __name__ == "__main__":
    main()
