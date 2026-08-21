#!/usr/bin/env python3
"""从本程序相机（或已有图片文件夹）采集 YOLO 训练图。

示例：
  # 用 cam3 采「槽有无鞋」分类图（先 Mock 出图也行，真机更好）
  python3 tools/yolo_train/capture_dataset.py --cam cam3 --task slot_check --n 80

  # 从目录导入已有照片
  python3 tools/yolo_train/capture_dataset.py --from-dir /path/to/jpgs --task toe_align

采完后：分类任务请把图挪到 datasets/<task>/train/<类名>/ ；
OBB 任务请用 labelImg / Roboflow / CVAT 标旋转框，再写 labels/*.txt。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TASKS = {
    "slot_check": {
        "cam": "cam3",
        "hint": "分类：把图分到 train/empty 与 train/has_shoe（0空槽/1有鞋）",
    },
    "toe_align": {
        "cam": "cam2",
        "hint": "分类：train/aligned(0到位) 与 train/forward(1需前进)；若有左右偏可加 left/right",
    },
    "shoe_lr": {
        "cam": "cam1",
        "hint": "分类：train/left 与 train/right（鞋头朝上左右脚）",
    },
    "shoe_obb": {
        "cam": "cam1",
        "hint": "OBB：先采整图，再用工具标旋转框 class=shoe",
    },
    "last_obb": {
        "cam": "cam1",
        "hint": "OBB：标鞋楦旋转框 class=last（楦）",
    },
    "rod_obb": {
        "cam": "cam4",
        "hint": "OBB：标压杆/夹爪 class=rod",
    },
}


def _save_bgr(path: Path, img) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def capture_from_cam(cam_id: str, out_dir: Path, n: int, interval_s: float) -> int:
    from core.app_context import AppContext

    ctx = AppContext()
    cam = ctx.cameras.get(cam_id)
    if cam is None:
        raise SystemExit(f"无相机 {cam_id}")
    if not cam.opened and not cam.use_mock:
        cam.open()
    ok = 0
    raw_dir = out_dir / "raw"
    for i in range(n):
        img = cam.grab(wait_s=0.5)
        if img is None:
            print(f"[{i+1}/{n}] 无图，跳过")
            time.sleep(interval_s)
            continue
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = raw_dir / f"{cam_id}_{ts}.jpg"
        _save_bgr(path, img)
        ok += 1
        print(f"[{ok}/{n}] {path}")
        time.sleep(interval_s)
    return ok


def import_dir(src: Path, out_dir: Path) -> int:
    import shutil

    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    n = 0
    for p in sorted(src.rglob("*")):
        if p.suffix.lower() not in exts:
            continue
        dst = raw_dir / p.name
        if dst.exists():
            dst = raw_dir / f"{p.stem}_{n}{p.suffix}"
        shutil.copy2(p, dst)
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="采集 YOLO 训练图")
    ap.add_argument("--task", choices=sorted(TASKS.keys()), required=True)
    ap.add_argument("--cam", default="", help="覆盖默认相机 id")
    ap.add_argument("--n", type=int, default=50, help="拍摄张数")
    ap.add_argument("--interval", type=float, default=0.4, help="间隔秒")
    ap.add_argument("--from-dir", type=Path, default=None, help="从已有图片目录导入")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出根目录，默认 datasets/<task>/",
    )
    args = ap.parse_args()
    meta = TASKS[args.task]
    out = args.out or (ROOT / "datasets" / args.task)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.txt").write_text(meta["hint"] + "\n", encoding="utf-8")

    if args.from_dir:
        n = import_dir(args.from_dir.expanduser().resolve(), out)
        print(f"已导入 {n} 张 → {out / 'raw'}")
    else:
        cam_id = (args.cam or meta["cam"]).strip()
        n = capture_from_cam(cam_id, out, max(1, args.n), max(0.05, args.interval))
        print(f"已拍摄 {n} 张 → {out / 'raw'}")

    print()
    print("下一步：")
    print(f"  1) 阅读 {out / 'README.txt'}")
    if args.task in ("slot_check", "toe_align", "shoe_lr"):
        print(f"  2) 建类目录并挪图，例如：")
        print(f"       mkdir -p {out}/train/empty {out}/train/has_shoe")
        print(f"       # 把 raw/ 里的图按类拷进去，再留约 20% 到 val/<类名>/")
        print(f"  3) python3 tools/yolo_train/train_classify.py --task {args.task}")
    else:
        print(f"  2) 用标注工具标 OBB，导出 YOLO-OBB 格式到 {out}/images 与 {out}/labels")
        print(f"  3) python3 tools/yolo_train/train_obb.py --task {args.task}")


if __name__ == "__main__":
    main()
