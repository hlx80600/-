# =============================================================================
# 初始化 —— CASE Main.Init_Auto OF  10/20/30/40
# =============================================================================

from __future__ import annotations

import logging

from core.machine_state import MachineState
from core.plc_util import cmd_reset, pulse_cmd, recover_stuck_move_cmd

log = logging.getLogger(__name__)


def start_init(ctx) -> None:
    """HMI 点「初始化」时调用。"""
    gvl = ctx.gvl
    gvl.Main.Initializing = True
    gvl.Main.InitDone = False
    gvl.Main.Init_Auto = 10
    gvl.Main.InitStepPulse = False
    ctx.machine.set_state(MachineState.INITIALIZING)
    ctx.init_message = "初始化中..."


def cycle(ctx) -> None:
    gvl = ctx.gvl
    A = gvl.Main.Init_Auto
    single = gvl.Main.Mode == "SINGLE_STEP"

    if not gvl.Main.Initializing:
        return
    if gvl.Main.EStopped or gvl.Main.Stop:
        gvl.Main.Init_Auto = 0
        gvl.Main.Initializing = False
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
                    f"上料机器人未连接 {ctx.robot1.ip}（看启动日志「连接失败」）。"
                    "请确认 fairino SDK 可导入、Docker/控制器已开、能 ping 通后再初始化。",
                    "Init",
                    20,
                )
                ctx.machine.set_state(MachineState.IDLE)
                return
            if pulse_cmd(gvl, "init_20"):
                try:
                    ctx.move_to_point("robot1", "home", step_key="init_r1_home")
                except Exception as e:
                    cmd_reset(gvl, "init_20")
                    gvl.Main.Initializing = False
                    gvl.Main.Init_Auto = 0
                    ctx.raise_alarm("INIT", f"上料回home失败: {e}", "Init", 20)
                    ctx.machine.set_state(MachineState.IDLE)
                    return
            if ctx.robot1.poll_move_done() and can_advance():
                cmd_reset(gvl, "init_20")
                gvl.Main.Init_Auto = 30
            else:
                recover_stuck_move_cmd(gvl, "init_20", ctx.robot1)

        case 30:
            if pulse_cmd(gvl, "init_30"):
                ctx.move_to_point("robot2", "home", step_key="init_r2_home")
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
            except Exception as e:
                gvl.Main.Initializing = False
                gvl.Main.Init_Auto = 0
                ctx.raise_alarm("INIT", str(e), "Init", 40)
                ctx.machine.set_state(MachineState.IDLE)

        case _:
            pass
