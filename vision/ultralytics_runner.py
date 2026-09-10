#!/usr/bin/env python3
"""Ultralytics 统一入口：train / val / predict / export。

HMI 与命令行共用。训练超参见 datasets/<slot>/hparams.yaml。
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _need_yolo():
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise SystemExit(
            "未安装 ultralytics。在 HMI「模型」步点「安装 ultralytics」，或：\n"
            "  python3 -m pip install --user ultralytics torch torchvision\n"
            f"详情: {e}"
        ) from e
    return YOLO


def _runs_dir(task: str) -> Path:
    name = {
        "classify": "classify",
        "detect": "detect",
        "obb": "obb",
        "segment": "segment",
    }.get(task, task)
    return ROOT / "runs" / name


def ensure_data_yaml(slot_id: str) -> Path:
    from vision import model_store as mstore

    meta = mstore.slot_meta(slot_id)
    data_dir = mstore.dataset_dir(slot_id)
    task = mstore.slot_task(slot_id)
    yaml_path = data_dir / "data.yaml"
    names = meta.get("names") or {}
    if not names:
        classes = list(meta.get("classes") or [])
        names = {i: str(c) for i, c in enumerate(classes)}
    if not names:
        names = {0: "obj"}
    names_block = "\n".join(f"  {k}: {v}" for k, v in names.items())
    if task == "classify":
        return data_dir
    if not yaml_path.exists():
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


def parse_results_csv(path: Path) -> dict[str, list[float]]:
    """读 ultralytics results.csv，返回列名 -> 数值序列。"""
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    headers = [h.strip() for h in rows[0]]
    cols: dict[str, list[float]] = {h: [] for h in headers}
    for row in rows[1:]:
        for i, h in enumerate(headers):
            if i >= len(row):
                continue
            try:
                cols[h].append(float(row[i].strip()))
            except ValueError:
                pass
    return cols


def find_run_dir(slot_id: str, task: str) -> Path | None:
    d = _runs_dir(task) / slot_id
    if d.is_dir():
        return d
    cands = list(_runs_dir(task).glob(f"{slot_id}*"))
    return cands[0] if cands else None


def find_weight(slot_id: str, task: str, *, last: bool = False) -> Path | None:
    from vision import model_store as mstore

    name = "last.pt" if last else "best.pt"
    run = find_run_dir(slot_id, task)
    if run is not None:
        p = run / "weights" / name
        if p.is_file():
            return p
        hits = list(run.rglob(name))
        if hits:
            return hits[0]
    inst = ROOT / str(mstore.slot_meta(slot_id).get("install") or "")
    if inst.is_file():
        return inst
    return None


def cmd_train(slot_id: str, hparams_file: Path | None, *, no_install: bool) -> int:
    from vision import model_store as mstore
    from vision import ultralytics_hparams as uhp

    YOLO = _need_yolo()
    task = mstore.slot_task(slot_id)
    hp = uhp.load_hparams(slot_id, task=task)
    if hparams_file is not None and hparams_file.is_file():
        import yaml

        extra = yaml.safe_load(hparams_file.read_text(encoding="utf-8")) or {}
        if isinstance(extra, dict):
            hp.update(extra)
    task = str(hp.get("task") or task)
    data = ensure_data_yaml(slot_id)
    model_src = str(hp.get("model") or uhp.DEFAULT_MODEL.get(task, "yolov8n.pt"))
    resume = bool(hp.get("resume"))
    if resume:
        last = find_weight(slot_id, task, last=True)
        if last is not None:
            model_src = str(last)
            print(f"从 last.pt 继续: {last}")
        else:
            print("没有 last.pt，改为普通训练")
            resume = False
    print(f"任务={slot_id} type={task}  data={data}  基座={model_src}")
    copied = mstore.ensure_val_split(slot_id)
    if copied:
        print(f"val 为空，已从 train 拷了 {copied} 张做验证")
    if task == "obb" and slot_id in ("shoe_obb", "last_obb"):
        print(
            "注意：皮带 ShoeVision 若仍用 casbot ultralytics_obb360，"
            "标准 yolov8-obb 启用前请在「验证」步用 cam1 实图确认。"
        )
    model = YOLO(model_src)
    kw = uhp.train_kwargs(hp)
    kw["data"] = str(data)
    kw["project"] = str(_runs_dir(task))
    kw["name"] = slot_id
    kw["exist_ok"] = True
    if resume:
        kw["resume"] = True
    device = kw.get("device")
    if device in ("", None):
        kw.pop("device", None)
    results = model.train(**kw)
    save_dir = Path(getattr(results, "save_dir", _runs_dir(task) / slot_id))
    best = save_dir / "weights" / "best.pt"
    if not best.exists():
        hits = list((_runs_dir(task) / slot_id).rglob("best.pt"))
        best = hits[0] if hits else best
    print(f"训练完成: {best}")
    csv_path = save_dir / "results.csv"
    if csv_path.is_file():
        print(f"曲线: {csv_path}")
    meta = mstore.slot_meta(slot_id)
    inst = meta.get("install")
    if not no_install and best.is_file() and inst:
        mstore.install_pt(best, ROOT / str(inst))
        print(f"已安装到 {inst}")
    return 0


def cmd_val(slot_id: str, weights: Path | None) -> int:
    from vision import model_store as mstore

    YOLO = _need_yolo()
    task = mstore.slot_task(slot_id)
    w = Path(weights) if weights else find_weight(slot_id, task)
    if w is None or not w.is_file():
        raise SystemExit("找不到权重。请先训练或导入 .pt")
    data = ensure_data_yaml(slot_id)
    model = YOLO(str(w))
    print(f"验证 slot={slot_id} weights={w}")
    results = model.val(data=str(data), plots=True)
    save_dir = Path(getattr(results, "save_dir", "") or "")
    summary: dict[str, Any] = {"weights": str(w), "save_dir": str(save_dir)}
    box = getattr(results, "box", None)
    if box is not None:
        for key in ("map50", "map", "mp", "mr"):
            if hasattr(box, key):
                try:
                    summary[key] = float(getattr(box, key))
                except Exception:
                    pass
    top1 = getattr(results, "top1", None)
    if top1 is not None:
        try:
            summary["top1"] = float(top1)
        except Exception:
            pass
    out = ROOT / "runs" / "val_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if save_dir.is_dir():
        for name in ("confusion_matrix.png", "confusion_matrix_normalized.png"):
            p = save_dir / name
            if p.is_file():
                print(f"混淆矩阵: {p}")
    return 0


def cmd_predict(weights: Path, source: Path, out: Path | None) -> int:
    YOLO = _need_yolo()
    if not weights.is_file():
        raise SystemExit(f"权重不存在: {weights}")
    if not source.exists():
        raise SystemExit(f"输入不存在: {source}")
    model = YOLO(str(weights))
    dest = out.parent if out else ROOT / "runs" / "predict"
    dest.mkdir(parents=True, exist_ok=True)
    results = model.predict(source=str(source), save=True, project=str(dest), name="pred", exist_ok=True)
    print(f"预测完成 save_dir={getattr(results[0], 'save_dir', dest) if results else dest}")
    return 0


def cmd_export(weights: Path, fmt: str) -> int:
    YOLO = _need_yolo()
    if not weights.is_file():
        raise SystemExit(f"权重不存在: {weights}")
    model = YOLO(str(weights))
    path = model.export(format=str(fmt or "onnx"))
    print(f"已导出: {path}")
    return 0


def overlay_predict_bgr(bgr, weights: Path, *, conf: float = 0.25):
    """对 BGR 图推理并返回叠框图（numpy uint8）。缺 ultralytics 则抛 RuntimeError。"""
    import numpy as np

    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "未安装 ultralytics。请到「模型」步安装，或 pip install ultralytics"
        ) from e
    model = YOLO(str(weights))
    res = model.predict(source=bgr, conf=float(conf), verbose=False)
    if not res:
        return bgr
    plotted = res[0].plot()
    return np.ascontiguousarray(plotted)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ultralytics 训练/验证/导出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tr = sub.add_parser("train")
    p_tr.add_argument("--slot", required=True)
    p_tr.add_argument("--hparams", type=Path, default=None)
    p_tr.add_argument("--no-install", action="store_true")

    p_val = sub.add_parser("val")
    p_val.add_argument("--slot", required=True)
    p_val.add_argument("--weights", type=Path, default=None)

    p_pr = sub.add_parser("predict")
    p_pr.add_argument("--weights", type=Path, required=True)
    p_pr.add_argument("--source", type=Path, required=True)
    p_pr.add_argument("--out", type=Path, default=None)

    p_ex = sub.add_parser("export")
    p_ex.add_argument("--weights", type=Path, required=True)
    p_ex.add_argument("--format", default="onnx")

    args = ap.parse_args(argv)
    if args.cmd == "train":
        return cmd_train(args.slot, args.hparams, no_install=bool(args.no_install))
    if args.cmd == "val":
        return cmd_val(args.slot, args.weights)
    if args.cmd == "predict":
        return cmd_predict(args.weights, args.source, args.out)
    if args.cmd == "export":
        return cmd_export(args.weights, args.format)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
