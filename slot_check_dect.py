"""鞋槽 YOLO 检测：有框=有鞋（对标双槽 ``slot_check_dect.py``）。

模型约定为 detect（单类）：
- 检测到目标框：有鞋，``has_shoe=1``
- 未检测到目标框：没鞋，``has_shoe=0``

不要和 ``slot_check.py`` 的 good/bad 分类、也不要和卡鞋 ``postion_slot_check`` 混用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from ultralytics import YOLO

_MODEL_CACHE: dict[str, YOLO] = {}

SLOT_CHECK_MODEL_PATH = (
    Path(__file__).resolve().parent / "models" / "slot_check" / "7.13_dect_1.pt"
)

CLASS_NO_SHOE = 0
CLASS_HAS_SHOE = 1


@dataclass(slots=True)
class SlotDetectResult:
    """单帧鞋槽检测结果。"""

    has_shoe: int
    class_id: int
    confidence: float
    num_boxes: int = 0
    image_bgr: NDArray[np.uint8] | None = None


def get_slot_detect_model(model_path: str | Path | None = None) -> YOLO:
    """按路径缓存并返回 YOLO 检测模型。

    model_path: str | Path | None: 权重；空则用默认 ``7.13_dect_1.pt``
    return: YOLO: 已加载模型
    """
    path = SLOT_CHECK_MODEL_PATH if model_path is None else Path(model_path)
    key = str(path.expanduser().resolve())
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = YOLO(key)
    return _MODEL_CACHE[key]


get_slot_check_model = get_slot_detect_model


def detect_shoe_in_image(
    image_bgr: NDArray[np.uint8],
    model_path: str | Path | None = None,
    *,
    imgsz: int = 640,
    conf: float = 0.1,
) -> SlotDetectResult:
    """对单帧 BGR 跑检测，返回是否有鞋。

    image_bgr: NDArray[np.uint8]: BGR，shape (H, W, 3)
    model_path: str | Path | None: 权重路径
    imgsz: int: 推理边长
    conf: float: 检测置信度门槛
    return: SlotDetectResult: 有鞋/框数/置信度
    """
    model = get_slot_detect_model(model_path)
    task = getattr(model, "task", "detect")
    results = model.predict(
        image_bgr,
        imgsz=int(imgsz),
        conf=float(conf),
        verbose=False,
    )
    if not results:
        return SlotDetectResult(has_shoe=CLASS_NO_SHOE, class_id=-1, confidence=0.0)

    result = results[0]

    if task == "classify":
        probs_obj = getattr(result, "probs", None)
        class_id = (
            int(getattr(probs_obj, "top1", CLASS_NO_SHOE))
            if probs_obj is not None
            else CLASS_NO_SHOE
        )
        confidence = (
            float(getattr(probs_obj, "top1conf", 0.0))
            if probs_obj is not None
            else 0.0
        )
        has_shoe = CLASS_HAS_SHOE if class_id == CLASS_HAS_SHOE else CLASS_NO_SHOE
        return SlotDetectResult(
            has_shoe=has_shoe,
            class_id=class_id,
            confidence=confidence,
            image_bgr=image_bgr,
        )

    boxes = getattr(result, "boxes", None)
    num_boxes = len(boxes) if boxes is not None else 0
    if num_boxes == 0:
        return SlotDetectResult(
            has_shoe=CLASS_NO_SHOE,
            class_id=CLASS_NO_SHOE,
            confidence=0.0,
            num_boxes=0,
            image_bgr=image_bgr,
        )

    best_idx = int(boxes.conf.argmax())
    confidence = float(boxes.conf[best_idx])
    return SlotDetectResult(
        has_shoe=CLASS_HAS_SHOE,
        class_id=CLASS_HAS_SHOE,
        confidence=confidence,
        num_boxes=num_boxes,
        image_bgr=image_bgr,
    )


class SlotChecker:
    """鞋槽检测器：有鞋返回 1，没鞋返回 0。"""

    def __init__(
        self,
        *,
        imgsz: int = 640,
        conf: float = 0.25,
        model_path: str | Path | None = None,
    ) -> None:
        self.model_path = str(
            SLOT_CHECK_MODEL_PATH if model_path is None else Path(model_path).expanduser()
        )
        self.imgsz = int(imgsz)
        self.conf = float(conf)

    def classify(self, image_bgr: NDArray[np.uint8]) -> SlotDetectResult:
        """对 BGR 图像检测是否有鞋。

        image_bgr: NDArray[np.uint8]: 输入图
        return: SlotDetectResult: 检测结果
        """
        return detect_shoe_in_image(
            image_bgr,
            self.model_path,
            imgsz=self.imgsz,
            conf=self.conf,
        )

    def detect(self, image_bgr: NDArray[np.uint8]) -> int:
        """对 BGR 图像检测是否有鞋，返回 0 或 1。

        image_bgr: NDArray[np.uint8]: 输入图
        return: int: 0=没鞋，1=有鞋
        """
        return int(self.classify(image_bgr).has_shoe)


SlotShoeDetector = SlotChecker
