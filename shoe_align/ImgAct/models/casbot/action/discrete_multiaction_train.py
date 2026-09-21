# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

from copy import copy

import torch

from ImgAct.data import DiscreteMultiActionHeadDataset, build_dataloader
from ImgAct.engine.trainer import BaseTrainer
from ImgAct.models.casbot.action.discrete_multiaction_val import DiscreteMultiActionHeadValidator
from ImgAct.nn.tasks import ClassificationModel
from ImgAct.utils import DEFAULT_CFG, RANK
from ImgAct.utils.plotting import plot_images
from ImgAct.utils.torch_utils import is_parallel, torch_distributed_zero_first


class DiscreteMultiActionHeadTrainer(BaseTrainer):
    """Trainer for discrete multi-action classification models."""

    def __init__(self, cfg=DEFAULT_CFG, overrides=None, _callbacks=None):
        if overrides is None:
            overrides = {}
        overrides["task"] = "classify"
        if overrides.get("imgsz") is None:
            overrides["imgsz"] = 224
        self.num_heads = overrides.pop("num_heads", 3)
        self.classes_per_head = overrides.pop("classes_per_head", 3)
        self.head_names = overrides.pop("head_names", None) or ["X", "Y", "Z"]
        default_class_names = [[str(index) for index in range(self.classes_per_head)] for _ in range(self.num_heads)]
        self.class_names_per_head = overrides.pop("class_names", None) or default_class_names
        super().__init__(cfg, overrides, _callbacks)

    def set_model_attributes(self):
        """Set discrete multi-action model attributes."""
        names = {}
        idx = 0
        for head_index, head_name in enumerate(self.head_names):
            for class_name in self.class_names_per_head[head_index]:
                names[idx] = f"{head_name}:{class_name}"
                idx += 1
        self.model.names = names
        self.model.num_heads = self.num_heads
        self.model.classes_per_head = self.classes_per_head

    def get_model(self, cfg=None, weights=None, verbose=True):
        """Return a ClassificationModel configured for discrete multi-action output."""
        nc = self.num_heads * self.classes_per_head
        model = ClassificationModel(cfg, nc=nc, ch=self.data.get("channels", 3), verbose=verbose and RANK == -1)
        if weights:
            model.load(weights)

        for module in model.modules():
            if self.args.pretrained is False and hasattr(module, "reset_parameters"):
                module.reset_parameters()
            if isinstance(module, torch.nn.Dropout) and self.args.dropout:
                module.p = self.args.dropout
        for parameter in model.parameters():
            parameter.requires_grad = True
        return model

    def setup_model(self):
        """Load or create model for discrete multi-action classification."""
        ckpt = super().setup_model()
        return ckpt

    def build_dataset(self, img_path, mode="train", batch=None):
        return DiscreteMultiActionHeadDataset(
            root=img_path,
            args=self.args,
            augment=mode == "train",
            prefix=mode,
            num_heads=self.num_heads,
            classes_per_head=self.classes_per_head,
            head_names=self.head_names,
            class_names=self.class_names_per_head,
        )

    def get_dataloader(self, dataset_path, batch_size=16, rank=0, mode="train"):
        with torch_distributed_zero_first(rank):
            dataset = self.build_dataset(dataset_path, mode)

        if not dataset.samples:
            raise FileNotFoundError(f"No images found in '{mode}' split of {dataset_path}")

        loader = build_dataloader(dataset, batch_size, self.args.workers, rank=rank, drop_last=self.args.compile)
        if mode != "train":
            if is_parallel(self.model):
                self.model.module.transforms = loader.dataset.torch_transforms
            else:
                self.model.transforms = loader.dataset.torch_transforms
        return loader

    def preprocess_batch(self, batch):
        batch["img"] = batch["img"].to(self.device, non_blocking=self.device.type == "cuda")
        batch["cls"] = batch["cls"].to(self.device, non_blocking=self.device.type == "cuda")
        return batch

    def progress_string(self):
        return ("\n" + "%11s" * (4 + len(self.loss_names))) % (
            "Epoch", "GPU_mem", *self.loss_names, "Instances", "Size"
        )

    def get_validator(self):
        self.loss_names = ["loss"]
        return DiscreteMultiActionHeadValidator(
            self.test_loader,
            self.save_dir,
            args=copy(self.args),
            _callbacks=self.callbacks,
            num_heads=self.num_heads,
            classes_per_head=self.classes_per_head,
            head_names=self.head_names,
        )

    def label_loss_items(self, loss_items=None, prefix="train"):
        keys = [f"{prefix}/{x}" for x in self.loss_names]
        if loss_items is None:
            return keys
        loss_items = [round(float(loss_items), 5)]
        return dict(zip(keys, loss_items))

    def plot_training_samples(self, batch, ni):
        plot_batch = {key: value for key, value in batch.items() if key != "cls"}
        plot_batch["batch_idx"] = torch.arange(batch["img"].shape[0])
        plot_images(labels=plot_batch, fname=self.save_dir / f"train_batch{ni}.jpg", on_plot=self.on_plot)

    def get_dataset(self):
        """Override to provide discrete multi-action dataset info."""
        data = self.args.data
        if isinstance(data, str):
            from pathlib import Path

            data_path = Path(data)
            self.data = {
                "train": str(data_path / "train"),
                "val": str(data_path / "val"),
                "nc": self.num_heads * self.classes_per_head,
                "channels": 3,
                "names": {
                    index: (
                        f"{self.head_names[index // self.classes_per_head]}:"
                        f"{self.class_names_per_head[index // self.classes_per_head][index % self.classes_per_head]}"
                    )
                    for index in range(self.num_heads * self.classes_per_head)
                },
            }
            self.trainset = self.data["train"]
            self.testset = self.data["val"]
        else:
            super().get_dataset()
        return self.data