"""棋盘格内参标定 + 手眼采样/矩阵存盘。

手眼 4×4 由 vision.handeye_solve 从采样点计算（HMI「视觉采图」或「视觉调试」按钮），
并写入 config/calib/<相机>_handeye.json；皮带生产再写入 shoe_vision_config.json 的 handeye.mat。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

CALIB_DIR = Path(__file__).resolve().parents[1] / "config" / "calib"
CALIB_DIR.mkdir(parents=True, exist_ok=True)


def _pattern_candidates(cols: int, rows: int) -> list[tuple[int, int]]:
    """只按用户填写的尺寸检测，并额外试行列对调（不改用户保存的数）。"""
    cols, rows = int(cols), int(rows)
    out: list[tuple[int, int]] = []
    for a, b in (
        (cols, rows),
        (rows, cols),
    ):
        if a < 3 or b < 3:
            continue
        if (a, b) not in out:
            out.append((a, b))
    return out


def _gray_variants(gray: np.ndarray) -> list[np.ndarray]:
    out = [gray]
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        out.append(clahe.apply(gray))
    except Exception:
        pass
    try:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        if blur is not None:
            out.append(blur)
    except Exception:
        pass
    return out


def _try_corners(gray: np.ndarray, pattern: tuple[int, int]) -> Optional[np.ndarray]:
    """只走 FAST_CHECK，无效帧立即返回，避免 findChessboardCorners 无 FAST_CHECK 卡死数秒。"""
    flags_fast = (
        cv2.CALIB_CB_ADAPTIVE_THRESH
        | cv2.CALIB_CB_NORMALIZE_IMAGE
        | cv2.CALIB_CB_FAST_CHECK
    )
    found, pts = cv2.findChessboardCorners(gray, pattern, flags_fast)
    if found and pts is not None:
        return pts
    return None


def find_chessboard(
    image_bgr: np.ndarray,
    cols: int,
    rows: int,
    *,
    cancel=None,
    deadline: Optional[float] = None,
) -> Tuple[bool, Optional[np.ndarray], Tuple[int, int]]:
    """
    内角点检测。返回 (找到, 角点, 实际用的 cols/rows)。
    只做 FAST_CHECK；无效帧快速失败，不跑穷举。
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    work = gray
    scale = 1.0
    max_w = 800
    if w > max_w:
        scale = max_w / float(w)
        work = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    corners = None
    used = (int(cols), int(rows))
    patterns = _pattern_candidates(cols, rows)
    variants = _gray_variants(work)
    for pattern in patterns:
        for g in variants:
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                return False, None, used
            if deadline is not None and time.monotonic() > float(deadline):
                return False, None, used
            pts = _try_corners(g, pattern)
            if pts is None:
                continue
            corners = pts
            used = pattern
            break
        if corners is not None:
            break

    if corners is None:
        return False, None, used
    if scale != 1.0:
        corners = corners / float(scale)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.001)
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
    return True, corners, used


def draw_chessboard(
    image_bgr: np.ndarray,
    cols: int,
    rows: int,
    *,
    cancel=None,
    timeout_s: float = 1.5,
) -> Tuple[bool, np.ndarray, str, Tuple[int, int]]:
    """在图上画角点；返回 (找到?, 画后图, 说明, 实际内角点尺寸)。超时当未识别。"""
    vis = image_bgr.copy()
    deadline = time.monotonic() + max(0.2, float(timeout_s))
    ok, corners, used = find_chessboard(
        image_bgr, cols, rows, cancel=cancel, deadline=deadline
    )
    pc, pr = used
    if not ok or corners is None:
        timed_out = time.monotonic() > deadline
        cancelled = bool(cancel is not None and getattr(cancel, "is_set", lambda: False)())
        cv2.putText(
            vis,
            "NO CHESSBOARD",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2,
        )
        if cancelled:
            hint = "已取消检测"
        elif timed_out:
            hint = f"本帧未识别（{timeout_s:.1f}s 超时，未卡死）。摆正棋盘后再采。"
        else:
            hint = f"未找到棋盘格 {cols}×{rows}。请确认填写的是内角点数。"
        return False, vis, hint, used
    cv2.drawChessboardCorners(vis, used, corners, True)
    n = int(len(corners))
    msg = f"已找到棋盘格 {pc}×{pr}，角点数={n}"
    return True, vis, msg, used


def calibrate_intrinsics(
    images: List[np.ndarray],
    cols: int,
    rows: int,
    square_size_mm: float,
) -> dict:
    objpoints = []
    imgpoints = []
    img_size = None
    used = None
    for img in images:
        ok, corners, pattern = find_chessboard(img, cols, rows)
        if not ok or corners is None:
            continue
        if used is None:
            used = pattern
        if pattern != used:
            continue
        pc, pr = used
        objp = np.zeros((pc * pr, 3), np.float32)
        objp[:, :2] = np.mgrid[0:pc, 0:pr].T.reshape(-1, 2)
        objp *= float(square_size_mm)
        objpoints.append(objp)
        imgpoints.append(corners)
        img_size = (img.shape[1], img.shape[0])

    if not objpoints or img_size is None or used is None:
        raise RuntimeError("有效棋盘格图像不足，请多采集几张")

    rms, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, img_size, None, None)
    return {
        "rms": float(rms),
        "K": K.tolist(),
        "dist": dist.tolist(),
        "image_size": list(img_size),
        "n_frames": len(objpoints),
        "cols": int(used[0]),
        "rows": int(used[1]),
        "square_size_mm": float(square_size_mm),
    }


def calib_path(camera_id: str) -> Path:
    return CALIB_DIR / f"{camera_id}_intrinsics.json"


def handeye_path(camera_id: str) -> Path:
    return CALIB_DIR / f"{camera_id}_handeye.json"


def handeye_samples_path(camera_id: str) -> Path:
    return CALIB_DIR / f"{camera_id}_handeye_samples.json"


def _unlink_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    return True


def save_calib(camera_id: str, data: dict) -> Path:
    path = calib_path(camera_id)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_calib(camera_id: str) -> Optional[dict]:
    path = calib_path(camera_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_calib(camera_id: str) -> bool:
    """删除已保存的棋盘内参文件。返回是否删到了文件。"""
    return _unlink_if_exists(calib_path(camera_id))


def calib_status_text(camera_id: str) -> str:
    data = load_calib(camera_id)
    if not data:
        return f"{camera_id} 内参: 未标定"
    rms = data.get("rms", "?")
    n = data.get("n_frames", "?")
    size = data.get("image_size", [])
    return f"{camera_id} 内参: 有 RMS={rms} 帧数={n} 尺寸={size}"


def save_handeye(camera_id: str, T_cam2base_or_flange: list) -> Path:
    path = handeye_path(camera_id)
    path.write_text(json.dumps({"T": T_cam2base_or_flange}, indent=2), encoding="utf-8")
    return path


def load_handeye(camera_id: str) -> Optional[dict]:
    path = handeye_path(camera_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_handeye(camera_id: str) -> bool:
    """删除已保存的手眼矩阵文件。"""
    return _unlink_if_exists(handeye_path(camera_id))


def handeye_status_text(camera_id: str) -> str:
    data = load_handeye(camera_id)
    if not data:
        he = f"{camera_id} 手眼: 未保存"
    else:
        t = data.get("T")
        n = len(t) if isinstance(t, list) else "?"
        he = f"{camera_id} 手眼: 已有文件（T长度={n}）"
    samples = load_handeye_samples(camera_id)
    if samples:
        he += f" | 采样文件={len(samples)}点"
    elif handeye_samples_path(camera_id).exists():
        he += " | 采样文件=空"
    else:
        he += " | 采样文件=无"
    return he


def save_handeye_samples(camera_id: str, samples: list) -> Path:
    path = handeye_samples_path(camera_id)
    path.write_text(
        json.dumps({"samples": samples}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_handeye_samples(camera_id: str) -> list:
    path = handeye_samples_path(camera_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("samples") or [])


def delete_handeye_samples(camera_id: str) -> bool:
    """删除已保存的手眼采样文件。"""
    return _unlink_if_exists(handeye_samples_path(camera_id))
