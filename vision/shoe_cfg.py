"""读写皮带生产配置 shoe_vision_config.json（内参 / ROI / 手眼 / 模型路径）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "shoe_vision_config.json"

# 旧机出厂矩阵平移（本机必须重标；用于 HMI 提示）
LEGACY_TVEC = (726.28, -534.433, 563.337)


def cfg_path(vis_cfg: Optional[dict] = None) -> Path:
    raw = None
    if isinstance(vis_cfg, dict):
        blk = vis_cfg.get("shoe_vision")
        if isinstance(blk, dict):
            raw = blk.get("config")
        if not raw and isinstance(vis_cfg.get("vision"), dict):
            blk = vis_cfg["vision"].get("shoe_vision")
            if isinstance(blk, dict):
                raw = blk.get("config")
    p = Path(str(raw)) if raw else DEFAULT_PATH
    if not p.is_absolute():
        p = ROOT / p
    return p


def load(vis_cfg: Optional[dict] = None) -> dict:
    path = cfg_path(vis_cfg)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save(data: dict, vis_cfg: Optional[dict] = None) -> Path:
    path = cfg_path(vis_cfg)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def update(patch: dict, vis_cfg: Optional[dict] = None) -> Path:
    data = load(vis_cfg)
    _deep_update(data, patch)
    return save(data, vis_cfg)


def _deep_update(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def relpath(path: Path) -> str:
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def k_from_calib(calib: dict) -> Optional[dict]:
    """棋盘格内参文件 → json 的 camera.fx/fy/cx/cy。"""
    K = calib.get("K") if isinstance(calib, dict) else None
    if not isinstance(K, (list, tuple)) or len(K) < 2:
        return None
    try:
        fx = float(K[0][0])
        fy = float(K[1][1])
        cx = float(K[0][2])
        cy = float(K[1][2])
    except (TypeError, ValueError, IndexError):
        return None
    if fx < 1.0 or fy < 1.0:
        return None
    return {"fx": fx, "fy": fy, "cx": cx, "cy": cy}


def camera_k(data: Optional[dict] = None) -> Optional[dict]:
    cam = (data or {}).get("camera") if isinstance(data, dict) else None
    if not isinstance(cam, dict):
        return None
    try:
        fx, fy = float(cam["fx"]), float(cam["fy"])
        cx, cy = float(cam["cx"]), float(cam["cy"])
    except (KeyError, TypeError, ValueError):
        return None
    if fx < 1.0 or fy < 1.0:
        return None
    return {"fx": fx, "fy": fy, "cx": cx, "cy": cy}


def roi_pixels_to_ratio(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> list[float]:
    iw = max(1, int(img_w))
    ih = max(1, int(img_h))
    x0 = max(0.0, min(1.0, float(x) / iw))
    y0 = max(0.0, min(1.0, float(y) / ih))
    x1 = max(0.0, min(1.0, float(x + w) / iw))
    y1 = max(0.0, min(1.0, float(y + h) / ih))
    if x1 <= x0:
        x1 = min(1.0, x0 + 0.02)
    if y1 <= y0:
        y1 = min(1.0, y0 + 0.02)
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def mat_list(T) -> list:
    arr = T
    if hasattr(T, "tolist"):
        arr = T.tolist()
    out = []
    for row in arr:
        out.append([float(v) for v in row])
    return out


def handeye_tvec(data: Optional[dict] = None) -> Optional[tuple[float, float, float]]:
    he = (data or {}).get("handeye") if isinstance(data, dict) else None
    if not isinstance(he, dict):
        return None
    mat = he.get("mat")
    if not isinstance(mat, (list, tuple)) or len(mat) < 3:
        return None
    try:
        return (float(mat[0][3]), float(mat[1][3]), float(mat[2][3]))
    except (TypeError, ValueError, IndexError):
        return None


def is_legacy_handeye(data: Optional[dict] = None, *, tol_mm: float = 2.0) -> bool:
    t = handeye_tvec(data)
    if t is None:
        return False
    return all(abs(a - b) <= tol_mm for a, b in zip(t, LEGACY_TVEC))


def write_intrinsics(k: dict, vis_cfg: Optional[dict] = None) -> Path:
    return update({"camera": {kk: float(k[kk]) for kk in ("fx", "fy", "cx", "cy")}}, vis_cfg)


def write_roi_ratio(ratio: Sequence[float], vis_cfg: Optional[dict] = None) -> Path:
    return update({"roi_ratio": [float(v) for v in ratio]}, vis_cfg)


def write_handeye_mat(T, vis_cfg: Optional[dict] = None) -> Path:
    return update({"handeye": {"mat": mat_list(T)}}, vis_cfg)


def write_model_key(json_key: str, rel: str, vis_cfg: Optional[dict] = None) -> Path:
    return update({json_key: rel}, vis_cfg)
