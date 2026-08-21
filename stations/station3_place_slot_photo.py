# =============================================================================
# Station3 —— 放料鞋槽拍照
# CASE Auto_A[10] OF  10/20
# =============================================================================

from __future__ import annotations

import logging

from core.plc_util import advance_step, cmd_reset, delay_done, delay_start, pulse_cmd, sync_mem
from devices.pose_utils import is_left_shoe_flag

log = logging.getLogger(__name__)


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[3]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        return

    if (
        (not gvl.Main.DebugBypass)
        and ctx.press.rotate_done
        and (not M[4])
        and M[2]
        and (not st.Busy)
        and gvl.Main.Running
        and (not gvl.Main.Paused)
        and A[10] == 0
    ):
        A[10] = 10

    if gvl.Main.Paused:
        return

    # CASE Auto_A[10] OF
    match A[10]:
        case 10:
            if pulse_cmd(gvl, "s3_10"):
                r = ctx.vision.photo_place_slot()
                gvl._last_place_result = r
                if r.ok:
                    gvl._photo_retries["s3"] = 0
                    if advance_step(st, single):
                        cmd_reset(gvl, "s3_10")
                        A[10] = 20
                else:
                    gvl._photo_retries["s3"] = gvl._photo_retries.get("s3", 0) + 1
                    delay_start(gvl, "s3_retry", float(ctx.cfg["vision"].get("photo_retry_interval_s", 1.0)))
                    cmd_reset(gvl, "s3_10")
            elif delay_done(gvl, "s3_retry"):
                if gvl._photo_retries.get("s3", 0) >= int(ctx.cfg["vision"].get("photo_retry", 3)):
                    ctx.raise_alarm("VISION3", "放料槽拍照失败", "Station3", 10)
                    gvl._photo_retries["s3"] = 0
                else:
                    cmd_reset(gvl, "s3_10")

        case 20:
            r = gvl._last_place_result
            # 手中鞋方向：优先 Mem8/9；若记忆异常则回退 PickPose.is_left_shoe
            if bool(M[8]) and not bool(M[9]):
                want_left = True
            elif bool(M[9]) and not bool(M[8]):
                want_left = False
            else:
                snap = getattr(gvl, "BeltPickSnapshot", None) or gvl.PickPose
                want_left = is_left_shoe_flag(
                    (snap or {}).get("is_left_shoe", gvl.PickPose.get("is_left_shoe", True))
                )
                log.warning(
                    "Station3: Mem8/9 异常(M8=%s M9=%s)，按快照/PickPose.is_left_shoe=%s",
                    M[8],
                    M[9],
                    want_left,
                )
            slot_left = bool(r.is_left_slot) if r and r.is_left_slot is not None else True
            hand = "左鞋" if want_left else "右鞋"
            slot = "左鞋槽" if slot_left else "右鞋槽"

            # 明确规则：左鞋→左槽、右鞋→右槽，且空槽才可放料
            if r and (not r.has_material) and (slot_left == want_left):
                sync_mem(ctx, 3, False)
                sync_mem(ctx, 4, True)
                sync_mem(ctx, 10, False)
                decision = f"可放料：{hand}→{slot}（空槽且左右对应）"
                log.info("Station3: %s", decision)
            elif r and (not r.has_material) and (slot_left != want_left):
                sync_mem(ctx, 3, True)
                sync_mem(ctx, 4, True)
                sync_mem(ctx, 10, True)
                decision = (
                    f"禁止放料Mem10=1：{hand}不能放进{slot}（须左→左/右→右），只转不压"
                )
                log.warning("Station3: %s", decision)
            else:
                sync_mem(ctx, 3, True)
                sync_mem(ctx, 4, True)
                sync_mem(ctx, 10, False)
                why = "槽内有料" if (r and r.has_material) else "拍照结果无效"
                decision = f"禁止放料：{why}（手中{hand}/目标{slot}）"
                log.info("Station3: %s", decision)

            # 供 HMI 常显（避免只看 Mem 勾选错过判定瞬间）
            gvl._last_place_decision = decision
            gvl._last_place_mem10 = bool(ctx.memory[10])

            if advance_step(st, single):
                A[10] = 0

        case _:
            pass
