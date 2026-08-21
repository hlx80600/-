"""鞋槽分类检测封装：运行 YOLO 二分类并返回原始结果。

模型类别约定：
- ``0``：没鞋
- ``1``：有鞋

典型用法::

    checker = SlotChecker()
    class_id = checker.classify_from_camera(camera)  # 0=没鞋, 1=有鞋
    print(class_id)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time
from typing import Any, Mapping

import cv2
import numpy as np
from ultralytics import YOLO

_MODEL_CACHE: dict[str, YOLO] = {}

SLOT_CHECK_MODEL_PATH = Path(
    Path(__file__).resolve().parent / "models" / "slot_check" / "7.10slot_check.pt"
)
SLOT_CHECK_ARTIFACTS_DIR = Path(__file__).resolve().parent / "position_artifacts" / "slot_check"

CLASS_NO_SHOE = 0
CLASS_HAS_SHOE = 1

_CLASS_LABELS = {
    CLASS_NO_SHOE: "no_shoe",
    CLASS_HAS_SHOE: "has_shoe",
}


@dataclass(slots=True)
class SlotCheckResult:
    """单帧鞋槽分类原始结果。"""

    class_id: int
    class_name: str
    confidence: float
    probs: dict[str, float] = field(default_factory=dict)
    image_bgr: np.ndarray | None = None


def get_slot_check_model(model_path: str | Path | None = None) -> YOLO:
    """按路径缓存并返回 YOLO 分类模型。"""
    path = SLOT_CHECK_MODEL_PATH if model_path is None else Path(model_path)
    key = str(path.expanduser().resolve())
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = YOLO(key)
    return _MODEL_CACHE[key]


def grab_color_frame(
    camera: Any,
    *,
    max_retries: int = 10,
    draw_depth_colormap: bool = False,
) -> np.ndarray:
    """从相机对象取一帧 BGR 彩色图。

    约定与 ``shoe_vision_seg`` / ``shoe_allign_controller`` 一致：
    相机需提供 ``get_one_frame(...)``，返回 ``(color_bgr, depth, depth_color)``。
    """
    if camera is None:
        raise RuntimeError("相机对象为空")

    get_frame = getattr(camera, "get_one_frame", None)
    if get_frame is None:
        raise RuntimeError("相机对象缺少 get_one_frame 方法")

    last_error: Exception | None = None
    for _ in range(max(1, int(max_retries))):
        try:
            try:
                frame = get_frame(draw_depth_colormap=draw_depth_colormap)
            except TypeError:
                try:
                    frame = get_frame(False)
                except TypeError:
                    frame = get_frame()
        except Exception as exc:
            last_error = exc
            continue

        if not isinstance(frame, (tuple, list)) or not frame:
            continue

        color_bgr = frame[0]
        if isinstance(color_bgr, np.ndarray) and color_bgr.size > 0:
            return color_bgr

    if last_error is not None:
        raise RuntimeError(f"无法从相机获取有效彩色帧: {last_error}")
    raise RuntimeError("无法从相机获取有效彩色帧")


def save_slot_check_frame(
    image_bgr: np.ndarray,
    *,
    save_dir: str | Path = SLOT_CHECK_ARTIFACTS_DIR,
    class_id: int = -1,
    confidence: float = 0.0,
    camera_name: str = "cam",
) -> Path | None:
    """保存鞋槽检测用的 BGR 原图。"""
    if image_bgr is None or not isinstance(image_bgr, np.ndarray) or image_bgr.size == 0:
        return None

    class_label = _CLASS_LABELS.get(class_id, "unknown")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    sample_id = f"slot_check_{camera_name}_{class_label}_{confidence:.3f}_{timestamp}"

    raw_dir = Path(save_dir).expanduser() / camera_name / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{sample_id}.jpg"
    if not cv2.imwrite(str(raw_path), image_bgr):
        print(f"保存鞋槽检测原图失败: {raw_path}")
        return None
    return raw_path


def stop_camera(camera: Any) -> None:
    """停止相机采集，避免进程退出时 Orbbec SDK 崩溃。"""
    if camera is None:
        return
    stop = getattr(camera, "stop", None)
    if stop is None:
        return
    try:
        stop()
    except Exception as exc:
        print(f"停止相机时出错: {exc}")


def _to_numpy(x: Any) -> np.ndarray | None:
    if x is None:
        return None
    try:
        import torch

        if isinstance(x, torch.Tensor):
            return x.detach().cpu().numpy()
    except Exception:
        pass
    return np.asarray(x)


def _collect_class_probs(result, names_map: dict) -> dict[str, float]:
    probs_obj = getattr(result, "probs", None)
    if probs_obj is None:
        return {}

    data = getattr(probs_obj, "data", None)
    if data is None:
        return {}

    arr = _to_numpy(data)
    if arr is None:
        return {}
    arr = np.asarray(arr).reshape(-1)
    slot_probs: dict[str, float] = {}
    for cls_id, prob in enumerate(arr):
        cls_name = names_map.get(cls_id, str(cls_id))
        slot_probs[str(cls_name)] = float(prob)
    return slot_probs


def classify_slot(
    image_bgr: np.ndarray,
    model_path: str | Path | None = None,
    *,
    imgsz: int = 640,
) -> SlotCheckResult:
    """对单帧图像运行鞋槽分类，返回 YOLO top1 与各类概率。"""
    model = get_slot_check_model(model_path)
    results = model.predict(image_bgr, imgsz=int(imgsz), verbose=False)
    if not results:
        return SlotCheckResult(class_id=-1, class_name="", confidence=0.0, probs={})

    result = results[0]
    names_map = getattr(result, "names", {}) or getattr(model, "names", {}) or {}
    probs = _collect_class_probs(result, names_map)

    probs_obj = getattr(result, "probs", None)
    top1 = int(getattr(probs_obj, "top1", -1)) if probs_obj is not None else -1
    top1conf = float(getattr(probs_obj, "top1conf", 0.0)) if probs_obj is not None else 0.0
    top1_name = str(names_map.get(top1, str(top1))) if top1 >= 0 else ""

    return SlotCheckResult(
        class_id=top1,
        class_name=top1_name,
        confidence=top1conf,
        probs=probs,
        image_bgr=image_bgr,
    )


class SlotChecker:
    """鞋槽分类检测器。"""

    def __init__(
        self,
        *,
        imgsz: int = 512,
        model_path: str | Path | None = None,
        max_retries: int = 10,
        draw_depth_colormap: bool = False,
        save_images: bool = False,
        save_dir: str | Path | None = None,
        camera_name: str = "cam",
    ) -> None:
        self.model_path = str(
            SLOT_CHECK_MODEL_PATH if model_path is None else Path(model_path).expanduser()
        )
        self.imgsz = int(imgsz)
        self.max_retries = int(max_retries)
        self.draw_depth_colormap = bool(draw_depth_colormap)
        self.save_images = bool(save_images)
        self.save_dir = SLOT_CHECK_ARTIFACTS_DIR if save_dir is None else Path(save_dir).expanduser()
        self.camera_name = str(camera_name)

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "SlotChecker":
        """从 position_config.yaml 风格的配置字典构建。"""
        return cls(
            imgsz=int(config.get("slot_check_img_size", 640)),
        )

    def classify(self, image_bgr: np.ndarray) -> SlotCheckResult:
        """对 BGR 图像运行分类检测。"""
        return classify_slot(
            image_bgr,
            self.model_path,
            imgsz=self.imgsz,
        )

    def classify_from_camera(self, camera: Any) -> int:
        """从相机取一帧图像并运行分类检测，返回类别 id（0=没鞋，1=有鞋）。"""
        image_bgr = None
        while image_bgr is None:
            image_bgr, _, _ = camera.get_one_frame()
        result = self.classify(image_bgr)
        class_id = result.class_id
        confidence = result.confidence
        if self.save_images:
            saved_path = save_slot_check_frame(
                image_bgr,
                save_dir=self.save_dir,
                class_id=class_id,
                confidence=confidence,
                camera_name=self.camera_name,
            )
            if saved_path is not None:
                print(f"已存图: {saved_path}")
        print(class_id, confidence)
        return class_id


if __name__ == "__main__":
    from RSDT_Simple_Automation.automation_machine import automationMachine

    machine = automationMachine()
    # 左
    # machine.hardwareModule.activate_orbbec_camera("cam1", "CPC4B41000G0")
    # 右
    machine.hardwareModule.activate_orbbec_camera("cam1", "CPCLA5300066")
    camera = machine.hardwareModule.orbbec_camera_dict.get("cam1")
    flag = camera.connect_camera([1280, 720], [848, 480], 30)
    if not flag:
        print("相机连接失败")
        raise SystemExit(1)

    try:
        checker = SlotChecker(save_images=True, camera_name="cam1")
        while True:
            class_id = checker.classify_from_camera(camera)
            print(f"class_id: {class_id}")
            time.sleep(0.3)
    finally:
        stop_camera(camera)
