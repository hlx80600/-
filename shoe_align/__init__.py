"""鞋头对位相关模块包。

使 ``press_shoes`` 等仓库内模块可通过::

    from shoe_align.shoe_allign_controller import ShoeAlignController

或::

    from shoe_align import ShoeAlignController

进行导入。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

for import_root in (PROJECT_ROOT, PACKAGE_ROOT):
    root_str = str(import_root)
    if import_root.exists() and root_str not in sys.path:
        sys.path.insert(0, root_str)

__all__ = ["ShoeAlignController"]


def __getattr__(name: str) -> Any:
    if name == "ShoeAlignController":
        from .shoe_allign_controller import ShoeAlignController

        return ShoeAlignController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
