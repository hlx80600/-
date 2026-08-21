from enum import Enum
from threading import RLock
from typing import Any, Callable, Optional, Tuple

class PressMachineStatus(Enum):
    LEFT_DONE = "左完成"
    RIGHT_DONE = "右完成"
    LEFT_WORKING = "左工作"
    RIGHT_WORKING = "右工作"
    LEFT_IDLE = "左空闲"
    RIGHT_IDLE = "右空闲"

PressMachineView = Tuple[PressMachineStatus, PressMachineStatus]

class PressMachineState:
    """Thread-safe container tracking press machine states."""
    def __init__(
        self,
        left: PressMachineStatus = PressMachineStatus.LEFT_IDLE,
        right: PressMachineStatus = PressMachineStatus.RIGHT_IDLE,
        notifier: Optional[Callable[[], None]] = None,
        *,
        log: Any,
    ) -> None:
        self.log = log
        self._left = left
        self._right = right
        self._left_lock = RLock()
        self._right_lock = RLock()
        self._notifier = notifier

    def _notify(self) -> None:
        if self._notifier:
            self._notifier()

    def set_state(self, *, left: Optional[PressMachineStatus] = None, right: Optional[PressMachineStatus] = None) -> None:
        self.log.info(f"[状态] 设置压机状态: left={left}, right={right}")
        if left is not None:
            with self._left_lock:
                self._left = left
                self._notify()
        if right is not None:
            with self._right_lock:
                self._right = right
                self._notify()

    def get_states(self) -> PressMachineView:
        with self._left_lock:
            left_state = self._left
        with self._right_lock:
            right_state = self._right
        return (left_state, right_state)

    def get_left_state(self) -> PressMachineStatus:
        with self._left_lock:
            return self._left

    def get_right_state(self) -> PressMachineStatus:
        with self._right_lock:
            return self._right
