"""小时钟：本机当前日期时间，每秒刷新。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class ClockLabel(QLabel):
    """右上角小时间，格式 ``2026-08-28 16:38:05``。"""

    def __init__(self, parent: QWidget | None = None, *, dark: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("clockLabel")
        self.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        if dark:
            self.setStyleSheet(
                "color:#d5dbdb;font-size:14px;font-weight:bold;"
                "padding:4px 8px;background:#0e1a24;border-radius:4px;"
            )
        else:
            self.setStyleSheet(
                "color:#1a5276;font-size:14px;font-weight:bold;"
                "padding:4px 10px;background:#eaf2f8;border-radius:6px;"
            )
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        self.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
