"""Compatibility helpers for loading checkpoints trained with the casbotXYZ package name."""

from __future__ import annotations

import sys
import types


def register_casbotxyz_aliases() -> None:
    """Map casbotXYZ.* imports to ImgAct so legacy best.pt weights can unpickle."""
    if "casbotXYZ.nn.modules" in sys.modules:
        return

    import ImgAct.nn as nn
    import ImgAct.nn.modules as modules

    casbot = sys.modules.get("casbotXYZ")
    if casbot is None:
        casbot = types.ModuleType("casbotXYZ")
        sys.modules["casbotXYZ"] = casbot
    casbot.nn = nn
    sys.modules["casbotXYZ.nn"] = nn
    sys.modules["casbotXYZ.nn.modules"] = modules
