# =============================================================================
# Station6 —— 取料槽已拍完且无料、放料侧结束后，才写启动字（1=空转 2=启动）
#
# 进入：Mem3（放料侧本圈结束）且 Mem7=1、Mem6=0（已拍、槽内无料：空槽或已取走）。
# 启动 1/2 都会转盘，站内再等压机放鞋完成=1（放过或禁放时 Station3 已置1；Mem10 空转仍可进）。
# 取料槽工作完成是压机 PLC 给出的，本工位不写。
# =============================================================================

from __future__ import annotations

import logging

from core.plc_util import advance_step, cmd_reset, delay_done, delay_start, pulse_cmd, sync_mem

log = logging.getLogger(__name__)


def _robots_clear_of_slots(ctx) -> tuple[bool, bool]:
    """放鞋完成是否已下发。Mem10 未放料时不要求放鞋完成。取料侧由进入条件 Mem7/Mem6 保证。"""
    gvl = ctx.gvl
    m = gvl.Memory_BOOL
    shoe_ok = bool(m[10]) or ctx.press.cas_word("shoe_done") == 1
    pick_ok = bool(m[7]) and (not m[6])
    return shoe_ok, pick_ok


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[6]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        ctx.press.estop_outputs_off()
        return

    if (
        (not gvl.Main.DebugBypass)
        and M[3]
        and M[7]
        and (not M[6])
        and ctx.press.is_press_idle()
        and (not st.Busy)
        and gvl.Main.Running
        and (not gvl.Main.Paused)
        and A[10] == 0
    ):
        A[10] = 10

    if gvl.Main.Paused:
        return

    match A[10]:
        case 10:
            shoe_ok, pick_ok = _robots_clear_of_slots(ctx)
            if shoe_ok and pick_ok and advance_step(st, single):
                A[10] = 20

        case 20:
            shoe_ok, pick_ok = _robots_clear_of_slots(ctx)
            if not (shoe_ok and pick_ok):
                A[10] = 10
            else:
                if pulse_cmd(gvl, "s6_20"):
                    mode = 1 if bool(M[10]) else 2
                    gvl._s6_place_slot = int(ctx.press.place_slot)
                    gvl._s6_pick_slot = int(ctx.press.pick_slot)
                    gvl._s6_slots_advanced = False
                    idle_s = float(
                        ctx.cfg.get("press", {}).get("mock_auto_press_done_s", 2.0) or 2.0
                    ) + float(
                        ctx.cfg.get("press", {}).get("mock_auto_rotate_done_s", 1.5)
                        or 1.5
                    )
                    delay_start(gvl, "s6_busy", max(2.0, idle_s + 1.0))
                    ctx.press.set_press_run_mode(mode)
                    log.info(
                        "Station6: 放鞋完成=%s 取料侧结束=%s → 启动=%s（%s）",
                        ctx.press.cas_word("shoe_done"),
                        int(pick_ok),
                        mode,
                        "空转" if mode == 1 else "启动",
                    )
                if ctx.press.cas_word("start") in (1, 2) and advance_step(st, single):
                    cmd_reset(gvl, "s6_20")
                    A[10] = 30

        case 30:
            if (not ctx.press.is_press_idle()) or delay_done(gvl, "s6_busy"):
                if advance_step(st, single):
                    A[10] = 40

        case 40:
            ctx.press.refresh_inputs()
            if ctx.press.is_press_idle() and advance_step(st, single):
                A[10] = 50

        case 50:
            if pulse_cmd(gvl, "s6_50"):
                ctx.press.set_press_run_mode(0)
                ctx.press.set_shoe_placed(False)
                if not getattr(gvl, "_s6_slots_advanced", False):
                    ctx.press.advance_slots_after_rotate()
                    gvl._s6_slots_advanced = True
                sync_mem(ctx, 10, False)
                sync_mem(ctx, 3, False)
                sync_mem(ctx, 7, False)
                sync_mem(ctx, 4, False)
            if advance_step(st, single):
                cmd_reset(gvl, "s6_50")
                A[10] = 0

        case _:
            pass
