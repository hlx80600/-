"""采图 / 训练 / 模型挂接工具接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def capture_to_slot(ctx, slot_id: str, cls_name: str = "", *, to_val: bool = False) -> Path:
    from vision import model_store as mstore

    return mstore.capture_to_slot(ctx, slot_id, cls_name, to_val=to_val)


def bind_model(ctx, slot_id: str, src: Path, *, copy_to_default: bool = True) -> str:
    from vision import model_store as mstore

    return mstore.bind_model(ctx, slot_id, src, copy_to_default=copy_to_default)


def link_legacy_models(old_dir: str = "") -> str:
    from vision import model_store as mstore

    return mstore.link_legacy_models(old_dir)


def train_cmd(slot_id: str, *, epochs: int = 80, device: str = "cpu", batch: int | None = None) -> list:
    from vision import model_store as mstore

    return mstore.train_cmd(slot_id, epochs=epochs, device=device, batch=batch)


def cuda_train_status() -> dict:
    from vision import model_store as mstore

    return mstore.cuda_train_status()


def pip_ultralytics_cmd(*, with_cuda: bool = False, cuda_tag: str = "cu124") -> list:
    from vision import model_store as mstore

    return mstore.pip_ultralytics_cmd(with_cuda=with_cuda, cuda_tag=cuda_tag)


def dataset_counts(slot_id: str) -> str:
    from vision import model_store as mstore

    return mstore.dataset_counts(slot_id)


def prepare_shoe_lr_crop(ctx, img_bgr) -> tuple:
    """左右脚采图：尽量抠鞋头朝上小图。返回 (crop_or_None, note)。"""
    from vision.cls_crop import prepare_shoe_lr

    return prepare_shoe_lr(ctx, img_bgr)


def grab_slot_image(ctx, cam_id: str):
    from vision.cls_crop import grab_slot_image as _g

    return _g(ctx, cam_id)


def slots() -> dict:
    from vision import model_store as mstore

    return dict(mstore.SLOTS)
