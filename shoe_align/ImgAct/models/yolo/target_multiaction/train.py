# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any

from ImgAct.data.dataset import TargetMultiActionDataset
from ImgAct.models import yolo
from ImgAct.models.yolo.detect.train import DetectionTrainer
from ImgAct.nn.tasks import DetectionModel
from ImgAct.utils import DEFAULT_CFG, RANK
from ImgAct.utils.torch_utils import unwrap_model


class TargetMultiActionTrainer(DetectionTrainer):
    """Trainer for TargetMultiActionDetect: detection + per-object action direction prediction.

    Uses TargetMultiActionDataset for data loading with action labels.
    Label format: class x y w h action_0 action_1 ... action_n

    Data config (data.yaml) must include:
        action_shape: [3, 3]  # [num_actions, classes_per_action]
    """

    def __init__(self, cfg=DEFAULT_CFG, overrides=None, _callbacks=None):
        """Initialize trainer."""
        if overrides is None:
            overrides = {}
        overrides["task"] = "detect"
        super().__init__(cfg, overrides, _callbacks)

    def get_model(self, cfg=None, weights=None, verbose=True):
        """Get detection model (uses DetectionModel which auto-detects TargetMultiActionDetect head)."""
        model = DetectionModel(
            cfg,
            nc=self.data["nc"],
            ch=self.data.get("channels", 3),
            verbose=verbose and RANK == -1,
        )
        if weights:
            model.load(weights)
        return model

    def build_dataset(self, img_path, mode="train", batch=None):
        """Build TargetMultiActionDataset instead of standard YOLODataset."""
        gs = max(int(unwrap_model(self.model).stride.max()), 32)
        pad = 0.0 if mode == "train" else 0.5
        cfg = self.args
        fraction = cfg.fraction if mode == "train" else 1.0
        return TargetMultiActionDataset(
            img_path=img_path,
            imgsz=cfg.imgsz,
            batch_size=batch,
            augment=mode == "train",
            hyp=cfg,
            rect=cfg.rect or (mode == "val"),
            cache=cfg.cache or None,
            single_cls=cfg.single_cls or False,
            stride=gs,
            pad=pad,
            prefix=f"{mode}: ",
            task=cfg.task,
            classes=cfg.classes,
            data=self.data,
            fraction=fraction,
        )

    def get_validator(self):
        """Return TargetMultiActionValidator."""
        self.loss_names = "box_loss", "cls_loss", "dfl_loss", "action_loss"
        return yolo.target_multiaction.TargetMultiActionValidator(
            self.test_loader, save_dir=self.save_dir, args=copy(self.args), _callbacks=self.callbacks
        )

    def set_model_attributes(self):
        """Set action shape on the model."""
        super().set_model_attributes()
        head = self.model.model[-1]
        if hasattr(head, "action_shape"):
            self.model.action_shape = head.action_shape

    def get_dataset(self):
        """Retrieve dataset and validate action_shape key exists."""
        data = super().get_dataset()
        if "action_shape" not in data:
            data["action_shape"] = [3, 3]  # default: 3 actions × 3 classes
        return data
