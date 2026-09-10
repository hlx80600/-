#!/usr/bin/env python3
"""训练 YOLO-OBB（鞋 / 楦 / 压杆）。

数据约定：
  datasets/<task>/
    images/train/*.jpg
    images/val/*.jpg
    labels/train/*.txt   # YOLO OBB: cls x1 y1 x2 y2 x3 y3 x4 y4 (归一化)
    labels/val/*.txt
    data.yaml

也可用 --data 直接指向已有 data.yaml。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]

TASK_CFG = {
    "shoe_obb": {
        "data_dir": ROOT / "datasets" / "shoe_obb",
        "install": ROOT / "models" / "shoe_vision" / "custom_鞋obb.pt",
        "names": {0: "shoe"},
        "base": "yolov8n-obb.pt",
    },
    "last_obb": {
        "data_dir": ROOT / "datasets" / "last_obb",
        "install": ROOT / "models" / "shoe_vision" / "custom_鞋楦obb.pt",
        "names": {0: "last"},
        "base": "yolov8n-obb.pt",
    },
    "rod_obb": {
        "data_dir": ROOT / "datasets" / "rod_obb",
        "install": ROOT / "models" / "position" / "rod" / "custom_obb.pt",
        "names": {0: "rod"},
        "base": "yolov8n-obb.pt",
    },
}


def _ensure_data_yaml(data_dir: Path, names: dict) -> Path:
    yaml_path = data_dir / "data.yaml"
    if yaml_path.exists():
        return yaml_path
    img_train = data_dir / "images" / "train"
    if not img_train.is_dir():
        raise SystemExit(
            f"缺少 {img_train}。请先采集并标注，或用 --data 指定已有 data.yaml"
        )
    names_block = "\n".join(f"  {k}: {v}" for k, v in names.items())
    yaml_path.write_text(
        dedent(
            f"""\
            path: {data_dir.as_posix()}
            train: images/train
            val: images/val
            names:
            {names_block}
            """
        ),
        encoding="utf-8",
    )
    print(f"已生成 {yaml_path}")
    return yaml_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASK_CFG.keys()), required=True)
    ap.add_argument("--data", type=Path, default=None, help="data.yaml 路径（默认用槽位 datasets/）")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--no-install", action="store_true")
    args = ap.parse_args()

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from vision import ultralytics_hparams as uhp
    from vision.ultralytics_runner import cmd_train, ensure_data_yaml

    if args.data is None:
        ensure_data_yaml(args.task)
    else:
        print(f"提示：统一入口使用 datasets/{args.task}/data.yaml；传入的 {args.data} 仅作提示")
        _ensure_data_yaml(TASK_CFG[args.task]["data_dir"], TASK_CFG[args.task]["names"])

    hp = uhp.load_hparams(args.task, task="obb")
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
