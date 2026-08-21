"""cam1 像素 → 上料臂基座 XY（与示教器同一套毫米）。

鞋长 = 抓取点、鞋头两点在机器人 XY 上的距离，直接给鞋头 TCP 用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

from vision.calib import load_calib, load_handeye, load_handeye_samples


@dataclass
class RobotSpan:
    ok: bool
    length: float = 0.0  # 机器人毫米
    grasp_xy: Optional[Tuple[float, float]] = None
    toe_xy: Optional[Tuple[float, float]] = None
    mm_per_px: float = 0.0
    method: str = ""
    message: str = ""


def _as_xy(tcp: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(tcp, dict):
        return None
    try:
        return float(tcp.get("x", 0)), float(tcp.get("y", 0))
    except (TypeError, ValueError):
        return None


def _usable_samples(samples: Sequence[dict]) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    pairs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    seen = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        try:
            uv = (float(s.get("pixel_u")), float(s.get("pixel_v")))
        except (TypeError, ValueError):
            continue
        xy = _as_xy(s.get("tcp"))
        if xy is None:
            continue
        dup = False
        for pu, pv in seen:
            if abs(pu - uv[0]) < 4 and abs(pv - uv[1]) < 4:
                dup = True
                break
        if dup:
            continue
        seen.append(uv)
        pairs.append((uv, xy))
    return pairs


def _similarity_3x3(
    uvs: Sequence[Tuple[float, float]], xys: Sequence[Tuple[float, float]]
) -> Optional[np.ndarray]:
    src = np.asarray(uvs, dtype=np.float64).reshape(-1, 2)
    dst = np.asarray(xys, dtype=np.float64).reshape(-1, 2)
    if len(src) < 2:
        return None
    if cv2 is not None and len(src) >= 3:
        M, _ = cv2.estimateAffinePartial2D(src.astype(np.float32), dst.astype(np.float32))
        if M is not None:
            H = np.eye(3, dtype=np.float64)
            H[:2, :] = np.asarray(M, dtype=np.float64)
            return H
    p0, p1 = src[0], src[1]
    q0, q1 = dst[0], dst[1]
    a = p1 - p0
    b = q1 - q0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 8.0 or nb < 1.0:
        return None
    s = nb / na
    ang = float(np.arctan2(b[1], b[0]) - np.arctan2(a[1], a[0]))
    c, si = np.cos(ang), np.sin(ang)
    R = s * np.array([[c, -si], [si, c]], dtype=np.float64)
    t = q0 - R @ p0
    H = np.eye(3, dtype=np.float64)
    H[:2, :2] = R
    H[:2, 2] = t
    return H


def _apply_h(H: np.ndarray, u: float, v: float) -> Tuple[float, float]:
    p = H @ np.array([u, v, 1.0], dtype=np.float64)
    if abs(float(p[2])) > 1e-9:
        p = p / p[2]
    return float(p[0]), float(p[1])


def _scale_of_h(H: np.ndarray) -> float:
    return float(np.hypot(H[0, 0], H[0, 1]))


def _T_matrix(raw: Any) -> Optional[np.ndarray]:
    if not isinstance(raw, (list, tuple)):
        return None
    arr = np.asarray(raw, dtype=np.float64)
    if arr.shape == (4, 4):
        return arr
    if arr.size == 16:
        return arr.reshape(4, 4)
    return None


def _uv_on_plane(
    u: float, v: float, K: Sequence, T_cb: np.ndarray, z_plane: float
) -> Optional[Tuple[float, float]]:
    try:
        fx = float(K[0][0])
        fy = float(K[1][1])
        cx = float(K[0][2])
        cy = float(K[1][2])
    except Exception:
        return None
    if fx < 1.0 or fy < 1.0:
        return None
    d = np.array([(u - cx) / fx, (v - cy) / fy, 1.0], dtype=np.float64)
    R = T_cb[:3, :3]
    t = T_cb[:3, 3]
    rd = R @ d
    if abs(float(rd[2])) < 1e-9:
        return None
    z_cam = (float(z_plane) - float(t[2])) / float(rd[2])
    if z_cam <= 1e-3:
        return None
    base = R @ (d * z_cam) + t
    return float(base[0]), float(base[1])


def span_in_robot_xy(
    grasp_uv: Tuple[float, float],
    toe_uv: Tuple[float, float],
    *,
    camera_id: str = "cam1",
    cfg: Optional[Dict[str, Any]] = None,
) -> RobotSpan:
    """
    全图像素抓取点、鞋头 → 机器人基座 XY 距离（示教器毫米）。
    """
    vis = cfg if isinstance(cfg, dict) else {}
    if "belt_toe" not in vis and isinstance(vis.get("vision"), dict):
        vis = vis["vision"]
    blk = vis.get("belt_toe") if isinstance(vis.get("belt_toe"), dict) else {}

    u0, v0 = float(grasp_uv[0]), float(grasp_uv[1])
    u1, v1 = float(toe_uv[0]), float(toe_uv[1])
    pix = float(np.hypot(u1 - u0, v1 - v0))

    manual = float(blk.get("mm_per_px") or 0.0)
    pairs = _usable_samples(load_handeye_samples(camera_id))
    H = None
    method = ""

    if len(pairs) >= 2:
        H = _similarity_3x3([p[0] for p in pairs], [p[1] for p in pairs])
        if H is not None:
            method = f"cam1手眼采样{len(pairs)}点→基座XY"

    if H is None:
        he = load_handeye(camera_id) or {}
        T = _T_matrix(he.get("T"))
        plane_z = float(blk.get("plane_z_mm") or blk.get("cam_z_mm") or 0.0)
        calib = load_calib(camera_id) or {}
        K = calib.get("K")
        if T is not None and K and plane_z != 0.0:
            g = _uv_on_plane(u0, v0, K, T, plane_z)
            t = _uv_on_plane(u1, v1, K, T, plane_z)
            if g is not None and t is not None:
                length = float(np.hypot(t[0] - g[0], t[1] - g[1]))
                return RobotSpan(
                    ok=True,
                    length=length,
                    grasp_xy=g,
                    toe_xy=t,
                    mm_per_px=length / pix if pix > 1e-6 else 0.0,
                    method="手眼矩阵T+皮带平面Z",
                    message=f"机器人XY 抓取={g[0]:.1f},{g[1]:.1f} 鞋头={t[0]:.1f},{t[1]:.1f} 距离={length:.1f}mm",
                )

    if H is not None:
        g = _apply_h(H, u0, v0)
        t = _apply_h(H, u1, v1)
        length = float(np.hypot(t[0] - g[0], t[1] - g[1]))
        return RobotSpan(
            ok=True,
            length=length,
            grasp_xy=g,
            toe_xy=t,
            mm_per_px=_scale_of_h(H),
            method=method,
            message=(
                f"{method} | 抓取XY=({g[0]:.1f},{g[1]:.1f}) "
                f"鞋头XY=({t[0]:.1f},{t[1]:.1f}) 距离={length:.1f}mm（示教器）"
            ),
        )

    if manual > 1e-6:
        length = pix * manual
        return RobotSpan(
            ok=True,
            length=float(length),
            mm_per_px=manual,
            method="yaml mm_per_px（按示教器毫米/像素）",
            message=f"像素距={pix:.1f} × {manual:.4f} → {length:.1f}mm（示教器）",
        )

    return RobotSpan(
        ok=False,
        message=(
            "无法把鞋长换成机器人毫米：请在 cam1 预览点选皮带上一点，"
            "把上料臂 TCP 对准同一点，采至少 2 个分开的点并保存手眼采样"
            "（或在 vision.belt_toe.mm_per_px 填示教器mm/像素）"
        ),
    )


def samples_scale_text(camera_id: str = "cam1") -> str:
    pairs = _usable_samples(load_handeye_samples(camera_id))
    if len(pairs) < 2:
        return f"{camera_id} 像素→机器人：采样有效点 {len(pairs)}（至少 2 个不同像素才能换算示教器毫米）"
    H = _similarity_3x3([p[0] for p in pairs], [p[1] for p in pairs])
    if H is None:
        return f"{camera_id} 像素→机器人：采样点太近，无法估比例"
    s = _scale_of_h(H)
    return f"{camera_id} 像素→机器人：{len(pairs)} 点  1px≈{s:.4f}mm（示教器 XY）"
