"""HMI 使用说明：各操作页的长释义集中在此。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from hmi.help_content import sections
from hmi import i18n
from hmi.style import apply_page_chrome


class HelpPage(QWidget):
    def __init__(self, _coord=None):
        super().__init__()
        self._secs: list = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setStyleSheet(
            "background:#1a5276;color:#ecf0f1;padding:8px;border-radius:4px;font-weight:bold;"
        )
        root.addWidget(self.head)

        self.ed_find = QLineEdit()
        self.ed_find.textChanged.connect(self._filter)
        root.addWidget(self.ed_find)

        body = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.setMinimumWidth(200)
        self.lst.setMaximumWidth(280)
        self.lst.currentRowChanged.connect(self._show_row)
        body.addWidget(self.lst, 0)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setStyleSheet(
            "QTextBrowser { background:#fafbfc; padding:8px; font-size:14px; }"
        )
        body.addWidget(self.browser, 1)
        root.addLayout(body, 1)

        apply_page_chrome(self)
        self.retranslate_ui()
        if self.lst.count():
            self.lst.setCurrentRow(0)

    def retranslate_ui(self) -> None:
        from PySide6.QtWidgets import QApplication

        from hmi.i18n import fonts as i18n_fonts

        self.head.setText(i18n.tr("help.title"))
        self.ed_find.setPlaceholderText(i18n.tr("help.search_placeholder"))
        query = self.ed_find.text()
        self._secs = sections()
        self._fill(query)
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        i18n_fonts.apply_font_to_widget(self, font)
        ff = font.family().replace('"', '\\"')
        self.browser.setStyleSheet(
            f'QTextBrowser {{ background:#fafbfc; padding:8px; font-size:14px; font-family: "{ff}"; }}'
        )

    def _fill(self, query: str = "") -> None:
        q = query.strip().lower()
        self.lst.blockSignals(True)
        self.lst.clear()
        for sid, title, html in self._secs:
            blob = f"{title} {html}".lower()
            if q and q not in blob:
                continue
            it = QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, html)
            it.setData(Qt.ItemDataRole.UserRole + 1, sid)
            self.lst.addItem(it)
        self.lst.blockSignals(False)
        if self.lst.count():
            self.lst.setCurrentRow(0)
        else:
            self.browser.setHtml(f"<p>{i18n.tr('help.no_match')}</p>")

    def _filter(self, text: str) -> None:
        self._fill(text)

    def _show_row(self, row: int) -> None:
        it = self.lst.item(row)
        if it is None:
            return
        html = it.data(Qt.ItemDataRole.UserRole) or ""
        self.browser.setHtml(html)
