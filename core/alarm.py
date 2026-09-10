"""报警管理：弹窗用队列 + 历史列表；同时写入落盘错误日志/黑匣子。"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Callable, Deque, List, Optional

log = logging.getLogger(__name__)


@dataclass
class AlarmItem:
    code: str
    message: str
    station: str = ""
    step: int = 0
    time: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    active: bool = True


class AlarmManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self.active: Optional[AlarmItem] = None
        self.history: Deque[AlarmItem] = deque(maxlen=200)
        self._popup_queue: Deque[AlarmItem] = deque()
        self._listeners: List[Callable[[], None]] = []

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def raise_alarm(
        self,
        code: str,
        message: str,
        station: str = "",
        step: int = 0,
        *,
        popup: bool = True,
    ) -> None:
        item = AlarmItem(code=code, message=message, station=station, step=step)
        with self._lock:
            same_active = (
                self.active is not None
                and self.active.code == item.code
                and self.active.station == item.station
                and self.active.message == item.message
            )
            already_queued = any(
                q.code == item.code
                and q.station == item.station
                and q.message == item.message
                for q in self._popup_queue
            )
            self.active = item
            self.history.appendleft(item)
            # 同一条报警只入队一次。夹爪 CAN 重连失败会每几秒再 raise，否则弹窗关不掉。
            if popup and not same_active and not already_queued:
                self._popup_queue.append(item)
        try:
            from core.blackbox import record_alarm

            record_alarm(
                code, message, station, step, popup=popup, active=True
            )
        except Exception:
            pass
        if not same_active:
            log.error("报警 [%s] %s@%s %s", code, station, step, message)
        self._notify()

    def note_event(
        self, code: str, message: str, station: str = "", step: int = 0
    ) -> None:
        """写入报警历史，不停机、不弹阻塞窗（用于「已自动消警」这类瞬态）。"""
        item = AlarmItem(
            code=code, message=message, station=station, step=step, active=False
        )
        with self._lock:
            self.history.appendleft(item)
        try:
            from core.blackbox import record_alarm

            record_alarm(
                code, message, station, step, popup=False, active=False
            )
        except Exception:
            pass
        log.warning("事件 [%s] %s@%s %s", code, station, step, message)
        self._notify()

    def reset(self) -> Optional[AlarmItem]:
        with self._lock:
            item = self.active
            if item:
                item.active = False
            self.active = None
            self._popup_queue.clear()
        self._notify()
        return item

    def pop_popup(self) -> Optional[AlarmItem]:
        with self._lock:
            if self._popup_queue:
                item = self._popup_queue.popleft()
                # 丢掉队列里同一条（重连期间可能已堆了多条）
                rest = deque()
                for q in self._popup_queue:
                    if not (
                        q.code == item.code
                        and q.station == item.station
                        and q.message == item.message
                    ):
                        rest.append(q)
                self._popup_queue = rest
                return item
            return None

    @property
    def has_alarm(self) -> bool:
        with self._lock:
            return self.active is not None
