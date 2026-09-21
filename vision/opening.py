"""开口相机与 PLC 槽号：四槽只有放料口/取料口两台顶视，槽会转到开口下。

本模块不读 PLC、不碰 Station。槽号由视觉配置或调用方传入。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional, Sequence

OpeningKind = Literal["place", "pick"]

CAM_PLACE = "cam3"
CAM_PICK = "cam4"

# 四槽绕圈 1→2→3→4 左右交替：奇数左槽、偶数右槽。装机若编号相反，改 yaml opening.left_slots。
DEFAULT_LEFT_SLOTS: tuple[int, ...] = (1, 3)


def camera_for_opening(kind: OpeningKind) -> str:
    """返回开口对应的相机 id。

    kind: OpeningKind: ``place`` 放料口 / ``pick`` 取料口
    return: str: ``cam3`` 或 ``cam4``
    """
    return CAM_PLACE if kind == "place" else CAM_PICK


def opening_for_camera(cam_id: str) -> OpeningKind | None:
    """相机 id 对应哪个开口；皮带/眼在手上返回 None。

    cam_id: str: ``cam1``…``cam4``
    return: OpeningKind | None
    """
    key = str(cam_id or "").strip().lower()
    if key == CAM_PLACE:
        return "place"
    if key == CAM_PICK:
        return "pick"
    return None


def parse_slot_id(raw: Any) -> int:
    """把配置/入参收成 0 或 1–4。0 表示未知。

    raw: Any: 槽号
    return: int: ``0`` 或 ``1``…``4``
    """
    try:
        slot_id = int(raw)
    except (TypeError, ValueError):
        return 0
    if 1 <= slot_id <= 4:
        return slot_id
    return 0


def slot_cfg_block(vis_cfg: Optional[Mapping[str, Any]], slot_id: int) -> dict[str, Any]:
    """取 ``vision.slots.<n>``；没有则空 dict。

    vis_cfg: Mapping | None: ``ctx.cfg["vision"]``
    slot_id: int: PLC 槽号 1–4
    return: dict: 该槽的 ROI/preset 覆盖项
    """
    if not isinstance(vis_cfg, Mapping) or slot_id < 1:
        return {}
    slots = vis_cfg.get("slots")
    if not isinstance(slots, Mapping):
        return {}
    block = slots.get(slot_id)
    if block is None:
        block = slots.get(str(slot_id))
    return dict(block) if isinstance(block, Mapping) else {}


def resolve_slot_id(
    *,
    kind: OpeningKind,
    explicit: Any = None,
    cached: Any = None,
    vis_cfg: Optional[Mapping[str, Any]] = None,
) -> int:
    """解析当前开口下的物理槽号，不读 Station / PLC。

    kind: OpeningKind: 放料口或取料口
    explicit: Any: ``photo_*`` 入参
    cached: Any: ``VisionService`` 上一次 ``set_opening_slots``
    vis_cfg: Mapping | None: ``vision`` 段（读 ``opening.place_slot`` / ``pick_slot``）
    return: int: ``0`` 未知，否则 1–4

    不用 ``press.mock_*_slot``：那是压机 Mock 的 yaml 初值，和当前开口槽号不是一回事。
    """
    for raw in (explicit, cached):
        slot_id = parse_slot_id(raw)
        if slot_id:
            return slot_id

    if isinstance(vis_cfg, Mapping):
        opening = vis_cfg.get("opening")
        if isinstance(opening, Mapping):
            key = "place_slot" if kind == "place" else "pick_slot"
            slot_id = parse_slot_id(opening.get(key))
            if slot_id:
                return slot_id
    return 0


def left_slot_ids(vis_cfg: Optional[Mapping[str, Any]] = None) -> tuple[int, ...]:
    """当前工程里哪些物理槽号算左鞋槽。

    vis_cfg: Mapping | None: ``ctx.cfg["vision"]``；读 ``opening.left_slots``
    return: tuple[int, ...]: 默认 ``(1, 3)``
    """
    opening = vis_cfg.get("opening") if isinstance(vis_cfg, Mapping) else None
    raw = opening.get("left_slots") if isinstance(opening, Mapping) else None
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        parsed = tuple(slot for slot in (parse_slot_id(item) for item in raw) if slot)
        if parsed:
            return parsed
    return DEFAULT_LEFT_SLOTS


def is_left_slot_for_id(
    slot_id: Any,
    vis_cfg: Optional[Mapping[str, Any]] = None,
) -> bool | None:
    """由 PLC 槽号推导 Station3 要用的 ``is_left_slot``。

    slot_id: Any: 1–4；0/非法则无法判断
    vis_cfg: Mapping | None: 含 ``opening.left_slots``
    return: bool | None: True 左槽 / False 右槽 / None 槽号未知
    """
    parsed = parse_slot_id(slot_id)
    if not parsed:
        return None
    return parsed in set(left_slot_ids(vis_cfg))
