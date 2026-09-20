# =============================================================================
# 初始化 —— CASE Main.Init_Auto OF  10/20/30/40
# =============================================================================

from __future__ import annotations

import logging
from typing import Any

from core.machine_state import MachineState
from core.plc_util import cmd_reset, pulse_cmd, recover_stuck_move_cmd
from devices.pose_utils import (
    extract_joints,
    joints_max_abs_diff_deg,
    numeric_pose,
    pose_rpy_max_abs_diff_deg,
    pose_xyz_distance_mm,
)

log = logging.getLogger(__name__)

# 初始化前「在 home 附近」的缺省允许范围（HMI / yaml 可改）
DEFAULT_INIT_NEAR_HOME_MM = 80.0
DEFAULT_INIT_NEAR_HOME_DEG = 15.0
DEFAULT_INIT_VEL_PCT = 20.0
_NEAR_HOME_MM_MIN = 1.0
_NEAR_HOME_MM_MAX = 500.0
_NEAR_HOME_DEG_MIN = 0.5
_NEAR_HOME_DEG_MAX = 90.0
_INIT_VEL_MIN = 1.0
_INIT_VEL_MAX = 100.0

_ROBOT_KEYS: tuple[str, ...] = ("robot1", "robot2")


def read_init_near_home_limits(cfg: dict[str, Any] | None) -> tuple[float, float]:
    """读初始化到位允许范围：XYZ [mm]、关节或姿态 [°]。越界则夹到合法区间。"""
    raw = (cfg or {}).get("motion")
    motion = raw if isinstance(raw, dict) else {}
    try:
        mm = float(motion.get("init_near_home_mm", DEFAULT_INIT_NEAR_HOME_MM))
    except (TypeError, ValueError):
        mm = DEFAULT_INIT_NEAR_HOME_MM
    try:
        deg = float(motion.get("init_near_home_deg", DEFAULT_INIT_NEAR_HOME_DEG))
    except (TypeError, ValueError):
        deg = DEFAULT_INIT_NEAR_HOME_DEG
    mm = max(_NEAR_HOME_MM_MIN, min(_NEAR_HOME_MM_MAX, mm))
    deg = max(_NEAR_HOME_DEG_MIN, min(_NEAR_HOME_DEG_MAX, deg))
    return mm, deg


def read_init_vel_pct(cfg: dict[str, Any] | None) -> float:
    """读初始化回零速度 %。与 robots.*.vel 运行速度分开，越界则夹到 1~100。"""
    raw = (cfg or {}).get("motion")
    motion = raw if isinstance(raw, dict) else {}
    try:
        pct = float(motion.get("init_vel", DEFAULT_INIT_VEL_PCT))
    except (TypeError, ValueError):
        pct = DEFAULT_INIT_VEL_PCT
    return max(_INIT_VEL_MIN, min(_INIT_VEL_MAX, pct))


def apply_init_controller_speed(ctx: Any) -> None:
    """初始化运动用：只 SetSpeed，不改 yaml 运行速度。"""
    pct = read_init_vel_pct(getattr(ctx, "cfg", None))
    log.info("初始化：控制器速度 %.0f%%（不改运行速度）", pct)
    ctx.robot1.push_speed(pct)
    ctx.robot2.push_speed(pct)


def apply_run_controller_speed(ctx: Any) -> None:
    """把控制器速度恢复为当前运行速度 robots.*.vel。"""
    log.info(
        "恢复运行速度：上料 %.0f%% 下料 %.0f%%",
        float(ctx.robot1.vel),
        float(ctx.robot2.vel),
    )
    ctx.robot1.push_speed()
    ctx.robot2.push_speed()


def check_robot_near_home(ctx: Any, robot_key: str) -> str | None:
    """真机须在示教 home 附近；Mock 跳过。

    Args:
        ctx: AppContext。
        robot_key: ``robot1`` / ``robot2``。

    Returns:
        None 表示通过或跳过；否则为该臂的中文失败说明。
    """
    robot = ctx.robot1 if robot_key == "robot1" else ctx.robot2
    label = str(getattr(robot, "name", None) or robot_key)
    if bool(getattr(robot, "use_mock", True)):
        log.debug("%s 为 Mock，跳过初始化到位检查", label)
        return None

    mm_lim, deg_lim = read_init_near_home_limits(getattr(ctx, "cfg", None))
    pts = (getattr(ctx, "cfg", {}) or {}).get("points") or {}
    rpts = pts.get(robot_key) if isinstance(pts, dict) else None
    home_raw = rpts.get("home") if isinstance(rpts, dict) else None
    if not isinstance(home_raw, dict):
        return f"{label} 未配置初始位 home，无法校验到位。"

    home_pose = numeric_pose(home_raw)
    home_joints = extract_joints(home_raw)
    try:
        cur_pose = robot.get_actual_tcp_pose()
    except Exception as e:
        return f"{label} 读取当前位姿失败：{e}"

    xyz = pose_xyz_distance_mm(cur_pose, home_pose)
    parts: list[str] = [f"位置偏差 {xyz:.1f} mm（允许 {mm_lim:.0f} mm）"]
    ang_ok = True
    if home_joints is not None:
        try:
            cur_joints = robot.get_actual_joint_pos()
        except Exception as e:
            return f"{label} 读取当前关节角失败：{e}"
        jdiff = joints_max_abs_diff_deg(cur_joints, home_joints)
        parts.append(f"关节最大偏差 {jdiff:.1f}°（允许 {deg_lim:.1f}°）")
        ang_ok = jdiff <= deg_lim
    else:
        rpy = pose_rpy_max_abs_diff_deg(cur_pose, home_pose)
        parts.append(f"姿态最大偏差 {rpy:.1f}°（允许 {deg_lim:.1f}°）")
        ang_ok = rpy <= deg_lim

    if xyz <= mm_lim and ang_ok:
        return None
    return f"{label} 不在初始位附近：{'，'.join(parts)}"


def check_both_near_home(ctx: Any) -> str | None:
    """初始化前检查双臂。任一真机不在 home 附近则返回完整报警文案。

    Returns:
        None 表示可以继续初始化。
    """
    fails: list[str] = []
    for key in _ROBOT_KEYS:
        err = check_robot_near_home(ctx, key)
        if err:
            fails.append(err)
    if not fails:
        return None
    head = (
        "初始化已中止：机器人不在初始位（home）附近，未开始回零。"
        "请点动回到初始位后点「报警复位」，再重新「初始化」。"
    )
    return head + "\n" + "\n".join(fails)


def start_init(ctx) -> None:
    """HMI 点「初始化」时调用。"""
    gvl = ctx.gvl
    gvl.Main.Initializing = True
    gvl.Main.InitDone = False
    gvl.Main.Init_Auto = 10
    gvl.Main.InitStepPulse = False
    ctx.machine.set_state(MachineState.INITIALIZING)
    ctx.init_message = "初始化中..."
    try:
        ctx.press.clear_host_run_signals()
    except Exception as exc:
        log.warning("初始化清压机放鞋/启动失败: %s", exc)
    apply_init_controller_speed(ctx)


def cycle(ctx) -> None:
    gvl = ctx.gvl
    A = gvl.Main.Init_Auto
    single = gvl.Main.Mode == "SINGLE_STEP"

    if not gvl.Main.Initializing:
        return
    if gvl.Main.EStopped or gvl.Main.Stop:
        gvl.Main.Init_Auto = 0
        gvl.Main.Initializing = False
        apply_run_controller_speed(ctx)
        return

    def can_advance() -> bool:
        if not single:
            return True
        if gvl.Main.InitStepPulse:
            gvl.Main.InitStepPulse = False
            return True
        return False

    # CASE Init_Auto OF
    match A:
        case 10:
            if pulse_cmd(gvl, "init_10"):
                ctx.set_robot_holding_shoe("robot1", False, force=True)
                ctx.set_robot_holding_shoe("robot2", False, force=True)
                ctx.gripper1.close()
                ctx.gripper2.close()
            if ctx.gripper1.poll_done() and ctx.gripper2.poll_done() and can_advance():
                cmd_reset(gvl, "init_10")
                gvl.Main.Init_Auto = 20

        case 20:
            if not ctx.robot1.use_mock and not ctx.robot1.connected:
                gvl.Main.Initializing = False
                gvl.Main.Init_Auto = 0
                ctx.raise_alarm(
                    "INIT",
                    f"【{ctx.robot1.name}】未连接\n"
                    f"地址: {ctx.robot1.ip}\n"
                    f"原因: 看启动日志「连接失败」。请确认 fairino SDK、控制器远程、能 ping 通后再初始化。",
                    ctx.robot1.name,
                    20,
                )
                ctx.machine.set_state(MachineState.IDLE)
                apply_run_controller_speed(ctx)
                return
            if pulse_cmd(gvl, "init_20"):
                try:
                    ctx.move_to_point("robot1", "home", step_key="init_r1_home")
                except Exception as e:
                    cmd_reset(gvl, "init_20")
                    gvl.Main.Initializing = False
                    gvl.Main.Init_Auto = 0
                    ctx.raise_alarm(
                        "INIT",
                        f"【{ctx.robot1.name}】回初始位失败\n原因: {e}",
                        ctx.robot1.name,
                        20,
                    )
                    ctx.machine.set_state(MachineState.IDLE)
                    apply_run_controller_speed(ctx)
                    return
            if ctx.robot1.poll_move_done() and can_advance():
                cmd_reset(gvl, "init_20")
                gvl.Main.Init_Auto = 30
            else:
                recover_stuck_move_cmd(gvl, "init_20", ctx.robot1)

        case 30:
            if not ctx.robot2.use_mock and not ctx.robot2.connected:
                gvl.Main.Initializing = False
                gvl.Main.Init_Auto = 0
                ctx.raise_alarm(
                    "INIT",
                    f"【{ctx.robot2.name}】未连接\n"
                    f"地址: {ctx.robot2.ip}\n"
                    f"原因: 看启动日志「连接失败」。请确认 fairino SDK、控制器远程、能 ping 通后再初始化。",
                    ctx.robot2.name,
                    30,
                )
                ctx.machine.set_state(MachineState.IDLE)
                apply_run_controller_speed(ctx)
                return
            if pulse_cmd(gvl, "init_30"):
                try:
                    ctx.move_to_point("robot2", "home", step_key="init_r2_home")
                except Exception as e:
                    cmd_reset(gvl, "init_30")
                    gvl.Main.Initializing = False
                    gvl.Main.Init_Auto = 0
                    ctx.raise_alarm(
                        "INIT",
                        f"【{ctx.robot2.name}】回初始位失败\n原因: {e}",
                        ctx.robot2.name,
                        30,
                    )
                    ctx.machine.set_state(MachineState.IDLE)
                    apply_run_controller_speed(ctx)
                    return
            if ctx.robot2.poll_move_done() and can_advance():
                cmd_reset(gvl, "init_30")
                gvl.Main.Init_Auto = 40
            else:
                recover_stuck_move_cmd(gvl, "init_30", ctx.robot2)

        case 40:
            try:
                ctx.press.refresh_inputs()
                if not ctx.press.connected:
                    raise RuntimeError("压鞋机通信失败")
                if not ctx.press.power_ok:
                    raise RuntimeError("压鞋机未上电完成")
                gvl.Main.InitDone = True
                gvl.Main.Initializing = False
                gvl.Main.Init_Auto = 0
                ctx.machine.set_state(MachineState.READY)
                ctx.init_message = "初始化完成"
                log.info("初始化完成")
                apply_run_controller_speed(ctx)
            except Exception as e:
                gvl.Main.Initializing = False
                gvl.Main.Init_Auto = 0
                ctx.raise_alarm(
                    "PRESS",
                    f"【压鞋机】初始化失败\n原因: {e}",
                    "压鞋机",
                    40,
                )
                ctx.machine.set_state(MachineState.IDLE)
                apply_run_controller_speed(ctx)

        case _:
            pass
