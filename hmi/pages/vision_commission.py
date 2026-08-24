"""视觉调试：当前相机投产检查清单 + 投产步骤（随所选相机切换）。

检测一律旧压鞋机 YOLO：OBB + 分类 + 深度 + 手眼矩阵 → 机器人毫米。
"""

from __future__ import annotations

from typing import Any

from vision import calib, roi
from vision.legacy_pipeline import listed_model_paths, model_status_text, stack_status, vision_method

CAM_TITLES = {
    "cam1": "cam1 皮带上料（YOLO+手眼）",
    "cam2": "cam2 鞋头对位",
    "cam3": "cam3 放料槽有无鞋",
    "cam4": "cam4 取料槽/压杆",
}


def _ok(flag: bool) -> str:
    return "✓" if flag else "✗"


def hardware_line(cam: Any, cid: str) -> str:
    if cam is None:
        return f"{cid} 未创建相机对象"
    mock = bool(cam.use_mock)
    if mock:
        link = "模拟出图"
    elif cam.opening:
        link = "正在连接真机…"
    elif cam.opened:
        link = "真机已打开"
    else:
        link = "真机未打开"
    err = (getattr(cam, "last_error", "") or "").strip()
    err_s = f" | {err}" if err else ""
    return (
        f"{CAM_TITLES.get(cid, cid)} | {link} | serial={cam.serial or '（空）'} "
        f"| index={cam.index}{err_s}"
    )


def stack_line(ctx: Any) -> str:
    vis = (ctx.cfg.get("vision") if ctx is not None else {}) or {}
    st = stack_status()
    method = vision_method(vis)
    return (
        f"方法={method}（YOLO，无模板备用） | {st.get('message', '')} | {model_status_text(vis)}"
    )


def models_list_text(ctx: Any) -> str:
    vis = (ctx.cfg.get("vision") if ctx is not None else {}) or {}
    lines = []
    for name, path in listed_model_paths(vis):
        mark = "✓" if path.exists() else "✗"
        lines.append(f"{mark} {name}: {path}")
    return "\n".join(lines) if lines else "未列出模型路径"


def checklist_text(ctx: Any, cid: str) -> str:
    cam = ctx.cameras.get(cid) if ctx is not None else None
    live = bool(cam) and (not cam.use_mock) and bool(cam.opened)
    mock = bool(cam.use_mock) if cam else True
    has_serial = bool(str(getattr(cam, "serial", "") or "").strip())
    has_intr = calib.load_calib(cid) is not None
    has_roi = roi.load_roi(cid) is not None
    n_he = len(calib.load_handeye_samples(cid))
    has_he = calib.load_handeye(cid) is not None
    vis = (ctx.cfg.get("vision") if ctx is not None else {}) or {}
    lines = [
        f"检查清单（{CAM_TITLES.get(cid, cid)}）  {_ok(not mock)}真机  "
        f"{_ok(live or mock)}能出图  {_ok(has_serial)}已填serial  "
        f"{_ok(has_intr)}内参  {_ok(has_roi)}ROI文件",
        f"  模型 {model_status_text(vis)}",
    ]
    if cid in ("cam1", "cam2"):
        lines.append(f"  手眼采样 {n_he} 点  {_ok(has_he)}手眼文件")
    return "\n".join(lines)


def steps_text(cid: str) -> str:
    common = (
        "【共同准备】把旧程序 models/ 拷到本工程 models/（鞋OBB、左右脚、楦、槽分类、鞋头对位、压杆）。"
        "装 ultralytics + 旧 YOLO 环境。USB 插稳 → 填 serial → 取消该路 Mock → 预览出真图。"
        "棋盘格：内角点 11×8、边长 15mm（只标内参，不参与检测）。"
    )
    if cid == "cam1":
        return (
            common + "\n"
            "【cam1 皮带 从 0 到用】彩色+深度 → YOLO鞋OBB → 左右脚分类 → 楦OBB → "
            "像素+深度→相机XYZ → 手眼4×4 → 机器人毫米(x,y,z,yaw)，鞋头距作 TCP 的 +Y。\n"
            "1. 出真图：皮带上能看清整只鞋。roi_ratio 在 shoe_vision_config.json。\n"
            "2. 在「视觉」页「手眼标定」计算手眼 4×4，写入 shoe_vision_config.json 的 handeye.mat。\n"
            "3. 内参 fx/fy/cx/cy 与皮带相机一致（可先用旧值，再棋盘格重标写入 json）。\n"
            "4. 「测试皮带拍照」应打出楦心基座 XYZ、左右脚、鞋头距 mm。这就是自动取料坐标。\n"
            "5. YOLO 未装或模型缺失时保持 Mock，用「屏蔽取料」。"
        )
    if cid == "cam2":
        return (
            common + "\n"
            "【cam2 鞋头对位 从 0 到用】槽/手眼画面 YOLO classify + 相对 MoveL 推进。\n"
            "1. yaml vision.toe_align.model_path 指向 models/toe_align/*.pt（如 0722best.pt）。\n"
            "2. 左右槽几何仍用 press_shoes/config/left_slot.yaml、right_slot.yaml。\n"
            "3. 「测试鞋头对位」：标签 0=到位，1=继续向前。自动放料时 Station2 按此推进。\n"
            "4. 模拟时第一次给偏移、第二次到位，方便单步。"
        )
    if cid == "cam3":
        return (
            common + "\n"
            "【cam3 放料槽 从 0 到用】YOLO 二分类 0=空槽 1=有鞋。\n"
            "1. 出真图，ROI 可框槽口（分类也可用全图）。\n"
            "2. 拷 models/slot_check/*.pt，「测试槽有无鞋」。\n"
            "3. 取消 Mock 后自动用分类结果；左右鞋槽仍走流程记忆，不看监视页 Mock。"
        )
    return (
        common + "\n"
        "【cam4 取料槽 从 0 到用】有无鞋分类 + 压杆/夹爪 X 距 → 基座 XY 毫米，只改示教取料点 XY。\n"
        "1. 出真图，槽口清晰。\n"
        "2. slot_check + position/rod/obb.pt，参数在 position_config.yaml（含夹爪预设 X）。\n"
        "3. 「测试取料槽」看有无鞋；「测试压杆偏移」看 dx/dy mm。\n"
        "4. 测到则 Station5 叠加到 slot_pick；测不到用示教点。"
    )


def status_copy_text(ctx: Any, cid: str, extra: str = "") -> str:
    cam = ctx.cameras.get(cid) if ctx is not None else None
    vis = (ctx.cfg.get("vision") if ctx is not None else {}) or {}
    models = [f"{n}: {p} 存在={p.exists()}" for n, p in listed_model_paths(vis)]
    parts = [
        hardware_line(cam, cid),
        stack_line(ctx),
        checklist_text(ctx, cid),
        f"内参文件: {calib.calib_path(cid)} 存在={calib.calib_path(cid).exists()}",
        f"ROI文件: {roi.roi_path(cid)} 存在={roi.roi_path(cid).exists()}",
        f"手眼采样: {calib.handeye_samples_path(cid)} 存在={calib.handeye_samples_path(cid).exists()}",
        f"手眼矩阵: {calib.handeye_path(cid)} 存在={calib.handeye_path(cid).exists()}",
        "YOLO模型/配置:",
        *models,
    ]
    if extra:
        parts.append(extra)
    return "\n".join(parts)
