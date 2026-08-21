"""眼在手外（cam1 皮带）：像素+深度 → 相机 XYZ，与 TCP 对齐，求 T_cam2base。

与 shoe_vision_seg 生产约定一致：base = T @ [X,Y,Z,1]。
采样：预览点选皮带上一点，上料臂 TCP 对准同一点。建议 ≥3 个散开的点，有深度更好。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from vision.numpy_compat import np

_LAST_PIXEL: dict[str, tuple[int, int]] = {}


def set_clicked_pixel(camera_id: str, u: int, v: int) -> None:
    _LAST_PIXEL[str(camera_id)] = (int(u), int(v))


def clicked_pixel(camera_id: str) -> Optional[tuple[int, int]]:
    return _LAST_PIXEL.get(str(camera_id))


def depth_at(
    depth,
    u: float,
    v: float,
    color_hw: Optional[tuple[int, int]] = None,
    kernel: int = 4,
) -> Optional[float]:
    """彩色像素在深度图上的邻域中值（mm）。深度分辨率可与彩色不同。"""
    if depth is None:
        return None
    arr = np.asarray(depth)
    if arr.ndim < 2:
        return None
    dh, dw = int(arr.shape[0]), int(arr.shape[1])
    if dh < 1 or dw < 1:
        return None
    xd, yd = float(u), float(v)
    if color_hw is not None:
        ch, cw = int(color_hw[0]), int(color_hw[1])
        if cw > 0 and ch > 0 and (cw != dw or ch != dh):
            xd = xd * dw / cw
            yd = yd * dh / ch
    xi, yi = int(round(xd)), int(round(yd))
    half = max(1, int(kernel) // 2)
    x1, x2 = max(0, xi - half), min(dw, xi + half + 1)
    y1, y2 = max(0, yi - half), min(dh, yi + half + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    patch = arr[y1:y2, x1:x2]
    valid = patch[(patch > 1.0) & np.isfinite(patch)]
    if valid.size == 0:
        return None
    return float(np.median(valid))


def pixel_to_cam(u: float, v: float, z: float, k: dict) -> tuple[float, float, float]:
    fx, fy, cx, cy = float(k["fx"]), float(k["fy"]), float(k["cx"]), float(k["cy"])
    z = float(z)
    return ((u - cx) * z / fx, (v - cy) * z / fy, z)


def rigid_cam_to_robot(cam_xyz: np.ndarray, robot_xyz: np.ndarray) -> np.ndarray:
    """Kabsch：robot = R @ cam + t。cam/robot 为 Nx3。"""
    src = np.asarray(cam_xyz, dtype=np.float64).reshape(-1, 3)
    dst = np.asarray(robot_xyz, dtype=np.float64).reshape(-1, 3)
    if src.shape[0] < 3:
        raise ValueError("至少 3 个三维点才能求刚体 4×4")
    c1 = src.mean(axis=0)
    c2 = dst.mean(axis=0)
    A = src - c1
    B = dst - c2
    H = A.T @ B
    U, _s, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if float(np.linalg.det(R)) < 0:
        Vt = Vt.copy()
        Vt[-1, :] *= -1
        R = Vt.T @ U.T
    t = c2 - R @ c1
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def _tcp_xyz(sample: dict) -> Optional[tuple[float, float, float]]:
    tcp = sample.get("tcp") if isinstance(sample, dict) else None
    if not isinstance(tcp, dict):
        return None
    try:
        return float(tcp["x"]), float(tcp["y"]), float(tcp["z"])
    except (KeyError, TypeError, ValueError):
        return None


def _uv(sample: dict) -> Optional[tuple[float, float]]:
    try:
        return float(sample["pixel_u"]), float(sample["pixel_v"])
    except (KeyError, TypeError, ValueError):
        return None


def _spread_ok(pts: Sequence[tuple], *, min_span: float) -> bool:
    if len(pts) < 2:
        return False
    arr = np.asarray(pts, dtype=np.float64)
    span = float(np.linalg.norm(arr.max(axis=0) - arr.min(axis=0)))
    return span >= min_span


@dataclass
class HandeyeResult:
    ok: bool
    T: Optional[np.ndarray] = None
    method: str = ""
    n: int = 0
    rms_mm: float = 0.0
    message: str = ""
    used_depth: int = 0
    used_assumed: int = 0
    residuals: list[float] = field(default_factory=list)


def solve_from_samples(
    samples: Sequence[dict],
    *,
    k: Optional[dict] = None,
    assumed_z_mm: float = 400.0,
) -> HandeyeResult:
    """
    优先用采样里的 cam_xyz / depth_mm；否则用内参 + assumed_z_mm 反投影。
    """
    if not samples:
        return HandeyeResult(ok=False, message="没有手眼采样点")

    cam_pts = []
    rob_pts = []
    uvs = []
    n_depth = 0
    n_assumed = 0
    skipped = 0
    for s in samples:
        if not isinstance(s, dict):
            skipped += 1
            continue
        xyz_r = _tcp_xyz(s)
        uv = _uv(s)
        if xyz_r is None or uv is None:
            skipped += 1
            continue
        cam = None
        raw = s.get("cam_xyz")
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            try:
                cam = (float(raw[0]), float(raw[1]), float(raw[2]))
                n_depth += 1
            except (TypeError, ValueError):
                cam = None
        if cam is None:
            z = s.get("depth_mm")
            try:
                zf = float(z) if z is not None else 0.0
            except (TypeError, ValueError):
                zf = 0.0
            if zf > 1.0 and k is not None:
                cam = pixel_to_cam(uv[0], uv[1], zf, k)
                n_depth += 1
            elif k is not None and assumed_z_mm > 1.0:
                cam = pixel_to_cam(uv[0], uv[1], float(assumed_z_mm), k)
                n_assumed += 1
        if cam is None:
            skipped += 1
            continue
        cam_pts.append(cam)
        rob_pts.append(xyz_r)
        uvs.append(uv)

    if len(cam_pts) < 3:
        return HandeyeResult(
            ok=False,
            n=len(cam_pts),
            message=(
                f"有效三维点只有 {len(cam_pts)} 个（跳过 {skipped}）。"
                "请先写 cam1 内参，再在皮带上换位置采至少 3 个点"
                "（点像素 → 手臂 TCP 对准同一点 → 记录）。有深度会更准。"
            ),
        )
    if not _spread_ok(uvs, min_span=40.0) and not _spread_ok(rob_pts, min_span=30.0):
        return HandeyeResult(
            ok=False,
            n=len(cam_pts),
            message="采样点几乎重合。请在皮带工作区换几个分开的位置再采。",
        )

    try:
        T = rigid_cam_to_robot(np.asarray(cam_pts), np.asarray(rob_pts))
    except Exception as e:
        return HandeyeResult(ok=False, n=len(cam_pts), message=f"求解失败: {e}")

    residuals = []
    for c, r in zip(cam_pts, rob_pts):
        ph = T @ np.array([c[0], c[1], c[2], 1.0], dtype=np.float64)
        err = float(np.linalg.norm(ph[:3] - np.asarray(r, dtype=np.float64)))
        residuals.append(err)
    rms = float(np.sqrt(np.mean(np.square(residuals)))) if residuals else 0.0
    method = "深度+内参→刚体4×4" if n_depth else f"假定深度{assumed_z_mm:.0f}mm+内参→刚体4×4"
    if n_depth and n_assumed:
        method = f"深度{n_depth}点+假定{n_assumed}点→刚体4×4"
    warn = ""
    if rms > 15.0:
        warn = f" RMS={rms:.1f}mm 偏大，请检查是否点错位置或深度单位。"
    elif rms > 8.0:
        warn = f" RMS={rms:.1f}mm，建议再采几个散开的点。"
    return HandeyeResult(
        ok=True,
        T=T,
        method=method,
        n=len(cam_pts),
        rms_mm=rms,
        used_depth=n_depth,
        used_assumed=n_assumed,
        residuals=residuals,
        message=f"{method}  n={len(cam_pts)}  RMS={rms:.2f}mm{warn}",
    )


def enrich_sample(
    sample: dict,
    *,
    depth=None,
    color_hw: Optional[tuple[int, int]] = None,
    k: Optional[dict] = None,
) -> dict:
    """给采样补 depth_mm / cam_xyz（不改原 dict 引用之外的拷贝）。"""
    out = dict(sample)
    uv = _uv(out)
    if uv is None:
        return out
    z = out.get("depth_mm")
    try:
        zf = float(z) if z is not None else 0.0
    except (TypeError, ValueError):
        zf = 0.0
    if zf <= 1.0 and depth is not None:
        got = depth_at(depth, uv[0], uv[1], color_hw)
        if got is not None:
            out["depth_mm"] = float(got)
            zf = float(got)
    if k is not None and zf > 1.0 and not out.get("cam_xyz"):
        out["cam_xyz"] = list(pixel_to_cam(uv[0], uv[1], zf, k))
    return out


def k_from_any(*sources: Any) -> Optional[dict]:
    """从棋盘格 calib dict / shoe json camera / 显式 fx 字典取内参。"""
    from vision import shoe_cfg

    for src in sources:
        if not src:
            continue
        if isinstance(src, dict) and "fx" in src and "fy" in src:
            try:
                fx, fy = float(src["fx"]), float(src["fy"])
                cx, cy = float(src.get("cx", 0)), float(src.get("cy", 0))
            except (TypeError, ValueError):
                continue
            if fx >= 1.0 and fy >= 1.0:
                return {"fx": fx, "fy": fy, "cx": cx, "cy": cy}
        got = shoe_cfg.k_from_calib(src) if isinstance(src, dict) else None
        if got:
            return got
        if isinstance(src, dict):
            got = shoe_cfg.camera_k(src)
            if got:
                return got
            cam = src.get("camera")
            got = shoe_cfg.camera_k({"camera": cam}) if isinstance(cam, dict) else None
            if got:
                return got
    return None
