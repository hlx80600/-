# =============================================================================
# PLC 小工具（代替 Codesys 里麻烦的 Ton）
#
# 延时对照：
#   Codesys:  Ton[1](IN:=TRUE, PT:=T#500ms);  IF Ton[1].Q THEN ...
#   本项目:   delay_start(gvl, "名字", 0.5);   if delay_done(gvl, "名字"): ...
#
# 发令对照：
#   PLC 里常在步 10 置位 GoAbsPos，到位后清掉，防止一直置位。
#   pulse_cmd：某一步里第一次调用返回 True（可以发 Move），之后返回 False。
# =============================================================================

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.gvl import GVL


def delay_start(gvl: GVL, name: str, seconds: float) -> None:
    """开始计时（相当于 Ton.IN:=TRUE，并设定 PT）。"""
    gvl._delay_until[name] = time.monotonic() + float(seconds)


def delay_done(gvl: GVL, name: str) -> bool:
    """时间到了吗？（相当于 Ton.Q）"""
    t = gvl._delay_until.get(name)
    if t is None:
        return True
    return time.monotonic() >= t


def delay_reset(gvl: GVL, name: str) -> None:
    """清掉延时。"""
    gvl._delay_until.pop(name, None)


def pulse_cmd(gvl: GVL, name: str) -> bool:
    """
    本步只发一次命令。
    返回 True  → 可以执行 open()/move_j() 等
    返回 False → 已经发过了，本步后面周期只做“等待完成”
    """
    if gvl._cmd_latch.get(name):
        return False
    gvl._cmd_latch[name] = True
    return True


def cmd_reset(gvl: GVL, name: str) -> None:
    """步号跳走前清掉发令标志。"""
    gvl._cmd_latch.pop(name, None)


def cmd_reset_prefix(gvl: GVL, prefix: str) -> None:
    """清掉某一类发令（例如整个 Auto_A10）。"""
    for k in list(gvl._cmd_latch.keys()):
        if k.startswith(prefix):
            del gvl._cmd_latch[k]


def recover_stuck_move_cmd(gvl: GVL, name: str, robot) -> None:
    """
    运动步卡死自愈：锁存已置位，但机器人并不在运动 → 清锁存，下拍可再发 Move。

    ★ 必须写在「poll_move_done 且跳步」判断之后。
    典型场景：真机 MoveL 失败后 halt，锁存仍 True；即使后来开了 Mock，
    pulse_cmd 也不再触发，会永远停在步30。
    """
    if not gvl._cmd_latch.get(name):
        return
    if getattr(robot, "is_move_pending", lambda: False)():
        return
    gvl._cmd_latch.pop(name, None)


def sync_mem(ctx, idx: int, val: bool) -> None:
    """
    写记忆位。
    Codesys: GVL_Memory.Memory_BOOL[i] := TRUE;
    这里同时写 GVL 和 HMI 显示用的 memory 表。
    """
    ctx.gvl.Memory_BOOL[idx] = bool(val)
    ctx.memory[idx] = bool(val)


def advance_step(st, single: bool) -> bool:
    """
    是否允许跳到下一步号。
    - 自动模式：条件满足就跳（返回 True）
    - 单步模式：还要等 HMI 点「下一步」（StepPulse）
    """
    if not single:
        return True
    if st.StepPulse:
        st.StepPulse = False
        return True
    return False
