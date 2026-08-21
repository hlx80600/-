"""生产算法：皮带抓取 / 槽有无鞋 / 鞋头对位 / 压杆偏移。

入参为图像或相机配置，不依赖 HMI。工位侧通常经 VisionService 取图后再调这里。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .results import BeltPickResult, RodOffsetResult, SlotResult, ToeAlignResult


def detect_belt_pick(
    cameras: Any,
    vis_cfg: Optional[dict],
    default_z: float,
    default_rx: float,
    default_ry: float,
) -> BeltPickResult:
    """cam1 皮带：YOLO OBB + 左右脚 + 楦 + 深度 + 手眼 → 基座毫米取料位姿。"""
    from vision.legacy_pipeline import detect_belt_legacy

    d, vis = detect_belt_legacy(cameras, vis_cfg or {}, default_z, default_rx, default_ry)
    toe = d.get("toe_offset_in_grasp_tcp")
    toe_list = None
    if isinstance(toe, (list, tuple)) and len(toe) >= 3:
        toe_list = [float(toe[0]), float(toe[1]), float(toe[2])]
    return BeltPickResult(
        ok=bool(d.get("ok")),
        x=float(d.get("x") or 0.0),
        y=float(d.get("y") or 0.0),
        z=float(d.get("z") or default_z),
        rx=float(d.get("rx") or default_rx),
        ry=float(d.get("ry") or default_ry),
        rz=float(d.get("rz") or 0.0),
        is_left_shoe=bool(d.get("is_left_shoe", True)),
        message=str(d.get("message") or ""),
        source=str(d.get("source") or "legacy_yolo_handeye"),
        toe_offset_in_grasp_tcp=toe_list,
        shoe_length_mm=float(d.get("shoe_length_mm") or 0.0),
        vis_bgr=vis,
    )


def classify_slot_occupied(
    image_bgr: Any,
    vis_cfg: Optional[dict] = None,
) -> SlotResult:
    """槽有无鞋二分类（cam3 放料 / cam4 取料共用）。"""
    from vision.legacy_pipeline import classify_slot_occupied as _cls

    occupied, msg, conf = _cls(image_bgr, vis_cfg)
    if occupied is None:
        return SlotResult(ok=False, message=str(msg or "分类失败"), confidence=float(conf or 0.0))
    return SlotResult(
        ok=True,
        has_material=bool(occupied),
        message=str(msg or ""),
        confidence=float(conf or 0.0),
    )


def classify_toe_align(
    image_bgr: Any,
    vis_cfg: Optional[dict] = None,
) -> ToeAlignResult:
    """cam2 鞋头对位分类：到位 / 需向前。"""
    from vision.legacy_pipeline import classify_toe_align as _cls

    label, msg = _cls(image_bgr, vis_cfg)
    if not label:
        return ToeAlignResult(ok=False, message=str(msg or "对位分类失败"))
    lab = str(label).strip().lower()
    aligned = lab in ("0", "aligned", "ok", "到位", "贴紧", "stop", "done")
    return ToeAlignResult(
        ok=True,
        aligned=aligned,
        label=str(label),
        message=str(msg or ""),
    )


def measure_rod_offset_mm(
    cameras: Any,
    vis_cfg: Optional[dict] = None,
    image_bgr: Any = None,
) -> RodOffsetResult:
    """cam4 压杆/夹爪 XY 偏移（毫米）；image_bgr 传入则不 grab。"""
    from vision.legacy_pipeline import measure_rod_offset_mm as _meas

    ok, dx, dy, dz, vis, msg = _meas(cameras, vis_cfg, image_bgr=image_bgr)
    return RodOffsetResult(
        ok=bool(ok),
        dx=float(dx or 0.0),
        dy=float(dy or 0.0),
        dz=float(dz or 0.0),
        message=str(msg or ""),
        vis_bgr=vis,
    )


def measure_rod_offset_tuple(
    cameras: Any,
    vis_cfg: Optional[dict] = None,
) -> Tuple[bool, float, float, float, Any, str]:
    """与 legacy_pipeline.measure_rod_offset_mm 相同元组返回，便于旧调用。"""
    r = measure_rod_offset_mm(cameras, vis_cfg)
    return r.ok, r.dx, r.dy, r.dz, r.vis_bgr, r.message


def detect_belt_shoes_mock(vis_cfg: Optional[dict] = None) -> list:
    """屏蔽取料：从 yaml belt_pick_mock 读示教鞋位列表。"""
    from vision.template_match import detect_belt_shoes_mock as _mock

    return list(_mock(vis_cfg or {}))


def stack_status() -> dict:
    from vision.legacy_pipeline import stack_status as _st

    return dict(_st())


def model_status_text(vis_cfg: Optional[dict] = None) -> str:
    from vision.legacy_pipeline import model_status_text as _t

    return str(_t(vis_cfg))


def listed_model_paths(vis_cfg: Optional[dict] = None) -> list:
    from vision.legacy_pipeline import listed_model_paths as _p

    return list(_p(vis_cfg or {}))


def reset_shoe_vision() -> None:
    from vision.legacy_pipeline import reset_shoe_vision as _r

    _r()
