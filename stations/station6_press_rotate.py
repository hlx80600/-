# =============================================================================
# Station6 —— 取料槽已拍完且无料、放料侧结束后，先发放鞋完成=1，再写启动（1=空转 2=启动）
#
# 进入：Mem3 且 Mem7=1、Mem6=0、空闲=1。
# 放鞋完成置 1 后须保持 500ms（可配 press.shoe_done_hold_s）才写启动/空转。
# 这段只卡本站写启动，其它工位照常跑。
# =============================================================================

from __future__ import annotations

import logging

from core.plc_util import advance_step, cmd_reset, delay_start, delay_done, pulse_cmd, sync_mem

log = logging.getLogger(__name__)

# 写启动后等空闲变 0 的超时（秒）；0=一直等，不超时
DEFAULT_IDLE_GO_BUSY_TIMEOUT_S = 0.0


def _pick_side_ok(ctx) -> bool:
    m = ctx.gvl.Memory_BOOL
    return bool(m[7]) and (not m[6])


def _idle_go_busy_timeout_s(ctx) -> float:
    """写启动后等空闲变 0；0 表示不超时。"""
    raw = (ctx.cfg.get("press") or {}).get(
        "idle_go_busy_timeout_s", DEFAULT_IDLE_GO_BUSY_TIMEOUT_S
    )
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_IDLE_GO_BUSY_TIMEOUT_S


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[6]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
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
            if not _pick_side_ok(ctx):
                return
            if pulse_cmd(gvl, "s6_10"):
                ctx.press.set_shoe_placed(True)
                log.info(
                    "Station6: 放鞋完成=1，%sms 后才允许启动/空转（其它工位继续）",
                    int(ctx.press.shoe_done_hold_s() * 1000),
                )
            if (
                ctx.press.can_issue_press_start()
                and advance_step(st, single)
            ):
                cmd_reset(gvl, "s6_10")
                A[10] = 20

        case 20:
            if ctx.press.cas_word("shoe_done") != 1 or (not _pick_side_ok(ctx)):
                A[10] = 10
            elif not ctx.press.can_issue_press_start():
                return
            else:
                if pulse_cmd(gvl, "s6_20"):
                    mode = 1 if bool(M[10]) else 2
                    gvl._s6_place_slot = int(ctx.press.place_slot)
                    gvl._s6_pick_slot = int(ctx.press.pick_slot)
                    gvl._s6_slots_advanced = False
                    busy_s = _idle_go_busy_timeout_s(ctx)
                    if busy_s > 0:
                        delay_start(gvl, "s6_busy", busy_s)
                    ctx.press.set_press_run_mode(mode)
                    log.info(
                        "Station6: 放鞋完成已保持 → 启动=%s（%s）等空闲变0超时=%.1fs",
                        mode,
                        "空转" if mode == 1 else "启动",
                        busy_s,
                    )
                if ctx.press.cas_word("start") in (1, 2) and advance_step(st, single):
                    cmd_reset(gvl, "s6_20")
                    A[10] = 30

        case 30:
            timed_out = False
            busy_s = _idle_go_busy_timeout_s(ctx)
            if busy_s > 0:
                timed_out = delay_done(gvl, "s6_busy")
            if (not ctx.press.is_press_idle()) or timed_out:
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
