from __future__ import annotations
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

# Make repo root importable
ROOT = os.path.dirname(os.path.dirname(__file__))
ROOT = os.path.dirname(ROOT)
sys.path.append(ROOT)
# Add ultralytics path to handle internal relative imports
sys.path.append(os.path.join(ROOT, "casbot123" ))

from ImgAct.nn.tasks import DetectionModel, yaml_model_load


def synchronize_if_needed(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def describe_output(output) -> str:
    if isinstance(output, torch.Tensor):
        return str(tuple(output.shape))
    if isinstance(output, (list, tuple)):
        return ", ".join(describe_output(item) for item in output)
    return type(output).__name__


def print_conv_feature_map_sizes(model: nn.Module, img: torch.Tensor, sensor: torch.Tensor) -> None:
    conv_summaries: list[dict[str, object]] = []
    hooks = []

    def make_hook(name: str, module: nn.Conv2d):
        def hook(_module: nn.Module, _inputs, output) -> None:
            if not isinstance(output, torch.Tensor) or output.ndim < 4:
                return
            conv_summaries.append(
                {
                    "name": name,
                    "out_channels": module.out_channels,
                    "kernel_size": module.kernel_size,
                    "stride": module.stride,
                    "height": int(output.shape[-2]),
                    "width": int(output.shape[-1]),
                }
            )

        return hook

    for name, module in model.named_modules():
        if isinstance(module, nn.Conv2d):
            hooks.append(module.register_forward_hook(make_hook(name, module)))

    try:
        with torch.inference_mode():
            model(img, sensor_data=sensor)
    finally:
        for hook in hooks:
            hook.remove()

    print("Per-conv feature map sizes:")
    prev_height = None
    prev_width = None
    for idx, summary in enumerate(conv_summaries, start=1):
        height = summary["height"]
        width = summary["width"]
        halved = ""
        if prev_height is not None and prev_width is not None:
            if height * 2 == prev_height and width * 2 == prev_width:
                halved = " <- spatial size halved"
        print(
            f"[{idx:03d}] {summary['name']}: "
            f"kernels={summary['out_channels']}, "
            f"kernel_size={summary['kernel_size']}, "
            f"stride={summary['stride']}, "
            f"feature_map={height}x{width}{halved}"
        )
        prev_height = height
        prev_width = width

if __name__ == "__main__":
    if torch.cuda.is_available():
        device = torch.device("cuda:0")
        torch.backends.cudnn.benchmark = True
        print(f"Running inference on GPU: {torch.cuda.get_device_name(device)}")
    else:
        device = torch.device("cpu")
        print("CUDA is not available. Running inference on CPU.")

    warmup_iters = 20
    benchmark_iters = 100

    # 1. Load the model with sensor fusion
    cfg = str(Path("/home/lcjpc/lcj/ultralytics/casbot123/cfg/models/26/ImgAct.yaml"))
    cfg_dict = yaml_model_load(cfg)
    # sensor_dim = int(cfg_dict.get("sensor_dim", 0))
    # if sensor_dim <= 0:
    #     raise ValueError(f"Expected cfg to define a positive sensor_dim, got {sensor_dim} from {cfg}")
    model = DetectionModel(cfg, ch=16).to(device).eval()

    # 2. Prepare dummy inputs
    # Image: (Batch, Channels, Height, Width)
    img = torch.randn(1, 16, 640, 640, device=device)
    # Sensor data: (Batch, SensorDim) - follow the YAML sensor_dim exactly.
    # sensor = torch.randn(1, sensor_dim, device=device)

    try:
        with torch.inference_mode():
            # print_conv_feature_map_sizes(model, img)

            for _ in range(warmup_iters):
                model(img)

            synchronize_if_needed(device)
            start_time = time.perf_counter()
            for _ in range(benchmark_iters):
                output = model(img)
            synchronize_if_needed(device)
            elapsed = time.perf_counter() - start_time

        avg_latency_ms = elapsed * 1000 / benchmark_iters
        throughput_fps = benchmark_iters / elapsed
        print("Inference successful!")
        print(f"Output shape(s): {describe_output(output)}")
        print(f"Warmup iterations: {warmup_iters}")
        print(f"Benchmark iterations: {benchmark_iters}")
        print(f"Average latency: {avg_latency_ms:.3f} ms")
        print(f"Throughput: {throughput_fps:.2f} FPS")
    except Exception as e:
        print(f"Inference failed: {e}")
        import traceback
        traceback.print_exc()
