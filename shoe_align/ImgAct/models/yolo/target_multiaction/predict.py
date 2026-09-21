# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

import torch

from ImgAct.engine.results import Results
from ImgAct.models.yolo.detect.predict import DetectionPredictor
from ImgAct.utils import DEFAULT_CFG, ops


class TargetMultiActionPredictor(DetectionPredictor):
    """Predictor for TargetMultiActionDetect: detection + per-object action directions.

    Each detection includes action predictions (e.g., X/Y/Z movement as -1, 0, 1).
    The extra columns after [x1, y1, x2, y2, conf, cls] contain raw action logits
    reshaped to (num_actions, classes_per_action), then argmaxed per action head.
    """

    def __init__(self, cfg=DEFAULT_CFG, overrides=None, _callbacks=None):
        """Initialize predictor."""
        super().__init__(cfg, overrides, _callbacks)
        self.args.task = "detect"

    def construct_result(self, pred, img, orig_img, img_path):
        """Construct result with detection boxes and action predictions.

        Args:
            pred: (N, 6 + na) tensor where na = num_actions * classes_per_action.
            img: Preprocessed image tensor.
            orig_img: Original image.
            img_path: Image file path.

        Returns:
            Results object with boxes and action_preds attribute.
        """
        result = super().construct_result(pred, img, orig_img, img_path)

        # Extract action predictions from extra columns
        action_raw = pred[:, 6:]  # (N, na)
        if action_raw.numel() > 0 and hasattr(self.model, "model"):
            head = self.model.model[-1]
            if hasattr(head, "action_shape"):
                num_actions, classes_per_action = head.action_shape
                # Reshape and take argmax per action head
                actions = action_raw.view(-1, num_actions, classes_per_action)
                action_preds = actions.argmax(dim=2)  # (N, num_actions) class indices
                result.action_preds = action_preds
                result.action_probs = actions.softmax(dim=2)  # (N, num_actions, classes_per_action)

        return result
