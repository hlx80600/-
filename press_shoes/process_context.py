from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Optional


@dataclass
class SlotRuntimeState:
    left_pick_dist: Optional[float] = None
    right_pick_dist: Optional[float] = None


class PressProcessContext:
    """Put/Take 共享的运行时状态，由 PressShoesWorkflow 创建并注入。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slot = SlotRuntimeState()

    def set_pick_dist(self, side: str, value: float) -> None:
        side = side.strip().lower()
        with self._lock:
            if side == "left":
                self._slot.left_pick_dist = value
            elif side == "right":
                self._slot.right_pick_dist = value

    def get_pick_dist(self, side: str) -> Optional[float]:
        side = side.strip().lower()
        with self._lock:
            if side == "left":
                return self._slot.left_pick_dist
            if side == "right":
                return self._slot.right_pick_dist
            return None
