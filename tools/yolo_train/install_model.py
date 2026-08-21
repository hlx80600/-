#!/usr/bin/env python3
"""把训好的 best.pt 装到 models/，并提示如何改 yaml/json。"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 任务 -> 改配置时写什么
CFG_HINT = {
    "slot_check": (
        "config/default.yaml → vision.slot_check.model_path: models/slot_check/<文件名>"
    ),
    "toe_align": (
        "config/default.yaml → vision.toe_align.model_path: models/toe_align/<文件名>"
    ),
    "shoe_lr": (
        "shoe_vision_config.json → shoe_cls_model_path: models/shoe_vision/<文件名>"
    ),
    "shoe_obb": (
        "shoe_vision_config.json → shoe_model_path: models/shoe_vision/<文件名>"
    ),
    "last_obb": (
        "shoe_vision_config.json → shoe_tree_model_path: models/shoe_vision/<文件名>"
    ),
    "rod_obb": (
        "config/default.yaml → vision.position.rod_model_path: models/position/rod/<文件名>\n"
        "  同时检查 position_config.yaml → rod_obb_model_path"
    ),
}


def install_pt(src: Path, dst: Path, task: str = "") -> Path:
    src = Path(src).expanduser().resolve()
    dst = Path(dst)
    if not dst.is_absolute():
        dst = ROOT / dst
    if not src.exists():
        raise SystemExit(f"源文件不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 若目标是软链指向旧模型，先删链再写真实文件，避免覆盖旧工程
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    print(f"已安装: {src} → {dst}")
    if task and task in CFG_HINT:
        print("请改配置指向新文件：")
        print(" ", CFG_HINT[task].replace("<文件名>", dst.name))
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, help="best.pt 或任意 .pt")
    ap.add_argument(
        "--to",
        type=Path,
        required=True,
        help="目标，如 models/slot_check/custom_slot_check.pt",
    )
    ap.add_argument(
        "--task",
        default="",
        choices=[""] + sorted(CFG_HINT.keys()),
        help="用于打印改配置提示",
    )
    args = ap.parse_args()
    install_pt(args.src, args.to, task=args.task)


if __name__ == "__main__":
    main()
