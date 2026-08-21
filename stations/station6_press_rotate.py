# =============================================================================
# Station6 —— 先压鞋(按放料槽号控压杆/底座) → 再旋转 → 清记忆
# CASE Auto_A[10] OF  10/20/30/40/50
#
# 四槽：左口=放料、右口=取料；压合打到当前放料槽号 slots[N]；
#       完成看该槽状态 / press_done。Mem10=1 只转不压。
#       旋转到位后按 HMI 所选顺序(12341/43214)自行推进槽号。
# =============================================================================

from __future__ import annotations

from core.plc_util import advance_step, cmd_reset, delay_done, delay_start, pulse_cmd, sync_mem


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
        and (not M[6])
        and ctx.press.press_done
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
            if pulse_cmd(gvl, "s6_10"):
                sync_mem(ctx, 7, False)
                sync_mem(ctx, 4, False)
                ctx.press.refresh_inputs()
                need_press = not bool(M[10])
                gvl._s6_need_press = need_press
                gvl._s6_place_slot = int(ctx.press.place_slot)
                gvl._s6_pick_slot = int(ctx.press.pick_slot)
                gvl._s6_slots_advanced = False
                if need_press:
                    ctx.press.set_start_press(True)
                    press_s = float(
                        ctx.cfg.get("press", {}).get("mock_auto_press_done_s", 0) or 0
                    )
                    if ctx.press.use_mock and press_s > 0:
                        delay_start(gvl, "s6_press", press_s)
            if advance_step(st, single):
                cmd_reset(gvl, "s6_10")
                A[10] = 20

        case 20:
            # 等「当前放料槽号」压合完成（slots[place_slot]）
            ctx.press.refresh_inputs()
            need_press = bool(getattr(gvl, "_s6_need_press", False))
            if need_press:
                press_s = float(
                    ctx.cfg.get("press", {}).get("mock_auto_press_done_s", 0) or 0
                )
                if ctx.press.use_mock and press_s > 0 and delay_done(gvl, "s6_press"):
                    ctx.press.simulate_press_done()
                place_ok = bool(ctx.press.press_done) or ctx.press.is_place_press_idle()
                if place_ok and advance_step(st, single):
                    A[10] = 30
            else:
                if advance_step(st, single):
                    A[10] = 30

        case 30:
            if pulse_cmd(gvl, "s6_30"):
                ctx.press.set_start_press(False)
                ctx.press.clear_place_press_cmds()
                ctx.press.set_rotate(True)
                auto_s = float(
                    ctx.cfg.get("press", {}).get("mock_auto_rotate_done_s", 0) or 0
                )
                if ctx.press.use_mock and auto_s > 0:
                    delay_start(gvl, "s6_rot", auto_s)
            if advance_step(st, single):
                cmd_reset(gvl, "s6_30")
                A[10] = 40

        case 40:
            ctx.press.refresh_inputs()
            auto_s = float(
                ctx.cfg.get("press", {}).get("mock_auto_rotate_done_s", 0) or 0
            )
            if ctx.press.use_mock and auto_s > 0 and delay_done(gvl, "s6_rot"):
                # 槽号推进放到 case50，避免与 advance 重复
                ctx.press.simulate_rotate_done(advance_slots=False)
            if ctx.press.rotate_done and advance_step(st, single):
                A[10] = 50

        case 50:
            if pulse_cmd(gvl, "s6_50"):
                ctx.press.set_rotate(False)
                ctx.press.set_start_press(False)
                ctx.press.clear_place_press_cmds()
                if not getattr(gvl, "_s6_slots_advanced", False):
                    ctx.press.advance_slots_after_rotate()
                    gvl._s6_slots_advanced = True
                sync_mem(ctx, 10, False)
                sync_mem(ctx, 3, False)
                sync_mem(ctx, 7, False)
                gvl._s6_need_press = False
            if advance_step(st, single):
                cmd_reset(gvl, "s6_50")
                A[10] = 0

        case _:
            pass
