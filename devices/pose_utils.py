"""位姿工具：字典 <-> 列表，偏移叠加；可选示教关节角 j1..j6。"""

from __future__ import annotations

from typing import Dict, List, Optional


POSE_AXES = ("x", "y", "z", "rx", "ry", "rz")
JOINT_AXES = ("j1", "j2", "j3", "j4", "j5", "j6")


def numeric_pose(pose: Dict) -> Dict[str, float]:
    """只取 XYZRxRyRz，去掉 name/关节等字段，避免误传。"""
    return {k: float(pose.get(k, 0)) for k in POSE_AXES}


def extract_joints(pose: Dict | None) -> Optional[List[float]]:
    """
    从点位字典取示教关节角 [°]。
    支持 j1..j6，或 joints: [j1..j6]。缺任一轴则返回 None。
    """
    if not isinstance(pose, dict):
        return None
    raw = pose.get("joints")
    if isinstance(raw, (list, tuple)) and len(raw) == 6:
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
    if all(k in pose and pose[k] is not None for k in JOINT_AXES):
        try:
            return [float(pose[k]) for k in JOINT_AXES]
        except (TypeError, ValueError):
            return None
    return None


def joints_to_dict(joints: List[float]) -> Dict[str, float]:
    return {k: float(joints[i]) for i, k in enumerate(JOINT_AXES)}


def has_taught_joints(pose: Dict | None) -> bool:
    return extract_joints(pose) is not None


def pose_to_list(pose: Dict) -> List[float]:
    p = numeric_pose(pose)
    return [p[k] for k in POSE_AXES]


def list_to_pose(vals: List[float]) -> Dict[str, float]:
    return {k: float(vals[i]) for i, k in enumerate(POSE_AXES)}


def apply_offset(base: Dict, offset: Dict) -> Dict[str, float]:
    out = numeric_pose(base)
    off = numeric_pose(offset)
    for k in POSE_AXES:
        out[k] = out[k] + off[k]
    return out


def is_left_shoe_flag(value, default: bool = True) -> bool:
    """
    严格解析左右鞋标志。
    避免 bool(\"false\")==True 这类把右鞋误判成左鞋。
    """
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("0", "false", "no", "n", "right", "r", "右", "右鞋"):
            return False
        if s in ("1", "true", "yes", "y", "left", "l", "左", "左鞋"):
            return True
        return bool(default)
    return bool(default)


def apply_xyr(base: Dict, x: float, y: float, rz: float) -> Dict[str, float]:
    """视觉只改 XYR（末端旋转角用 rz）。"""
    out = numeric_pose(base)
    out["x"] = float(x)
    out["y"] = float(y)
    out["rz"] = float(rz)
    return out


# 点位英文键 → 默认中文名（按机器人分开，避免上料/下料混淆）
DEFAULT_POINT_NAMES_R1: Dict[str, str] = {
    "home": "【上料R1】初始位（回零/待机）",
    "pick_entry": "【上料R1】皮带取料进入点",
    "pick_above_offset": "【上料R1】皮带取料上方偏移",
    "place_entry": "【上料R1】鞋槽放料进入点",
    "place_slot": "【上料R1】鞋槽放料点",
    "place_above_offset": "【上料R1】鞋槽放料上方偏移",
}
DEFAULT_POINT_NAMES_R2: Dict[str, str] = {
    "home": "【下料R2】初始位（回零/待机）",
    "slot_pick_entry": "【下料R2】鞋槽取料进入点",
    "slot_pick": "【下料R2】鞋槽取料点（固定示教）",
    "slot_pick_above_offset": "【下料R2】鞋槽取料上方偏移",
    "belt_place_entry": "【下料R2】皮带放料进入点",
    "belt_place": "【下料R2】皮带放料点（固定示教）",
    "belt_place_above_offset": "【下料R2】皮带放料上方偏移",
}
# 兼容旧调用
DEFAULT_POINT_NAMES: Dict[str, str] = {**DEFAULT_POINT_NAMES_R1, **DEFAULT_POINT_NAMES_R2}


def point_display_name(
    point_key: str,
    pose: Dict | None = None,
    robot_key: str | None = None,
) -> str:
    """HMI 显示用中文备注；优先 yaml.name，否则按 robot1/robot2 默认表。"""
    if isinstance(pose, dict):
        n = pose.get("name")
        if n is not None and str(n).strip():
            return str(n).strip()
    if robot_key == "robot1":
        return DEFAULT_POINT_NAMES_R1.get(point_key, f"【上料R1】{point_key}")
    if robot_key == "robot2":
        return DEFAULT_POINT_NAMES_R2.get(point_key, f"【下料R2】{point_key}")
    return DEFAULT_POINT_NAMES.get(point_key, point_key)


# 流程核心点配置键（过渡点命名不可覆盖这些键）
CORE_POINT_KEYS = frozenset(
    {
        "home",
        "pick_entry",
        "pick_above_offset",
        "place_entry",
        "place_slot",
        "place_above_offset",
        "slot_pick_entry",
        "slot_pick",
        "slot_pick_above_offset",
        "belt_place_entry",
        "belt_place",
        "belt_place_above_offset",
    }
)


def normalize_via_name(raw: str) -> str:
    """过渡点显示名/键：允许中文；去首尾空白。"""
    return (raw or "").strip()


def validate_via_name(name: str) -> str | None:
    """合法返回 None；否则返回错误说明。"""
    if not name:
        return "名称不能为空"
    if len(name) > 64:
        return "名称过长（最多64字）"
    # 禁止路径/yaml 易出问题的字符
    bad = set('\\/:*?"<>|\n\r\t')
    if any(c in bad for c in name):
        return "名称不能包含 \\ / : * ? \" < > | 或换行"
    if name in CORE_POINT_KEYS:
        return f"「{name}」是流程核心点配置键，请换一个名称"
    return None


def resolve_via_point_key(pts: Dict, name: str) -> tuple[str, bool]:
    """
    按名称解析过渡点配置键。
    - 键名 == name，或某点的 name 字段 == name → 视为同一点（更新）
    - 否则新建，配置键直接用该名称（可中文）
    返回 (config_key, is_update)。
    """
    name = normalize_via_name(name)
    if not isinstance(pts, dict):
        return name, False
    if name in pts:
        return name, True
    for k, v in pts.items():
        if not isinstance(v, dict):
            continue
        dn = str(v.get("name") or "").strip()
        if dn == name:
            return str(k), True
    return name, False
