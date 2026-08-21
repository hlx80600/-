"""SAM 模型加载工具。"""

import os

import torch

try:
    from segment_anything import sam_model_registry
except ImportError as e:
    raise ImportError("请先安装 segment-anything: pip install git+https://github.com/facebookresearch/segment-anything.git") from e


def load_sam(checkpoint: str, model_type: str, device: str | None = None):
    """加载 SAM 模型并移动到指定设备。

    Parameters
    ----------
    checkpoint : str
        权重文件路径（如 ``weights/sam_vit_b_01ec64.pth``）。
    model_type : str
        模型规格，可选 ``"vit_h"`` / ``"vit_l"`` / ``"vit_b"``。
    device : str, optional
        运行设备。默认自动选择：有 GPU 用 ``"cuda"``，否则 ``"cpu"``。

    Returns
    -------
    sam : segment_anything.modeling.Sam
    """
    if not os.path.exists(checkpoint):
        raise FileNotFoundError(
            f"找不到权重文件: {checkpoint}\n"
            "可通过以下命令下载 vit_b（375 MB）:\n"
            "  wget -P weights/ https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
        )
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    sam = sam_model_registry[model_type](checkpoint=checkpoint)
    sam.to(device=device)
    return sam
