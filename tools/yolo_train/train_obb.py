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
    ap.add_argument("--data", type=Path, default=None, help="data.yaml 路径")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--no-install", action="store_true")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise SystemExit(
            "未安装 ultralytics。先执行：\n"
            "  python3 -m pip install --user ultralytics torch torchvision\n"
            f"详情: {e}"
        ) from e

    cfg = TASK_CFG[args.task]
    if args.data:
        data_yaml = args.data.resolve()
    else:
        data_yaml = _ensure_data_yaml(cfg["data_dir"], cfg["names"])
    if not data_yaml.exists():
        raise SystemExit(f"找不到 {data_yaml}")

    base = args.base or cfg["base"]
    device = args.device or None
    print(f"任务={args.task}  data={data_yaml}  基座={base}")
    print(
        "注意：皮带 ShoeVision 生产栈若用 casbot ultralytics_obb360，"
        "标准 yolov8-obb 权重需在该环境验证；槽分类/鞋头对位无此限制。"
    )
    model = YOLO(base)
    results = model.train(
        data=str(data_yaml),
        epochs=int(args.epochs),
        imgsz=int(args.imgsz),
        batch=int(args.batch),
        project=str(ROOT / "runs" / "obb"),
        name=args.task,
        exist_ok=True,
        device=device,
    )
    save_dir = Path(getattr(results, "save_dir", ROOT / "runs" / "obb" / args.task))
    best = save_dir / "weights" / "best.pt"
    if not best.exists():
        cands = list((ROOT / "runs" / "obb" / args.task).rglob("best.pt"))
        best = cands[0] if cands else best
    print(f"训练完成: {best}")
    if not args.no_install and best.exists():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from install_model import install_pt

        install_pt(best, cfg["install"], task=args.task)


if __name__ == "__main__":
    main()
