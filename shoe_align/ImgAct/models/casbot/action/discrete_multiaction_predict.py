# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license

from __future__ import annotations

import cv2
import torch
from PIL import Image

from ImgAct.data.augment import classify_transforms
from ImgAct.engine.predictor import BasePredictor
from ImgAct.engine.results import Results
from ImgAct.utils import DEFAULT_CFG, ops


class DiscreteMultiActionHeadPredictor(BasePredictor):
    """Predictor for discrete multi-action classification models."""

    def __init__(self, cfg=DEFAULT_CFG, overrides=None, _callbacks=None):
        super().__init__(cfg, overrides, _callbacks)
        self.args.task = "classify"

    def setup_source(self, source):
        """Set up source and classify transforms."""
        super().setup_source(source)
        updated = (
            self.model.model.transforms.transforms[0].size != max(self.imgsz)
            if hasattr(self.model.model, "transforms") and hasattr(self.model.model.transforms.transforms[0], "size")
            else False
        )
        self.transforms = (
            classify_transforms(self.imgsz) if updated or self.model.format != "pt" else self.model.model.transforms
        )

    def preprocess(self, img):
        """Convert input images to model-compatible tensor format."""
        if not isinstance(img, torch.Tensor):
            img = torch.stack(
                [self.transforms(Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))) for im in img], dim=0
            )
        img = (img if isinstance(img, torch.Tensor) else torch.from_numpy(img)).to(self.model.device)
        return img.half() if self.model.fp16 else img.float()

    def postprocess(self, preds, img, orig_imgs):
        """Process discrete multi-action predictions to return Results objects."""
        if not isinstance(orig_imgs, list):
            orig_imgs = ops.convert_torch2numpy_batch(orig_imgs)[..., ::-1]

        preds = preds[0] if isinstance(preds, (list, tuple)) else preds

        model_module = self.model.model
        if hasattr(model_module, "model"):
            last_layer = model_module.model[-1]
        else:
            last_layer = list(model_module.children())[-1]

        from ImgAct.nn.modules.head import DiscreteMultiActionHead

        if isinstance(last_layer, DiscreteMultiActionHead):
            num_heads = last_layer.num_heads
            classes_per_head = last_layer.classes_per_head
        else:
            num_heads = getattr(self.model, "num_heads", 3)
            classes_per_head = getattr(self.model, "classes_per_head", 3)

        results = []
        for pred, orig_img, img_path in zip(preds, orig_imgs, self.batch[0]):
            reshaped = pred.view(num_heads, classes_per_head)
            head_probs = reshaped.softmax(dim=1)
            head_preds = head_probs.argmax(dim=1)
            head_confs = head_probs.max(dim=1).values

            result = Results(orig_img, path=img_path, names=self.model.names, probs=head_probs.reshape(-1))
            result.multihead_logits = reshaped
            result.multihead_probs = head_probs
            result.head_preds = head_preds
            result.head_confs = head_confs
            result.num_heads = num_heads
            result.classes_per_head = classes_per_head
            results.append(result)

        return results