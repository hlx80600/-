"""Ultralytics 训练超参：默认值、读写 yaml。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

TASKS = ("classify", "detect", "obb", "segment")

DEFAULT_MODEL: dict[str, str] = {
    "classify": "yolov8n-cls.pt",
    "detect": "yolov8n.pt",
    "obb": "yolov8n-obb.pt",
    "segment": "yolov8n-seg.pt",
}

DEFAULT_IMGSZ: dict[str, int] = {
    "classify": 224,
    "detect": 640,
    "obb": 640,
    "segment": 640,
}


def default_hparams(task: str) -> dict[str, Any]:
    """现场「基本」+「高级」默认超参。"""
    t = str(task or "classify")
    if t not in TASKS:
        t = "classify"
    return {
        "task": t,
        "model": DEFAULT_MODEL[t],
        "epochs": 40,
        "imgsz": DEFAULT_IMGSZ[t],
        "batch": 8,
        "device": "cpu",
        "patience": 20,
        "resume": False,
        "pretrained": True,
        "lr0": 0.01,
        "lrf": 0.01,
        "optimizer": "auto",
        "cos_lr": False,
        "freeze": 0,
        "workers": 2,
        "seed": 0,
        "hsv_h": 0.015,
        "hsv_s": 0.7,
        "hsv_v": 0.4,
        "degrees": 0.0,
        "translate": 0.1,
        "scale": 0.5,
        "fliplr": 0.5,
        "flipud": 0.0,
        "mosaic": 1.0 if t != "classify" else 0.0,
        "mixup": 0.0,
        "close_mosaic": 10,
        "plots": True,
    }


def hparams_path(slot_id: str) -> Path:
    from vision import model_store as mstore

    return mstore.dataset_dir(slot_id) / "hparams.yaml"


def load_hparams(slot_id: str, *, task: str | None = None) -> dict[str, Any]:
    t = task or "classify"
    out = default_hparams(t)
    path = hparams_path(slot_id)
    if not path.is_file():
        return out
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k in out or k in ("model", "task"):
            out[k] = v
    if str(out.get("task") or "") not in TASKS:
        out["task"] = t
    return out


def save_hparams(slot_id: str, data: dict[str, Any]) -> Path:
    path = hparams_path(slot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = default_hparams(str(data.get("task") or "classify"))
    merged.update(data)
    path.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def train_kwargs(hp: dict[str, Any]) -> dict[str, Any]:
    """转成 YOLO.train(**kwargs)，去掉非 ultralytics 键。"""
    skip = {"task", "model", "pretrained"}
    out: dict[str, Any] = {}
    for k, v in hp.items():
        if k in skip:
            continue
        if k == "resume" and not v:
            continue
        if k == "freeze" and (v is None or int(v) == 0):
            continue
        out[k] = v
    return out
