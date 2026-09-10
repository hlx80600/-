"""HMI/脚本共用：模型槽位、挂接旧权重、采图目录、写入 yaml/json。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
LINK_SCRIPT = ROOT / "tools" / "yolo_train" / "link_legacy_models.sh"
POSITION_YAML = ROOT / "position_config.yaml"
CUSTOM_PROJECTS = ROOT / "datasets" / "custom_projects.yaml"
DEFAULT_LEGACY_ROOT = Path("/home/hlx8060/文档/program/压鞋机_旧/Casbot_Press_Shoes-main")
RUNNER = ROOT / "vision" / "ultralytics_runner.py"

SLOTS: dict[str, dict[str, Any]] = {
    "shoe_obb": {
        "label": "皮带-鞋OBB",
        "kind": "obb",
        "install": "models/shoe_vision/custom_鞋obb.pt",
        "json_key": "shoe_model_path",
        "cam": "cam1",
        "classes": [],
        "dataset": "datasets/shoe_obb",
        "train": "obb",
        "task": "obb",
        "names": {0: "shoe"},
    },
    "shoe_lr": {
        "label": "皮带-左右脚",
        "kind": "cls",
        "install": "models/shoe_vision/custom_鞋头朝上左右脚分类.pt",
        "json_key": "shoe_cls_model_path",
        "cam": "cam1",
        "classes": ["left", "right"],
        "dataset": "datasets/shoe_lr",
        "train": "classify",
        "task": "classify",
        "crop": "shoe_lr",
    },
    "last_obb": {
        "label": "皮带-鞋楦OBB",
        "kind": "obb",
        "install": "models/shoe_vision/custom_鞋楦obb.pt",
        "json_key": "shoe_tree_model_path",
        "cam": "cam1",
        "classes": [],
        "dataset": "datasets/last_obb",
        "train": "obb",
        "task": "obb",
        "names": {0: "last"},
    },
    "toe_align": {
        "label": "鞋头对位",
        "kind": "cls",
        "install": "models/toe_align/custom_toe_align.pt",
        "yaml": ("vision", "toe_align", "model_path"),
        "cam": "cam2",
        "classes": ["aligned", "forward"],
        "dataset": "datasets/toe_align",
        "train": "classify",
        "task": "classify",
    },
    "slot_check": {
        "label": "槽有无鞋",
        "kind": "cls",
        "install": "models/slot_check/custom_slot_check.pt",
        "yaml": ("vision", "slot_check", "model_path"),
        "cam": "cam3",
        "classes": ["empty", "has_shoe"],
        "dataset": "datasets/slot_check",
        "train": "classify",
        "task": "classify",
    },
    "rod_obb": {
        "label": "取槽压杆",
        "kind": "obb",
        "install": "models/position/rod/custom_obb.pt",
        "yaml": ("vision", "position", "rod_model_path"),
        "position_key": "rod_obb_model_path",
        "cam": "cam4",
        "classes": [],
        "dataset": "datasets/rod_obb",
        "train": "obb",
        "task": "obb",
        "names": {0: "rod"},
    },
}

BUILTIN_SLOT_IDS = frozenset(SLOTS.keys())


def slot_meta(slot_id: str) -> dict[str, Any]:
    meta = SLOTS.get(slot_id)
    if not meta:
        raise KeyError(f"未知槽位: {slot_id}")
    return meta


def slot_task(slot_id: str) -> str:
    meta = slot_meta(slot_id)
    t = str(meta.get("task") or meta.get("train") or "")
    if t in ("classify", "detect", "obb", "segment"):
        return t
    if meta.get("kind") == "cls":
        return "classify"
    return "obb"


def is_classify(slot_id: str) -> bool:
    return slot_task(slot_id) == "classify"


def relpath(path: Path) -> str:
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def dataset_dir(slot_id: str) -> Path:
    return ROOT / str(slot_meta(slot_id)["dataset"])


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

CLASS_LABELS = {
    "left": "左脚",
    "right": "右脚",
    "empty": "空槽",
    "has_shoe": "有鞋",
    "aligned": "到位",
    "forward": "向前",
}


def class_label(name: str) -> str:
    key = str(name or "")
    return CLASS_LABELS.get(key, key)


def val_twin(path: Path) -> Optional[Path]:
    path = Path(path)
    if "train" not in path.parts:
        return None
    idx = path.parts.index("train")
    return Path(*path.parts[:idx]) / "val" / Path(*path.parts[idx + 1 :])


def newest_train_image(slot_id: str, cls_name: str = "") -> Optional[Path]:
    meta = slot_meta(slot_id)
    imgs: list[Path] = []
    if is_classify(slot_id):
        names = [cls_name] if cls_name else list(meta.get("classes") or [])
        for c in names:
            imgs.extend(list_images(class_dir(slot_id, str(c), split="train")))
    else:
        imgs.extend(list_images(class_dir(slot_id, "", split="train")))
    if not imgs:
        return None
    return max(imgs, key=lambda p: p.stat().st_mtime)


def class_dir(slot_id: str, cls_name: str, *, split: str = "train") -> Path:
    if is_classify(slot_id):
        return dataset_dir(slot_id) / split / str(cls_name)
    return dataset_dir(slot_id) / "images" / split


def ensure_dirs(slot_id: str) -> None:
    meta = slot_meta(slot_id)
    if is_classify(slot_id):
        for split in ("train", "val"):
            for c in meta.get("classes") or []:
                class_dir(slot_id, str(c), split=split).mkdir(parents=True, exist_ok=True)
    else:
        for split in ("train", "val"):
            class_dir(slot_id, "", split=split).mkdir(parents=True, exist_ok=True)
            (dataset_dir(slot_id) / "labels" / split).mkdir(parents=True, exist_ok=True)


def list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def count_images(folder: Path) -> int:
    return len(list_images(folder))


def label_path_for(image: Path) -> Path:
    """images/train/a.jpg → labels/train/a.txt"""
    image = Path(image)
    parts = list(image.parts)
    if "images" in parts:
        parts[parts.index("images")] = "labels"
        return Path(*parts).with_suffix(".txt")
    return image.with_suffix(".txt")


def delete_labels_for(image: Path) -> list[Path]:
    removed = []
    for p in (image,):
        lp = label_path_for(p)
        if lp.is_file():
            lp.unlink()
            removed.append(lp)
    if "train" in image.parts:
        idx = image.parts.index("train")
        val_img = Path(*image.parts[:idx]) / "val" / Path(*image.parts[idx + 1 :])
        lp = label_path_for(val_img)
        if lp.is_file():
            lp.unlink()
            removed.append(lp)
    return removed


def delete_image_pair(path: Path) -> list[Path]:
    """删 train 图；val 同名图和对应 OBB 标注一并删。"""
    removed: list[Path] = []
    path = Path(path)
    removed.extend(delete_labels_for(path))
    if path.is_file():
        path.unlink()
        removed.append(path)
    if "train" in path.parts:
        idx = path.parts.index("train")
        val = Path(*path.parts[:idx]) / "val" / Path(*path.parts[idx + 1 :])
        if val.is_file() and val.resolve() != path.resolve():
            val.unlink()
            removed.append(val)
    return removed


def delete_class_images(slot_id: str, cls_name: str = "") -> int:
    n = 0
    for split in ("train", "val"):
        for p in list_images(class_dir(slot_id, cls_name, split=split)):
            n += len(delete_image_pair(p))
    return n


def delete_dataset_images(slot_id: str) -> int:
    meta = slot_meta(slot_id)
    n = 0
    if is_classify(slot_id):
        for c in meta.get("classes") or []:
            n += delete_class_images(slot_id, str(c))
    else:
        n += delete_class_images(slot_id, "")
        raw = dataset_dir(slot_id) / "raw"
        if raw.is_dir():
            for p in list_images(raw):
                p.unlink()
                n += 1
    return n


def dataset_counts(slot_id: str) -> str:
    meta = slot_meta(slot_id)
    parts = []
    if is_classify(slot_id):
        for c in meta.get("classes") or []:
            ntr = count_images(class_dir(slot_id, c, split="train"))
            nva = count_images(class_dir(slot_id, c, split="val"))
            parts.append(f"{c} train={ntr} val={nva}")
    else:
        ntr = count_images(class_dir(slot_id, "", split="train"))
        nva = count_images(class_dir(slot_id, "", split="val"))
        labeled = 0
        for p in list_images(class_dir(slot_id, "", split="train")):
            if label_path_for(p).is_file() and label_path_for(p).stat().st_size > 0:
                labeled += 1
        parts.append(f"images train={ntr} val={nva}  已圈图={labeled}/{ntr}")
    return "  ".join(parts) if parts else "-"


# 旧压鞋机 models 相对路径 → 本仓库目标（不含 custom_*.pt，避免盖掉自训）
LEGACY_WEIGHT_LINKS: tuple[tuple[str, str], ...] = (
    ("shoe_vision/7.23鞋obb.pt", "models/shoe_vision/7.23鞋obb.pt"),
    ("shoe_vision/7.1鞋头朝上左右脚分类.pt", "models/shoe_vision/7.1鞋头朝上左右脚分类.pt"),
    ("shoe_vision/7.24鞋楦obb.pt", "models/shoe_vision/7.24鞋楦obb.pt"),
    ("toe_align/0722best.pt", "models/toe_align/0722best.pt"),
    ("slot_check/7.10slot_check.pt", "models/slot_check/7.10slot_check.pt"),
    ("position/rod/obb.pt", "models/position/rod/obb.pt"),
    ("position/slot/slot_check.pt", "models/position/slot/slot_check.pt"),
)

CLASS_SYNONYMS: dict[str, set[str]] = {
    "left": {"left", "l", "左", "左脚"},
    "right": {"right", "r", "右", "右脚"},
    "empty": {"empty", "empty_slot", "空", "空槽"},
    "has_shoe": {"has_shoe", "has-shoe", "shoe", "有鞋", "occupied"},
    "aligned": {"aligned", "ok", "到位"},
    "forward": {"forward", "fwd", "向前"},
}


def resolve_legacy_root(path: Path | str) -> Path:
    """工程根或 models/ 都能认。"""
    raw = Path(path).expanduser().resolve()
    if not raw.exists():
        raise FileNotFoundError(f"找不到路径: {raw}")
    if raw.is_file():
        return raw.parent
    if raw.name == "models":
        return raw.parent
    if (raw / "models").is_dir():
        return raw
    return raw


def resolve_legacy_models_dir(path: Path | str) -> Path:
    root = resolve_legacy_root(path)
    models = root / "models"
    if models.is_dir():
        return models
    if path and Path(path).name == "models" and Path(path).is_dir():
        return Path(path).expanduser().resolve()
    if root.is_dir() and any(root.glob("*.pt")):
        return root
    raise FileNotFoundError(f"里面没有 models/: {root}")


def peek_legacy_project(path: Path | str) -> dict[str, Any]:
    """看旧工程里有没有 models / datasets / runs。"""
    root = resolve_legacy_root(path)
    models: Path | None
    try:
        models = resolve_legacy_models_dir(root)
    except FileNotFoundError:
        models = None
    ds = root / "datasets"
    runs = root / "runs"
    return {
        "root": root,
        "models": models,
        "datasets": ds if ds.is_dir() else None,
        "runs": runs if runs.is_dir() else None,
    }


def _is_custom_pt(path: Path) -> bool:
    return "custom_" in path.name


def link_legacy_models(old_dir: str = "", *, only_missing: bool = True) -> str:
    """把旧工程 .pt 软链到本仓库 models/。只补缺失项，不覆盖 custom_*.pt。"""
    src_root = old_dir or str(DEFAULT_LEGACY_ROOT)
    models_src = resolve_legacy_models_dir(src_root)
    lines: list[str] = [f"源: {models_src}"]
    linked = 0
    skipped = 0
    missing = 0
    for rel_src, rel_dst in LEGACY_WEIGHT_LINKS:
        dst = ROOT / rel_dst
        if _is_custom_pt(dst):
            skipped += 1
            lines.append(f"跳过自训 {rel_dst}")
            continue
        src = models_src / rel_src
        if not src.exists():
            alt = models_src / Path(rel_src).name
            src = alt if alt.exists() else src
        if not src.exists():
            missing += 1
            lines.append(f"源缺失 {rel_src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and _is_custom_pt(dst):
            skipped += 1
            lines.append(f"不覆盖自训 {rel_dst}")
            continue
        if only_missing and dst.exists():
            skipped += 1
            lines.append(f"已存在，跳过 {rel_dst}")
            continue
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        dst.symlink_to(src.resolve())
        linked += 1
        lines.append(f"链接 {rel_dst}")
    legacy_dir = ROOT / "models" / "legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    pack = legacy_dir / "Casbot_Press_Shoes_models"
    if not pack.exists() and not pack.is_symlink():
        pack.symlink_to(models_src)
        lines.append(f"链接 {relpath(pack)}")
    lines.append(f"完成：新建链接 {linked}，跳过 {skipped}，源缺失 {missing}")
    return "\n".join(lines)


def install_pt(src: Path, dst: Path) -> Path:
    src = Path(src).expanduser().resolve()
    dst = Path(dst)
    if not dst.is_absolute():
        dst = ROOT / dst
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    return dst


def bind_model(ctx, slot_id: str, src: Path, *, copy_to_default: bool = True) -> str:
    """把 .pt 装到槽位并写 yaml / json / position_config。"""
    from core.config_loader import save_config
    from vision import shoe_cfg
    from vision.legacy_pipeline import reset_shoe_vision

    meta = SLOTS[slot_id]
    src = Path(src).expanduser()
    if copy_to_default:
        dst = install_pt(src, Path(meta["install"]))
        rel = relpath(dst)
    else:
        rel = relpath(src if src.is_absolute() else ROOT / src)
    vis = ctx.cfg.setdefault("vision", {})
    yaml_keys = meta.get("yaml")
    if yaml_keys:
        keys = list(yaml_keys)
        if keys and keys[0] == "vision":
            keys = keys[1:]
        node = vis
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = rel
        save_config(ctx.cfg)
    json_key = meta.get("json_key")
    if json_key:
        shoe_cfg.write_model_key(json_key, rel, vis)
        reset_shoe_vision()
    pos_key = meta.get("position_key")
    if pos_key and POSITION_YAML.exists():
        text = POSITION_YAML.read_text(encoding="utf-8")
        lines = []
        found = False
        for line in text.splitlines():
            if line.startswith(f"{pos_key}:"):
                lines.append(f"{pos_key}: {rel}")
                found = True
            else:
                lines.append(line)
        if not found:
            lines.append(f"{pos_key}: {rel}")
        POSITION_YAML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rel


def save_bgr(path: Path, img) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img):
        raise RuntimeError(f"写图失败: {path}")


def capture_to_slot(ctx, slot_id: str, cls_name: str = "", *, to_val: bool = False) -> Path:
    meta = SLOTS[slot_id]
    cam_id = str(meta.get("cam") or "cam1")
    from vision.cls_crop import grab_slot_image, prepare_shoe_lr

    img = grab_slot_image(ctx, cam_id)
    note = ""
    if meta.get("crop") == "shoe_lr":
        cropped, note = prepare_shoe_lr(ctx, img)
        if cropped is not None:
            img = cropped
    capture_to_slot.last_note = note
    split = "val" if to_val else "train"
    if is_classify(slot_id):
        if not cls_name:
            raise RuntimeError("分类任务请先选类别")
        folder = class_dir(slot_id, cls_name, split=split)
    else:
        folder = class_dir(slot_id, "", split=split)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"{cam_id}_{cls_name or slot_id}_{ts}.jpg"
    save_bgr(path, img)
    return path


capture_to_slot.last_note = ""


def normalize_train_device(device: str) -> str:
    """HMI / 脚本统一：cpu | 0 | 0,1 | cuda → 传给 ultralytics。"""
    d = str(device or "cpu").strip().lower()
    if d in ("", "cpu", "cpu（不加gpu）", "cpu(不加gpu)"):
        return "cpu"
    if d in ("gpu", "cuda", "cuda:0", "gpu（cuda:0）", "gpu(cuda:0)"):
        return "0"
    if d.startswith("cuda:"):
        return d.split(":", 1)[1] or "0"
    return str(device).strip()


def is_gpu_train_device(device: str) -> bool:
    return normalize_train_device(device) != "cpu"


def default_train_batch(device: str) -> int:
    return 8 if not is_gpu_train_device(device) else 16


def cuda_train_status() -> dict:
    """供 HMI 显示：本机能否 GPU 训练。"""
    out: dict = {
        "torch_ok": False,
        "cuda": False,
        "count": 0,
        "name": "",
        "message": "未安装 torch",
    }
    try:
        import torch
    except ImportError:
        return out
    out["torch_ok"] = True
    try:
        ok = bool(torch.cuda.is_available())
        out["cuda"] = ok
        if ok:
            n = int(torch.cuda.device_count())
            out["count"] = n
            try:
                out["name"] = str(torch.cuda.get_device_name(0) or "")
            except Exception:
                out["name"] = ""
            tip = out["name"] or f"{n} 张卡"
            out["message"] = f"CUDA 可用（{tip}）— 可选「GPU训练」"
        else:
            out["message"] = "已装 torch，但 CUDA 不可用（多为 CPU 版 torch 或无 NVIDIA 驱动）"
    except Exception as e:
        out["message"] = f"检测 CUDA 失败: {e}"
    return out


def train_cmd(
    slot_id: str,
    *,
    epochs: int = 80,
    device: str = "cpu",
    batch: int | None = None,
    hparams: dict[str, Any] | None = None,
) -> list[str]:
    import sys

    from vision import ultralytics_hparams as uhp

    hp = uhp.load_hparams(slot_id, task=slot_task(slot_id))
    if hparams:
        hp.update(hparams)
    hp["epochs"] = int(epochs)
    hp["device"] = normalize_train_device(device)
    if batch is None:
        batch = default_train_batch(str(hp["device"]))
    hp["batch"] = int(batch)
    path = uhp.save_hparams(slot_id, hp)
    return [
        sys.executable,
        "-u",
        str(RUNNER),
        "train",
        "--slot",
        slot_id,
        "--hparams",
        str(path),
    ]


def val_cmd(slot_id: str, weights: Path | None = None) -> list[str]:
    import sys

    cmd = [sys.executable, "-u", str(RUNNER), "val", "--slot", slot_id]
    if weights is not None:
        cmd.extend(["--weights", str(weights)])
    return cmd


def export_cmd(weights: Path, *, fmt: str = "onnx") -> list[str]:
    import sys

    return [
        sys.executable,
        "-u",
        str(RUNNER),
        "export",
        "--weights",
        str(weights),
        "--format",
        str(fmt),
    ]


def _write_custom_projects() -> None:
    rows = []
    for sid, meta in SLOTS.items():
        if sid in BUILTIN_SLOT_IDS:
            continue
        rows.append(
            {
                "id": sid,
                "label": meta.get("label"),
                "task": meta.get("task"),
                "kind": meta.get("kind"),
                "cam": meta.get("cam"),
                "classes": meta.get("classes") or [],
                "names": meta.get("names") or {},
                "install": meta.get("install"),
                "dataset": meta.get("dataset"),
            }
        )
    CUSTOM_PROJECTS.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_PROJECTS.write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_custom_slots() -> None:
    if not CUSTOM_PROJECTS.is_file():
        return
    try:
        rows = yaml.safe_load(CUSTOM_PROJECTS.read_text(encoding="utf-8")) or []
    except Exception:
        return
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid or sid in SLOTS:
            continue
        task = str(row.get("task") or "detect")
        SLOTS[sid] = {
            "label": str(row.get("label") or sid),
            "kind": "cls" if task == "classify" else "box",
            "task": task,
            "train": "classify" if task == "classify" else task,
            "install": str(row.get("install") or f"models/custom/{sid}.pt"),
            "cam": str(row.get("cam") or "cam1"),
            "classes": list(row.get("classes") or []),
            "names": dict(row.get("names") or {}),
            "dataset": str(row.get("dataset") or f"datasets/{sid}"),
            "custom": True,
        }


def add_custom_project(
    *,
    label: str,
    task: str,
    cam: str,
    classes: list[str],
) -> str:
    base = "custom_" + "".join(ch if ch.isalnum() else "_" for ch in label)[:24]
    sid = base.strip("_") or "custom"
    n = 1
    while sid in SLOTS:
        n += 1
        sid = f"{base}_{n}"
    names = {i: c for i, c in enumerate(classes)}
    SLOTS[sid] = {
        "label": label,
        "kind": "cls" if task == "classify" else "box",
        "task": task,
        "train": "classify" if task == "classify" else task,
        "install": f"models/custom/{sid}.pt",
        "cam": cam,
        "classes": list(classes),
        "names": names,
        "dataset": f"datasets/{sid}",
        "custom": True,
    }
    ensure_dirs(sid)
    _write_custom_projects()
    return sid


def _copy_tree_merge(src: Path, dst: Path) -> int:
    n = 0
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        n += 1
    return n


def list_dataset_class_dirs(src: Path) -> list[str]:
    """分类数据集 train/ 下的类文件夹名。"""
    train = Path(src) / "train"
    if not train.is_dir():
        return []
    names = [p.name for p in train.iterdir() if p.is_dir()]
    names.sort()
    return names


def _class_matches(expected: str, found: str) -> bool:
    e = str(expected).strip()
    f = str(found).strip()
    if e == f or e.lower() == f.lower():
        return True
    syn = CLASS_SYNONYMS.get(e, {e.lower()})
    return f.lower() in {s.lower() for s in syn} or f in syn


def dataset_class_warnings(slot_id: str, src: Path) -> str:
    """类名对不上时返回提示；对得上或非分类则空字符串。不自动改名。"""
    if not is_classify(slot_id):
        return ""
    expected = [str(c) for c in (slot_meta(slot_id).get("classes") or [])]
    if not expected:
        return ""
    found = list_dataset_class_dirs(src)
    if not found:
        return ""
    missing = [e for e in expected if not any(_class_matches(e, f) for f in found)]
    extra = [f for f in found if not any(_class_matches(e, f) for e in expected)]
    if not missing and not extra:
        return ""
    return (
        f"类名可能对不上，不会自动改文件夹名。\n"
        f"本槽约定: {expected}\n"
        f"源目录 train/: {found}\n"
        f"缺少约定类: {missing or '无'}；多出来的: {extra or '无'}。\n"
        f"对不上时产线 0/1 类号会错，请改名后再训。"
    )


def import_weights(slot_id: str, src: Path, *, copy: bool = True) -> Path:
    """导入 .pt 到槽位安装路径（不写生产配置，由 HMI bind_model 决定）。

    src 可以是文件，或含 weights/best.pt、last.pt 的 runs 目录。
    """
    src = Path(src).expanduser()
    if src.is_dir():
        picked: Path | None = None
        for name in ("best.pt", "last.pt"):
            hit = src / "weights" / name
            if hit.is_file():
                picked = hit
                break
            hits = list(src.rglob(name))
            if hits:
                picked = hits[0]
                break
        if picked is None:
            pts = [p for p in src.rglob("*.pt") if p.is_file()]
            if pts:
                picked = pts[0]
        if picked is None:
            raise FileNotFoundError(f"目录里找不到 .pt: {src}")
        src = picked
    if not src.is_file():
        raise FileNotFoundError(f"找不到权重: {src}")
    inst = Path(slot_meta(slot_id)["install"])
    if copy:
        return install_pt(src, inst)
    return src


def import_weights_bundle(slot_id: str, src: Path, *, copy: bool = True) -> str:
    """导入权重；若是 runs 目录则同时拷曲线和 last.pt，便于续训。"""
    src = Path(src).expanduser()
    notes: list[str] = []
    if src.is_dir() and (
        (src / "weights").is_dir()
        or (src / "results.csv").is_file()
        or list(src.rglob("best.pt"))
    ):
        notes.append(import_runs(slot_id, src))
    dst = import_weights(slot_id, src, copy=copy)
    notes.append(f"权重 → {relpath(dst)}")
    return "\n".join(notes)


def import_dataset(slot_id: str, src: Path) -> str:
    """把 YOLO 分类或 images/labels 目录拷进本槽位数据集。"""
    src = Path(src).expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"不是目录: {src}")
    dst = dataset_dir(slot_id)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    if (src / "train").is_dir() and is_classify(slot_id):
        n += _copy_tree_merge(src / "train", dst / "train")
        if (src / "val").is_dir():
            n += _copy_tree_merge(src / "val", dst / "val")
    elif (src / "images").is_dir():
        n += _copy_tree_merge(src / "images", dst / "images")
        if (src / "labels").is_dir():
            n += _copy_tree_merge(src / "labels", dst / "labels")
        if (src / "data.yaml").is_file():
            shutil.copy2(src / "data.yaml", dst / "data.yaml")
            n += 1
    else:
        n += _copy_tree_merge(src, dst)
    ensure_dirs(slot_id)
    warn = dataset_class_warnings(slot_id, dst if is_classify(slot_id) else src)
    msg = f"已导入 {n} 个文件 → {relpath(dst)}"
    if warn:
        msg += "\n" + warn
    return msg


def import_runs(slot_id: str, src: Path) -> str:
    """导入一次 Ultralytics runs 目录（权重 + results.csv）。"""
    src = Path(src).expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"不是目录: {src}")
    task = slot_task(slot_id)
    dest = ROOT / "runs" / task / slot_id
    dest.mkdir(parents=True, exist_ok=True)
    n = _copy_tree_merge(src, dest)
    return f"已导入 runs {n} 个文件 → {relpath(dest)}"


def import_legacy_project(
    path: Path | str,
    *,
    copy_datasets: bool = False,
    copy_runs: bool = False,
) -> str:
    """一键：软链 models（只补缺失）+ 可选按槽位名拷 datasets/runs。"""
    info = peek_legacy_project(path)
    lines = [link_legacy_models(str(info["root"]), only_missing=True)]
    ds = info.get("datasets")
    if copy_datasets and isinstance(ds, Path) and ds.is_dir():
        copied = 0
        for sid in BUILTIN_SLOT_IDS:
            sub = ds / sid
            if sub.is_dir():
                lines.append(f"[{sid}] " + import_dataset(sid, sub))
                copied += 1
        if copied == 0:
            lines.append(f"旧工程 datasets/ 下没有与 6 槽同名的子目录（{ds}）")
    elif copy_datasets:
        lines.append("旧工程没有 datasets/，跳过")
    runs = info.get("runs")
    if copy_runs and isinstance(runs, Path) and runs.is_dir():
        copied_r = 0
        for sid in BUILTIN_SLOT_IDS:
            task = slot_task(sid)
            cands = [
                runs / task / sid,
                runs / sid,
            ]
            hit = next((p for p in cands if p.is_dir()), None)
            if hit is None:
                continue
            lines.append(f"[{sid}] " + import_runs(sid, hit))
            copied_r += 1
        if copied_r == 0:
            lines.append(f"旧工程 runs/ 下没有与槽位对应的目录（{runs}）")
    elif copy_runs:
        lines.append("旧工程没有 runs/，跳过")
    return "\n".join(lines)


def import_images_folder(slot_id: str, src: Path, cls_name: str = "") -> int:
    """把文件夹里的图片拷进 train（分类需给类名；递归，跳过叠图）。"""
    src = Path(src).expanduser()
    if not src.is_dir():
        raise FileNotFoundError(str(src))
    files: list[Path] = []
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        name = p.name.lower()
        if any(tag in name for tag in ("_overlay", "_vis", "_pred")):
            continue
        files.append(p)
    return import_image_files(slot_id, files, cls_name=cls_name)


def import_image_files(slot_id: str, files: list[Path], cls_name: str = "") -> int:
    """把若干图片拷进 train（分类需给类名）。"""
    if is_classify(slot_id):
        if not cls_name:
            raise RuntimeError("分类任务导入图片请先选类别")
        folder = class_dir(slot_id, cls_name, split="train")
    else:
        folder = class_dir(slot_id, "", split="train")
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    used: set[str] = set()
    for src in files:
        src = Path(src)
        if not src.is_file() or src.suffix.lower() not in IMAGE_EXTS:
            continue
        dest_name = src.name
        if dest_name in used or (folder / dest_name).exists():
            dest_name = f"{src.stem}_{n}{src.suffix.lower()}"
        shutil.copy2(src, folder / dest_name)
        used.add(dest_name)
        n += 1
    return n


def ensure_val_split(slot_id: str) -> int:
    """val 为空时从 train 每 5 张拷 1 张，避免 Ultralytics 无验证集。"""
    copied = 0
    if is_classify(slot_id):
        meta = slot_meta(slot_id)
        pairs = [(str(c), str(c)) for c in (meta.get("classes") or [])]
    else:
        pairs = [("", "")]
    for cls_name, _ in pairs:
        train_dir = class_dir(slot_id, cls_name, split="train")
        val_dir = class_dir(slot_id, cls_name, split="val")
        val_dir.mkdir(parents=True, exist_ok=True)
        if count_images(val_dir) > 0:
            continue
        imgs = list_images(train_dir)
        if not imgs:
            continue
        picks = imgs[::5] or imgs[:1]
        for p in picks:
            shutil.copy2(p, val_dir / p.name)
            lp = label_path_for(p)
            if lp.is_file():
                dst_l = label_path_for(val_dir / p.name)
                dst_l.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(lp, dst_l)
            copied += 1
    return copied


def results_csv_path(slot_id: str) -> Path | None:
    task = slot_task(slot_id)
    run = ROOT / "runs" / task / slot_id
    direct = run / "results.csv"
    if direct.is_file():
        return direct
    if run.is_dir():
        hits = list(run.rglob("results.csv"))
        if hits:
            return hits[0]
    return None


def list_slot_weights(slot_id: str) -> list[tuple[str, Path]]:
    """生产路径 + runs 下 best/last。"""
    rows: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    inst = ROOT / str(slot_meta(slot_id).get("install") or "")
    if inst.is_file():
        rows.append(("生产路径", inst))
        try:
            seen.add(inst.resolve())
        except OSError:
            pass
    task = slot_task(slot_id)
    run = ROOT / "runs" / task / slot_id
    if run.is_dir():
        for name in ("best.pt", "last.pt"):
            for p in run.rglob(name):
                try:
                    key = p.resolve()
                except OSError:
                    key = p
                if key in seen:
                    continue
                seen.add(key)
                rows.append((name, p))
    return rows


def confusion_matrix_path(slot_id: str) -> Path | None:
    task = slot_task(slot_id)
    run = ROOT / "runs" / task / slot_id
    names = ("confusion_matrix_normalized.png", "confusion_matrix.png")
    search: list[Path] = [run]
    val_json = ROOT / "runs" / "val_last.json"
    if val_json.is_file():
        try:
            data = json.loads(val_json.read_text(encoding="utf-8"))
            save_dir = Path(str(data.get("save_dir") or ""))
            if save_dir.is_dir():
                search.insert(0, save_dir)
        except Exception:
            pass
    for folder in search:
        if not folder.is_dir():
            continue
        for name in names:
            hits = sorted(folder.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
            if hits:
                return hits[0]
    return None


load_custom_slots()


def pip_ultralytics_cmd(*, with_cuda: bool = False, cuda_tag: str = "cu124") -> list[str]:
    """安装 ultralytics + torch。with_cuda=True 时从 PyTorch 官方 wheel 装 CUDA 版。"""
    import sys

    base = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        "--break-system-packages",
        "ultralytics",
    ]
    if with_cuda:
        # 默认 cu124；驱动过旧可改 cu118。见 https://pytorch.org/get-started/locally/
        return base + [
            "torch",
            "torchvision",
            "--index-url",
            f"https://download.pytorch.org/whl/{cuda_tag}",
        ]
    return base + ["torch", "torchvision"]
