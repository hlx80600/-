# =============================================================================
# Station4 —— 取料鞋槽拍照
#
# Codesys:
#   CASE Auto_A[10] OF
#     10: ...
#     20: ...
#   END_CASE
#
# Python 3.10+ 写法（更像 CASE）:
#   match A[10]:
#     case 10: ...
#     case 20: ...
# =============================================================================

from __future__ import annotations

from core.plc_util import advance_step, cmd_reset, delay_done, delay_start, pulse_cmd, sync_mem


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[4]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    # Busy
    st.update_busy()

    # 停机清零
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        return

    # 进入条件 → Auto_A[10]:=10
    if (
        (not gvl.Main.DebugBypass)
        and ctx.press.rotate_done
        and (not M[7])
        and ctx.press.press_done
        and (not st.Busy)
        and gvl.Main.Running
        and (not gvl.Main.Paused)
        and A[10] == 0
    ):
        A[10] = 10

    # IF NOT Main.Paused THEN CASE ...
    if gvl.Main.Paused:
        return

    # CASE Auto_A[10] OF
    match A[10]:
        case 10:
            # 拍照
            if pulse_cmd(gvl, "s4_10"):
                r = ctx.vision.photo_pick_slot()
                gvl._last_pick_result = r
                if r.ok:
                    gvl._photo_retries["s4"] = 0
                    if advance_step(st, single):
                        cmd_reset(gvl, "s4_10")
                        A[10] = 20
                else:
                    gvl._photo_retries["s4"] = gvl._photo_retries.get("s4", 0) + 1
                    delay_start(gvl, "s4_retry", float(ctx.cfg["vision"].get("photo_retry_interval_s", 1.0)))
                    cmd_reset(gvl, "s4_10")
            elif delay_done(gvl, "s4_retry"):
                if gvl._photo_retries.get("s4", 0) >= int(ctx.cfg["vision"].get("photo_retry", 3)):
                    ctx.raise_alarm("VISION4", "取料槽拍照失败", "Station4", 10)
                    gvl._photo_retries["s4"] = 0
                else:
                    cmd_reset(gvl, "s4_10")

        case 20:
            # 写记忆并结束
            r = gvl._last_pick_result
            sync_mem(ctx, 7, True)
            sync_mem(ctx, 6, bool(r.has_material) if r else False)
            if advance_step(st, single):
                A[10] = 0

        case _:
            # 0 或其他：不做事
            pass
