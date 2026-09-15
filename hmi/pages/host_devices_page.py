"""本机设备：USB 列表 + 网卡 IP，方便填通信配置。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from core.host_inventory import collect, format_report, summary_line
from hmi import i18n
from hmi.scroll_util import disable_tab_bar_wheel
from hmi.style import apply_page_chrome, style_button


def _table() -> QTableWidget:
    tbl = QTableWidget()
    tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setVisible(False)
    tbl.setWordWrap(False)
    hdr = tbl.horizontalHeader()
    hdr.setStretchLastSection(True)
    hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return tbl


def _set_row(tbl: QTableWidget, row: int, cells: list[str]) -> None:
    tbl.setRowCount(max(tbl.rowCount(), row + 1))
    for col, text in enumerate(cells):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        tbl.setItem(row, col, item)


class HostDevicesPage(QWidget):
    """列出本机 USB 与 IP，只读、可复制。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self._snap: dict[str, Any] = {}

        root = QVBoxLayout(self)
        self.tip = QLabel()
        self.tip.setWordWrap(True)
        self.tip.setStyleSheet("color:#566573;")
        root.addWidget(self.tip)

        bar = QHBoxLayout()
        self.btn_refresh = QPushButton()
        style_button(self.btn_refresh, "primary")
        self.btn_refresh.clicked.connect(self._scan)
        self.btn_copy = QPushButton()
        style_button(self.btn_copy, "neutral")
        self.btn_copy.clicked.connect(self._copy)
        bar.addWidget(self.btn_refresh, 0)
        bar.addWidget(self.btn_copy, 0)
        bar.addStretch(1)
        root.addLayout(bar)

        self.lbl_sum = QLabel("-")
        self.lbl_sum.setStyleSheet("font-weight:bold;color:#1a5276;")
        self.lbl_sum.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.lbl_sum)

        self.grp_net = QGroupBox()
        net_lay = QVBoxLayout(self.grp_net)
        self.tbl_net = _table()
        self.tbl_net.setColumnCount(6)
        net_lay.addWidget(self.tbl_net)

        self.grp_usb = QGroupBox()
        usb_lay = QVBoxLayout(self.grp_usb)
        self.tbl_usb = _table()
        self.tbl_usb.setColumnCount(5)
        usb_lay.addWidget(self.tbl_usb)

        self.grp_nodes = QGroupBox()
        node_lay = QVBoxLayout(self.grp_nodes)
        self.tbl_nodes = _table()
        self.tbl_nodes.setColumnCount(3)
        node_lay.addWidget(self.tbl_nodes)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        disable_tab_bar_wheel(tabs)
        tabs.addTab(self.grp_net, "")
        tabs.addTab(self.grp_usb, "")
        tabs.addTab(self.grp_nodes, "")
        self._host_tabs = tabs
        root.addWidget(tabs, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#1a7a37;")
        root.addWidget(self.lbl_status)

        apply_page_chrome(self)
        self.retranslate_ui()
        self.tbl_net.itemDoubleClicked.connect(self._copy_cell)
        self.tbl_usb.itemDoubleClicked.connect(self._copy_cell)
        self.tbl_nodes.itemDoubleClicked.connect(self._copy_cell)

    def retranslate_ui(self) -> None:
        self.tip.setText(i18n.tr("host.tip"))
        self.btn_refresh.setText(i18n.tr("host.btn.refresh"))
        self.btn_copy.setText(i18n.tr("host.btn.copy"))
        self.grp_net.setTitle(i18n.tr("host.net.title"))
        self.grp_usb.setTitle(i18n.tr("host.usb.title"))
        self.grp_nodes.setTitle(i18n.tr("host.nodes.title"))
        self._host_tabs.setTabText(0, i18n.tr("host.net.title"))
        self._host_tabs.setTabText(1, i18n.tr("host.usb.title"))
        self._host_tabs.setTabText(2, i18n.tr("host.nodes.title"))
        self.tbl_net.setHorizontalHeaderLabels(
            [
                i18n.tr("host.col.iface"),
                i18n.tr("host.col.kind"),
                i18n.tr("host.col.state"),
                i18n.tr("host.col.ipv4"),
                i18n.tr("host.col.ipv6"),
                i18n.tr("host.col.mac"),
            ]
        )
        self.tbl_usb.setHorizontalHeaderLabels(
            [
                i18n.tr("host.col.bus"),
                i18n.tr("host.col.vid"),
                i18n.tr("host.col.name"),
                i18n.tr("host.col.serial"),
                i18n.tr("host.col.nodes"),
            ]
        )
        self.tbl_nodes.setHorizontalHeaderLabels(
            [
                i18n.tr("host.col.kind"),
                i18n.tr("host.col.name"),
                i18n.tr("host.col.target"),
            ]
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._scan()

    def refresh(self) -> None:
        """主窗口慢刷会调用；本页只在打开/点刷新时扫描，避免表格被清空。"""
        return

    def _scan(self) -> None:
        self._snap = collect()
        self.lbl_sum.setText(summary_line(self._snap))
        nets: list[dict[str, str]] = list(self._snap.get("net") or [])
        self.tbl_net.setRowCount(0)
        for i, row in enumerate(nets):
            _set_row(
                self.tbl_net,
                i,
                [
                    row.get("iface", ""),
                    row.get("kind", ""),
                    row.get("state", ""),
                    row.get("ipv4", "") or "-",
                    row.get("ipv6", "") or "-",
                    row.get("mac", "") or "-",
                ],
            )
        usbs: list[dict[str, str]] = list(self._snap.get("usb") or [])
        self.tbl_usb.setRowCount(0)
        for i, row in enumerate(usbs):
            _set_row(
                self.tbl_usb,
                i,
                [
                    row.get("bus", ""),
                    row.get("vid_pid", ""),
                    row.get("name", ""),
                    row.get("serial", "") or "-",
                    row.get("nodes", "") or "-",
                ],
            )
        self.tbl_nodes.setRowCount(0)
        n = 0
        for kind, key in (
            (i18n.tr("host.kind.serial"), "serial"),
            (i18n.tr("host.kind.v4l"), "v4l"),
        ):
            for row in list(self._snap.get(key) or []):
                _set_row(
                    self.tbl_nodes,
                    n,
                    [kind, row.get("name", ""), row.get("target", "")],
                )
                n += 1
        self.lbl_status.setText("")

    def _copy(self) -> None:
        text = format_report(self._snap or None)
        QGuiApplication.clipboard().setText(text)
        self.lbl_status.setText(i18n.tr("host.copied"))

    def _copy_cell(self, item: QTableWidgetItem) -> None:
        text = item.text().strip()
        if not text or text == "-":
            return
        QGuiApplication.clipboard().setText(text)
        self.lbl_status.setText(i18n.tr("host.copied_cell", text=text))
