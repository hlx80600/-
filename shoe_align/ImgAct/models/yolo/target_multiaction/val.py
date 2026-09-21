# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

from typing import Any

import numpy as np
import torch

from ImgAct.models.yolo.detect.val import DetectionValidator
from ImgAct.utils.metrics import DetMetrics


class TargetMultiActionValidator(DetectionValidator):
    """Validator for TargetMultiActionDetect: detection + per-object action accuracy.

    Extends DetectionValidator to track per-action-head accuracy on correctly detected objects.
    """

    def __init__(self, dataloader=None, save_dir=None, args=None, _callbacks=None):
        """Initialize validator with action tracking."""
        super().__init__(dataloader, save_dir, args, _callbacks)
        self.action_shape = None
        self.action_correct = 0
        self.action_total = 0

    def init_metrics(self, model):
        """Initialize metrics with action shape from model."""
        super().init_metrics(model)
        head = model.model[-1] if hasattr(model, "model") else None
        if head and hasattr(head, "action_shape"):
            self.action_shape = head.action_shape
        else:
            self.action_shape = [3, 3]

    def preprocess(self, batch):
        """Preprocess batch, handle action labels."""
        batch = super().preprocess(batch)
        if "actions" in batch:
            batch["actions"] = batch["actions"].to(self.device).long()
        return batch

    def postprocess(self, preds):
        """Postprocess: extract action predictions from extra columns."""
        preds = super().postprocess(preds)
        if self.action_shape is not None:
            num_actions, classes_per_action = self.action_shape
            na = num_actions * classes_per_action
            for pred in preds:
                extra = pred.pop("extra", None)
                if extra is not None and extra.shape[1] >= na:
                    pred["actions"] = extra[:, :na].view(-1, num_actions, classes_per_action)
                else:
                    pred["actions"] = torch.zeros(len(pred["cls"]), num_actions, classes_per_action, device=self.device)
        return preds

    def _prepare_batch(self, si, batch):
        """Prepare batch with action labels."""
        pbatch = super()._prepare_batch(si, batch)
        if "actions" in batch:
            idx = batch["batch_idx"] == si
            pbatch["actions"] = batch["actions"][idx]
        return pbatch

    def _process_batch(self, preds, batch):
        """Process batch: compute detection metrics + action accuracy."""
        tp = super()._process_batch(preds, batch)

        # Compute action accuracy for matched predictions
        if "actions" in preds and "actions" in batch and len(batch["actions"]) > 0:
            pred_actions = preds["actions"].argmax(dim=2)  # (N_pred, num_actions)
            gt_actions = batch["actions"]  # (N_gt, num_actions)

            # For correctly detected objects (true positives at IoU>=0.5), check action accuracy
            tp_mask = tp.get("tp", np.zeros((0, 1), dtype=bool))
            if len(tp_mask) > 0 and tp_mask.shape[1] > 0:
                tp_at_50 = tp_mask[:, 0]  # IoU >= 0.5
                if tp_at_50.any() and len(pred_actions) > 0 and len(gt_actions) > 0:
                    n_correct = min(tp_at_50.sum(), len(pred_actions), len(gt_actions))
                    matched_pred = pred_actions[:n_correct]
                    matched_gt = gt_actions[:n_correct]
                    self.action_correct += (matched_pred == matched_gt).sum().item()
                    self.action_total += matched_pred.numel()

        return tp

    def get_desc(self):
        """Return description with action accuracy."""
        return ("%22s" + "%11s" * 7) % (
            "Class", "Images", "Instances", "Box(P", "R", "mAP50", "mAP50-95)", "Act_Acc",
        )

    def print_results(self):
        """Print results with action accuracy."""
        super().print_results()
        if self.action_total > 0:
            acc = self.action_correct / self.action_total
            self.logger.info(f"Action accuracy: {acc:.4f} ({self.action_correct}/{self.action_total})")

    def get_stats(self):
        """Get stats dict with action accuracy."""
        stats = super().get_stats()
        if self.action_total > 0:
            stats["action_accuracy"] = self.action_correct / self.action_total
        else:
            stats["action_accuracy"] = 0.0
        return stats
