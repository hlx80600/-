"""视觉采图动作：写 json、求解手眼、应用 PickPose、试走 MoveL。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from devices.pose_utils import apply_offset, is_left_shoe_flag, numeric_pose
from vision import calib, roi, shoe_cfg
from vision.handeye_solve import enrich_sample, k_from_any, solve_from_samples
from vision.legacy_pipeline import reset_shoe_vision

ROOT = Path(__file__).resolve().parents[1]


def vis_cfg(ctx) -> dict:
    if ctx is None:
        return {}
    vis = ctx.cfg.get("vision")
    return vis if isinstance(vis, dict) else {}


def resolve_k(ctx=None, camera_id: str = "cam1") -> Optional[dict]:
    return k_from_any(
        calib.load_calib(camera_id),
        shoe_cfg.load(vis_cfg(ctx) if ctx is not None else None),
    )


def write_intrinsics_from_calib(ctx, camera_id: str = "cam1") -> str:
    if camera_id != "cam1":
        raise RuntimeError("皮带 json 的 camera 内参只对应 cam1。请先选 cam1。")
    data = calib.load_calib(camera_id)
    if not data:
        raise RuntimeError(f"{camera_id} 还没有棋盘格内参。请到「视觉」页「棋盘格内参」采集并计算。")
    k = shoe_cfg.k_from_calib(data)
    if not k:
        raise RuntimeError("内参文件里没有有效的 K 矩阵")
    path = shoe_cfg.write_intrinsics(k, vis_cfg(ctx))
    reset_shoe_vision()
    return (
        f"已写入 {path.name} camera: fx={k['fx']:.2f} fy={k['fy']:.2f} "
        f"cx={k['cx']:.2f} cy={k['cy']:.2f}"
    )


def write_roi_ratio_from_file(ctx, camera_id: str = "cam1") -> str:
    if camera_id != "cam1":
        raise RuntimeError("皮带 json 的 roi_ratio 只对应 cam1。请先选 cam1 并保存绿框。")
    r = roi.load_roi(camera_id)
    if not r:
        raise RuntimeError(f"{camera_id} 还没有 ROI 文件。请在「视觉」页「相机与ROI」拖绿框并写入配置。")
    cam = ctx.cameras.get(camera_id) if ctx is not None else None
    img = getattr(cam, "last_color", None) if cam is not None else None
    if img is None and ctx is not None and hasattr(ctx, "vision"):
        img = (ctx.vision.last_raw or {}).get(camera_id)
    iw = ih = 0
    if img is not None and hasattr(img, "shape"):
        ih, iw = int(img.shape[0]), int(img.shape[1])
    if iw < 8 or ih < 8:
        cal = calib.load_calib(camera_id) or {}
        size = cal.get("image_size") or []
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            iw, ih = int(size[0]), int(size[1])
    if iw < 8 or ih < 8:
        iw, ih = 1280, 720
    ratio = shoe_cfg.roi_pixels_to_ratio(
        int(r.get("x", 0)),
        int(r.get("y", 0)),
        int(r.get("w", iw)),
        int(r.get("h", ih)),
        iw,
        ih,
    )
    path = shoe_cfg.write_roi_ratio(ratio, vis_cfg(ctx))
    reset_shoe_vision()
    return f"已写入 {path.name} roi_ratio={ratio}（按图像 {iw}×{ih}）"


def _dedupe_samples(samples: list) -> list:
    seen: set = set()
    out = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        tcp = s.get("tcp") if isinstance(s.get("tcp"), dict) else {}
        try:
            key = (
                round(float(s.get("pixel_u", 0)), 1),
                round(float(s.get("pixel_v", 0)), 1),
                round(float(tcp.get("x", 0)), 2),
                round(float(tcp.get("y", 0)), 2),
                round(float(tcp.get("z", 0)), 2),
            )
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def solve_handeye_and_write(
    ctx,
    camera_id: str = "cam1",
    *,
    extra_samples: Optional[list] = None,
    assumed_z_mm: float = 400.0,
) -> str:
    samples = _dedupe_samples(
        list(calib.load_handeye_samples(camera_id) or []) + list(extra_samples or [])
    )
    k = resolve_k(ctx, camera_id)
    cam = ctx.cameras.get(camera_id) if ctx is not None else None
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    color_hw = None
    img = getattr(cam, "last_color", None) if cam is not None else None
    if img is None and ctx is not None and hasattr(ctx, "vision"):
        img = (ctx.vision.last_raw or {}).get(camera_id)
    if img is not None and hasattr(img, "shape"):
        color_hw = (int(img.shape[0]), int(img.shape[1]))
    packed = [enrich_sample(s, depth=depth, color_hw=color_hw, k=k) for s in samples]
    result = solve_from_samples(packed, k=k, assumed_z_mm=float(assumed_z_mm))
    if not result.ok or result.T is None:
        raise RuntimeError(result.message)
    he_path = calib.save_handeye(camera_id, result.T.tolist())
    msg = f"{result.message}\n已保存 {he_path}"
    if camera_id == "cam1":
        js = shoe_cfg.write_handeye_mat(result.T, vis_cfg(ctx))
        reset_shoe_vision()
        msg += f"\n已写入皮带 {js.name} handeye.mat（下次检测用本机矩阵）"
        if shoe_cfg.is_legacy_handeye(shoe_cfg.load(vis_cfg(ctx))):
            msg += "\n注意：矩阵仍像旧机出厂值，请确认采样点来自本机。"
    else:
        msg += f"\n{camera_id} 不是皮带相机，未改 shoe_vision_config.json。"
    return msg


def apply_belt_pick(ctx) -> tuple[Any, str]:
    mock = (vis_cfg(ctx).get("belt_pick_mock") or {}) if ctx is not None else {}
    z0 = float(mock.get("z", ctx.gvl.PickPose.get("z", 120)))
    rx0 = float(mock.get("rx", ctx.gvl.PickPose.get("rx", -178)))
    ry0 = float(mock.get("ry", ctx.gvl.PickPose.get("ry", -2)))
    r = ctx.vision.photo_belt_pick(z0, rx0, ry0)
    if not r.ok:
        raise RuntimeError(f"皮带拍照失败: {r.message}")
    pose = {
        "x": float(r.x),
        "y": float(r.y),
        "z": float(mock.get("z", r.z)),
        "rx": float(mock.get("rx", getattr(r, "rx", rx0))),
        "ry": float(mock.get("ry", getattr(r, "ry", ry0))),
        "rz": float(r.rz),
        "is_left_shoe": is_left_shoe_flag(r.is_left_shoe),
    }
    gvl = ctx.gvl
    for k in ("x", "y", "z", "rx", "ry", "rz"):
        gvl.PickPose[k] = pose[k]
    gvl.PickPose["is_left_shoe"] = pose["is_left_shoe"]
    toe_raw = getattr(r, "toe_offset_in_grasp_tcp", None)
    if isinstance(toe_raw, (list, tuple)) and len(toe_raw) >= 3:
        toe_off = [float(toe_raw[0]), float(toe_raw[1]), float(toe_raw[2])]
    else:
        d = mock.get("toe_offset_in_grasp_tcp") or [0.0, 120.0, 0.0]
        toe_off = [float(d[0]), float(d[1]), float(d[2])]
    length_mm = float(getattr(r, "shoe_length_mm", 0.0) or 0.0)
    snap = dict(pose)
    snap.update(
        {
            "source": str(getattr(r, "source", "")),
            "message": str(getattr(r, "message", "")),
            "toe_offset_in_grasp_tcp": toe_off,
            "shoe_length_mm": length_mm,
        }
    )
    gvl.BeltPickSnapshot = snap
    ctx.runtime_pick.update(gvl.PickPose)
    side = "左鞋" if pose["is_left_shoe"] else "右鞋"
    msg = (
        f"已写入 PickPose {side}  "
        f"X={pose['x']:.1f} Y={pose['y']:.1f} Z={pose['z']:.1f} Rz={pose['rz']:.1f}\n"
        f"Z/Rx/Ry 用示教默认（belt_pick_mock），XYRz 来自视觉。source={r.source}\n"
        f"{r.message}"
    )
    return r, msg


def pick_above_pose(ctx) -> dict:
    pick = numeric_pose(ctx.gvl.PickPose)
    try:
        off = ctx.offset("robot1", "pick_above_offset")
        return apply_offset(pick, off)
    except Exception:
        pick["z"] = float(pick.get("z", 0)) + 80.0
        return pick


def move_robot1_to_pick(ctx, *, above: bool = True) -> str:
    if ctx.machine.state.name == "RUNNING":
        raise RuntimeError("自动运行中禁止点动，请先停止")
    target = pick_above_pose(ctx) if above else numeric_pose(ctx.gvl.PickPose)
    label = "视觉取料上方" if above else "视觉取料点"
    ctx.robot1.move_l(target, label=label, precise=True, vel=20.0)
    xyz = ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
    return f"已发 MoveL → {label}\n{xyz}"


def handeye_capture_cfg(ctx) -> dict[str, float | bool]:
    """手眼标定拍照参数（config vision.handeye_capture）。"""
    vis = vis_cfg(ctx)
    hc = vis.get("handeye_capture") or vis.get("handeye_retreat") or {}
    if not isinstance(hc, dict):
        hc = {}
    return {
        "open_gripper": bool(hc.get("open_gripper", False)),
        "gripper_wait_s": float(hc.get("gripper_wait_s", 0.5)),
        "grab_wait_s": float(hc.get("grab_wait_s", 0.65)),
        "repick_vel": float(hc.get("repick_vel", 20.0)),
    }


def handeye_retreat_cfg(ctx) -> dict[str, float | bool]:
    """兼容旧名。"""
    return handeye_capture_cfg(ctx)


def _require_handeye_manual(ctx) -> None:
    if ctx is None:
        raise RuntimeError("无 app_context")
    if ctx.machine.state.name == "RUNNING":
        raise RuntimeError("自动运行中禁止手眼标定操作，请先停止")


def read_handeye_robot_pose(ctx) -> dict[str, Any]:
    """读取上料臂当前 TCP 与关节角（对准标定点、尚未退开时调用）。"""
    from datetime import datetime

    _require_handeye_manual(ctx)
    try:
        tcp_raw = ctx.robot1.get_actual_tcp_pose()
        joints = ctx.robot1.get_actual_joint_pos()
    except Exception as e:
        raise RuntimeError(f"读上料臂位姿失败: {e}") from e
    tcp = {k: float(tcp_raw.get(k, 0)) for k in ("x", "y", "z", "rx", "ry", "rz")}
    j_list = [float(v) for v in joints]
    return {
        "tcp": tcp,
        "joints": j_list,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


def handeye_retreat_and_capture(ctx, camera_id: str = "cam1") -> tuple[Any, Any, str]:
    """手动移开臂/夹爪后拍照（不自动发 MoveL；可选仅张爪）。"""
    import time

    _require_handeye_manual(ctx)
    cfg = handeye_capture_cfg(ctx)
    notes: list[str] = []
    if cfg["open_gripper"]:
        try:
            ok = bool(ctx.gripper1.open_claw())
            notes.append("夹爪张开指令已发" if ok else "夹爪张开指令失败(请手动确认)")
        except Exception as e:
            notes.append(f"夹爪张开异常: {e}")
        time.sleep(max(0.0, float(cfg["gripper_wait_s"])))
    else:
        notes.append("未自动张爪/退开：请在示教器手动移出相机视野")

    cam = ctx.cameras.get(camera_id) if ctx is not None else None
    img = None
    wait_s = float(cfg["grab_wait_s"])
    if ctx is not None and hasattr(ctx, "vision"):
        img = ctx.vision.grab_raw(camera_id, wait_s=wait_s)
        if img is None:
            img = (ctx.vision.last_raw or {}).get(camera_id)
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    if img is None:
        raise RuntimeError("拍照失败：无彩色图，请检查相机连接或 Mock 设置")

    ih, iw = int(img.shape[0]), int(img.shape[1])
    z_note = "有深度" if depth is not None else "无深度"
    extra = "\n  ".join(notes)
    msg = (
        f"已拍照 ({iw}×{ih}, {z_note})\n"
        f"  {extra}\n"
        "请在上方预览点击标定点像素，再点「③ 完成本点采样」。"
    )
    return img, depth, msg


def write_pick_pose_from_tcp(ctx, tcp: dict[str, float]) -> str:
    """把手眼记录的对准位姿写入 PickPose（供回夹验证 / 视觉试走）。"""
    pick = numeric_pose(tcp)
    gvl = ctx.gvl
    for k in ("x", "y", "z", "rx", "ry", "rz"):
        gvl.PickPose[k] = float(pick[k])
    ctx.runtime_pick.update(gvl.PickPose)
    return (
        f"已写入 PickPose（本标定点取料位）\n"
        f"  X={pick['x']:.1f} Y={pick['y']:.1f} Z={pick['z']:.1f} "
        f"Rz={pick['rz']:.1f}"
    )


def pick_poses_from_tcp(ctx, tcp: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """由对准 TCP 生成取料点与上方点（上方=取料点+pick_above_offset）。"""
    pick = numeric_pose(tcp)
    try:
        above = apply_offset(pick, ctx.offset("robot1", "pick_above_offset"))
    except Exception:
        above = dict(pick)
        above["z"] = float(pick.get("z", 0)) + 80.0
    return above, pick


def move_robot1_pick_entry(ctx, *, vel: float | None = None) -> str:
    """MoveJ 到示教 pick_entry。"""
    _require_handeye_manual(ctx)
    v = float(vel if vel is not None else handeye_capture_cfg(ctx)["repick_vel"])
    tag = ctx.named_point_tag("robot1", "pick_entry")
    ctx.move_to_point("robot1", "pick_entry", precise=True, vel=v)
    return f"已发 MoveJ → {tag}"


def move_robot1_to_handeye_tcp(
    ctx,
    tcp: dict[str, float],
    *,
    above: bool,
    vel: float | None = None,
) -> str:
    """MoveL 到本标定点的取料上方或取料点。"""
    _require_handeye_manual(ctx)
    v = float(vel if vel is not None else handeye_capture_cfg(ctx)["repick_vel"])
    above_pose, pick_pose = pick_poses_from_tcp(ctx, tcp)
    target = above_pose if above else pick_pose
    label = "手眼标定-取料上方" if above else "手眼标定-取料点"
    ctx.robot1.move_l(target, label=label, precise=True, vel=v)
    xyz = ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
    return f"已发 MoveL → {label}\n{xyz}"


def move_robot1_handeye_repick(ctx, tcp: dict[str, float], *, vel: float | None = None) -> str:
    """张爪 → pick_entry → 取料上方 → 取料点 → 夹紧（与 Station2 取料段一致，用于验证）。"""
    _require_handeye_manual(ctx)
    v = float(vel if vel is not None else handeye_capture_cfg(ctx)["repick_vel"])
    above_pose, pick_pose = pick_poses_from_tcp(ctx, tcp)
    entry_tag = ctx.named_point_tag("robot1", "pick_entry")
    ctx.gripper1.open()
    ctx.move_to_point("robot1", "pick_entry", precise=True, vel=v)
    ctx.robot1.move_l(
        above_pose,
        label="手眼验证取料上方",
        from_label=entry_tag,
        precise=True,
        vel=v,
    )
    ctx.robot1.move_l(pick_pose, label="手眼验证取料点", precise=True, vel=v)
    ctx.gripper1.close()
    xyz = ", ".join(f"{k}={pick_pose[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
    return (
        "已按取料路径回夹：张爪 → pick_entry → 上方 → 取料点 → 夹紧\n"
        f"  取料点 {xyz}\n"
        "请确认夹取是否正常；可对比示教偏差评估手眼误差。"
    )


def append_handeye_sample(
    ctx,
    camera_id: str,
    *,
    pixel_u: int,
    pixel_v: int,
    robot_record: dict[str, Any],
    img: Any = None,
) -> str:
    """用手眼记录位姿 + 退开后拍照上的像素，追加一条采样。"""
    from datetime import datetime

    tcp_in = robot_record.get("tcp") if isinstance(robot_record.get("tcp"), dict) else {}
    if not tcp_in:
        raise RuntimeError("缺少已记录的机械臂 TCP，请先点「① 记录机械臂位姿」")
    u, v = int(pixel_u), int(pixel_v)

    cam = ctx.cameras.get(camera_id) if ctx is not None else None
    if img is None:
        img = getattr(cam, "last_color", None) if cam is not None else None
    if img is None and ctx is not None and hasattr(ctx, "vision"):
        img = (ctx.vision.last_raw or {}).get(camera_id)

    sample: dict[str, Any] = {
        "pixel_u": u,
        "pixel_v": v,
        "tcp": {k: float(tcp_in.get(k, 0)) for k in ("x", "y", "z", "rx", "ry", "rz")},
        "camera": camera_id,
        "time": datetime.now().isoformat(timespec="seconds"),
        "recorded_at": robot_record.get("time"),
        "workflow": "retreat_capture",
    }
    joints = robot_record.get("joints")
    if isinstance(joints, (list, tuple)) and len(joints) >= 6:
        sample["joints"] = [float(x) for x in joints[:6]]

    if img is not None and hasattr(img, "shape"):
        sample["image_w"] = int(img.shape[1])
        sample["image_h"] = int(img.shape[0])
        color_hw = (int(img.shape[0]), int(img.shape[1]))
    else:
        color_hw = None
    k = resolve_k(ctx, camera_id)
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    sample = enrich_sample(sample, depth=depth, color_hw=color_hw, k=k)
    samples = list(calib.load_handeye_samples(camera_id) or [])
    samples.append(sample)
    path = calib.save_handeye_samples(camera_id, samples)
    z = sample.get("depth_mm")
    z_s = f"{float(z):.0f}mm" if z else "无深度"
    j = sample.get("joints") or []
    j_s = ", ".join(f"{x:.2f}" for x in j[:6]) if j else "-"
    return (
        f"手眼采样 +1（共{len(samples)}） {path.name}\n"
        f"  像素=({u},{v}) 深度={z_s}（移开后拍照）\n"
        f"  记录位 TCP x={sample['tcp']['x']:.1f} y={sample['tcp']['y']:.1f} "
        f"z={sample['tcp']['z']:.1f} rz={sample['tcp']['rz']:.1f}\n"
        f"  记录位关节 J=[{j_s}]"
    )


def record_handeye_sample(
    ctx,
    camera_id: str = "cam1",
    *,
    pixel: Optional[tuple[int, int]] = None,
    use_center: bool = False,
) -> str:
    from datetime import datetime

    from vision.handeye_solve import clicked_pixel, enrich_sample, set_clicked_pixel

    cam = ctx.cameras.get(camera_id) if ctx is not None else None
    img = getattr(cam, "last_color", None) if cam is not None else None
    if img is None and ctx is not None and hasattr(ctx, "vision"):
        img = ctx.vision.grab_raw(camera_id, wait_s=0.4)
        if img is None:
            img = (ctx.vision.last_raw or {}).get(camera_id)
    if use_center:
        if img is None:
            raise RuntimeError("当前无图，无法用画面中心")
        u, v = int(img.shape[1] // 2), int(img.shape[0] // 2)
        set_clicked_pixel(camera_id, u, v)
    else:
        uv = pixel or clicked_pixel(camera_id)
        if uv is None:
            raise RuntimeError("请先到「视觉」预览上点选皮带上一点，或改用画面中心")
        u, v = int(uv[0]), int(uv[1])
    try:
        pose = ctx.robot1.get_actual_tcp_pose()
    except Exception as e:
        raise RuntimeError(f"读上料臂 TCP 失败: {e}") from e
    sample = {
        "pixel_u": int(u),
        "pixel_v": int(v),
        "tcp": {k: float(pose.get(k, 0)) for k in ("x", "y", "z", "rx", "ry", "rz")},
        "camera": camera_id,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    if img is not None:
        sample["image_w"] = int(img.shape[1])
        sample["image_h"] = int(img.shape[0])
        color_hw = (int(img.shape[0]), int(img.shape[1]))
    else:
        color_hw = None
    k = resolve_k(ctx, camera_id)
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    sample = enrich_sample(sample, depth=depth, color_hw=color_hw, k=k)
    samples = list(calib.load_handeye_samples(camera_id) or [])
    samples.append(sample)
    path = calib.save_handeye_samples(camera_id, samples)
    z = sample.get("depth_mm")
    z_s = f"{float(z):.0f}mm" if z else "无深度"
    return (
        f"手眼采样 +1（共{len(samples)}） {path.name}\n"
        f"  像素=({u},{v}) 深度={z_s}\n"
        f"  TCP x={sample['tcp']['x']:.1f} y={sample['tcp']['y']:.1f} "
        f"z={sample['tcp']['z']:.1f} rz={sample['tcp']['rz']:.1f}"
    )


def checklist_lines(ctx) -> list[str]:
    from vision.legacy_pipeline import listed_model_paths, stack_status

    vis = vis_cfg(ctx)
    st = stack_status()
    js = shoe_cfg.load(vis)
    cam1 = ctx.cameras.get("cam1") if ctx is not None else None
    live = bool(cam1) and (not cam1.use_mock) and bool(cam1.opened)
    mock = bool(cam1.use_mock) if cam1 else True
    has_k_file = calib.load_calib("cam1") is not None
    k_json = shoe_cfg.camera_k(js) is not None
    n_he = len(calib.load_handeye_samples("cam1") or [])
    has_he_file = calib.load_handeye("cam1") is not None
    legacy = shoe_cfg.is_legacy_handeye(js)
    models_ok = all(p.exists() for _n, p in listed_model_paths(vis) if _n != "ShoeVision配置")
    ultra = bool(st.get("ultralytics"))
    lines = [
        f"{'✓' if ultra else '✗'} ultralytics/YOLO 环境",
        f"{'✓' if models_ok else '✗'} 六个 .pt 文件都在",
        f"{'✓' if (live or mock) else '✗'} cam1 能出图  ({'模拟' if mock else '真机'})",
        f"{'✓' if has_k_file else '✗'} cam1 棋盘格内参文件",
        f"{'✓' if k_json else '✗'} json 已写 camera 内参",
        f"{'✓' if n_he >= 3 else '✗'} cam1 手眼采样 {n_he} 点（至少 3）",
        f"{'✓' if has_he_file else '✗'} 已求解手眼矩阵文件",
        f"{'✓' if (js.get('handeye') and not legacy) else ('～' if js.get('handeye') else '✗')} "
        f"json handeye.mat{'（仍是旧机矩阵，请本机重标）' if legacy else ''}",
        f"{'✓' if ctx.gvl.PickPose else '✗'} 当前 PickPose "
        f"X={float(ctx.gvl.PickPose.get('x', 0)):.1f} "
        f"Y={float(ctx.gvl.PickPose.get('y', 0)):.1f}",
    ]
    return lines
