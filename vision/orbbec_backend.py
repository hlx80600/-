"""奥比相机后端：工位仍调 OrbbecCamera.grab；真机可走 RSDT 驱动。

RSDT：hardware_module.orbbec_camera.orbbec_camera_driver.orbbec_camera
（热插拔、AlignFilter、OrbbecSDKConfig_casbot.xml）。
导入失败时 OrbbecCamera 退回本仓 pyorbbecsdk / OpenCV。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BACKEND_LOCAL = "local"
BACKEND_RSDT = "rsdt"

_ROOT = Path(__file__).resolve().parents[1]
_RSDT = _ROOT / "RSDT_Simple_Automation"

_RGB_PRESETS = ([1280, 720], [848, 480], [640, 480], [480, 270])
_DEPTH_PRESETS = ([1280, 480], [848, 480], [640, 480], [480, 270])
_FPS_PRESETS = (30, 15, 10, 5)


def normalize_orbbec_backend(raw: Any) -> str:
    key = str(raw or BACKEND_RSDT).strip().lower()
    if key in ("local", "fr5", "native", "sdk", "legacy"):
        return BACKEND_LOCAL
    return BACKEND_RSDT


def pick_rgb_res(width: int, height: int) -> list[int]:
    w, h = int(width or 0), int(height or 0)
    if w > 0 and h > 0:
        for item in _RGB_PRESETS:
            if int(item[0]) == w and int(item[1]) == h:
                return list(item)
        best = min(
            _RGB_PRESETS,
            key=lambda it: abs(int(it[0]) - w) + abs(int(it[1]) - h),
        )
        return list(best)
    return list(_RGB_PRESETS[1])


def pick_depth_res(width: int, height: int) -> list[int]:
    w, h = int(width or 0), int(height or 0)
    if w > 0 and h > 0:
        for item in _DEPTH_PRESETS:
            if int(item[0]) == w and int(item[1]) == h:
                return list(item)
    rgb = pick_rgb_res(width, height)
    for item in _DEPTH_PRESETS:
        if int(item[0]) == int(rgb[0]) and int(item[1]) == int(rgb[1]):
            return list(item)
    return list(_DEPTH_PRESETS[1])


def pick_fps(fps: int) -> int:
    want = max(1, int(fps or 15))
    return min(_FPS_PRESETS, key=lambda v: abs(int(v) - want))


def import_rsdt_orbbec_cls() -> Any:
    """导入 RSDT orbbec_camera 类。"""
    if not _RSDT.is_dir():
        raise RuntimeError(f"未找到 {_RSDT}")
    root_s = str(_RSDT)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    from hardware_module.orbbec_camera.orbbec_camera_driver import (  # type: ignore
        orbbec_camera,
    )

    return orbbec_camera


def create_rsdt_orbbec(serial: str) -> Any:
    cls = import_rsdt_orbbec_cls()
    sn = str(serial or "").strip() or "000000"
    cam = cls(sn)
    log.info("RSDT 奥比驱动已创建 serial=%s", sn)
    return cam
