"""YOLO-OBB 标注：圈旋转框，存归一化四点。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from vision import model_store as mstore

Box = dict  # cls, cx, cy, w, h, angle_deg  （像素）


def rotated_corners(cx: float, cy: float, w: float, h: float, angle_deg: float) -> list[tuple[float, float]]:
    rad = math.radians(float(angle_deg))
    c, s = math.cos(rad), math.sin(rad)
    hw, hh = float(w) / 2.0, float(h) / 2.0
    out = []
    for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        out.append((cx + x * c - y * s, cy + x * s + y * c))
    return out


def corners_to_box(pts: Iterable[tuple[float, float]], cls_id: int = 0) -> Box:
    pts = [(float(a), float(b)) for a, b in pts]
    try:
        import cv2
        import numpy as np

        arr = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
        (cx, cy), (w, h), ang = cv2.minAreaRect(arr)
        if w < 1 or h < 1:
            raise ValueError("empty")
        if w < h:
            w, h = h, w
            ang += 90.0
        return {
            "cls": int(cls_id),
            "cx": float(cx),
            "cy": float(cy),
            "w": float(w),
            "h": float(h),
            "angle_deg": float(ang),
        }
    except Exception:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return {
            "cls": int(cls_id),
            "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0,
            "w": max(4.0, x1 - x0),
            "h": max(4.0, y1 - y0),
            "angle_deg": 0.0,
        }


def box_from_drag(x0: float, y0: float, x1: float, y1: float, cls_id: int = 0) -> Box:
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    return {
        "cls": int(cls_id),
        "cx": (xa + xb) / 2.0,
        "cy": (ya + yb) / 2.0,
        "w": max(4.0, xb - xa),
        "h": max(4.0, yb - ya),
        "angle_deg": 0.0,
    }


def load_boxes(image: Path, img_w: int, img_h: int) -> list[Box]:
    path = mstore.label_path_for(image)
    if not path.is_file():
        return []
    boxes: list[Box] = []
    iw = max(1, int(img_w))
    ih = max(1, int(img_h))
    for line in path.read_text(encoding="utf-8").splitlines():
        bits = line.strip().split()
        if len(bits) < 9:
            continue
        try:
            cls_id = int(float(bits[0]))
            nums = [float(v) for v in bits[1:9]]
        except ValueError:
            continue
        pts = [(nums[i] * iw, nums[i + 1] * ih) for i in range(0, 8, 2)]
        boxes.append(corners_to_box(pts, cls_id))
    return boxes


def save_boxes(image: Path, boxes: list[Box], img_w: int, img_h: int) -> Path:
    path = mstore.label_path_for(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    iw = max(1, int(img_w))
    ih = max(1, int(img_h))
    lines = []
    for b in boxes:
        pts = rotated_corners(b["cx"], b["cy"], b["w"], b["h"], b.get("angle_deg") or 0.0)
        vals = []
        for x, y in pts:
            vals.append(f"{max(0.0, min(1.0, x / iw)):.6f}")
            vals.append(f"{max(0.0, min(1.0, y / ih)):.6f}")
        lines.append(f"{int(b.get('cls', 0))} " + " ".join(vals))
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    # 若 val 有同名图，同步一份标注
    if "train" in image.parts:
        idx = image.parts.index("train")
        val_img = Path(*image.parts[:idx]) / "val" / Path(*image.parts[idx + 1 :])
        if val_img.is_file():
            vp = mstore.label_path_for(val_img)
            if lines:
                vp.parent.mkdir(parents=True, exist_ok=True)
                vp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif vp.exists():
                vp.unlink()
    return path


def list_slot_images(slot_id: str) -> list[Path]:
    mstore.ensure_dirs(slot_id)
    files = mstore.list_images(mstore.class_dir(slot_id, "", split="train"))
    files.sort(key=lambda p: p.name)
    return files


def is_labeled(image: Path) -> bool:
    p = mstore.label_path_for(image)
    return p.is_file() and p.stat().st_size > 2
