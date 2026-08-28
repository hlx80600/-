"""报警历史 + 落盘错误日志 / 黑匣子 / 视觉运行快照（退出程序后仍可查）。

历史列表分页显示；刷新时若内容未变则不重绘；用户正在选中时暂缓重建。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.blackbox import (
    format_error_line,
    log_dir,
    read_blackbox_records,
    read_error_records,
)
from core.coordinator import Coordinator
from hmi import i18n
from hmi.alarm_dialog import format_alarm_text, show_copyable_alarm
from hmi.style import style_button
from hmi.pages.vision_snap_page import VisionSnapPage

# 触摸屏一页条数（约一屏，避免一次刷几百条）
_PAGE_SIZE = 20
_DISK_READ_LIMIT = 2000
_BLACKBOX_READ_LIMIT = 1000


class _PagedAlarmList(QWidget):
    """历史记录分页列表：首页 / 上一页 / 下一页 / 末页。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[str] = []
        self._page = 0
        self._fp = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        root.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_first = QPushButton()
        self.btn_prev = QPushButton()
        self.lbl_page = QLabel()
        self.lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page.setMinimumWidth(180)
        self.lbl_page.setStyleSheet("color:#1a5276;font-weight:bold;")
        self.btn_next = QPushButton()
        self.btn_last = QPushButton()
        style_button(self.btn_first, "neutral")
        style_button(self.btn_prev, "neutral")
        style_button(self.btn_next, "neutral")
        style_button(self.btn_last, "neutral")
        self.btn_first.clicked.connect(lambda: self._goto(0))
        self.btn_prev.clicked.connect(lambda: self._goto(self._page - 1))
        self.btn_next.clicked.connect(lambda: self._goto(self._page + 1))
        self.btn_last.clicked.connect(lambda: self._goto(self.page_count() - 1))
        bar.addWidget(self.btn_first)
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.lbl_page, 1)
        bar.addWidget(self.btn_next)
        bar.addWidget(self.btn_last)
        root.addLayout(bar)
        self.retranslate_ui()

    def page_count(self) -> int:
        n = len(self._rows)
        if n <= 0:
            return 1
        return max(1, (n + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def is_selecting(self) -> bool:
        return self.list.hasFocus() and bool(self.list.selectedItems())

    def set_rows(self, rows: list[str], *, reset_to_first: bool = False) -> None:
        """写入全量行（新→旧）。内容未变则不刷新，避免清掉选区。"""
        fp = "\n".join(rows)
        if fp == self._fp and not reset_to_first:
            self._refresh_chrome()
            return
        self._fp = fp
        self._rows = list(rows)
        if reset_to_first:
            self._page = 0
        else:
            self._page = min(max(0, self._page), self.page_count() - 1)
        self._render()

    def selected_or_page_text(self) -> str:
        items = self.list.selectedItems()
        if items:
            return "\n".join(it.text() for it in items)
        start = self._page * _PAGE_SIZE
        return "\n".join(self._rows[start : start + _PAGE_SIZE])

    def retranslate_ui(self) -> None:
        self.btn_first.setText(i18n.tr("alarm.page.first"))
        self.btn_prev.setText(i18n.tr("alarm.page.prev"))
        self.btn_next.setText(i18n.tr("alarm.page.next"))
        self.btn_last.setText(i18n.tr("alarm.page.last"))
        self._refresh_chrome()

    def _goto(self, page: int) -> None:
        self._page = max(0, min(int(page), self.page_count() - 1))
        self._render()

    def _refresh_chrome(self) -> None:
        pages = self.page_count()
        total = len(self._rows)
        self.lbl_page.setText(
            i18n.tr(
                "alarm.page.status",
                page=self._page + 1,
                pages=pages,
                total=total,
                size=_PAGE_SIZE,
            )
        )
        self.btn_first.setEnabled(self._page > 0)
        self.btn_prev.setEnabled(self._page > 0)
        self.btn_next.setEnabled(self._page < pages - 1)
        self.btn_last.setEnabled(self._page < pages - 1)

    def _render(self) -> None:
        keep = [it.text() for it in self.list.selectedItems()]
        start = self._page * _PAGE_SIZE
        chunk = self._rows[start : start + _PAGE_SIZE]
        self.list.blockSignals(True)
        self.list.clear()
        for line in chunk:
            self.list.addItem(line)
        if keep:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if it is not None and it.text() in keep:
                    it.setSelected(True)
        self.list.blockSignals(False)
        self._refresh_chrome()


class AlarmPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._banner_fp = ""
        self._status_until = 0.0
        self._disk_at = 0.0
        root = QVBoxLayout(self)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        session = QWidget()
        sroot = QVBoxLayout(session)
        self.tip_session = QLabel()
        self.tip_session.setWordWrap(True)
        self.tip_session.setStyleSheet("color:#566573;")
        sroot.addWidget(self.tip_session)

        self.banner = QTextEdit()
        self.banner.setReadOnly(True)
        self.banner.setMaximumHeight(96)
        self.banner.setToolTip("活动报警全文，可直接拖选复制")
        sroot.addWidget(self.banner)

        self.paged_session = _PagedAlarmList()
        self.paged_session.list.itemDoubleClicked.connect(self._copy_selected)
        sroot.addWidget(self.paged_session, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#1a5276;")
        sroot.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.btn_reset = QPushButton()
        style_button(self.btn_reset, "primary")
        self.btn_reset.clicked.connect(self._on_alarm_reset)
        self.btn_copy = QPushButton()
        style_button(self.btn_copy, "neutral")
        self.btn_copy.clicked.connect(self._copy_selected)
        self.btn_show = QPushButton()
        self.btn_show.clicked.connect(self._show_copyable)
        row.addWidget(self.btn_reset)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_show)
        row.addStretch(1)
        sroot.addLayout(row)
        self.tabs.addTab(session, "")

        disk = QWidget()
        droot = QVBoxLayout(disk)
        self.tip_disk = QLabel()
        self.tip_disk.setWordWrap(True)
        self.tip_disk.setStyleSheet("color:#566573;")
        droot.addWidget(self.tip_disk)
        self.paged_disk = _PagedAlarmList()
        droot.addWidget(self.paged_disk, 1)
        drow = QHBoxLayout()
        self.btn_refresh_disk = QPushButton()
        style_button(self.btn_refresh_disk, "primary")
        self.btn_refresh_disk.clicked.connect(
            lambda: self._reload_disk(reset_to_first=True)
        )
        self.btn_copy_disk = QPushButton()
        self.btn_copy_disk.clicked.connect(
            lambda: self._copy_text(self.paged_disk.selected_or_page_text())
        )
        self.btn_open_dir = QPushButton()
        style_button(self.btn_open_dir, "neutral")
        self.btn_open_dir.clicked.connect(self._open_log_dir)
        drow.addWidget(self.btn_refresh_disk)
        drow.addWidget(self.btn_copy_disk)
        drow.addWidget(self.btn_open_dir)
        drow.addStretch(1)
        droot.addLayout(drow)
        self.tabs.addTab(disk, "")

        box = QWidget()
        broot = QVBoxLayout(box)
        self.tip_box = QLabel()
        self.tip_box.setWordWrap(True)
        self.tip_box.setStyleSheet("color:#566573;")
        broot.addWidget(self.tip_box)
        self.paged_box = _PagedAlarmList()
        broot.addWidget(self.paged_box, 1)
        brow = QHBoxLayout()
        self.btn_refresh_box = QPushButton()
        style_button(self.btn_refresh_box, "primary")
        self.btn_refresh_box.clicked.connect(
            lambda: self._reload_blackbox(reset_to_first=True)
        )
        self.btn_copy_box = QPushButton()
        self.btn_copy_box.clicked.connect(
            lambda: self._copy_text(self.paged_box.selected_or_page_text())
        )
        self.btn_open_dir2 = QPushButton()
        self.btn_open_dir2.clicked.connect(self._open_log_dir)
        brow.addWidget(self.btn_refresh_box)
        brow.addWidget(self.btn_copy_box)
        brow.addWidget(self.btn_open_dir2)
        brow.addStretch(1)
        broot.addLayout(brow)
        self.tabs.addTab(box, "")

        self.snap_page = VisionSnapPage()
        self.tabs.addTab(self.snap_page, "")

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.tabs.setTabText(0, i18n.tr("alarm.tab.session"))
        self.tabs.setTabText(1, i18n.tr("alarm.tab.disk"))
        self.tabs.setTabText(2, i18n.tr("alarm.tab.blackbox"))
        self.tabs.setTabText(3, i18n.tr("alarm.tab.snaps"))
        self.tip_session.setText(i18n.tr("alarm.tip.session"))
        self.tip_disk.setText(i18n.tr("alarm.tip.disk", path=str(log_dir())))
        self.tip_box.setText(i18n.tr("alarm.tip.blackbox"))
        self.banner.setPlaceholderText(i18n.tr("alarm.empty_active"))
        self.btn_reset.setText(i18n.tr("alarm.btn.reset"))
        self.btn_copy.setText(i18n.tr("alarm.btn.copy"))
        self.btn_show.setText(i18n.tr("alarm.btn.window"))
        self.btn_refresh_disk.setText(i18n.tr("alarm.btn.refresh_disk"))
        self.btn_copy_disk.setText(i18n.tr("alarm.btn.copy_page"))
        self.btn_open_dir.setText(i18n.tr("alarm.btn.open_folder"))
        self.btn_refresh_box.setText(i18n.tr("alarm.btn.refresh_disk"))
        self.btn_copy_box.setText(i18n.tr("alarm.btn.copy_page"))
        self.btn_open_dir2.setText(i18n.tr("alarm.btn.open_folder"))
        self.paged_session.retranslate_ui()
        self.paged_disk.retranslate_ui()
        self.paged_box.retranslate_ui()

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._reload_disk()
        elif index == 2:
            self._reload_blackbox()
        elif index == 3:
            self.snap_page.reload()

    def select_tab(self, name: str) -> bool:
        """按页签标题或别名切换（供 goto / 使用说明跳转）。"""
        key = str(name or "").strip()
        aliases = {
            "session": 0,
            "本次运行": 0,
            "disk": 1,
            "落盘错误": 1,
            "blackbox": 2,
            "黑匣子": 2,
            "运行快照": 3,
            "历史快照": 3,
            "视觉log": 3,
            "快照": 3,
            "snaps": 3,
            "Run snaps": 3,
        }
        idx = aliases.get(key)
        if idx is None:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == key:
                    idx = i
                    break
        if idx is None:
            return False
        self.tabs.setCurrentIndex(idx)
        return True

    def _copy_text(self, text: str) -> None:
        if not (text or "").strip():
            self.lbl_status.setText(i18n.tr("alarm.copy_empty"))
            return
        QGuiApplication.clipboard().setText(text)
        self._status_until = time.monotonic() + 2.0
        self.lbl_status.setText(i18n.tr("alarm.copied"))

    def _open_log_dir(self) -> None:
        path = log_dir()
        path.mkdir(parents=True, exist_ok=True)
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not ok:
            QMessageBox.information(
                self,
                i18n.tr("alarm.btn.open_folder"),
                i18n.tr("alarm.folder_fail", path=str(path)),
            )

    def _reload_disk(self, *, reset_to_first: bool = False) -> None:
        recs = read_error_records(_DISK_READ_LIMIT)
        rows = [format_error_line(r) for r in recs]
        self._disk_at = time.monotonic()
        self.paged_disk.set_rows(rows, reset_to_first=reset_to_first)

    def _reload_blackbox(self, *, reset_to_first: bool = False) -> None:
        recs = read_blackbox_records(_BLACKBOX_READ_LIMIT)
        rows = [format_error_line(r) for r in recs]
        self._disk_at = time.monotonic()
        self.paged_box.set_rows(rows, reset_to_first=reset_to_first)

    def _user_selecting(self) -> bool:
        """正在选文字或列表有焦点选中时，不要重建控件。"""
        if self.banner.hasFocus():
            cur = self.banner.textCursor()
            if cur.hasSelection():
                return True
        if self.paged_session.is_selecting():
            return True
        if self.banner.viewport().hasFocus():
            cur = self.banner.textCursor()
            if cur.hasSelection():
                return True
        return False

    def _selected_or_active_text(self) -> str:
        text = self.paged_session.selected_or_page_text()
        if text.strip():
            return text
        cur = self.banner.textCursor()
        if cur.hasSelection():
            return cur.selectedText().replace("\u2029", "\n")
        body = self.banner.toPlainText().strip()
        if body and body != i18n.tr("alarm.empty_active"):
            return body
        a = self.ctx.alarms.active
        if a:
            return format_alarm_text(
                a.code, a.station, a.step, a.message, time=a.time
            )
        return ""

    def _copy_selected(self) -> None:
        text = self._selected_or_active_text()
        if not text:
            self.lbl_status.setText(i18n.tr("alarm.copy_empty"))
            return
        QGuiApplication.clipboard().setText(text)
        self._status_until = time.monotonic() + 2.0
        self.lbl_status.setText(i18n.tr("alarm.copied"))

    def _show_copyable(self) -> None:
        a = self.ctx.alarms.active
        if a:
            show_copyable_alarm(
                self,
                code=a.code,
                station=a.station,
                step=a.step,
                message=a.message,
                time=a.time,
            )
            return
        text = self._selected_or_active_text()
        if not text:
            QMessageBox.information(
                self, i18n.tr("nav.alarm"), i18n.tr("alarm.copy_empty")
            )
            return
        show_copyable_alarm(self, code="HIST", station="", step=0, message=text)

    def _on_alarm_reset(self) -> None:
        tips = self.coord.cmd_alarm_reset() or []
        text = "\n".join(str(t) for t in tips) if tips else i18n.tr("alarm.reset_ok")
        failed = any(
            "失败" in str(t) or "仍有故障" in str(t) or "未连接" in str(t) for t in tips
        )
        if failed:
            QMessageBox.warning(self, i18n.tr("alarm.btn.reset"), text)
        else:
            QMessageBox.information(self, i18n.tr("alarm.btn.reset"), text)
        self._banner_fp = ""
        self.refresh()

    def refresh(self) -> None:
        if self._status_until and time.monotonic() > self._status_until:
            self._status_until = 0.0
            self.lbl_status.setText("")

        a = self.ctx.alarms.active
        if a:
            banner = format_alarm_text(
                a.code, a.station, a.step, a.message, time=a.time
            )
        else:
            banner = i18n.tr("alarm.empty_active")

        rows: list[str] = []
        for item in list(self.ctx.alarms.history):
            if item.active:
                flag = "ACTIVE"
            elif str(item.code).startswith("AUTO"):
                flag = "瞬态"
            else:
                flag = "OK"
            rows.append(
                f"{item.time} [{flag}] {item.code} {item.station}@{item.step} {item.message}"
            )

        if self._user_selecting():
            return

        if banner != self._banner_fp:
            self._banner_fp = banner
            self.banner.setPlainText(banner)

        self.paged_session.set_rows(rows)

        idx = self.tabs.currentIndex()
        if idx in (1, 2) and time.monotonic() - self._disk_at >= 2.0:
            if idx == 1 and not self.paged_disk.is_selecting():
                self._reload_disk()
            elif idx == 2 and not self.paged_box.is_selecting():
                self._reload_blackbox()
