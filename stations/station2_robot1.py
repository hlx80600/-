# =============================================================================
# Station2 —— 上料机器人（请先读这个文件！）
#
# Codesys:
#   CASE Auto_A[10] OF
#     10: ...
#     20: ...
#   END_CASE
#
# Python（一样好认）:
#   match A[10]:
#     case 10: ...
#     case 20: ...
# =============================================================================

from __future__ import annotations

import logging

from core.plc_util import (
    advance_step,
    cmd_reset,
    cmd_reset_prefix,
    pulse_cmd,
    recover_stuck_move_cmd,
    sync_mem,
)
from devices.pose_utils import apply_offset, is_left_shoe_flag, numeric_pose
from devices.toe_tcp import (
    apply_holding_keep_grasp_tcp,
    apply_toe_tcp_after_grasp,
    restore_grasp_or_empty_tcp,
)
from stations.toe_place_assist import poll_heel_down, run_heel_down_rotate, run_toe_align_step

log = logging.getLogger(__name__)


def _belt_pick_pose(gvl) -> dict:
    """
    本拍皮带取料位：优先 Station1 锁定的 BeltPickSnapshot，
    避免取料过程中 PickPose 被改回左鞋导致「抬起上方点跑去左鞋」。
    """
    snap = getattr(gvl, "BeltPickSnapshot", None)
    if isinstance(snap, dict) and ("x" in snap or "y" in snap):
        out = numeric_pose(snap)
        out["is_left_shoe"] = is_left_shoe_flag(snap.get("is_left_shoe", True))
        return out
    out = numeric_pose(gvl.PickPose)
    out["is_left_shoe"] = is_left_shoe_flag(gvl.PickPose.get("is_left_shoe", True))
    return out


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[2]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    # ① Busy
    st.update_busy()

    # ② 停机清零
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        A[20] = 0
        # 锁存名为 s2a10_30 / s2a20_10…，须用前缀 "s2"（勿写 "s2_"，否则清不掉）
        cmd_reset_prefix(gvl, "s2")
        return

    # ③ 进入条件
    if (not gvl.Main.DebugBypass) and (not st.Busy) and gvl.Main.Running and (not gvl.Main.Paused):
        if M[1] and (not M[2]) and A[10] == 0 and A[20] == 0:
            A[10] = 10
        elif (
            M[2]
            and ctx.press.rotate_done
            and (not M[3])
            and (not M[10])  # 放料槽方向不匹配：禁止放料（仅转盘）
            and M[4]
            and A[10] == 0
            and A[20] == 0
        ):
            A[20] = 10

    # 方向不匹配时若已误入放料 Auto，立即中止
    if M[10] and A[20] != 0:
        log.warning("Station2: Mem10=1（放料不匹配），中止放料 Auto_A[20]=%s", A[20])
        A[20] = 0
        cmd_reset_prefix(gvl, "s2a20_")
        ctx.robot1.halt_motion()

    # ④ 暂停不跑 CASE
    if gvl.Main.Paused:
        return

    # =========================================================================
    # CASE Auto_A[10] OF   —— 皮带取料
    # 路径：张爪 → 进入点 pick_entry → 取料上方 → 取料 → 抬起 → 退回进入点
    # =========================================================================
    match A[10]:
        case 10:
            if pulse_cmd(gvl, "s2a10_10"):
                ctx.set_robot_holding_shoe("robot1", False)
                ctx.gripper1.open()
            # 等张开完成（不再固定延时 0.5s）
            if ctx.gripper1.poll_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_10")
                A[10] = 30

        case 20:
            # 兼容旧步号：已取消固定延时，直接进入进入点
            if advance_step(st, single):
                A[10] = 30

        case 30:
            # 先进皮带取料进入点（MoveJ），再去取料上方
            if pulse_cmd(gvl, "s2a10_30"):
                ctx.move_to_point("robot1", "pick_entry", step_key="s2a10_30")
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_30")
                A[10] = 40
            else:
                recover_stuck_move_cmd(gvl, "s2a10_30", ctx.robot1)

        case 40:
            if pulse_cmd(gvl, "s2a10_40"):
                pick = _belt_pick_pose(gvl)
                above = apply_offset(pick, ctx.offset("robot1", "pick_above_offset"))
                side = "左鞋" if pick.get("is_left_shoe", True) else "右鞋"
                # ★ 必须用 MoveL：上方点无示教关节。若用 MoveJ→MoveCart，
                #   在「上一段平滑、本段到位」时易从半路/异构型拧腕撞机。
                ctx.robot1.move_l(
                    above,
                    label=(
                        f"皮带取料上方({side} XY={pick['x']:.1f},{pick['y']:.1f}"
                        f"+{ctx.named_point_tag('robot1', 'pick_above_offset')})"
                    ),
                    from_label=ctx.named_point_tag("robot1", "pick_entry"),
                    **ctx.step_motion_kwargs("s2a10_40"),
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_40")
                A[10] = 50
            else:
                recover_stuck_move_cmd(gvl, "s2a10_40", ctx.robot1)

        case 50:
            if pulse_cmd(gvl, "s2a10_50"):
                pick = _belt_pick_pose(gvl)
                side = "左鞋" if pick.get("is_left_shoe", True) else "右鞋"
                ctx.robot1.move_l(
                    pick,
                    label=f"皮带取料点({side} X={pick['x']:.1f} Y={pick['y']:.1f})",
                    precise=True,
                    **ctx.step_motion_kwargs("s2a10_50", precise=True),
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_50")
                A[10] = 60
            else:
                recover_stuck_move_cmd(gvl, "s2a10_50", ctx.robot1)

        case 60:
            if pulse_cmd(gvl, "s2a10_60"):
                ctx.gripper1.close()
            # 等夹紧完成（不再固定延时 0.5s）
            if ctx.gripper1.poll_done() and advance_step(st, single):
                # 只切抓鞋负载，TCP 仍用抓取中心——与下降时同 TCP，抬起才不奇异
                grasp = apply_holding_keep_grasp_tcp(ctx, "robot1")
                log.info("Station2: 夹紧完成，保持抓取TCP=%s（退出取料区后再切鞋头）", grasp)
                cmd_reset(gvl, "s2a10_60")
                A[10] = 80

        case 70:
            # 兼容旧步号：已取消固定延时，直接抬起
            if advance_step(st, single):
                A[10] = 80

        case 80:
            if pulse_cmd(gvl, "s2a10_80"):
                # 抬起上方 = 本拍取料坐标 + pick_above_offset（须与取料点同一 XY、同一抓取TCP）
                pick = _belt_pick_pose(gvl)
                above = apply_offset(pick, ctx.offset("robot1", "pick_above_offset"))
                side = "左鞋" if pick.get("is_left_shoe", True) else "右鞋"
                ctx.robot1.move_l(
                    above,
                    label=(
                        f"皮带取料上方抬起({side} XY={pick['x']:.1f},{pick['y']:.1f}"
                        f"+Z偏移)"
                    ),
                    **ctx.step_motion_kwargs("s2a10_80"),
                )
                log.info(
                    "Station2: 抬起上方 %s 目标 XY=%.1f,%.1f Z=%.1f（取料点+offset，抓取TCP）",
                    side,
                    above["x"],
                    above["y"],
                    above["z"],
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_80")
                A[10] = 90
            else:
                recover_stuck_move_cmd(gvl, "s2a10_80", ctx.robot1)

        case 90:
            # 取料完成后退出至进入点 pick_entry —— 必须到位后再写记忆（禁止平滑提前放行）
            if pulse_cmd(gvl, "s2a10_90"):
                ctx.move_to_point(
                    "robot1", "pick_entry", step_key="s2a10_90", precise=True
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_90")
                A[10] = 100
            else:
                recover_stuck_move_cmd(gvl, "s2a10_90", ctx.robot1)

        case 100:
            # 仅在本步发令一次写记忆（已确认回到进入点）
            if pulse_cmd(gvl, "s2a10_100"):
                pick = _belt_pick_pose(gvl)
                is_left = is_left_shoe_flag(pick.get("is_left_shoe", True))
                # ★ 此处不切鞋头工具/TCP。示教点 pick_entry→place_entry 在工具1下，
                #   若先切工具2再 MoveJ，法奥常报 err=154 关节指令点错误。
                sync_mem(ctx, 1, False)
                sync_mem(ctx, 2, True)
                if is_left:
                    sync_mem(ctx, 8, True)
                    sync_mem(ctx, 9, False)
                else:
                    sync_mem(ctx, 9, True)
                    sync_mem(ctx, 8, False)
                gvl.PickPose["is_left_shoe"] = is_left
                log.info(
                    "Station2: 已回pick_entry，取料完成 Mem8/9 → %s (X=%.1f Y=%.1f)",
                    "左鞋" if is_left else "右鞋",
                    pick.get("x", 0),
                    pick.get("y", 0),
                )
            if advance_step(st, single):
                cmd_reset(gvl, "s2a10_100")
                A[10] = 0
                cmd_reset_prefix(gvl, "s2a10_")

        case _:
            pass

    # =========================================================================
    # CASE Auto_A[20] OF   —— 鞋槽放料（鞋头TCP对位 + 绕鞋头压跟）
    # 路径：进入点 → 上方 → 接近放料点(工具1) → 切鞋头TCP → 对位 → 压跟 → 张爪 → 上方 → 退出
    # =========================================================================
    match A[20]:
        case 10:
            # 过渡到放料进入点：必须仍用抓取工具号（与示教关节一致）
            if pulse_cmd(gvl, "s2a20_10"):
                ctx.move_to_point("robot1", "place_entry", step_key="s2a20_10")
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a20_10")
                A[20] = 20
            else:
                recover_stuck_move_cmd(gvl, "s2a20_10", ctx.robot1)

        case 20:
            if pulse_cmd(gvl, "s2a20_20"):
                above = apply_offset(
                    ctx.pose("robot1", "place_slot"),
                    ctx.offset("robot1", "place_above_offset"),
                )
                ctx.robot1.move_l(
                    above,
                    label=f"鞋槽放料上方({ctx.named_point_tag('robot1', 'place_slot')}+{ctx.named_point_tag('robot1', 'place_above_offset')})",
                    from_label=ctx.named_point_tag("robot1", "place_entry"),
                    **ctx.step_motion_kwargs("s2a20_20"),
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a20_20")
                A[20] = 30
            else:
                recover_stuck_move_cmd(gvl, "s2a20_20", ctx.robot1)

        case 30:
            # 接近放料示教点（仍工具1）；到位静止后再切鞋头 TCP
            if pulse_cmd(gvl, "s2a20_30"):
                ctx.move_to_point(
                    "robot1", "place_slot", linear=True, precise=True, step_key="s2a20_30"
                )
                gvl._toe_align_tries = 0
                gvl._toe_align_done = False
                gvl._heel_seg_poses = []
                gvl._heel_seg_i = 0
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                if not getattr(gvl, "ToeTcpActive", False):
                    toe = apply_toe_tcp_after_grasp(ctx, "robot1")
                    log.info(
                        "Station2: 已到放料点，鞋头TCP已记录=%s（运动工具=%s）",
                        toe,
                        ctx.robot1.tool,
                    )
                cmd_reset(gvl, "s2a20_30")
                # 默认不切运动工具号，无需步35同步；直接对位
                switch = bool(
                    (ctx.cfg.get("vision") or {}).get("toe_tcp_switch_motion_tool", False)
                )
                A[20] = 35 if switch else 40
            else:
                recover_stuck_move_cmd(gvl, "s2a20_30", ctx.robot1)

        case 35:
            # 仅 toe_tcp_switch_motion_tool=true 时进入：换工具后关节同步
            if pulse_cmd(gvl, "s2a20_35"):
                ctx.robot1.resync_after_tool_change(label="切鞋头后关节同步")
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a20_35")
                gvl._toe_align_tries = 0
                gvl._toe_align_done = False
                A[20] = 40
            else:
                recover_stuck_move_cmd(gvl, "s2a20_35", ctx.robot1)

        case 40:
            # 鞋头对位：边推进边改姿态，直到鞋头紧贴鞋槽
            side_left = bool(M[8])
            if pulse_cmd(gvl, "s2a20_40"):
                r = run_toe_align_step(ctx, side_left=side_left)
                gvl._last_toe_align = r
                if r.get("done"):
                    gvl._toe_align_done = True
                log.info("Station2: %s", r.get("message", "鞋头对位"))

            if gvl._toe_align_done:
                if advance_step(st, single):
                    cmd_reset(gvl, "s2a20_40")
                    A[20] = 45
            elif ctx.robot1.poll_move_done():
                # 本拍推进完成 → 再拍继续对位
                cmd_reset(gvl, "s2a20_40")
            else:
                recover_stuck_move_cmd(gvl, "s2a20_40", ctx.robot1)

        case 45:
            # 鞋头已贴槽：绕鞋头旋转压跟，使鞋底水平
            side_left = bool(M[8])
            if pulse_cmd(gvl, "s2a20_45"):
                r = run_heel_down_rotate(ctx, side_left=side_left)
                log.info("Station2: %s", r.get("message", "压跟开始"))
            r = poll_heel_down(ctx)
            # 无分段（姿态已近跳过）时 pulse 当拍即可进下一步
            if r.get("done") and advance_step(st, single):
                log.info("Station2: %s", r.get("message", "压跟完成"))
                cmd_reset(gvl, "s2a20_45")
                A[20] = 50
            elif not r.get("done"):
                recover_stuck_move_cmd(gvl, "s2a20_45", ctx.robot1)

        case 50:
            if pulse_cmd(gvl, "s2a20_50"):
                ctx.gripper1.open()
            # 等张开完成（不再固定延时）
            if ctx.gripper1.poll_done() and advance_step(st, single):
                restore_grasp_or_empty_tcp(ctx, "robot1")
                cmd_reset(gvl, "s2a20_50")
                A[20] = 70

        case 60:
            # 兼容旧步号：已取消固定延时
            if advance_step(st, single):
                A[20] = 70

        case 70:
            if pulse_cmd(gvl, "s2a20_70"):
                above = apply_offset(
                    ctx.pose("robot1", "place_slot"),
                    ctx.offset("robot1", "place_above_offset"),
                )
                ctx.robot1.move_l(
                    above,
                    label=f"鞋槽放料上方抬起({ctx.named_point_tag('robot1', 'place_slot')}+offset)",
                    **ctx.step_motion_kwargs("s2a20_70"),
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a20_70")
                A[20] = 80
            else:
                recover_stuck_move_cmd(gvl, "s2a20_70", ctx.robot1)

        case 80:
            # 放料完成后退出至进入点 place_entry —— 必须到位后再写记忆
            if pulse_cmd(gvl, "s2a20_80"):
                ctx.move_to_point(
                    "robot1", "place_entry", step_key="s2a20_80", precise=True
                )
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a20_80")
                A[20] = 90
            else:
                recover_stuck_move_cmd(gvl, "s2a20_80", ctx.robot1)

        case 90:
            # 放料完成：手爪空；槽侧有料待转；左右脚标志必须清掉，
            # 否则下一次 S3 若在异常/调试时仍读到旧 Mem8/9 会判错方向。
            if pulse_cmd(gvl, "s2a20_90"):
                sync_mem(ctx, 2, False)
                sync_mem(ctx, 3, True)
                sync_mem(ctx, 8, False)
                sync_mem(ctx, 9, False)
                log.info("Station2: 已回place_entry，放料完成 Mem2=0 Mem3=1 清Mem8/9")
            if advance_step(st, single):
                cmd_reset(gvl, "s2a20_90")
                A[20] = 0
                cmd_reset_prefix(gvl, "s2a20_")

        case _:
            pass
