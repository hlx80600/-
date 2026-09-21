# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

import torch
import torch.distributed as dist

from ImgAct.data import DiscreteMultiActionHeadDataset, build_dataloader
from ImgAct.engine.validator import BaseValidator
from ImgAct.utils import LOGGER, RANK


class DiscreteMultiActionHeadValidator(BaseValidator):
    """Validator for discrete multi-action classification models."""

    def __init__(self, dataloader=None, save_dir=None, args=None, _callbacks=None,
                 num_heads=3, classes_per_head=3, head_names=None):
        super().__init__(dataloader, save_dir, args, _callbacks)
        self.targets = None
        self.pred = None
        self.args.task = "classify"
        self.num_heads = num_heads
        self.classes_per_head = classes_per_head
        self.head_names = head_names or [f"head{i}" for i in range(num_heads)]
        self.metrics = DiscreteMultiActionHeadMetrics(num_heads=num_heads, head_names=self.head_names)

    def get_desc(self) -> str:
        """Return a formatted string summarizing discrete multi-action classification metrics."""
        parts = ["%22s"] + ["%11s" for _ in self.head_names] + ["%11s"]
        return ("".join(parts)) % ("classes", *[f"{name}_acc" for name in self.head_names], "avg_acc")

    def init_metrics(self, model) -> None:
        self.names = model.names
        self.pred = []
        self.targets = []

    def preprocess(self, batch):
        batch["img"] = batch["img"].to(self.device, non_blocking=self.device.type == "cuda")
        batch["img"] = batch["img"].half() if self.args.half else batch["img"].float()
        batch["cls"] = batch["cls"].to(self.device, non_blocking=self.device.type == "cuda")
        return batch

    def update_metrics(self, preds, batch):
        """Store predictions and targets for later evaluation."""
        batch_size = preds.shape[0]
        reshaped = preds.view(batch_size, self.num_heads, self.classes_per_head)
        head_preds = reshaped.argmax(dim=2)
        self.pred.append(head_preds.cpu())
        self.targets.append(batch["cls"].long().cpu())

    def finalize_metrics(self) -> None:
        self.metrics.speed = self.speed
        self.metrics.save_dir = self.save_dir

    def postprocess(self, preds):
        return preds[0] if isinstance(preds, (list, tuple)) else preds

    def get_stats(self):
        self.metrics.process(self.targets, self.pred)
        return self.metrics.results_dict

    def gather_stats(self) -> None:
        if RANK == 0:
            gathered_preds = [None] * dist.get_world_size()
            gathered_targets = [None] * dist.get_world_size()
            dist.gather_object(self.pred, gathered_preds, dst=0)
            dist.gather_object(self.targets, gathered_targets, dst=0)
            self.pred = [pred for rank_preds in gathered_preds for pred in rank_preds]
            self.targets = [target for rank_targets in gathered_targets for target in rank_targets]
        elif RANK > 0:
            dist.gather_object(self.pred, None, dst=0)
            dist.gather_object(self.targets, None, dst=0)

    def build_dataset(self, img_path):
        return DiscreteMultiActionHeadDataset(
            root=img_path,
            args=self.args,
            augment=False,
            prefix=self.args.split,
            num_heads=self.num_heads,
            classes_per_head=self.classes_per_head,
        )

    def get_dataloader(self, dataset_path, batch_size):
        dataset = self.build_dataset(dataset_path)
        return build_dataloader(dataset, batch_size, self.args.workers, rank=-1)

    def print_results(self) -> None:
        parts = ["%22s"] + ["%11.3g" for _ in self.head_names] + ["%11.3g"]
        LOGGER.info(("".join(parts)) % ("all", *self.metrics.per_head_acc, self.metrics.avg_acc))


class DiscreteMultiActionHeadMetrics:
    """Metrics for multi-head classification."""

    def __init__(self, num_heads=3, head_names=None):
        self.num_heads = num_heads
        self.head_names = head_names or [f"head{i}" for i in range(num_heads)]
        self.per_head_acc = [0.0] * num_heads
        self.avg_acc = 0.0
        self.speed = {}
        self.save_dir = None
        self.keys = [f"{name}_acc" for name in self.head_names] + ["avg_acc"]

    @property
    def results_dict(self):
        metrics = {}
        for name, accuracy in zip(self.head_names, self.per_head_acc):
            metrics[f"metrics/{name}_acc"] = accuracy
        metrics["metrics/avg_acc"] = self.avg_acc
        metrics["fitness"] = self.avg_acc
        return metrics

    @property
    def fitness(self):
        return self.avg_acc

    def process(self, targets, preds):
        """Compute per-head accuracy."""
        all_targets = torch.cat(targets, dim=0)
        all_preds = torch.cat(preds, dim=0)
        for index in range(self.num_heads):
            correct = (all_preds[:, index] == all_targets[:, index]).float().mean().item()
            self.per_head_acc[index] = correct
        self.avg_acc = sum(self.per_head_acc) / self.num_heads