"""中科院四工位压鞋机 Modbus 点表（Excel 协议）。

HMI 只显示中文名与值，不展示 PLC 符号（M/D/X/T）。
读写按 plc_kind：M 线圈、D 保持寄存器、X 离散输入只读、T 计时只读。

Excel「MODBUS地址(Dec)」约定：
  M：1 基线圈号（M573→574）→ PDU = Dec - 1
  D：4xxxxx → PDU = Dec - 400001（等于 D 号）
  X：1xxxxx → PDU = Dec - 100001
  T：表内 Dec 当保持寄存器地址读
D22002 表上写成 42203（缺位），按 D 号 22002 作为保持寄存器 PDU。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PlcKind = Literal["M", "D", "X", "T"]
RwMode = Literal["rw", "ro"]

PROTOCOL_IP_HINT = "192.168.1.5"
PROTOCOL_PORT_HINT = 502


@dataclass(frozen=True)
class CasPoint:
    id: str
    group: str
    label: str
    plc_kind: PlcKind
    modbus_dec: int
    rw: RwMode = "rw"
    hint: str = ""
    d_index: int | None = None  # D 号；表笔误时优先用它当 PDU


def _st_manual(slot: int) -> list[CasPoint]:
    """工位手动：M21 起，表 Dec = M+1。slot1 base M=21 → Dec 22。"""
    g = f"压机手动 · 工位{slot}"
    # 表：启用 M29/129/… Dec=30/130；摆杆起 M21+100*(slot-1)
    en_m = 29 + 100 * (slot - 1)
    b = 21 + 100 * (slot - 1)
    return [
        CasPoint(f"s{slot}_man_en", g, "手动操作启用", "M", en_m + 1),
        CasPoint(f"s{slot}_swing", g, "摆杆", "M", b + 1),
        CasPoint(f"s{slot}_cinch_in", g, "束紧进", "M", b + 1 + 1),
        CasPoint(f"s{slot}_cinch_out", g, "束紧退", "M", b + 2 + 1),
        CasPoint(f"s{slot}_edge", g, "压边", "M", b + 3 + 1),
        CasPoint(f"s{slot}_up", g, "上升", "M", b + 5 + 1),
        CasPoint(f"s{slot}_second", g, "二次压", "M", b + 6 + 1),
        CasPoint(f"s{slot}_down", g, "下降", "M", b + 7 + 1),
    ]


def _st_rod(slot: int) -> list[CasPoint]:
    g = f"压杆操作 · 工位{slot}"
    # 点进 M176+2*(slot-1) Dec=+1；点退 +1；完成 M192+(slot-1)
    fwd_m = 176 + 2 * (slot - 1)
    done_m = 192 + (slot - 1)
    x_dec = 124598 + (slot - 1)
    return [
        CasPoint(f"s{slot}_rod_fwd", g, "压杆点进", "M", fwd_m + 1),
        CasPoint(f"s{slot}_rod_back", g, "压杆点退", "M", fwd_m + 1 + 1),
        CasPoint(f"s{slot}_rod_done", g, "移动完成", "M", done_m + 1),
        CasPoint(f"s{slot}_rod_home", g, "压杆原点", "X", x_dec, rw="ro"),
    ]


def default_cas_points() -> list[CasPoint]:
    """Excel Sheet1 全部点位（可后续在列表末尾追加）。"""
    rows: list[CasPoint] = [
        CasPoint("online", "联机", "联机模式", "M", 574),
        CasPoint("idle", "联机", "空闲信号", "D", 400061, hint="1=空闲"),
        CasPoint(
            "start",
            "联机",
            "启动信号",
            "D",
            400062,
            hint="1=空转　2=启动",
        ),
        CasPoint("shoe_done", "联机", "放鞋完成", "D", 400063, hint="1=完成"),
        CasPoint("station_no", "联机", "当前工位显示", "D", 420491),
        CasPoint(
            "host_estop",
            "联机",
            "急停信号",
            "M",
            51,
            hint="工控机发出：开=本机急停，关=急停复位（Mock 同样联动）",
        ),
    ]
    for slot in (1, 2, 3, 4):
        rows.append(
            CasPoint(
                f"s{slot}_slot_done",
                "联机",
                f"{slot}槽工作完成",
                "M",
                49 + 100 * (slot - 1),
                rw="ro",
                hint="压机PLC给出：该槽取料工作完成",
            )
        )
    for slot in (1, 2, 3, 4):
        rows.extend(_st_manual(slot))
        rows.extend(_st_rod(slot))
    rows.extend(
        [
            CasPoint(
                "move_dist",
                "公共参数",
                "移动距离",
                "D",
                422005,
                d_index=22004,
            ),
            # Excel 写成 42203，缺一位；按 D22002
            CasPoint(
                "move_speed",
                "公共参数",
                "移动速度",
                "D",
                42203,
                d_index=22002,
            ),
            CasPoint("s1_hold_set", "压着时间与计数", "1工位压着时间", "D", 420410),
            CasPoint(
                "s1_hold_show",
                "压着时间与计数",
                "1压着时间显示",
                "T",
                57349,
                rw="ro",
            ),
            CasPoint("s2_hold_set", "压着时间与计数", "2工位压着时间", "D", 420417),
            CasPoint(
                "s2_hold_show",
                "压着时间与计数",
                "2压着时间显示",
                "T",
                57369,
                rw="ro",
            ),
            CasPoint("s3_hold_set", "压着时间与计数", "3工位压着时间", "D", 420450),
            CasPoint(
                "s3_hold_show",
                "压着时间与计数",
                "3压着时间显示",
                "T",
                57389,
                rw="ro",
            ),
            CasPoint("s4_hold_set", "压着时间与计数", "4工位压着时间", "D", 420457),
            CasPoint(
                "s4_hold_show",
                "压着时间与计数",
                "4压着时间显示",
                "T",
                57409,
                rw="ro",
            ),
            CasPoint("s1_count", "压着时间与计数", "1工位压着计数", "D", 420437),
            CasPoint("s2_count", "压着时间与计数", "2工位压着计数", "D", 420439),
            CasPoint("s3_count", "压着时间与计数", "3工位压着计数", "D", 420477),
            CasPoint("s4_count", "压着时间与计数", "4工位压着计数", "D", 420479),
            CasPoint("s1_count_clr", "压着时间与计数", "1计数清零", "M", 19),
            CasPoint("s2_count_clr", "压着时间与计数", "2计数清零", "M", 119),
            CasPoint("s3_count_clr", "压着时间与计数", "3计数清零", "M", 219),
            CasPoint("s4_count_clr", "压着时间与计数", "4计数清零", "M", 319),
        ]
    )
    return rows


# 槽号页：仅 Excel 有的工位信号（yaml 键 → 点表 id 后缀）。不含表外「放鞋完成」等。
SLOT_CAS_ROWS: tuple[tuple[str, str, str], ...] = (
    ("addr_man_en", "man_en", "手动操作启用"),
    ("addr_swing", "swing", "摆杆"),
    ("addr_cinch_in", "cinch_in", "束紧进"),
    ("addr_cinch_out", "cinch_out", "束紧退"),
    ("addr_edge", "edge", "压边"),
    ("addr_press_up", "up", "上升"),
    ("addr_second", "second", "二次压"),
    ("addr_press_down", "down", "下降"),
    ("addr_rod_forward", "rod_fwd", "压杆点进"),
    ("addr_rod_back", "rod_back", "压杆点退"),
    ("addr_motor_done", "rod_done", "移动完成"),
    ("addr_slot_done", "slot_done", "取料槽工作完成"),
    ("addr_rod_home", "rod_home", "压杆原点"),
)
SLOT_YAML_TO_SUFFIX: dict[str, str] = {k: suf for k, suf, _ in SLOT_CAS_ROWS}


def cas_point_by_id() -> dict[str, CasPoint]:
    return {p.id: p for p in default_cas_points()}


def slot_cas_point_id(slot: int, yaml_key: str) -> str:
    """槽号 yaml 键 → 点表 id（如 s1_up）。"""
    return f"s{int(slot)}_{SLOT_YAML_TO_SUFFIX[yaml_key]}"


def default_slot_modbus_map(slot: int) -> dict[str, int]:
    """该槽/工位各信号的协议 Modbus Dec（与点表一致）。"""
    by_id = cas_point_by_id()
    out: dict[str, int] = {}
    for key, suffix, _lab in SLOT_CAS_ROWS:
        pt = by_id.get(f"s{int(slot)}_{suffix}")
        out[key] = int(hmi_modbus_dec(pt)) if pt is not None else 0
    return out


def pdu_addr(point: CasPoint, *, modbus_dec: int | None = None) -> int:
    """Excel「MODBUS地址(Dec)」→ pymodbus 0 基 PDU。"""
    dec = int(point.modbus_dec if modbus_dec is None else modbus_dec)
    kind = point.plc_kind
    if kind == "M":
        return max(0, dec - 1)
    if kind == "D":
        if point.d_index is not None and dec < 100000:
            return int(point.d_index)
        if dec >= 400001:
            return dec - 400001
        return int(point.d_index if point.d_index is not None else dec)
    if kind == "X":
        if dec >= 100001:
            return dec - 100001
        return max(0, dec)
    # T：表 Dec 当保持寄存器
    return max(0, dec)


def hmi_modbus_dec(point: CasPoint) -> int:
    """HMI 显示/编辑用的协议 Modbus 地址（不是 M/D 号）。

    线圈：1 基 Dec（M573 → 574）。
    保持：4xxxxx（D60 → 400061）。
    离散：1xxxxx。
    """
    kind = point.plc_kind
    if kind == "D":
        dec = int(point.modbus_dec)
        if dec >= 400001:
            return dec
        return 400001 + int(pdu_addr(point))
    if kind == "X":
        dec = int(point.modbus_dec)
        if dec >= 100001:
            return dec
        return 100001 + int(pdu_addr(point))
    return int(point.modbus_dec)


def apply_cas_overrides(
    points: list[CasPoint], overrides: dict[str, Any] | None
) -> list[CasPoint]:
    """yaml press.cas_points.{id} = 覆盖后的 modbus_dec。"""
    ov = overrides if isinstance(overrides, dict) else {}
    out: list[CasPoint] = []
    for p in points:
        raw = ov.get(p.id)
        if raw is None:
            out.append(p)
            continue
        try:
            dec = int(raw)
        except (TypeError, ValueError):
            out.append(p)
            continue
        # HMI 存协议 Modbus Dec；旧配置若只写了 D 号（<100000）仍当 PDU
        new_d_index = p.d_index
        if p.plc_kind == "D":
            if 0 <= dec < 100000:
                new_d_index = dec
            else:
                new_d_index = None
        out.append(
            CasPoint(
                id=p.id,
                group=p.group,
                label=p.label,
                plc_kind=p.plc_kind,
                modbus_dec=dec,
                rw=p.rw,
                hint=p.hint,
                d_index=new_d_index,
            )
        )
    return out
