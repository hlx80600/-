from pathlib import Path
import re
import sys
import yaml

from fairino_robot_arm_inherit import FairinoRobotArm


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


def choose_arm():
    print("请选择要控制的机械臂配置:")
    for key, info in ARM_CONFIGS.items():
        print(f"  {key}. {info['desc']} ({info['filename']})")
    print("  q. 退出")

    while True:
        choice = input("请输入机械臂编号: ").strip()
        if choice.lower() == "q":
            print("已退出，不做任何操作。")
            sys.exit(0)
        if choice in ARM_CONFIGS:
            return ARM_CONFIGS[choice]
        print("无效选项，请重新输入。")


def _is_6d_pose(value) -> bool:
    """判断value是否为有效的6D位姿（浮点数列表，长度>=6）。"""
    if not isinstance(value, list) or len(value) < 6:
        return False
    try:
        [float(v) for v in value[:6]]
        return True
    except (TypeError, ValueError):
        return False


def _is_name_route(value) -> bool:
    """判断value是否为位姿名称路由（非空字符串列表）。"""
    if not isinstance(value, list) or len(value) == 0:
        return False
    return all(isinstance(v, str) for v in value)


def _is_joint_key(key: str) -> bool:
    """判断是否为关节角字段（_j 或 _j+数字 结尾）。"""
    return bool(re.search(r"_j\d*$", key))


def validate_config_pairs(cfg: dict) -> None:
    """
    验证配置中所有位姿/路由的配对完整性，并打印报告。
    检查两类配对:
      1. 普通位姿 key <-> 关节角 key_j / key_jN
      2. move*** 路由 key <-> 关节角路由 key_j
    """
    print("\n=== 配置完整性检查 ===")
    pattern = re.compile(r"^(.*?)(\d*)$")
    checked = set()
    all_ok = True

    for key, val in cfg.items():
        # --- 普通位姿对 ---
        if _is_joint_key(key):
            continue  # 关节键本身跳过，由位姿键侧驱动
        if not _is_6d_pose(val):
            continue

        m = pattern.match(key)
        if not m:
            continue
        base, num = m.group(1), m.group(2)
        joint_key = f"{base}_j{num}"

        if (key, joint_key) in checked:
            continue
        checked.add((key, joint_key))

        pose_ok = _is_6d_pose(cfg.get(key))
        joint_ok = _is_6d_pose(cfg.get(joint_key))
        status = "✓" if (pose_ok and joint_ok) else "✗"
        if not (pose_ok and joint_ok):
            all_ok = False
        pose_mark = "有效" if pose_ok else "缺失/无效"
        joint_mark = "有效" if joint_ok else "缺失/无效"
        print(f"  {status} 位姿对: {key}({pose_mark}) <-> {joint_key}({joint_mark})")

    # --- move*** 路由配对 ---
    for key, val in cfg.items():
        if not key.startswith("move"):
            continue
        if _is_joint_key(key):
            continue
        if not _is_name_route(val):
            continue

        joint_key = f"{key}_j"
        route_ok = _is_name_route(val)
        joint_route_ok = _is_name_route(cfg.get(joint_key))
        status = "✓" if (route_ok and joint_route_ok) else "✗"
        if not (route_ok and joint_route_ok):
            all_ok = False
        joint_mark = "有效" if joint_route_ok else "缺失/无效"
        print(f"  {status} 路由对: {key}(有效) <-> {joint_key}({joint_mark})")

    print(f"{'=== 检查通过 ===' if all_ok else '=== 存在未配对项，请检查配置 ==='}\n")


def build_options(cfg: dict):
    """
    构建所有可用选项，统一编号。
    每条选项为 dict，含 idx / kind / key / desc:
      kind='pose': 普通6D位姿（跳过 _j 关节角键）
      kind='move': move*** 路由（跳过 _j 变体）
    """
    options = []
    idx = 1
    used_pose = set()
    pattern = re.compile(r"^(.*?)(\d*)$")

    # --- 普通位姿对（跳过所有 _j 键） ---
    for key in cfg.keys():
        if _is_joint_key(key):
            continue  # 不在菜单中显示 _j 关节角键
        m = pattern.match(key)
        if not m:
            continue
        base, num = m.group(1), m.group(2)
        joint_key = f"{base}_j{num}"
        if not _is_6d_pose(cfg.get(key)):
            continue
        if (key, joint_key) in used_pose:
            continue
        used_pose.add((key, joint_key))
        options.append({"idx": str(idx), "kind": "pose", "key": key, "joint_key": joint_key, "desc": key})
        idx += 1

    # --- move*** 样条路由（排除 _j 变体） ---
    for key, val in cfg.items():
        if not key.startswith("move"):
            continue
        if _is_joint_key(key):
            continue  # 跳过关节角路由版本
        if not _is_name_route(val):
            continue
        options.append({"idx": str(idx), "kind": "move", "key": key, "desc": key})
        idx += 1

    return options


def choose_option(options: list):
    print("\n请选择要执行的操作:")
    for opt in options:
        tag = "[样条路由]" if opt["kind"] == "move" else "[位姿]"
        print(f"  {opt['idx']}.{tag}{opt['desc']}")
    print("  q. 退出")

    while True:
        choice = input("请输入编号: ").strip()
        if choice.lower() == "q":
            return None
        for opt in options:
            if choice == opt["idx"]:
                return opt
        print("无效选项，请重新输入。")


def choose_motion_mode():
    """选择普通位姿的运动模式: 笛卡尔 MoveCart 或 关节 MoveJ。"""
    print("\n请选择位姿运动模式 (move*** 路由固定使用样条):")
    print("  1. 笛卡尔 MoveCart (按位姿 desc_pos)")
    print("  2. 关节 MoveJ (按关节 joint_pos)")
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


def resolve_move_points(cfg: dict, move_key: str) -> list:
    """将 move*** 键中的位姿名称列表解析为实际 6D 位姿列表，跳过无效项。"""
    points = []
    for name in cfg.get(move_key, []):
        val = cfg.get(name)
        if not _is_6d_pose(val):
            print(f"  警告: 位姿键 '{name}' 未配置或非有效6D位姿，已跳过")
            continue
        points.append([float(v) for v in val[:6]])
    return points


def main():
    # 1. 选择机械臂配置
    arm_cfg = choose_arm()
    filename = arm_cfg["filename"]
    arm_name = arm_cfg["arm_name"]
    print(f"\n已选择机械臂: {arm_cfg['desc']} ({filename})")

    # 2. 加载配置
    cfg = load_config(filename)
    arm_ip = cfg.get("arm_ip", "fake")
    tool = int(cfg.get("tool", 0))
    spline_speed = float(cfg.get("spline_speed", 100.0) or 100.0)

    if arm_ip == "fake":
        print("当前配置 arm_ip 为 'fake'，无法控制真实机械臂，请先在对应 YAML 中配置真实 IP。")
        sys.exit(1)

    # 3. 配置完整性验证（位姿/关节角配对检查）
    validate_config_pairs(cfg)

    # 4. 构建选项列表
    options = build_options(cfg)

    # 5. 选择普通位姿的运动模式（move*** 路由固定用样条）
    motion_mode = choose_motion_mode()

    # 6. 连接机械臂
    arm = FairinoRobotArm(arm_name, robot_ip=arm_ip)
    print("正在连接机械臂...")
    if not arm.ConnectRobotArm():
        print("机械臂连接失败，退出。")
        sys.exit(1)

    # 7. 循环选择并执行
    while True:
        opt = choose_option(options)
        if opt is None:
            print("已退出，程序结束。")
            break

        if opt["kind"] == "pose":
            # --- 普通位姿: MoveCart 或 MoveJ ---
            pose_key = opt["key"]
            joint_key = opt["joint_key"]
            pose = cfg.get(pose_key, [])
            if not _is_6d_pose(pose):
                print(f"配置项 {pose_key} 未配置或为空，无法移动。")
                continue
            target_pose = [float(v) for v in pose[:6]]

            joint_vals = None
            if motion_mode == "joint":
                joint = cfg.get(joint_key, [])
                if not _is_6d_pose(joint):
                    print(f"配置项 {joint_key} 未配置或为空，无法使用 MoveJ。")
                    continue
                joint_vals = [float(v) for v in joint[:6]]

            mode_label = "MoveCart" if motion_mode == "cart" else "MoveJ"
            input(f"\n即将以 {mode_label} 移动到 {pose_key}，按回车开始，Ctrl+C 可中断...")
            if motion_mode == "cart":
                ret = arm.MoveCart(desc_pos=target_pose, tool=tool, user=0, vel=50, ovl=100.0, blendT=-1.0)
            else:
                ret = arm.MoveJ(joint_pos=joint_vals, desc_pos=target_pose, tool=tool, user=0, vel=50, ovl=100.0, blendT=-1.0)
            if ret != 0:
                print(f"移动失败，返回值: {ret}")
            else:
                print("移动成功！")

        elif opt["kind"] == "move":
            # --- move*** 路由: RobotServoSpline ---
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
                max_ori_step_deg=0.1,
                tool=tool,
                user=0,
            )
            if ret != 0:
                print(f"样条运动失败，返回值: {ret}")
            else:
                print("样条运动成功！")


if __name__ == "__main__":
    main()
