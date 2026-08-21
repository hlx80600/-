"""整机运行模式 / 主状态。"""

from __future__ import annotations

from enum import Enum, auto
from threading import RLock
from typing import Callable, List


class RunMode(Enum):
    AUTO = auto()
    SINGLE_STEP = auto()
    MANUAL = auto()


class MachineState(Enum):
    IDLE = auto()
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ESTOP = auto()
    ALARM = auto()


class MachineController:
    """主状态与运行模式（线程安全）。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self.state = MachineState.IDLE
        self.mode = RunMode.AUTO
        self.debug_bypass = False
        self.init_ok = False
        self._listeners: List[Callable[[], None]] = []

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def set_state(self, state: MachineState) -> None:
        with self._lock:
            self.state = state
            if state == MachineState.READY:
                self.init_ok = True
            if state == MachineState.IDLE:
                self.init_ok = False
        self._notify()

    def set_mode(self, mode: RunMode) -> None:
        with self._lock:
            self.mode = mode
        self._notify()

    def set_debug_bypass(self, enabled: bool) -> None:
        with self._lock:
            self.debug_bypass = bool(enabled)
        self._notify()

    @property
    def is_auto_running(self) -> bool:
        """自动运行中：记忆锁定。"""
        with self._lock:
            return self.state == MachineState.RUNNING and self.mode == RunMode.AUTO

    @property
    def memory_editable(self) -> bool:
        """非自动运行时可改记忆。"""
        return not self.is_auto_running

    @property
    def stations_may_auto_start(self) -> bool:
        with self._lock:
            return self.state == MachineState.RUNNING and self.mode == RunMode.AUTO

    @property
    def stations_may_execute(self) -> bool:
        with self._lock:
            if self.state in (MachineState.ESTOP, MachineState.STOPPED, MachineState.IDLE):
                return False
            if self.state == MachineState.ALARM:
                return False
            if self.state == MachineState.PAUSED:
                return False
            if self.state == MachineState.RUNNING:
                return True
            return self.mode in (RunMode.SINGLE_STEP, RunMode.MANUAL) and self.state in (
                MachineState.READY,
                MachineState.RUNNING,
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self.state.name,
                "mode": self.mode.name,
                "debug_bypass": self.debug_bypass,
                "init_ok": self.init_ok,
                "memory_editable": self.memory_editable,
            }
