"""
夹爪电机槽位（最多 99 路）配置规范化。

yaml 结构：
  grippers:
    max_motors: 99
    motor_count: 2          # HMI 选用数量 1～99
    load_index: 1           # 上料工位绑定的电机序号 → ctx.gripper1
    unload_index: 2         # 下料工位绑定的电机序号 → ctx.gripper2
    motors:
      "1": { interface, can_id, gripper_type, open_speed, close_speed, use_mock, label }
      "2": ...
    gripper1 / gripper2:    # 与 load/unload 同步的别名（兼容旧代码）

使用：
  from devices.gripper_bank import normalize_grippers_cfg, motor_cfg, sync_role_aliases
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping, Optional

MAX_MOTORS = 99
DEFAULT_GRIPPER_TYPE = 2
DEFAULT_OPEN_SPEED = 50.0
DEFAULT_CLOSE_SPEED = 50.0


def _default_motor(index: int) -> Dict[str, Any]:
    """未填写时的空槽默认值（不强制接真机）。"""
    # 1/2 给现场常用默认；其余槽给占位 can_id，避免全 0 冲突
    if index == 1:
        return {
            "label": "上料",
            "interface": "can0",
            "can_id": 0x103,
            "gripper_type": DEFAULT_GRIPPER_TYPE,
            "open_speed": DEFAULT_OPEN_SPEED,
            "close_speed": DEFAULT_CLOSE_SPEED,
            "use_mock": True,
        }
    if index == 2:
        return {
            "label": "下料",
            "interface": "can1",
            "can_id": 0x101,
            "gripper_type": DEFAULT_GRIPPER_TYPE,
            "open_speed": 100.0,
            "close_speed": DEFAULT_CLOSE_SPEED,
            "use_mock": True,
        }
    return {
        "label": f"电机{index}",
        "interface": "can0",
        "can_id": 0x100 + int(index),
        "gripper_type": DEFAULT_GRIPPER_TYPE,
        "open_speed": DEFAULT_OPEN_SPEED,
        "close_speed": DEFAULT_CLOSE_SPEED,
        "use_mock": True,
    }


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _motor_key(index: int) -> str:
    return str(int(index))


def motor_cfg(grippers: Mapping[str, Any], index: int) -> Dict[str, Any]:
    """取第 index 路电机配置（1-based）；缺失则返回默认副本。"""
    motors = grippers.get("motors") if isinstance(grippers.get("motors"), dict) else {}
    raw = motors.get(_motor_key(index)) or motors.get(index)
    if isinstance(raw, dict):
        out = _default_motor(index)
        out.update(raw)
        return out
    return _default_motor(index)


def sync_role_aliases(grippers: MutableMapping[str, Any]) -> None:
    """把 load_index / unload_index 对应电机同步到 gripper1 / gripper2。"""
    load_i = _as_int(grippers.get("load_index", 1), 1)
    unload_i = _as_int(grippers.get("unload_index", 2), 2)
    count = _as_int(grippers.get("motor_count", 2), 2)
    count = max(1, min(MAX_MOTORS, count))
    load_i = max(1, min(count, load_i))
    unload_i = max(1, min(count, unload_i))
    grippers["load_index"] = load_i
    grippers["unload_index"] = unload_i
    grippers["gripper1"] = dict(motor_cfg(grippers, load_i))
    grippers["gripper2"] = dict(motor_cfg(grippers, unload_i))


def normalize_grippers_cfg(cfg: MutableMapping[str, Any]) -> Dict[str, Any]:
    """
    规范化 grippers：兼容旧 yaml（仅 gripper1/2），补齐 motor_count / motors。
    返回规范化后的 grippers 字典（已写回 cfg['grippers']）。
    """
    raw = cfg.get("grippers")
    if not isinstance(raw, dict):
        raw = {}
        cfg["grippers"] = raw

    raw["max_motors"] = MAX_MOTORS

    # 旧格式：只有 gripper1/gripper2
    has_motors = isinstance(raw.get("motors"), dict) and bool(raw.get("motors"))
    if not has_motors:
        motors: Dict[str, Any] = {}
        g1 = raw.get("gripper1") if isinstance(raw.get("gripper1"), dict) else {}
        g2 = raw.get("gripper2") if isinstance(raw.get("gripper2"), dict) else {}
        m1 = _default_motor(1)
        m1.update(g1)
        m1.setdefault("label", "上料")
        m2 = _default_motor(2)
        m2.update(g2)
        m2.setdefault("label", "下料")
        motors["1"] = m1
        motors["2"] = m2
        raw["motors"] = motors
        raw.setdefault("motor_count", 2)
        raw.setdefault("load_index", 1)
        raw.setdefault("unload_index", 2)
    else:
        motors = raw["motors"]
        # 统一键为字符串
        fixed: Dict[str, Any] = {}
        for k, v in list(motors.items()):
            if not isinstance(v, dict):
                continue
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= MAX_MOTORS:
                base = _default_motor(i)
                base.update(v)
                fixed[_motor_key(i)] = base
        raw["motors"] = fixed

    count = _as_int(raw.get("motor_count", len(raw.get("motors") or {}) or 2), 2)
    raw["motor_count"] = max(1, min(MAX_MOTORS, count))

    # 确保 1..motor_count 都有条目（便于 HMI 填地址）
    motors = raw.setdefault("motors", {})
    for i in range(1, int(raw["motor_count"]) + 1):
        key = _motor_key(i)
        if key not in motors:
            motors[key] = _default_motor(i)

    raw.setdefault("load_index", 1)
    raw.setdefault("unload_index", 2 if raw["motor_count"] >= 2 else 1)
    sync_role_aliases(raw)
    return raw


def write_motor(grippers: MutableMapping[str, Any], index: int, data: Mapping[str, Any]) -> None:
    """写入第 index 路并刷新角色别名。"""
    i = max(1, min(MAX_MOTORS, int(index)))
    motors = grippers.setdefault("motors", {})
    cur = motor_cfg(grippers, i)
    cur.update(dict(data))
    motors[_motor_key(i)] = cur
    sync_role_aliases(grippers)


def role_motor_index(grippers: Mapping[str, Any], role: str) -> int:
    """role: 'load' | 'unload' → 电机序号。"""
    if role == "unload":
        return _as_int(grippers.get("unload_index", 2), 2)
    return _as_int(grippers.get("load_index", 1), 1)
