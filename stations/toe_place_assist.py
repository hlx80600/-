"""Station2 放料：鞋头对位推进 + 绕鞋头压跟（鞋底水平）。

无 Casbot 视觉伺服时：默认不对位发 MoveL（已在 place_slot），直接完成，避免 err=74。
压跟：仅改姿态 / 相对下压，不追 place_slot 绝对坐标。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from devices.pose_utils import numeric_pose

log = logging.getLogger(__name__)


def _pose6(robot) -> List[float]:
    p = numeric_pose(robot.get_actual_tcp_pose())
    return [p["x"], p["y"], p["z"], p["rx"], p["ry"], p["rz"]]


def _sync_robot_state(robot) -> None:
    try:
        robot.current_pose = numeric_pose(robot.get_actual_tcp_pose())
    except Exception:
        pass
    try:
        if hasattr(robot, "get_actual_joint_pos"):
            j = robot.get_actual_joint_pos()
            if j and len(j) == 6:
                robot.current_joints = [float(v) for v in j]
    except Exception:
        pass


def _orient_delta_deg(a: List[float], b: Dict[str, float]) -> float:
    return max(
        abs(a[3] - b["rx"]),
        abs(a[4] - b["ry"]),
        abs(a[5] - b["rz"]),
    )


def run_toe_align_step(ctx, *, side_left: bool) -> Dict[str, Any]:
    try:
        return _run_casbot_toe_align(ctx, side_left=side_left)
    except Exception as e:
        log.warning("Casbot 鞋头对位不可用，走简化路径: %s", e)
        return _run_simple_toe_align(ctx, side_left=side_left)


def _run_casbot_toe_align(ctx, *, side_left: bool) -> Dict[str, Any]:
    """旧视觉：cam2 YOLO classify → 相对 MoveL 推进（不走 Fairino ServoCart 线程）。"""
    vis = ctx.cfg.get("vision") or {}
    cam_key = str((vis.get("toe_align") or {}).get("camera") or "cam2")
    if ctx.vision.cam_is_mock(cam_key):
        raise RuntimeError("对位相机 Mock，走简化对位")
    cam = ctx.cameras.get(cam_key)
    img = cam.grab() if cam else None
    if img is None:
        raise RuntimeError(f"{cam_key} 无图，无法鞋头对位")
    from algorithm_module import algo

    label_r = algo.classify_toe_align(img, vis)
    if not label_r.ok:
        raise RuntimeError(label_r.message or "鞋头对位分类失败")
    label, msg = label_r.label, label_r.message
    aligned = label_r.aligned
    try:
        from vision.monitor_frames import annotate_bgr

        shown = annotate_bgr(
            img,
            ["TOE", f"L={label or '-'}", "ALIGNED" if aligned else "MOVE"],
            ok=bool(label),
            cam_id=cam_key,
            kind="VIS",
        )
        ctx.vision.publish_vis(cam_key, shown, msg or "", bool(label), raw=img)
    except Exception:
        pass
    if not label:
        raise RuntimeError(msg or "鞋头对位分类失败")

    lab = str(label).strip().lower()
    if aligned:
        gvl = ctx.gvl
        gvl._toe_align_tries = 0
        return {"done": True, "ok": True, "message": f"旧视觉鞋头对位到位 {msg}"}

    enable = True
    blk = vis.get("toe_align") if isinstance(vis.get("toe_align"), dict) else {}
    if isinstance(blk, dict) and "enable_motion" in blk:
        enable = bool(blk.get("enable_motion"))
    if not enable:
        return {"done": True, "ok": True, "message": f"对位未到位但禁止运动: {msg}"}

    gvl = ctx.gvl
    tries = int(getattr(gvl, "_toe_align_tries", 0) or 0)
    max_tries = int(vis.get("toe_align_mock_tries", 8) or 8)
    if tries >= max_tries:
        gvl._toe_align_tries = 0
        return {"done": True, "ok": True, "message": f"鞋头对位次数用尽 tries={tries} 末次={msg}"}

    import math

    robot = ctx.robot1
    _sync_robot_state(robot)
    step_mm = float(vis.get("toe_align_mock_step_mm", 8.0))
    adv = vis.get("toe_align_advance_mm") or [0.0, step_mm, 0.0]
    if not (isinstance(adv, (list, tuple)) and len(adv) >= 3):
        adv = [0.0, step_mm, 0.0]
    dx, dy, dz = float(adv[0]), float(adv[1]), float(adv[2])
    if lab in ("2", "right", "右"):
        dx, dy = -abs(step_mm), 0.0
    elif lab in ("left", "左"):
        dx, dy = abs(step_mm), 0.0

    cur = numeric_pose(robot.get_actual_tcp_pose())
    target = dict(cur)
    target["x"] = cur["x"] + dx
    target["y"] = cur["y"] + dy
    target["z"] = cur["z"] + dz
    dpos = math.hypot(dx, dy) + abs(dz)
    if dpos < 0.3:
        gvl._toe_align_tries = 0
        return {"done": True, "ok": True, "message": f"对位位移可忽略 {msg}"}

    robot.move_l(
        target,
        label=f"旧视觉鞋头推进({'左' if side_left else '右'} {label} 第{tries+1}拍)",
        from_label="当前位置",
        precise=True,
        **ctx.step_motion_kwargs("s2a20_40", precise=True),
    )
    gvl._toe_align_tries = tries + 1
    return {
        "done": False,
        "ok": True,
        "message": f"旧视觉鞋头推进中 tries={tries+1} {msg}",
    }


def _run_simple_toe_align(ctx, *, side_left: bool) -> Dict[str, Any]:
    """
    简化对位：默认不发 MoveL（已在 place_slot，无视觉伺服再动易 74）。
    yaml vision.toe_align_mock_motion=true 时才做相对小步（调试用）。
    """
    gvl = ctx.gvl
    allow_move = bool((ctx.cfg.get("vision") or {}).get("toe_align_mock_motion", False))
    gvl._toe_align_tries = 0
    if not allow_move:
        return {
            "done": True,
            "ok": True,
            "message": (
                f"鞋头对位跳过(无视觉伺服/已在放料点，"
                f"{'左' if side_left else '右'}；设 toe_align_mock_motion=true 才相对推进)"
            ),
        }

    # 可选调试运动：相对当前位姿小步（须控制器当前工具与 robot.tool 一致）
    import math

    robot = ctx.robot1
    _sync_robot_state(robot)
    tries = int(getattr(gvl, "_toe_align_tries", 0) or 0)
    max_tries = int(ctx.cfg.get("vision", {}).get("toe_align_mock_tries", 3))
    step_mm = float(ctx.cfg.get("vision", {}).get("toe_align_mock_step_mm", 8.0))
    adv = ctx.cfg.get("vision", {}).get("toe_align_advance_mm") or [0.0, step_mm, 0.0]
    if not (isinstance(adv, (list, tuple)) and len(adv) >= 3):
        adv = [0.0, step_mm, 0.0]

    if tries >= max_tries:
        gvl._toe_align_tries = 0
        return {"done": True, "ok": True, "message": f"鞋头对位完成 tries={tries}"}

    cur = numeric_pose(robot.get_actual_tcp_pose())
    place = numeric_pose(ctx.pose("robot1", "place_slot"))
    scale = 1.0 / max(1, max_tries)
    target = dict(cur)
    target["x"] = cur["x"] + float(adv[0]) * scale
    target["y"] = cur["y"] + float(adv[1]) * scale
    target["z"] = cur["z"] + float(adv[2]) * scale
    blend = min(1.0, 0.25 + 0.2 * tries)
    for k in ("rx", "ry", "rz"):
        target[k] = cur[k] + (place[k] - cur[k]) * blend

    dpos = math.hypot(target["x"] - cur["x"], target["y"] - cur["y"]) + abs(
        target["z"] - cur["z"]
    )
    if dpos < 0.5:
        gvl._toe_align_tries = 0
        return {"done": True, "ok": True, "message": "鞋头对位完成(位移可忽略)"}

    robot.move_l(
        target,
        label=f"鞋头相对推进({'左' if side_left else '右'} 第{tries+1}拍)",
        from_label="当前位置",
        precise=True,
        **ctx.step_motion_kwargs("s2a20_40", precise=True),
    )
    gvl._toe_align_tries = tries + 1
    return {
        "done": False,
        "ok": True,
        "message": f"鞋头相对推进中 tries={tries+1}",
    }


def run_heel_down_rotate(ctx, *, side_left: bool) -> Dict[str, Any]:
    """压跟：默认跳过运动；heel_down_dz_mm≠0 或姿态差大且 heel_down_mock_motion=true 才动。"""
    robot = ctx.robot1
    _sync_robot_state(robot)
    place = numeric_pose(ctx.pose("robot1", "place_slot"))
    cur6 = _pose6(robot)
    steps = int(ctx.cfg.get("vision", {}).get("heel_down_steps", 12))
    dz = float(ctx.cfg.get("vision", {}).get("heel_down_dz_mm", 0.0))
    allow_move = bool((ctx.cfg.get("vision") or {}).get("heel_down_mock_motion", False))

    gvl = ctx.gvl
    if (not allow_move) or (
        _orient_delta_deg(cur6, place) < 0.5 and abs(dz) < 0.5
    ):
        gvl._heel_seg_poses = []
        gvl._heel_seg_i = 0
        return {
            "ok": True,
            "started": True,
            "total": 0,
            "message": "压跟跳过(无 mock 运动或姿态已接近)",
        }

    n = max(2, int(steps))
    segs: List[List[float]] = []
    for i in range(1, n + 1):
        t = i / float(n)
        segs.append(
            [
                cur6[0],
                cur6[1],
                cur6[2] + dz * t,
                cur6[3] + (place["rx"] - cur6[3]) * t,
                cur6[4] + (place["ry"] - cur6[4]) * t,
                cur6[5] + (place["rz"] - cur6[5]) * t,
            ]
        )
    gvl._heel_seg_poses = segs
    gvl._heel_seg_i = 0
    first = segs[0]
    robot.move_l(
        {
            "x": first[0],
            "y": first[1],
            "z": first[2],
            "rx": first[3],
            "ry": first[4],
            "rz": first[5],
        },
        label="压跟姿态插值(段0)",
        from_label="当前位置",
        precise=True,
        **ctx.step_motion_kwargs("s2a20_45", precise=True),
    )
    return {
        "ok": True,
        "started": True,
        "total": len(segs),
        "message": f"压跟开始 segments={len(segs)} dz={dz}",
    }


def poll_heel_down(ctx) -> Dict[str, Any]:
    robot = ctx.robot1
    gvl = ctx.gvl
    segs: List = list(getattr(gvl, "_heel_seg_poses", None) or [])
    i = int(getattr(gvl, "_heel_seg_i", 0) or 0)
    if not segs:
        return {"done": True, "ok": True, "message": "压跟完成(无段/已跳过)"}

    if not robot.poll_move_done():
        return {"done": False, "ok": True, "message": f"压跟段 {i}/{len(segs)}"}

    i += 1
    gvl._heel_seg_i = i
    if i >= len(segs):
        gvl._heel_seg_poses = []
        return {"done": True, "ok": True, "message": "压跟分段全部完成"}

    p = segs[i]
    robot.move_l(
        {"x": p[0], "y": p[1], "z": p[2], "rx": p[3], "ry": p[4], "rz": p[5]},
        label=f"压跟(段{i})",
        from_label=f"压跟段{i-1}",
        precise=True,
        **ctx.step_motion_kwargs("s2a20_45", precise=True),
    )
    return {"done": False, "ok": True, "message": f"压跟段 {i}/{len(segs)}"}
