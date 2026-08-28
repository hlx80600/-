"""HMI 加载进度：在父页面/主窗内容区内显示遮罩 + 进度条（非独立浮窗）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from hmi import i18n
from hmi.logo_label import LOAD_PX, LogoLabel

T = TypeVar("T")

_CARD_QSS = """
    QWidget#loadProgressCard {
        background: #154360;
        border: 1px solid #1a5276;
        border-radius: 10px;
    }
    QLabel#loadProgressTitle {
        color: #ffffff;
        font-size: 16px;
        font-weight: bold;
    }
    QLabel#loadProgressStatus {
        color: #d5dbdb;
        font-size: 13px;
    }
    QProgressBar {
        border: 1px solid #5dade2;
        border-radius: 6px;
        background: #1a5276;
        text-align: center;
        color: #ecf0f1;
        height: 22px;
    }
    QProgressBar::chunk {
        background: #2ecc71;
        border-radius: 5px;
    }
"""


def load_host_for(widget: QWidget | None) -> QWidget | None:
    """尽量落在主窗右侧内容区；子页内加载则落在该页自身。"""
    if widget is None:
        return None
    win = widget.window()
    content_host = getattr(win, "_content_host", None)
    if content_host is not None and widget is win:
        return content_host
    return widget


class LoadProgressOverlay(QWidget):
    """父控件内全屏半透明遮罩 + 居中进度卡片。"""

    def __init__(self, parent: QWidget, title: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("loadProgressOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(28, 40, 51, 0.42);")
        self.setAutoFillBackground(True)

        self._card = QWidget(self)
        self._card.setObjectName("loadProgressCard")
        self._card.setFixedSize(420, 248)
        self._card.setStyleSheet(_CARD_QSS)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(24, 16, 24, 20)
        card_lay.setSpacing(8)

        self._logo = LogoLabel(side=LOAD_PX)
        card_lay.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title = QLabel(title or i18n.tr("load.progress.title"))
        self._title.setObjectName("loadProgressTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay.addWidget(self._title)

        self._status = QLabel(i18n.tr("load.progress.wait"))
        self._status.setObjectName("loadProgressStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        card_lay.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFormat("%p%")
        card_lay.addWidget(self._bar)

        parent.installEventFilter(self)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def _sync_geometry(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        self.setGeometry(0, 0, host.width(), host.height())
        x = max(0, (self.width() - self._card.width()) // 2)
        y = max(0, (self.height() - self._card.height()) // 2)
        self._card.move(x, y)

    def show_overlay(self) -> None:
        self._sync_geometry()
        self.show()
        self.raise_()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def set_progress(self, value: int, message: str) -> None:
        self._bar.setValue(max(0, min(100, int(value))))
        self._status.setText(message)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def close_overlay(self) -> None:
        host = self.parentWidget()
        if host is not None:
            host.removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return super().eventFilter(watched, event)


# 兼容旧名
LoadProgressDialog = LoadProgressOverlay


def run_with_progress(
    parent: QWidget | None,
    title: str,
    steps: list[tuple[int, str, Callable[[], T]]],
    *,
    default_title: str | None = None,
) -> T:
    """按步骤执行同步加载，每步更新进度条。"""
    host = load_host_for(parent)
    if host is None:
        raise ValueError("run_with_progress 需要有效的 parent 控件")
    overlay = LoadProgressOverlay(
        host,
        title or (default_title or i18n.tr("load.progress.title")),
    )
    overlay.show_overlay()
    result: T | None = None
    try:
        for value, message, fn in steps:
            overlay.set_progress(value, message)
            result = fn()
        overlay.set_progress(100, i18n.tr("load.progress.done"))
        return result
    finally:
        overlay.close_overlay()


def run_load_task(
    parent: QWidget | None,
    title: str,
    message: str,
    fn: Callable[[], T],
    *,
    start: int = 15,
) -> T:
    """单段重任务：在父页面内显示进度后执行 fn。"""
    host = load_host_for(parent)
    if host is None:
        raise ValueError("run_load_task 需要有效的 parent 控件")
    overlay = LoadProgressOverlay(host, title)
    overlay.show_overlay()
    try:
        overlay.set_progress(start, message)
        result = fn()
        overlay.set_progress(100, i18n.tr("load.progress.done"))
        return result
    finally:
        overlay.close_overlay()
