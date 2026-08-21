"""
GVL_Memery[1..10] —— 程序内部 BOOL 记忆（对应你原来的 PLC 全局变量习惯）。

规则：
- 自动运行中：仅程序可改，HMI 锁定
- 非自动（空闲/暂停/停止/报警/单步/手动）：HMI 可改
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Callable, Dict, List


MEMORY_LABELS: Dict[int, str] = {
    1: "皮带上料拍照完成",
    2: "机器人1手爪有料",
    3: "放料鞋槽有料",
    4: "放料鞋槽拍照完成",
    5: "机器人2手爪有料",
    6: "取料鞋槽有料",
    7: "取料鞋槽拍照完成",
    8: "机器人1取到左鞋",
    9: "机器人1取到右鞋",
    10: "放料鞋槽不匹配",
}


@dataclass
class MemoryBank:
    """线程安全的 BOOL 记忆表。"""

    _bits: Dict[int, bool] = field(default_factory=lambda: {i: False for i in range(1, 11)})
    _lock: RLock = field(default_factory=RLock)
    _listeners: List[Callable[[], None]] = field(default_factory=list)

    def get(self, index: int) -> bool:
        with self._lock:
            return bool(self._bits.get(index, False))

    def set(self, index: int, value: bool) -> None:
        with self._lock:
            self._bits[index] = bool(value)
        self._notify()

    def write_many(self, updates: Dict[int, bool]) -> None:
        with self._lock:
            for k, v in updates.items():
                self._bits[int(k)] = bool(v)
        self._notify()

    def snapshot(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._bits)

    def reset_all(self, value: bool = False) -> None:
        with self._lock:
            for k in self._bits:
                self._bits[k] = value
        self._notify()

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def __getitem__(self, index: int) -> bool:
        return self.get(index)

    def __setitem__(self, index: int, value: bool) -> None:
        self.set(index, value)
