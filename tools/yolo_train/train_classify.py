#!/usr/bin/env python3
"""训练 YOLO 分类模型（槽有无鞋 / 鞋头对位 / 左右脚）。

目录约定（Ultralytics classify）：
  datasets/<task>/
    train/<类名>/*.jpg
    val/<类名>/*.jpg

类名与程序约定见 README.md。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# task -> (默认数据目录, 安装到的目标 .pt, 推荐基座模型)
TASK_CFG = {
    "slot_check": {
        "data": ROOT / "datasets" / "slot_check",
        "install": ROOT / "models" / "slot_check" / "custom_slot_check.pt",
        "classes_hint": "empty(=0), has_shoe(=1)  文件夹名可改，但训练后需按 0/1 顺序对齐",
        "base": "yolov8n-cls.pt",
    },
    "toe_align": {
        "data": ROOT / "datasets" / "toe_align",
        "install": ROOT / "models" / "toe_align" / "custom_toe_align.pt",
        "classes_hint": "aligned(=0到位), forward(=1向前)",
        "base": "yolov8n-cls.pt",
    },
    "shoe_lr": {
        "data": ROOT / "datasets" / "shoe_lr",
        "install": ROOT / "models" / "shoe_vision" / "custom_鞋头朝上左右脚分类.pt",
        "classes_hint": "left, right（类名含左/右或 left/right）",
        "base": "yolov8n-cls.pt",
    },
}


def _check_layout(data: Path) -> None:
    train = data / "train"
    if not train.is_dir():
        raise SystemExit(f"缺少 {train}，请先建 train/<类名>/ 并放图")
    classes = [p for p in train.iterdir() if p.is_dir()]
    if len(classes) < 2:
        raise SystemExit(f"{train} 下至少要有 2 个类文件夹，当前: {[c.name for c in classes]}")
    val = data / "val"
    if not val.is_dir():
        print(f"警告: 无 {val}，将用 train 做 val（效果较差，建议拆 20%）")
        val.mkdir(parents=True, exist_ok=True)
        for c in classes:
            dst = val / c.name
            dst.mkdir(parents=True, exist_ok=True)
            imgs = list(c.glob("*"))
            for p in imgs[::5][: max(1, len(imgs) // 5)]:
                if p.is_file():
                    shutil.copy2(p, dst / p.name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASK_CFG.keys()), required=True)
    ap.add_argument("--data", type=Path, default=None)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=224)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="", help="空=自动；cpu / 0 / 0,1")
    ap.add_argument("--base", default="", help="覆盖基座权重，如 yolov8s-cls.pt")
    ap.add_argument("--no-install", action="store_true", help="不自动拷到 models/")
    args = ap.parse_args()

    cfg = TASK_CFG[args.task]
    data = (args.data or cfg["data"]).resolve()
    _check_layout(data)
    if args.data is not None and data != cfg["data"].resolve():
        print(f"提示：统一入口使用 datasets/{args.task}/，请把图放到该目录。当前检查了 {data}")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from vision import ultralytics_hparams as uhp
    from vision.ultralytics_runner import cmd_train

    hp = uhp.load_hparams(args.task, task="classify")
    hp["epochs"] = int(args.epochs)
    hp["imgsz"] = int(args.imgsz)
    hp["batch"] = int(args.batch)
    if args.device:
        hp["device"] = args.device
    if args.base:
        hp["model"] = args.base
    path = uhp.save_hparams(args.task, hp)
    raise SystemExit(cmd_train(args.task, path, no_install=bool(args.no_install)))


if __name__ == "__main__":
    main()
