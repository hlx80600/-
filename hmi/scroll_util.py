"""HMI 滚动与防滚轮误触。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

# 所有滚动页统一倍率（仍封顶，避免触控板一冲到底）
PAGE_WHEEL_SCALE = 0.55
MONITOR_WHEEL_SCALE = 0.55
# 每次滚轮最多移动的像素（防止一滑到底）
_MAX_STEP_PX = 140
_BASE_STEP_PX = 100
# 数值框已聚焦时，滚轮改值也放慢
_SPIN_WHEEL_SCALE = 0.35
# 兼容旧名
_PAGE_WHEEL_SCALE = PAGE_WHEEL_SCALE
_MONITOR_WHEEL_SCALE = MONITOR_WHEEL_SCALE


def _clamp_i(v: int, lim: int) -> int:
    if v > lim:
        return lim
    if v < -lim:
        return -lim
    return v


class _PageScrollFilter(QObject):
    """
    页面滚动：自己改滚动条，不把巨大 wheel/pixelDelta 交给 Qt。
    - 吞掉触控板惯性阶段（ScrollMomentum），避免松手后继续飞到底
    - 单次位移封顶
    """

    def __init__(self, scroll: QScrollArea, scale: float, parent: Optional[QObject] = None):
        super().__init__(parent or scroll)
        self.scroll = scroll
        self.scale = max(0.05, min(1.0, float(scale)))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(event, QWheelEvent):
            return False

        # 惯性滚动：直接吃掉，否则松手后还会 Continuously 往下冲
        try:
            phase = event.phase()
            if phase == Qt.ScrollPhase.ScrollMomentum:
                return True
        except Exception:
            pass

        ang = event.angleDelta()
        pix = event.pixelDelta()

        # 优先用角度（鼠标滚轮）；触控板常带巨大 pixel，有 angle 时忽略 pixel
        use_x = False
        raw = 0
        if ang.y() != 0 or ang.x() != 0:
            if abs(ang.x()) > abs(ang.y()):
                use_x = True
                raw = int(ang.x())
            else:
                raw = int(ang.y())
            # 120 ≈ 一格
            step = int(round(_BASE_STEP_PX * self.scale * (abs(raw) / 120.0)))
            step = max(8, min(_MAX_STEP_PX, step))
            if raw < 0:
                step = -step
        elif pix.y() != 0 or pix.x() != 0:
            if abs(pix.x()) > abs(pix.y()):
                use_x = True
                raw = int(pix.x())
            else:
                raw = int(pix.y())
            step = _clamp_i(int(round(raw * self.scale)), _MAX_STEP_PX)
            if step == 0 and raw != 0:
                step = 8 if raw > 0 else -8
        else:
            return True

        if event.inverted():
            step = -step

        bar = (
            self.scroll.horizontalScrollBar()
            if use_x
            else self.scroll.verticalScrollBar()
        )
        # angle/pixel >0 通常表示指向上/远离用户 → 内容上移 → 滚动条值减小
        bar.setValue(bar.value() - step)
        return True


class _SlowSpinFilter(QObject):
    """聚焦的 spin/combo/slider：缩小滚轮改值幅度。"""

    def __init__(self, scale: float, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.scale = float(scale)
        self._rewriting = False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or self._rewriting:
            return False
        if not isinstance(event, QWheelEvent):
            return False
        try:
            if event.phase() == Qt.ScrollPhase.ScrollMomentum:
                return True
        except Exception:
            pass

        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QWheelEvent as WE

        ang = event.angleDelta()
        sp = max(0.05, min(1.0, self.scale))
        ay = int(ang.y() * sp)
        ax = int(ang.x() * sp)
        if ay == 0 and ang.y() != 0:
            ay = 1 if ang.y() > 0 else -1
        if ax == 0 and ang.x() != 0:
            ax = 1 if ang.x() > 0 else -1
        # spin 改值只用角度，避免触控板 pixel 一次跳很多
        cloned = WE(
            QPointF(event.position()),
            QPointF(event.globalPosition()),
            QPoint(0, 0),
            QPoint(ax, ay),
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
            event.source(),
        )
        self._rewriting = True
        try:
            QApplication.sendEvent(obj, cloned)
        finally:
            self._rewriting = False
        return True


class _WheelGuard(QObject):
    """
    数值类控件：未获得焦点时滚轮不改值，并把滚轮交给外层滚动区。
    已聚焦时仍允许滚轮微调（需先单击该控件），且步进放慢。
    """

    _TYPES = (QAbstractSpinBox, QComboBox, QAbstractSlider)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._slow = _SlowSpinFilter(_SPIN_WHEEL_SCALE, self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(obj, self._TYPES):
            return False
        w = obj  # type: ignore[assignment]
        if w.hasFocus():
            return self._slow.eventFilter(obj, event)
        scroll = _find_scroll_area(w)
        if scroll is not None and isinstance(event, QWheelEvent):
            QApplication.sendEvent(scroll.viewport(), event)
            return True
        event.ignore()
        return True


_GUARD: Optional[_WheelGuard] = None


def _find_scroll_area(w: QWidget) -> Optional[QScrollArea]:
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea):
            return p
        p = p.parentWidget()
    return None


def harden_wheel(root: QWidget) -> None:
    """对 root 下所有 spin/combo/slider 安装防误触（可重复调用）。"""
    global _GUARD
    if _GUARD is None:
        _GUARD = _WheelGuard(QApplication.instance())
    for w in root.findChildren(QWidget):
        if isinstance(w, (QAbstractSpinBox, QComboBox, QAbstractSlider)):
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(_GUARD)


def attach_page_scroll(scroll: QScrollArea, *, wheel_scale: float | None = None) -> QScrollArea:
    """给已有 QScrollArea 安装与 wrap_in_scroll 相同的慢速/封顶滚轮。"""
    scale = float(PAGE_WHEEL_SCALE if wheel_scale is None else wheel_scale)
    scale = max(0.05, min(1.0, scale))
    step = max(12, int(round(28 * scale / max(0.05, PAGE_WHEEL_SCALE))))
    scroll.verticalScrollBar().setSingleStep(step)
    scroll.horizontalScrollBar().setSingleStep(step)
    scroll.verticalScrollBar().setPageStep(140)
    scroll.horizontalScrollBar().setPageStep(140)
    filt = _PageScrollFilter(scroll, scale, scroll)
    scroll.viewport().installEventFilter(filt)
    scroll.installEventFilter(filt)
    scroll.setProperty("_page_scroll_filter", filt)
    return scroll


def wrap_in_scroll(page: QWidget, *, wheel_scale: float | None = None) -> QScrollArea:
    """
    把整页放进可滚动区域，内容随窗口变窄自动换行/收缩，超出则滚轮滑动。
    wheel_scale: 滚轮倍率，越小越慢；默认 PAGE_WHEEL_SCALE。
    """
    scale = float(PAGE_WHEEL_SCALE if wheel_scale is None else wheel_scale)
    scale = max(0.05, min(1.0, scale))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    scroll.setWidget(page)
    attach_page_scroll(scroll, wheel_scale=scale)
    harden_wheel(page)
    return scroll
