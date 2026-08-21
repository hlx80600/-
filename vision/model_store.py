"""HMI/脚本共用：模型槽位、挂接旧权重、采图目录、写入 yaml/json。"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
LINK_SCRIPT = ROOT / "tools" / "yolo_train" / "link_legacy_models.sh"
POSITION_YAML = ROOT / "position_config.yaml"

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
        "names": {0: "rod"},
    },
}


def relpath(path: Path) -> str:
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def dataset_dir(slot_id: str) -> Path:
    return ROOT / str(SLOTS[slot_id]["dataset"])


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
    meta = SLOTS[slot_id]
    imgs: list[Path] = []
    if meta["kind"] == "cls":
        names = [cls_name] if cls_name else list(meta.get("classes") or [])
        for c in names:
            imgs.extend(list_images(class_dir(slot_id, str(c), split="train")))
    else:
        imgs.extend(list_images(class_dir(slot_id, "", split="train")))
    if not imgs:
        return None
    return max(imgs, key=lambda p: p.stat().st_mtime)


def class_dir(slot_id: str, cls_name: str, *, split: str = "train") -> Path:
    meta = SLOTS[slot_id]
    if meta["kind"] == "cls":
        return dataset_dir(slot_id) / split / str(cls_name)
    return dataset_dir(slot_id) / "images" / split


def ensure_dirs(slot_id: str) -> None:
    meta = SLOTS[slot_id]
    if meta["kind"] == "cls":
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
    meta = SLOTS[slot_id]
    n = 0
    if meta["kind"] == "cls":
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
    meta = SLOTS[slot_id]
    parts = []
    if meta["kind"] == "cls":
        for c in meta["classes"]:
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


def link_legacy_models(old_dir: str = "") -> str:
    if not LINK_SCRIPT.exists():
        raise FileNotFoundError(str(LINK_SCRIPT))
    cmd = ["bash", str(LINK_SCRIPT)]
    if old_dir:
        cmd.append(old_dir)
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise RuntimeError(out.strip() or f"挂接失败 exit={r.returncode}")
    return out.strip() or "已挂接旧模型"


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
    if meta["kind"] == "cls":
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


def train_cmd(slot_id: str, *, epochs: int = 80, device: str = "cpu", batch: int | None = None) -> list[str]:
    import sys

    meta = SLOTS[slot_id]
    if meta.get("train") == "obb":
        script = ROOT / "tools" / "yolo_train" / "train_obb.py"
    else:
        script = ROOT / "tools" / "yolo_train" / "train_classify.py"
    dev = normalize_train_device(device)
    if batch is None:
        batch = default_train_batch(dev)
    cmd = [
        sys.executable,
        "-u",
        str(script),
        "--task",
        slot_id,
        "--epochs",
        str(int(epochs)),
        "--batch",
        str(int(batch)),
    ]
    if dev:
        cmd.extend(["--device", str(dev)])
    return cmd


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
