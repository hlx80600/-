"""报警历史列表（可选中 / 一键复制）。

刷新时若内容未变则不重绘；用户正在选中/聚焦列表时暂缓重建，避免选中被清掉。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
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
        self._list_fp = ""
        self._banner_fp = ""
        self._status_until = 0.0  # 复制成功提示保留到该时刻
        root = QVBoxLayout(self)

        tip = QLabel("可选中下方文字后 Ctrl+C，或点「复制」；刷新不会在选中时清掉选区。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#566573;")
        root.addWidget(tip)

        self.banner = QTextEdit()
        self.banner.setReadOnly(True)
        self.banner.setMaximumHeight(96)
        self.banner.setPlaceholderText("当前无活动报警")
        self.banner.setToolTip("活动报警全文，可直接拖选复制")
        root.addWidget(self.banner)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._copy_selected)
        root.addWidget(self.list, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#1a5276;")
        root.addWidget(self.lbl_status)

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
        root.addLayout(row)

    def _user_selecting(self) -> bool:
        """正在选文字或列表有焦点选中时，不要重建控件。"""
        if self.banner.hasFocus():
            cur = self.banner.textCursor()
            if cur.hasSelection():
                return True
        if self.list.hasFocus() and bool(self.list.selectedItems()):
            return True
        # 拖选过程中焦点可能在 viewport
        if self.banner.viewport().hasFocus():
            cur = self.banner.textCursor()
            if cur.hasSelection():
                return True
        return False

    def _selected_or_active_text(self) -> str:
        items = self.list.selectedItems()
        if items:
            return "\n".join(it.text() for it in items)
        item = self.list.currentItem()
        if item is not None:
            return item.text()
        cur = self.banner.textCursor()
        if cur.hasSelection():
            return cur.selectedText().replace("\u2029", "\n")
        body = self.banner.toPlainText().strip()
        if body and body != "当前无活动报警":
            return body
        a = self.ctx.alarms.active
        if a:
            return format_alarm_text(a.code, a.station, a.step, a.message)
        if self.list.count() > 0:
            return self.list.item(0).text()
        return ""

    def _copy_selected(self) -> None:
        import time

        text = self._selected_or_active_text()
        if not text:
            self.lbl_status.setText("没有可复制的报警")
            return
        QGuiApplication.clipboard().setText(text)
        self._status_until = time.monotonic() + 2.0
        self.lbl_status.setText("已复制到剪贴板（不弹窗）")

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
        self._list_fp = ""
        self._banner_fp = ""
        self.refresh()

    def refresh(self) -> None:
        import time

        if self._status_until and time.monotonic() > self._status_until:
            self._status_until = 0.0
            self.lbl_status.setText("")

        a = self.ctx.alarms.active
        if a:
            banner = format_alarm_text(a.code, a.station, a.step, a.message)
        else:
            banner = "当前无活动报警"

        rows: list[str] = []
        for item in list(self.ctx.alarms.history)[:50]:
            if item.active:
                flag = "ACTIVE"
            elif str(item.code).startswith("AUTO"):
                flag = "瞬态"
            else:
                flag = "OK"
            rows.append(
                f"{item.time} [{flag}] {item.code} {item.station}@{item.step} {item.message}"
            )
        list_fp = "\n".join(rows)

        # 用户正在选中：只更新状态栏提示到期，不碰 banner/list
        if self._user_selecting():
            return

        if banner != self._banner_fp:
            # 仅内容变化时改写，避免清掉选区
            self._banner_fp = banner
            self.banner.setPlainText(banner)

        if list_fp == self._list_fp:
            return
        self._list_fp = list_fp

        # 尽量保留原先选中行（按全文匹配）
        keep = [it.text() for it in self.list.selectedItems()]
        self.list.blockSignals(True)
        self.list.clear()
        for line in rows:
            self.list.addItem(line)
        if keep:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if it is not None and it.text() in keep:
                    it.setSelected(True)
        self.list.blockSignals(False)
