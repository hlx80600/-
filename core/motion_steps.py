"""
程序步运动参数（速度/加速度/平滑）。

★ 键与发令锁存一致：s{站}a{Auto}_{步}，例如 s2a10_30、s2a10_90。
  同一示教点 pick_entry 在「进入」与「退出」两步可有不同 vel/blend。
★ 实际速度 ≈ 监视页全局 SetSpeed% × 本步 vel%。
★ MoveJ 的 acc 法奥文档暂不开放；仍保存并传入，MoveL 用 oacc。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from stations.step_catalog import AUTO_TITLES, STEP_CATALOG, auto_title


def step_key(station_no: int, auto_key: int, step: int) -> str:
    return f"s{int(station_no)}a{int(auto_key)}_{int(step)}"


def parse_step_key(key: str) -> Optional[Tuple[int, int, int]]:
    """s2a10_30 → (2, 10, 30)。"""
    try:
        if not key.startswith("s"):
            return None
        rest = key[1:]
        a_idx = rest.index("a")
        station = int(rest[:a_idx])
        rest2 = rest[a_idx + 1 :]
        u_idx = rest2.index("_")
        auto = int(rest2[:u_idx])
        step = int(rest2[u_idx + 1 :])
        return station, auto, step
    except (ValueError, IndexError):
        return None


def default_entry_for_kind(kind: str) -> Dict[str, Any]:
    """新建步参数默认值。取放到位步默认不平滑。"""
    precise_like = kind == "move_l"  # 工作点多为 MoveL+precise；进入多为 MoveJ
    return {
        "vel": 100.0,
        "acc": 100.0,
        "blend": False if precise_like else True,
    }


def list_move_steps() -> List[Dict[str, Any]]:
    """HMI 列表：所有 move_j / move_l 程序步。"""
    rows: List[Dict[str, Any]] = []
    for st_no, autos in sorted(STEP_CATALOG.items()):
        for auto_key, steps in sorted(autos.items()):
            for s in steps:
                kind = str(s.get("kind") or "")
                if kind not in ("move_j", "move_l"):
                    continue
                step = int(s["step"])
                key = step_key(st_no, auto_key, step)
                robot = str(s.get("robot") or "")
                pts = s.get("points") or []
                rows.append(
                    {
                        "key": key,
                        "station": st_no,
                        "auto": auto_key,
                        "step": step,
                        "kind": kind,
                        "title": str(s.get("title") or ""),
                        "detail": str(s.get("detail") or ""),
                        "robot": robot,
                        "points": list(pts),
                        "auto_title": auto_title(st_no, auto_key),
                        "station_title": AUTO_TITLES.get(st_no, {}).get(
                            auto_key, f"Station{st_no}"
                        ),
                    }
                )
    # 初始化回零
    rows.append(
        {
            "key": "init_r1_home",
            "station": 0,
            "auto": 0,
            "step": 0,
            "kind": "move_j",
            "title": "初始化→R1 home",
            "detail": "上电回零",
            "robot": "robot1",
            "points": ["home"],
            "auto_title": "初始化",
            "station_title": "Init",
        }
    )
    rows.append(
        {
            "key": "init_r2_home",
            "station": 0,
            "auto": 0,
            "step": 0,
            "kind": "move_j",
            "title": "初始化→R2 home",
            "detail": "上电回零",
            "robot": "robot2",
            "points": ["home"],
            "auto_title": "初始化",
            "station_title": "Init",
        }
    )
    return rows


def ensure_motion_steps(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """保证 cfg['motion_steps'] 含全部程序运动步；已有项不覆盖。"""
    store = cfg.setdefault("motion_steps", {})
    if not isinstance(store, dict):
        store = {}
        cfg["motion_steps"] = store
    for row in list_move_steps():
        key = row["key"]
        if key not in store or not isinstance(store.get(key), dict):
            store[key] = default_entry_for_kind(row["kind"])
            # 到位步（标题含取料点/放料点/引导）默认不平滑
            title = row["title"]
            if any(x in title for x in ("取料点", "放料点", "引导", "下压", "slot_pick", "belt_place", "place_slot")):
                store[key]["blend"] = False
            elif "进入" in title or "上方" in title or "抬起" in title or "退出" in title:
                store[key]["blend"] = True
    return store


def read_step_motion(cfg: Dict[str, Any], key: Optional[str]) -> Dict[str, Any]:
    """
    返回传给 robot.move_* 的 kwargs：
    vel, acc, blend, 可选 blend_t_ms / blend_r_mm。
    """
    ensure_motion_steps(cfg)
    if not key:
        return {"vel": 100.0, "acc": 100.0, "blend": False}
    raw = (cfg.get("motion_steps") or {}).get(key)
    if not isinstance(raw, dict):
        return {"vel": 100.0, "acc": 100.0, "blend": False}
    out: Dict[str, Any] = {
        "vel": _clamp(raw.get("vel", 100), 1.0, 100.0),
        "acc": _clamp(raw.get("acc", 100), 0.0, 100.0),
        "blend": bool(raw.get("blend", False)),
    }
    if raw.get("blend_t_ms") is not None:
        try:
            out["blend_t_ms"] = float(raw["blend_t_ms"])
        except (TypeError, ValueError):
            pass
    if raw.get("blend_r_mm") is not None:
        try:
            out["blend_r_mm"] = float(raw["blend_r_mm"])
        except (TypeError, ValueError):
            pass
    return out


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = hi
    return max(lo, min(hi, x))
