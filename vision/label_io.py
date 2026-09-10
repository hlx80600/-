"""检测 / OBB / 分割标注读写（YOLO 归一化文本）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vision import obb_label
from vision import model_store as mstore

Shape = dict[str, Any]


def slot_task(slot_id: str) -> str:
    meta = mstore.slot_meta(slot_id)
    t = str(meta.get("task") or meta.get("train") or "")
    if t in ("classify", "detect", "obb", "segment"):
        return t
    if meta.get("kind") == "cls":
        return "classify"
    return "obb"


def load_shapes(image: Path, img_w: int, img_h: int, *, task: str) -> list[Shape]:
    """按任务读标签；文件格式不对时尽量兼容。"""
    path = mstore.label_path_for(image)
    if not path.is_file():
        return []
    iw = max(1, int(img_w))
    ih = max(1, int(img_h))
    out: list[Shape] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        bits = line.strip().split()
        if len(bits) < 5:
            continue
        try:
            cls_id = int(float(bits[0]))
            nums = [float(v) for v in bits[1:]]
        except ValueError:
            continue
        if task == "detect" and len(nums) >= 4:
            cx, cy, bw, bh = nums[0] * iw, nums[1] * ih, nums[2] * iw, nums[3] * ih
            out.append(
                {
                    "kind": "detect",
                    "cls": cls_id,
                    "cx": cx,
                    "cy": cy,
                    "w": max(4.0, bw),
                    "h": max(4.0, bh),
                    "angle_deg": 0.0,
                    "points": [],
                }
            )
            continue
        if task == "segment" and len(nums) >= 6 and len(nums) % 2 == 0:
            pts = [(nums[i] * iw, nums[i + 1] * ih) for i in range(0, len(nums), 2)]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append(
                {
                    "kind": "seg",
                    "cls": cls_id,
                    "cx": (min(xs) + max(xs)) / 2.0,
                    "cy": (min(ys) + max(ys)) / 2.0,
                    "w": max(4.0, max(xs) - min(xs)),
                    "h": max(4.0, max(ys) - min(ys)),
                    "angle_deg": 0.0,
                    "points": pts,
                }
            )
            continue
        if len(nums) >= 8:
            pts = [(nums[i] * iw, nums[i + 1] * ih) for i in range(0, 8, 2)]
            box = obb_label.corners_to_box(pts, cls_id)
            box["kind"] = "obb"
            box["points"] = []
            out.append(box)
    return out


def save_shapes(
    image: Path,
    shapes: list[Shape],
    img_w: int,
    img_h: int,
    *,
    task: str,
) -> Path:
    path = mstore.label_path_for(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    iw = max(1, int(img_w))
    ih = max(1, int(img_h))
    lines: list[str] = []
    for sh in shapes:
        cls_id = int(sh.get("cls") or 0)
        if task == "detect":
            cx = float(sh["cx"]) / iw
            cy = float(sh["cy"]) / ih
            bw = float(sh["w"]) / iw
            bh = float(sh["h"]) / ih
            lines.append(
                f"{cls_id} {max(0.0, min(1.0, cx)):.6f} {max(0.0, min(1.0, cy)):.6f} "
                f"{max(1e-6, min(1.0, bw)):.6f} {max(1e-6, min(1.0, bh)):.6f}"
            )
        elif task == "segment":
            pts = list(sh.get("points") or [])
            if len(pts) < 3:
                continue
            bits = [str(cls_id)]
            for x, y in pts:
                bits.append(f"{max(0.0, min(1.0, float(x) / iw)):.6f}")
                bits.append(f"{max(0.0, min(1.0, float(y) / ih)):.6f}")
            lines.append(" ".join(bits))
        else:
            pts = obb_label.rotated_corners(
                float(sh["cx"]),
                float(sh["cy"]),
                float(sh["w"]),
                float(sh["h"]),
                float(sh.get("angle_deg") or 0.0),
            )
            bits = [str(cls_id)]
            for x, y in pts:
                bits.append(f"{max(0.0, min(1.0, x / iw)):.6f}")
                bits.append(f"{max(0.0, min(1.0, y / ih)):.6f}")
            lines.append(" ".join(bits))
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
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
    return obb_label.is_labeled(image)
