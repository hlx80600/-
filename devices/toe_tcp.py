"""鞋头 TCP：抓取中心 → 鞋头 刚性偏移，切换工具坐标到鞋头。

工艺：
  皮带拍照得到 toe_offset_in_grasp_tcp（鞋头在抓取 TCP 坐标系下的 mm）
  夹紧后先保持抓取 TCP 抬起/退出（避免同笛卡尔点换 TCP 奇异）
  退至 pick_entry 后再把工具 TCP 改成鞋头，供放料对位使用。
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)


def _as6(tcp: Sequence[float]) -> List[float]:
    v = [float(x) for x in list(tcp)[:6]]
    while len(v) < 6:
        v.append(0.0)
    return v


def _rot_xyz_deg(rx: float, ry: float, rz: float) -> np.ndarray:
    """欧拉角 xyz（度）→ 3x3 旋转矩阵（与常见法兰 TCP 约定一致）。"""
    ax, ay, az = map(math.radians, (rx, ry, rz))
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx_m = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=float)
    ry_m = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=float)
    rz_m = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=float)
    return rz_m @ ry_m @ rx_m


def compose_toe_tcp_from_grasp(
    grasp_tcp_flange: Sequence[float],
    toe_offset_in_grasp_tcp: Sequence[float],
) -> List[float]:
    """
    抓取 TCP（相对法兰）+ 鞋头在抓取系下的偏移 → 鞋头 TCP（相对法兰）。
    姿态沿用抓取 TCP 的 rx/ry/rz。
    """
    g = _as6(grasp_tcp_flange)
    off = [float(x) for x in list(toe_offset_in_grasp_tcp)[:3]]
    while len(off) < 3:
        off.append(0.0)
    rot = _rot_xyz_deg(g[3], g[4], g[5])
    p = np.asarray(g[:3], dtype=float) + rot @ np.asarray(off, dtype=float)
    return [float(p[0]), float(p[1]), float(p[2]), g[3], g[4], g[5]]


def offset_from_snapshot(snap: Optional[dict]) -> Optional[List[float]]:
    if not isinstance(snap, dict):
        return None
    raw = snap.get("toe_offset_in_grasp_tcp")
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    return [float(raw[0]), float(raw[1]), float(raw[2])]


def _grasp_tcp_from_profiles(robot) -> List[float]:
    """抓取中心 TCP（相对法兰）：优先 empty.tcp，否则备份/with_shoe。"""
    profiles = robot.payload_profiles()
    grasp = list((profiles.get("empty") or {}).get("tcp") or [0, 0, 0, 0, 0, 0])
    if all(abs(v) < 1e-9 for v in grasp[:3]):
        # 避免沿用上一拍已改成鞋头的 with_shoe.tcp
        stored = getattr(robot, "_grasp_tcp_backup", None)
        if isinstance(stored, (list, tuple)) and len(stored) >= 6:
            grasp = [float(x) for x in stored[:6]]
        else:
            grasp = list((profiles.get("with_shoe") or {}).get("tcp") or grasp)
    return _as6(grasp)


def apply_holding_keep_grasp_tcp(ctx, robot_key: str = "robot1") -> List[float]:
    """
    夹紧后立刻调用：只切换抓鞋负载（质量/质心），工具号与 TCP 不变。

    ★ 下降取料用工具1；若夹紧后立刻改工具号/TCP，下一拍 MoveL 抬起会按新工具逆解，
      易报「关节指令点错误」或奇异。抬起、退回 pick_entry 完成后再 apply_toe_tcp。
    """
    robot = ctx.robot1 if robot_key == "robot1" else ctx.robot2
    grasp = _grasp_tcp_from_profiles(robot)
    robot._grasp_tcp_backup = list(grasp)

    profiles = robot.payload_profiles()
    shoe = dict(profiles.get("with_shoe") or {})
    empty = dict(profiles.get("empty") or {})
    # 配置里 with_shoe.tcp 先保持抓取系，避免下一拍误用鞋头值；工具号仍用 empty（取料路径）
    keep_tool = int(empty.get("tool", robot.tool or 1))

    rcfg = ctx.cfg.setdefault("robots", {}).setdefault(robot_key, {})
    payloads = rcfg.setdefault("payloads", {})
    slot = payloads.setdefault("with_shoe", {})
    slot["tcp"] = [float(v) for v in grasp]
    slot["load_num"] = int(slot.get("load_num", shoe.get("load_num", 2)))
    # 取料抬起阶段不改工具号；鞋头阶段再切 with_shoe.tool
    robot.apply_payload_cfg(payloads)

    # 只下发负载2，工具号保持取料时的工具1
    load_profile = dict(shoe)
    load_profile["tcp"] = [float(v) for v in grasp]
    if hasattr(robot, "_write_payload_to_controller"):
        robot._write_payload_to_controller(load_profile)
    robot.tool = keep_tool
    robot._payload_mode = "with_shoe"
    ctx.gvl.ToeTcpActive = False
    log.info(
        "[%s] 抓鞋负载已切(load=%s)，工具号保持 %s / 抓取TCP=%s（退出取料区后再切鞋头工具）",
        robot.name,
        load_profile.get("load_num"),
        keep_tool,
        grasp,
    )
    return list(grasp)


def apply_toe_tcp_after_grasp(ctx, robot_key: str = "robot1") -> List[float]:
    """
    计算并下发鞋头 TCP 到工具2列表；默认**不切换运动工具号**。

    法奥在「工具1终点 → 立刻用工具2 MoveJ/MoveL」会报 154/74。
    因此默认只写入工具2坐标系备查，运动仍用工具1（与 place_slot 示教一致）；
    真正鞋头伺服对位接入后，再在 yaml 打开 toe_tcp_switch_motion_tool。
    """
    robot = ctx.robot1 if robot_key == "robot1" else ctx.robot2
    snap = getattr(ctx.gvl, "BeltPickSnapshot", None)
    off = offset_from_snapshot(snap)
    grasp = _grasp_tcp_from_profiles(robot)
    if off is None:
        profiles = robot.payload_profiles()
        toe = list((profiles.get("with_shoe") or {}).get("tcp") or grasp)
        log.warning(
            "[%s] 快照无 toe_offset_in_grasp_tcp，沿用当前 with_shoe.tcp=%s",
            robot.name,
            toe,
        )
    else:
        toe = compose_toe_tcp_from_grasp(grasp, off)
        length = 0.0
        if isinstance(snap, dict):
            length = float(snap.get("shoe_length_mm") or 0.0)
        log.info(
            "[%s] 鞋头TCP: grasp=%s 鞋长=%.1fmm offset=%s → toe=%s",
            robot.name,
            grasp,
            length if length > 0 else (off[0] ** 2 + off[1] ** 2) ** 0.5,
            off,
            toe,
        )

    rcfg = ctx.cfg.setdefault("robots", {}).setdefault(robot_key, {})
    payloads = rcfg.setdefault("payloads", {})
    slot = payloads.setdefault("with_shoe", {})
    slot["tcp"] = [float(v) for v in toe]
    slot["load_num"] = int(slot.get("load_num", 2))
    slot["tool"] = int(slot.get("tool", 2))
    robot.apply_payload_cfg(payloads)

    # 始终把鞋头 TCP 写入工具2列表，但不要 SetToolCoord 激活工具2
    # （否则控制器当前工具变2，随后 MoveL(tool=1) → err=74 工具不符）
    profile = dict(robot.payload_profiles().get("with_shoe") or {})
    profile["tcp"] = [float(v) for v in toe]
    profile["tool"] = int(profile.get("tool", 2))
    if hasattr(robot, "_write_tool_tcp_to_controller"):
        try:
            robot._write_tool_tcp_to_controller(profile, activate=False)
        except Exception as e:
            log.warning("[%s] 下发鞋头TCP到工具2列表失败(忽略): %s", robot.name, e)

    # 保持抓鞋负载，运动工具默认仍为工具1，并重新激活工具1 TCP
    if hasattr(robot, "_write_payload_to_controller"):
        robot._write_payload_to_controller(profile)
    empty = robot.payload_profiles().get("empty") or {}
    keep_tool = int(empty.get("tool", 1))
    switch_motion = bool(
        (ctx.cfg.get("vision") or {}).get("toe_tcp_switch_motion_tool", False)
    )
    if switch_motion:
        robot.set_holding_shoe(True, force=True)
        robot.tool = int(profile.get("tool", 2))
        log.warning(
            "[%s] 已切换运动工具→%s（toe_tcp_switch_motion_tool=true，后续须关节同步）",
            robot.name,
            robot.tool,
        )
    else:
        robot.tool = keep_tool
        robot._payload_mode = "with_shoe"
        # 重新激活工具1，保证控制器当前工具与 Move 参数一致
        empty_prof = dict(empty)
        empty_prof.setdefault("load_num", 1)
        empty_prof.setdefault("tool", keep_tool)
        if hasattr(robot, "_write_tool_tcp_to_controller") and robot._tcp_nontrivial(
            empty_prof.get("tcp")
        ):
            try:
                robot._write_tool_tcp_to_controller(empty_prof, activate=True)
            except Exception as e:
                log.warning("[%s] 重新激活工具1 TCP 失败: %s", robot.name, e)
        log.info(
            "[%s] 鞋头TCP已写入工具2列表=%s；运动仍用工具%s（已重新激活）",
            robot.name,
            toe,
            keep_tool,
        )

    ctx.gvl.ToeTcpActive = True
    ctx.gvl.ActiveToeTcp = [float(v) for v in toe]
    return [float(v) for v in toe]


def restore_grasp_or_empty_tcp(ctx, robot_key: str = "robot1") -> None:
    """放料张爪后：恢复未抓鞋工具1（手爪 TCP）。"""
    robot = ctx.robot1 if robot_key == "robot1" else ctx.robot2
    # 把 with_shoe.tcp 恢复为抓取备份，避免下一拍夹紧瞬间又带上鞋头 TCP
    backup = getattr(robot, "_grasp_tcp_backup", None)
    if isinstance(backup, (list, tuple)) and len(backup) >= 6:
        rcfg = ctx.cfg.setdefault("robots", {}).setdefault(robot_key, {})
        payloads = rcfg.setdefault("payloads", {})
        slot = payloads.setdefault("with_shoe", {})
        slot["tcp"] = [float(v) for v in backup[:6]]
        robot.apply_payload_cfg(payloads)
    robot.set_holding_shoe(False, force=True)
    ctx.gvl.ToeTcpActive = False
    log.info("[%s] 已恢复工具1（手爪），鞋头 TCP 模式关闭", robot.name)
