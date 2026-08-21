"""报警历史列表（可选中 / 一键复制）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi.alarm_dialog import format_alarm_text, show_copyable_alarm
from hmi.style import style_button


class AlarmPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        root = QVBoxLayout(self)
        self.lbl = QLabel("当前无活动报警")
        self.lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.lbl.setWordWrap(True)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._copy_selected)

        row = QHBoxLayout()
        btn_reset = QPushButton("报警复位")
        style_button(btn_reset, "primary")
        btn_reset.clicked.connect(self._on_alarm_reset)
        btn_copy = QPushButton("复制选中/活动报警")
        style_button(btn_copy, "neutral")
        btn_copy.clicked.connect(self._copy_selected)
        btn_show = QPushButton("打开可复制窗口")
        btn_show.clicked.connect(self._show_copyable)
        row.addWidget(btn_reset)
        row.addWidget(btn_copy)
        row.addWidget(btn_show)
        row.addStretch(1)

        root.addWidget(self.lbl)
        root.addWidget(self.list)
        root.addLayout(row)

    def _selected_or_active_text(self) -> str:
        item = self.list.currentItem()
        if item is not None:
            return item.text()
        a = self.ctx.alarms.active
        if a:
            return format_alarm_text(a.code, a.station, a.step, a.message)
        if self.list.count() > 0:
            return self.list.item(0).text()
        return ""

    def _copy_selected(self) -> None:
        text = self._selected_or_active_text()
        if not text:
            self.lbl.setText("没有可复制的报警")
            return
        QGuiApplication.clipboard().setText(text)
        self.lbl.setText("已复制到剪贴板（不弹窗）")

    def _show_copyable(self) -> None:
        a = self.ctx.alarms.active
        if a:
            show_copyable_alarm(
                self,
                code=a.code,
                station=a.station,
                step=a.step,
                message=a.message,
            )
            return
        text = self._selected_or_active_text()
        if not text:
            QMessageBox.information(self, "报警", "没有可显示的报警")
            return
        show_copyable_alarm(self, code="HIST", station="", step=0, message=text)

    def _on_alarm_reset(self) -> None:
        tips = self.coord.cmd_alarm_reset() or []
        text = "\n".join(str(t) for t in tips) if tips else "已复位"
        failed = any("失败" in str(t) or "仍有故障" in str(t) or "未连接" in str(t) for t in tips)
        if failed:
            QMessageBox.warning(self, "报警复位", text)
        else:
            QMessageBox.information(self, "报警复位", text)
        self.refresh()

    def refresh(self) -> None:
        a = self.ctx.alarms.active
        if a:
            self.lbl.setText(f"活动报警: [{a.code}] {a.station} 步{a.step} {a.message}")
        else:
            self.lbl.setText("当前无活动报警")
        self.list.clear()
        for item in list(self.ctx.alarms.history)[:50]:
            if item.active:
                flag = "ACTIVE"
            elif str(item.code).startswith("AUTO"):
                flag = "瞬态"
            else:
                flag = "OK"
            self.list.addItem(
                f"{item.time} [{flag}] {item.code} {item.station}@{item.step} {item.message}"
            )
