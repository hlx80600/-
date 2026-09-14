"""鞋头对位 ImgAct 双头推理（只出类别，不写臂）。

对标双槽 ``shoe_allign_controller_twice.predict_frame``：
- X（前进头）：0=到位/停，1=向前，2=向后
- Y（侧向头）：0=停，1=左，2=右

Station 运动仍走控制同事的 ``toe_place_assist``；这里只提供数字。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from vision.numpy_compat import np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
_ALIGN = ROOT / "shoe_align"
if _ALIGN.is_dir() and str(_ALIGN) not in sys.path:
    sys.path.insert(0, str(_ALIGN))
_INFER: Optional["ToeImgActInfer"] = None
_INFER_KEY = ""


def later_model_path(model_path: str | Path) -> Path:
    """由前进模型路径推导侧向模型：stem 后加 ``_later``。

    model_path: str | Path: 前进权重
    return: Path: 侧向权重路径
    """
    path = Path(model_path)
    return path.with_name(f"{path.stem}_later{path.suffix}")


def _preprocess_bgr(frame_bgr: Any, imgsz: int) -> Any:
    """OpenCV Resize+CenterCrop+ToTensor，与双槽 preprocess 一致。"""
    import cv2
    import torch

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    if height < width:
        new_h, new_w = imgsz, int(round(width * imgsz / height))
    else:
        new_w, new_h = imgsz, int(round(height * imgsz / width))
    rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    height, width = rgb.shape[:2]
    y0 = max(0, (height - imgsz) // 2)
    x0 = max(0, (width - imgsz) // 2)
    rgb = rgb[y0 : y0 + imgsz, x0 : x0 + imgsz]
    return torch.from_numpy(rgb).permute(2, 0, 1).contiguous().float().div_(255.0).unsqueeze(0)


class ToeImgActInfer:
    """加载前进+侧向 DiscreteMultiAction 模型，只做 ``predict``。"""

    def __init__(
        self,
        model_path: str | Path,
        later_path: str | Path | None = None,
        *,
        imgsz: int = 256,
        device: str | None = None,
    ) -> None:
        import torch
        from shoe_align.imgact_compat import register_casbotxyz_aliases
        from ImgAct.models.casbot.action.model import DiscreteMultiActionModel
        from ImgAct.nn.modules.head import DiscreteMultiActionHead

        register_casbotxyz_aliases()
        self.imgsz = int(imgsz)
        if device:
            self._device = torch.device(device)
        else:
            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        fwd = DiscreteMultiActionModel(str(model_path), task="classify")
        lat = DiscreteMultiActionModel(
            str(later_path or later_model_path(model_path)), task="classify"
        )
        self._fwd_net, self._fwd_heads, self._fwd_cls = self._prepare(fwd, DiscreteMultiActionHead)
        self._lat_net, self._lat_heads, self._lat_cls = self._prepare(lat, DiscreteMultiActionHead)

    def _prepare(self, model: Any, head_cls: Any) -> tuple[Any, int, int]:
        net = model.model.to(self._device).eval()
        module = net.model if hasattr(net, "model") else net
        last = (
            module[-1]
            if hasattr(module, "__getitem__")
            else list(module.children())[-1]
        )
        if isinstance(last, head_cls):
            return net, int(last.num_heads), int(last.classes_per_head)
        return net, 1, 3

    def predict(self, image_bgr: Any) -> tuple[str, float, str, float]:
        """推理一帧。

        image_bgr: ndarray: BGR
        return: tuple: (x_label, x_conf, y_label, y_conf)，label 为 ``"0"|"1"|"2"``
        """
        import torch

        tensor = _preprocess_bgr(image_bgr, self.imgsz).to(self._device)
        with torch.inference_mode():
            fwd = self._fwd_net(tensor)[0].view(self._fwd_heads, self._fwd_cls).softmax(dim=1)
            lat = self._lat_net(tensor)[0].view(self._lat_heads, self._lat_cls).softmax(dim=1)
            x_label = str(int(fwd[0].argmax().item()))
            x_conf = float(fwd[0].max().item())
            y_label = str(int(lat[0].argmax().item()))
            y_conf = float(lat[0].max().item())
        return x_label, x_conf, y_label, y_conf


def get_toe_imgact(
    model_path: str | Path,
    later_path: str | Path | None = None,
    *,
    imgsz: int = 256,
) -> ToeImgActInfer:
    """懒加载并缓存推理器。

    model_path: str | Path: 前进权重
    later_path: str | Path | None: 侧向权重；空则自动 ``_later``
    imgsz: int: 输入边长
    return: ToeImgActInfer
    """
    global _INFER, _INFER_KEY
    later = str(Path(later_path) if later_path else later_model_path(model_path))
    key = f"{Path(model_path).resolve()}|{later}|{int(imgsz)}"
    if _INFER is None or _INFER_KEY != key:
        _INFER = ToeImgActInfer(model_path, later, imgsz=imgsz)
        _INFER_KEY = key
        log.info("[视觉] ImgAct 鞋头对位已加载 %s", key)
    return _INFER


def reset_toe_imgact() -> None:
    """丢掉推理缓存（换权重后调用）。"""
    global _INFER, _INFER_KEY
    _INFER = None
    _INFER_KEY = ""
