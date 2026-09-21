# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license
from __future__ import annotations

from pathlib import Path
from typing import Any

from ImgAct.models.casbot.action.discrete_multiaction_predict import DiscreteMultiActionHeadPredictor
from ImgAct.models.casbot.action.multiaction_predict import MultiActionHeadPredictor
from ImgAct.models.yolo.model import YOLO


class MultiActionModel(YOLO):
    """YOLO loader with MultiActionHeadPredictor selected by default."""

    default_predictor = MultiActionHeadPredictor

    def __init__(self, model: str | Path, task: str = "classify", verbose: bool = False):
        super().__init__(model, task=task, verbose=verbose)

    def predict(self, source=None, stream: bool = False, **kwargs: Any):
        kwargs.setdefault("predictor", self.default_predictor)
        return super().predict(source=source, stream=stream, **kwargs)


class DiscreteMultiActionModel(YOLO):
    """YOLO loader with DiscreteMultiActionHeadPredictor selected by default."""

    default_predictor = DiscreteMultiActionHeadPredictor

    def __init__(self, model: str | Path, task: str = "classify", verbose: bool = False):
        super().__init__(model, task=task, verbose=verbose)

    def predict(self, source=None, stream: bool = False, **kwargs: Any):
        kwargs.setdefault("predictor", self.default_predictor)
        return super().predict(source=source, stream=stream, **kwargs)
