"""双槽同源算法依赖：路径、体检、压杆用的官方 ultralytics 隔离。

皮带 ``ShoeVision``：``casbot_yolo_point4d`` + ``ultralytics_obb360``（``init.sh`` clone，不进 git）。
鞋头：``shoe_align/ImgAct`` 的 ``DiscreteMultiActionHead``。
槽占用：与双槽一样 ``from ultralytics import YOLO`` 跑检测权重。
压杆：双槽 ``position_obb`` 会暂时摘掉 obb360 对 ``ultralytics`` 的别名，再用 pip 的 YOLO 加载普通 OBB 权重。
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
_PATHS_READY = False
_ULTRALYTICS_IMPORT_LOCK = threading.Lock()
_OFFICIAL_ULTRALYTICS_MODULES: list[Any] = []


def point4d_dir() -> Path:
    """``casbot_yolo_point4d`` 克隆根目录。"""
    return ROOT / "casbot_yolo_point4d"


def obb360_dir() -> Path:
    """``casbot_yolo_obb360`` 克隆根目录（在 point4d 内）。"""
    return point4d_dir() / "casbot_yolo_obb360"


def imgact_dir() -> Path:
    """ImgAct 源码目录（``from ImgAct...`` 需要 ``shoe_align`` 在 path 上）。"""
    return ROOT / "shoe_align" / "ImgAct"


def _dir_has_pkg(base: Path, name: str) -> bool:
    try:
        return (base / name / "__init__.py").is_file() or (base / f"{name}.py").is_file()
    except OSError:
        return False


def _insert_path(path: Path) -> None:
    if not path.is_dir():
        return
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def ensure_algo_paths() -> None:
    """把 point4d / obb360 / ImgAct 接到 ``sys.path``。可重复调用。

    ``casbot_yolo_point4d`` 目录本身**不要**进 path：否则其中的
    ``casbot_yolo_point4d.py`` 会变成顶层模块，
    ``from casbot_yolo_point4d.casbot_yolo_point4d import CasbotYoloP3D`` 会断。
    仓库根进 path 即可把它当包用。obb360 仓库根进 path，才能
    ``from ultralytics_obb360 import YOLO``。
    """
    global _PATHS_READY
    _insert_path(ROOT)
    _insert_path(ROOT / "shoe_align")
    _insert_path(obb360_dir())
    _PATHS_READY = True


def _ultralytics_alias_finders() -> list[Any]:
    try:
        from ultralytics_obb360._bootstrap import _UltralyticsAliasFinder
    except ImportError:
        return []
    return [finder for finder in sys.meta_path if isinstance(finder, _UltralyticsAliasFinder)]


@contextmanager
def site_packages_ultralytics() -> Iterator[None]:
    """暂时露出官方 ultralytics，藏起 obb360 的 ``ultralytics`` 别名。"""
    with _ULTRALYTICS_IMPORT_LOCK:
        finders = _ultralytics_alias_finders()
        if not finders:
            yield
            return

        for finder in finders:
            sys.meta_path.remove(finder)
        saved = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "ultralytics" or name.startswith("ultralytics.")
        }
        try:
            yield
            _OFFICIAL_ULTRALYTICS_MODULES.extend(
                module
                for name, module in sys.modules.items()
                if name == "ultralytics" or name.startswith("ultralytics.")
            )
        finally:
            for name in list(sys.modules):
                if name == "ultralytics" or name.startswith("ultralytics."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)
            for finder in reversed(finders):
                sys.meta_path.insert(0, finder)


def load_official_yolo(model_path: str | Path) -> Any:
    """用 site-packages ultralytics 加载压杆 OBB（对标双槽 ``position_obb._load_official_yolo``）。

    皮带先 import ``shoe_vision_seg`` 后，``ultralytics`` 会被别名到 obb360；
    双槽压杆权重是普通 yolov8-obb，必须躲开这个别名。槽检测 / 鞋头 ImgAct 不要走这里。

    model_path: str | Path: ``.pt`` 路径
    return: Any: ``ultralytics.YOLO`` 实例
    """
    import numpy as np

    ensure_algo_paths()
    with site_packages_ultralytics():
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        model.predict(np.zeros((64, 64, 3), dtype=np.uint8), verbose=False)
        return model


def algo_dep_status() -> dict[str, Any]:
    """磁盘体检，不 import torch。"""
    ensure_algo_paths()
    p4d = point4d_dir()
    obb = obb360_dir()
    imgact = imgact_dir()
    has_p4d = _dir_has_pkg(p4d, "casbot_yolo_point4d") or (p4d / "casbot_yolo_point4d.py").is_file()
    has_obb = (
        _dir_has_pkg(obb, "ultralytics_obb360")
        or (obb / "__init__.py").is_file()
        or (obb / "ultralytics_obb360" / "__init__.py").is_file()
    )
    has_imgact = (imgact / "nn" / "modules" / "head.py").is_file() and (
        imgact / "models" / "casbot" / "action" / "model.py"
    ).is_file()
    missing: list[str] = []
    if not has_p4d:
        missing.append("casbot_yolo_point4d（请 bash init.sh）")
    if not has_obb:
        missing.append("casbot_yolo_obb360（请 bash init.sh）")
    if not has_imgact:
        missing.append("shoe_align/ImgAct（DiscreteMultiActionHead）")
    return {
        "point4d": has_p4d,
        "obb360": has_obb,
        "imgact": has_imgact,
        "point4d_dir": str(p4d),
        "obb360_dir": str(obb),
        "imgact_dir": str(imgact),
        "message": " | ".join(missing) if missing else "point4d / obb360 / ImgAct 源码已找到",
    }
