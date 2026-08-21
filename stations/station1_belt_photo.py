# =============================================================================
# Station1 —— 皮带上料拍照
# CASE Auto_A[10] OF  10/20/30
#
# 输出 PickPose + BeltPickSnapshot（含每只鞋自己的 toe_offset / shoe_length_mm）
# =============================================================================

from __future__ import annotations

import logging

from core.plc_util import advance_step, cmd_reset, delay_done, delay_start, pulse_cmd, sync_mem
from devices.pose_utils import is_left_shoe_flag

log = logging.getLogger(__name__)


def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[1]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()

    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0
        return

    if not gvl.Main.DebugBypass:
        di = int(ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        belt = ctx.robot1.get_di(di)
        if (
            belt
            and (not M[1])
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
            if pulse_cmd(gvl, "s1_10"):
                r = ctx.vision.photo_belt_pick(
                    default_z=float(gvl.PickPose.get("z", 120)),
                    default_rx=float(gvl.PickPose.get("rx", 180)),
                    default_ry=float(gvl.PickPose.get("ry", 0)),
                )
                gvl._last_belt_result = r
                if not r.ok:
                    gvl._photo_retries["s1"] = gvl._photo_retries.get("s1", 0) + 1
                    delay_start(gvl, "s1_retry", float(ctx.cfg["vision"].get("photo_retry_interval_s", 1.0)))
                    cmd_reset(gvl, "s1_10")
            r = gvl._last_belt_result
            if r and r.ok:
                gvl._photo_retries["s1"] = 0
                if advance_step(st, single):
                    cmd_reset(gvl, "s1_10")
                    A[10] = 20
            elif delay_done(gvl, "s1_retry"):
                max_r = int(ctx.cfg["vision"].get("photo_retry", 3))
                if gvl._photo_retries.get("s1", 0) >= max_r:
                    ctx.raise_alarm("VISION1", "皮带拍照失败超过重试次数", "Station1", 10)
                    gvl._photo_retries["s1"] = 0
                else:
                    cmd_reset(gvl, "s1_10")

        case 20:
            # 仅在跳到下一步时写入，避免单步停在本步时反复 commit 交替相位
            if advance_step(st, single):
                r = gvl._last_belt_result
                if r and r.ok:
                    is_left = is_left_shoe_flag(r.is_left_shoe)
                    toe_raw = getattr(r, "toe_offset_in_grasp_tcp", None)
                    if isinstance(toe_raw, (list, tuple)) and len(toe_raw) >= 3:
                        toe_off = [float(toe_raw[0]), float(toe_raw[1]), float(toe_raw[2])]
                    else:
                        # 真机应由 cam1 测长；未给时 Mock 才用 yaml 默认
                        vcfg = ctx.cfg.get("vision") or {}
                        mock = vcfg.get("belt_pick_mock") or {}
                        d = mock.get("toe_offset_in_grasp_tcp") or [0.0, 120.0, 0.0]
                        toe_off = [float(d[0]), float(d[1]), float(d[2])]
                    length_mm = float(getattr(r, "shoe_length_mm", 0.0) or 0.0)
                    if length_mm <= 1e-6:
                        length_mm = (toe_off[0] ** 2 + toe_off[1] ** 2) ** 0.5
                    snap = {
                        "x": float(r.x),
                        "y": float(r.y),
                        "z": float(r.z),
                        "rx": float(getattr(r, "rx", gvl.PickPose.get("rx", 180))),
                        "ry": float(getattr(r, "ry", gvl.PickPose.get("ry", 0))),
                        "rz": float(r.rz),
                        "is_left_shoe": is_left,
                        "source": str(getattr(r, "source", "")),
                        "message": str(getattr(r, "message", "")),
                        # 抓取中心→鞋头（抓取TCP系 mm）；每只鞋不同，Station2 据此改工具TCP
                        "toe_offset_in_grasp_tcp": toe_off,
                        "shoe_length_mm": length_mm,
                    }
                    gvl.PickPose.update(
                        {
                            "x": snap["x"],
                            "y": snap["y"],
                            "z": snap["z"],
                            "rx": snap["rx"],
                            "ry": snap["ry"],
                            "rz": snap["rz"],
                            "is_left_shoe": is_left,
                        }
                    )
                    # 锁定本拍结果，Station2 取料全程只认这份，不被后续拍照/HMI 改写
                    gvl.BeltPickSnapshot = dict(snap)
                    ctx.runtime_pick.update(gvl.PickPose)
                    # 无论是否识别为 shield_mock，cam1 Mock 拍照成功后都推进轮询
                    if snap["source"] == "shield_mock" or ctx.vision.cam_is_mock("cam1"):
                        ctx.vision.commit_belt_mock_advance()
                    log.info(
                        "Station1: 确认取料位 %s X=%.1f Y=%.1f Z=%.1f 鞋长=%.1fmm 鞋头偏移=%s（%s）",
                        "左鞋" if is_left else "右鞋",
                        snap["x"],
                        snap["y"],
                        snap["z"],
                        length_mm,
                        toe_off,
                        snap.get("message", ""),
                    )
                A[10] = 30

        case 30:
            sync_mem(ctx, 1, True)
            if advance_step(st, single):
                A[10] = 0

        case _:
            pass
