"""图像标注：原图/结果图叠加 ROI 与短英文状态（Hershey 不画汉字）。"""

from __future__ import annotations

from typing import Any, Sequence

from vision.numpy_compat import np
from vision.roi import load_roi

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

CAM_IDS = ("cam1", "cam2", "cam3", "cam4")

CAM_TITLES = {
    "cam1": "cam1 皮带上料",
    "cam2": "cam2 鞋头对位",
    "cam3": "cam3 放料槽",
    "cam4": "cam4 取料槽",
}


def copy_bgr(img: Any) -> Any:
    if img is None:
        return None
    try:
        return img.copy()
    except Exception:
        return img


def draw_roi(img: Any, cam_id: str) -> Any:
    if img is None or cv2 is None:
        return img
    roi = load_roi(cam_id)
    if not roi:
        return img
    x, y = int(roi.get("x", 0) or 0), int(roi.get("y", 0) or 0)
    w, h = int(roi.get("w", 0) or 0), int(roi.get("h", 0) or 0)
    if w < 4 or h < 4:
        return img
    vis = img
    cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 220, 80), 2)
    return vis


def annotate_bgr(
    img: Any,
    lines: Sequence[str],
    *,
    ok: bool = True,
    cam_id: str = "",
    kind: str = "",
) -> Any:
    """在图上写 ASCII 状态；缺图时造一块灰底。"""
    if cv2 is None:
        return img
    if img is None:
        vis = np.zeros((360, 640, 3), dtype=np.uint8)
        vis[:] = (36, 36, 36)
    else:
        vis = img.copy()
    if cam_id:
        vis = draw_roi(vis, cam_id)
    color = (40, 200, 40) if ok else (40, 40, 230)
    y0 = 28
    header = " ".join(p for p in (cam_id.upper(), kind) if p)
    texts = [header] if header else []
    texts.extend(str(x) for x in lines if x)
    for i, line in enumerate(texts[:8]):
        cv2.putText(
            vis,
            line[:72],
            (12, y0 + i * 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            color,
            2,
        )
    return vis


def draw_point_pair(img: Any, p0, p1, *, ok: bool = True) -> Any:
    if img is None or cv2 is None:
        return img
    vis = img
    try:
        a = (int(p0[0]), int(p0[1]))
        b = (int(p1[0]), int(p1[1]))
    except Exception:
        return vis
    color = (0, 220, 255) if ok else (0, 0, 255)
    cv2.circle(vis, a, 6, (0, 255, 255), -1)
    cv2.circle(vis, b, 6, (0, 0, 255), -1)
    cv2.arrowedLine(vis, a, b, color, 2, tipLength=0.12)
    return vis
