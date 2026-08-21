# =============================================================================
# Station5 —— 下料机器人（robot2）
#
# Auto_A[10] 鞋槽取料（slot_pick 示教点 + 旧视觉压杆 XY 毫米偏移）：
#   张爪 → 进入点 slot_pick_entry → 取料上方 → slot_pick → 抬起 → 退回进入点
# Auto_A[20] 下料皮带放料（belt_place 亦为固定示教点）：
#   进入点 belt_place_entry → 放料上方 → belt_place → 张爪 → 抬起 → 退回进入点
# Station4 只判断槽内有无料(Mem[6])，不改 robot2 取料坐标。
# =============================================================================

from __future__ import annotations

from core.plc_util import (
    advance_step,
    cmd_reset,
    cmd_reset_prefix,
    pulse_cmd,
    recover_stuck_move_cmd,
    sync_mem,
)
from devices.pose_utils import apply_offset, numeric_pose


def _slot_pick_pose(ctx) -> dict:
    """示教取料点 + 旧视觉压杆 XY 毫米偏移（测不到则原样）。"""
    pose = numeric_pose(ctx.pose("robot2", "slot_pick"))
    off = getattr(ctx.vision, "last_pick_xy_offset_mm", None)
    if isinstance(off, (list, tuple)) and len(off) >= 2:
        pose["x"] = float(pose.get("x") or 0.0) + float(off[0])
        pose["y"] = float(pose.get("y") or 0.0) + float(off[1])
    return pose


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[5]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        A[20] = 0
        # 锁存名为 s5a10_10…，须用前缀 "s5"（勿写 "s5_"）
        cmd_reset_prefix(gvl, "s5")
        return

    if (not gvl.Main.DebugBypass) and (not st.Busy) and gvl.Main.Running and (not gvl.Main.Paused):
        if (
            ctx.press.rotate_done
            and ctx.press.press_done
            and ctx.press.pick_ready  # 读 slots[当前取料槽号] 工作完成/可取
            and M[6]
            and (not M[5])
            and A[10] == 0
            and A[20] == 0
        ):
            A[10] = 10
        elif M[5] and A[10] == 0 and A[20] == 0:
            A[20] = 10

    if gvl.Main.Paused:
        return

    # CASE Auto_A[10] OF —— 鞋槽取料
    match A[10]:
        case 10:
            if pulse_cmd(gvl, "s5a10_10"):
                ctx.set_robot_holding_shoe("robot2", False)
                ctx.gripper2.open()
            if ctx.gripper2.poll_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_10")
                A[10] = 30

        case 20:
            if advance_step(st, single):
                A[10] = 30

        case 30:
            # 先进鞋槽取料进入点
            if pulse_cmd(gvl, "s5a10_30"):
                ctx.move_to_point("robot2", "slot_pick_entry", step_key="s5a10_30")
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_30")
                A[10] = 40
            else:
                recover_stuck_move_cmd(gvl, "s5a10_30", ctx.robot2)

        case 40:
            if pulse_cmd(gvl, "s5a10_40"):
                pick = _slot_pick_pose(ctx)
                above = apply_offset(
                    pick,
                    ctx.offset("robot2", "slot_pick_above_offset"),
                )
                # 与上料一致：上方点无示教关节，用 MoveL，避免到位模式下 MoveCart 拧腕
                ctx.robot2.move_l(
                    above,
                    label=f"鞋槽取料上方({ctx.named_point_tag('robot2', 'slot_pick')}+offset)",
                    from_label=ctx.named_point_tag("robot2", "slot_pick_entry"),
                    **ctx.step_motion_kwargs("s5a10_40"),
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_40")
                A[10] = 50
            else:
                recover_stuck_move_cmd(gvl, "s5a10_40", ctx.robot2)

        case 50:
            if pulse_cmd(gvl, "s5a10_50"):
                ctx.robot2.move_l(
                    _slot_pick_pose(ctx),
                    label=f"鞋槽取料({ctx.named_point_tag('robot2', 'slot_pick')}+视觉XY)",
                    precise=True,
                    **ctx.step_motion_kwargs("s5a10_50", precise=True),
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_50")
                A[10] = 60
            else:
                recover_stuck_move_cmd(gvl, "s5a10_50", ctx.robot2)

        case 60:
            if pulse_cmd(gvl, "s5a10_60"):
                ctx.gripper2.close()
            if ctx.gripper2.poll_done() and advance_step(st, single):
                ctx.set_robot_holding_shoe("robot2", True)
                cmd_reset(gvl, "s5a10_60")
                A[10] = 80

        case 70:
            if advance_step(st, single):
                A[10] = 80

        case 80:
            if pulse_cmd(gvl, "s5a10_80"):
                above = apply_offset(
                    _slot_pick_pose(ctx),
                    ctx.offset("robot2", "slot_pick_above_offset"),
                )
                ctx.robot2.move_l(
                    above,
                    label=f"鞋槽取料上方抬起({ctx.named_point_tag('robot2', 'slot_pick')}+offset)",
                    **ctx.step_motion_kwargs("s5a10_80"),
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_80")
                A[10] = 90
            else:
                recover_stuck_move_cmd(gvl, "s5a10_80", ctx.robot2)

        case 90:
            # 取料完成后退出至进入点
            if pulse_cmd(gvl, "s5a10_90"):
                ctx.move_to_point("robot2", "slot_pick_entry", step_key="s5a10_90")
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a10_90")
                A[10] = 100
            else:
                recover_stuck_move_cmd(gvl, "s5a10_90", ctx.robot2)

        case 100:
            sync_mem(ctx, 5, True)
            sync_mem(ctx, 6, False)
            if advance_step(st, single):
                A[10] = 0
                cmd_reset_prefix(gvl, "s5a10_")

        case _:
            pass

    # CASE Auto_A[20] OF —— 下料皮带放料
    match A[20]:
        case 10:
            # 先进下料皮带放料进入点
            if pulse_cmd(gvl, "s5a20_10"):
                ctx.move_to_point("robot2", "belt_place_entry", step_key="s5a20_10")
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a20_10")
                A[20] = 20
            else:
                recover_stuck_move_cmd(gvl, "s5a20_10", ctx.robot2)

        case 20:
            if pulse_cmd(gvl, "s5a20_20"):
                above = apply_offset(
                    ctx.pose("robot2", "belt_place"),
                    ctx.offset("robot2", "belt_place_above_offset"),
                )
                ctx.robot2.move_l(
                    above,
                    label=f"下料皮带放料上方({ctx.named_point_tag('robot2', 'belt_place')}+offset)",
                    from_label=ctx.named_point_tag("robot2", "belt_place_entry"),
                    **ctx.step_motion_kwargs("s5a20_20"),
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a20_20")
                A[20] = 30
            else:
                recover_stuck_move_cmd(gvl, "s5a20_20", ctx.robot2)

        case 30:
            if pulse_cmd(gvl, "s5a20_30"):
                ctx.move_to_point(
                    "robot2", "belt_place", linear=True, precise=True, step_key="s5a20_30"
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a20_30")
                A[20] = 40
            else:
                recover_stuck_move_cmd(gvl, "s5a20_30", ctx.robot2)

        case 40:
            if pulse_cmd(gvl, "s5a20_40"):
                ctx.gripper2.open()
            if ctx.gripper2.poll_done() and advance_step(st, single):
                ctx.set_robot_holding_shoe("robot2", False)
                cmd_reset(gvl, "s5a20_40")
                A[20] = 60

        case 50:
            if advance_step(st, single):
                A[20] = 60

        case 60:
            if pulse_cmd(gvl, "s5a20_60"):
                above = apply_offset(
                    ctx.pose("robot2", "belt_place"),
                    ctx.offset("robot2", "belt_place_above_offset"),
                )
                ctx.robot2.move_l(
                    above,
                    label=f"下料皮带放料上方抬起({ctx.named_point_tag('robot2', 'belt_place')}+offset)",
                    **ctx.step_motion_kwargs("s5a20_60"),
                )
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a20_60")
                A[20] = 70
            else:
                recover_stuck_move_cmd(gvl, "s5a20_60", ctx.robot2)

        case 70:
            # 放料完成后退出至进入点
            if pulse_cmd(gvl, "s5a20_70"):
                ctx.move_to_point("robot2", "belt_place_entry", step_key="s5a20_70")
            if ctx.robot2.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s5a20_70")
                A[20] = 80
            else:
                recover_stuck_move_cmd(gvl, "s5a20_70", ctx.robot2)

        case 80:
            sync_mem(ctx, 5, False)
            if advance_step(st, single):
                A[20] = 0
                cmd_reset_prefix(gvl, "s5a20_")
                ctx.production.record_unload()

        case _:
            pass
