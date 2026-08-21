"""皮带 cam1：从画面量「抓取中心 → 鞋头」长度，供每只鞋改鞋头 TCP。

抓取后 TCP 在抓取系下，鞋头沿 +Y。不同鞋长 Y 不同，不能共用同一个 TCP。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

@dataclass
class ToeMeasure:
    ok: bool
    offset_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    length_mm: float = 0.0
    length_px: float = 0.0
    grasp_uv: Tuple[float, float] = (0.0, 0.0)
    toe_uv: Tuple[float, float] = (0.0, 0.0)
    message: str = ""


def belt_toe_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    vis = cfg.get("vision") if isinstance(cfg, dict) else None
    if not isinstance(vis, dict):
        vis = cfg if isinstance(cfg, dict) else {}
    blk = vis.get("belt_toe") if isinstance(vis, dict) else None
    return blk if isinstance(blk, dict) else {}


def estimate_grasp_to_toe(
    crop_bgr,
    grasp_xy: Tuple[float, float],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> ToeMeasure:
    """
    在 ROI 裁剪图上找抓取点、鞋头像素。长度换算在 pixel_to_robot（示教器毫米）。
    """
    gx, gy = float(grasp_xy[0]), float(grasp_xy[1])
    if cv2 is None or crop_bgr is None:
        return ToeMeasure(ok=False, grasp_uv=(gx, gy), message="无图像/无OpenCV")
    blk = belt_toe_cfg(cfg)

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 鞋在皮带上可能深色或浅色，取面积更大的那一极
    inv = cv2.bitwise_not(mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    cands = []
    for m in (mask, inv):
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel, iterations=1)
        cands.append(m)

    best = None
    best_d = 1e9
    for m in cands:
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = float(cv2.contourArea(c))
            if area < 800:
                continue
            dist = abs(cv2.pointPolygonTest(c, (gx, gy), True))
            inside = cv2.pointPolygonTest(c, (gx, gy), False) >= 0
            score = dist if inside else dist + 200.0
            if score < best_d:
                best_d = score
                best = c

    if best is None or len(best) < 5:
        return ToeMeasure(
            ok=False,
            grasp_uv=(gx, gy),
            message="画面里没找到包住抓取点的鞋轮廓",
        )

    rect = cv2.minAreaRect(best.astype(np.float32))
    (cx, cy), (rw, rh), ang = rect
    if rw < rh:
        length_px, width_px = float(rh), float(rw)
        axis_deg = float(ang) + 90.0
    else:
        length_px, width_px = float(rw), float(rh)
        axis_deg = float(ang)
    rad = np.deg2rad(axis_deg)
    direc = np.array([np.cos(rad), np.sin(rad)], dtype=float)
    center = np.array([float(cx), float(cy)], dtype=float)
    end_a = center + direc * (length_px * 0.5)
    end_b = center - direc * (length_px * 0.5)

    def _end_width(pt) -> float:
        pts = best.reshape(-1, 2).astype(np.float32)
        along = (pts - pt.reshape(1, 2)) @ direc
        near = pts[np.abs(along) < max(8.0, length_px * 0.18)]
        if len(near) < 4:
            return float(width_px)
        v = near - pt.reshape(1, 2)
        nrm = np.array([-direc[1], direc[0]])
        return float(np.std(v @ nrm) * 2.5)

    wa, wb = _end_width(end_a), _end_width(end_b)
    toe = end_a if wa <= wb else end_b  # 较窄一端为鞋头
    grasp = np.array([gx, gy], dtype=float)
    rel = toe - grasp
    nlen = float(np.linalg.norm(rel))
    if nlen < 3.0:
        return ToeMeasure(
            ok=False,
            grasp_uv=(gx, gy),
            toe_uv=(float(toe[0]), float(toe[1])),
            message="抓取点几乎就在鞋头，量不出长度",
        )
    # 像素系：+Y 沿鞋长轴指向鞋头；毫米换算交给机器人坐标
    axis = direc if float(rel @ direc) >= 0.0 else -direc
    perp = np.array([-axis[1], axis[0]], dtype=float)
    y_px = float(rel @ axis)
    x_px = float(rel @ perp)
    if not bool(blk.get("keep_lateral_mm", False)):
        x_px = 0.0
    return ToeMeasure(
        ok=True,
        offset_xyz=[float(x_px), float(y_px), 0.0],
        length_px=float(nlen),
        grasp_uv=(gx, gy),
        toe_uv=(float(toe[0]), float(toe[1])),
        message=f"像素距={nlen:.1f}px",
    )
