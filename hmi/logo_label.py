"""RSDT 圆形徽章：透明底，按屏幕像素密度从原图缩小，避免发糊。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPaintEvent, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

_BADGE = Path(__file__).resolve().parent / "assets" / "rsdt_badge.png"

# 侧栏要够大，环上 R.S.D.T 才能看清；工具条/启动窗略小
NAV_PX = 140
BAR_PX = 64
SPLASH_PX = 100
LOAD_PX = 88

_src_cache: QPixmap | None = None


def badge_path() -> Path:
    return _BADGE


def source_pixmap() -> QPixmap:
    """原图只读一次（约 379px，缩小显示）。"""
    global _src_cache
    if _src_cache is None or _src_cache.isNull():
        _src_cache = QPixmap(str(_BADGE))
    return _src_cache


def app_icon() -> QIcon:
    pm = source_pixmap()
    icon = QIcon()
    if not pm.isNull():
        icon.addPixmap(pm)
    return icon


def apply_window_icon(target: Any) -> None:
    """给主窗 / 独立窗 / QApplication 设任务栏图标。"""
    fn = getattr(target, "setWindowIcon", None)
    if callable(fn):
        fn(app_icon())


class LogoLabel(QLabel):
    """圆形徽章。``side`` 为界面逻辑像素；绘制时按 devicePixelRatio 取样。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        side: int = NAV_PX,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("rsdtLogo")
        self._side = max(24, int(side))
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple[int, float] = (0, 0.0)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setToolTip("Robot Skills Development Team")
        self.setFixedSize(self._side, self._side)
        src = source_pixmap()
        if src.isNull():
            self.hide()

    def sizeHint(self) -> QSize:
        return QSize(self._side, self._side)

    def _ensure_scaled(self) -> QPixmap | None:
        src = source_pixmap()
        if src.isNull():
            return None
        dpr = float(self.devicePixelRatioF() or 1.0)
        px = max(1, int(round(self._side * dpr)))
        key = (px, round(dpr, 3))
        if self._scaled is not None and self._scaled_key == key:
            return self._scaled
        scaled = src.scaled(
            px,
            px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self._scaled = scaled
        self._scaled_key = key
        return scaled

    def paintEvent(self, event: QPaintEvent) -> None:
        pm = self._ensure_scaled()
        if pm is None or pm.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = max(pm.devicePixelRatio(), 1.0)
        lw = pm.width() / dpr
        lh = pm.height() / dpr
        x = (self.width() - lw) / 2.0
        y = (self.height() - lh) / 2.0
        painter.drawPixmap(int(round(x)), int(round(y)), pm)
        painter.end()
        event.accept()
