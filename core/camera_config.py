"""从 yaml / shoe_vision_config 解析每路相机目标帧率与分辨率。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple


def resolve_camera_fps(key: str, ccfg: Dict[str, Any], cfg: Dict[str, Any]) -> int:
    raw = ccfg.get("fps")
    if raw is not None:
        return max(1, int(raw))
    try:
        vis = cfg.get("vision") or {}
        shoe = vis.get("shoe_vision") if isinstance(vis.get("shoe_vision"), dict) else {}
        sv_path = shoe.get("config") or "shoe_vision_config.json"
        p = Path(str(sv_path))
        if not p.is_file():
            p = Path(cfg.get("_config_dir", ".")) / str(sv_path)
        if p.is_file():
            ob = json.loads(p.read_text(encoding="utf-8")).get("orbbec") or {}
            if key == str(ob.get("cam_id") or "cam1"):
                return max(1, int(ob.get("fps") or 30))
    except Exception:
        pass
    return 30


def resolve_camera_color_res(ccfg: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[int, int]:
    raw = ccfg.get("color_res")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return max(1, int(raw[0])), max(1, int(raw[1]))
    try:
        vis = cfg.get("vision") or {}
        shoe = vis.get("shoe_vision") if isinstance(vis.get("shoe_vision"), dict) else {}
        sv_path = shoe.get("config") or "shoe_vision_config.json"
        p = Path(str(sv_path))
        if not p.is_file():
            p = Path(cfg.get("_config_dir", ".")) / str(sv_path)
        if p.is_file():
            ob = json.loads(p.read_text(encoding="utf-8")).get("orbbec") or {}
            cr = ob.get("color_res")
            if isinstance(cr, (list, tuple)) and len(cr) >= 2:
                return max(1, int(cr[0])), max(1, int(cr[1]))
    except Exception:
        pass
    return 0, 0


def preview_interval_ms(cfg: Dict[str, Any], fps: int, *, inactive: bool = False) -> int:
    """UI 刷新间隔（ms），上限由 system.hmi.preview_max_fps 控制。

    inactive=True：程序失焦时用 preview_inactive_fps，避免占满事件循环导致切窗卡顿。
    """
    hmi = (cfg.get("system") or {}).get("hmi") or {}
    if inactive:
        cap = max(1, int(hmi.get("preview_inactive_fps", 5)))
    else:
        cap = max(15, int(hmi.get("preview_max_fps", 60)))
    use_fps = min(max(1, int(fps or 30)), cap)
    return max(8, int(1000 / use_fps))
