"""设置总页：语言 / 界面 / 运动 / 通信 / 手机监控。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from core.coordinator import Coordinator
from hmi import i18n
from hmi.load_progress import run_load_task
from hmi.pages.settings_interface_page import SettingsInterfacePage
from hmi.pages.settings_language_page import SettingsLanguagePage
from hmi.scroll_util import disable_tab_bar_wheel
from hmi.style import apply_page_chrome

TAB_LANG = "language"
TAB_UI = "interface"
TAB_MOTION = "motion"
TAB_COMM = "communication"
TAB_MOBILE = "mobile"

_TAB_KEYS = {
    TAB_LANG: "settings.tab.language",
    TAB_UI: "settings.tab.interface",
    TAB_MOTION: "settings.tab.motion",
    TAB_COMM: "settings.tab.communication",
    TAB_MOBILE: "settings.tab.mobile",
}

_TAB_ALIASES: dict[str, str] = {
    "通信配置": TAB_COMM,
    "通信": TAB_COMM,
    "config": TAB_COMM,
    "语言": TAB_LANG,
    "界面": TAB_UI,
    "运动": TAB_MOTION,
    "手机": TAB_MOBILE,
}


class SettingsHubPage(QWidget):
    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self._comm_page: QWidget | None = None
        self._comm_host = QWidget()
        comm_lay = QVBoxLayout(self._comm_host)
        comm_lay.setContentsMargins(0, 0, 0, 0)
        self._comm_placeholder = QLabel()
        self._comm_placeholder.setAlignment(Qt.AlignCenter)
        self._comm_placeholder.setStyleSheet("color:#7f8c8d;padding:24px;")
        comm_lay.addWidget(self._comm_placeholder)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._lang_page = SettingsLanguagePage(coord)
        self._ui_page = SettingsInterfacePage(coord, section="interface")
        self._motion_page = SettingsInterfacePage(coord, section="motion")
        self._mobile_page = SettingsInterfacePage(coord, section="mobile")
        self.tabs.addTab(self._lang_page, "")
        self.tabs.addTab(self._ui_page, "")
        self.tabs.addTab(self._motion_page, "")
        self.tabs.addTab(self._comm_host, "")
        self.tabs.addTab(self._mobile_page, "")
        self._tab_ids = [TAB_LANG, TAB_UI, TAB_MOTION, TAB_COMM, TAB_MOBILE]
        self.tabs.currentChanged.connect(self._on_tab_changed)
        disable_tab_bar_wheel(self.tabs)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.tabs, 1)
        apply_page_chrome(self)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        for i, tab_id in enumerate(self._tab_ids):
            self.tabs.setTabText(i, i18n.tr(_TAB_KEYS[tab_id]))
        self._comm_placeholder.setText(i18n.tr("settings.comm.loading"))
        self._lang_page.retranslate_ui()
        self._ui_page.retranslate_ui()
        self._motion_page.retranslate_ui()
        self._mobile_page.retranslate_ui()

    def select_tab(self, name: str) -> bool:
        target = _TAB_ALIASES.get(name, name)
        key = _TAB_KEYS.get(target, target)
        for i, tab_id in enumerate(self._tab_ids):
            if tab_id == target or i18n.tr(_TAB_KEYS[tab_id]) == name or i18n.tr(key) == name:
                self.tabs.setCurrentIndex(i)
                return True
        return False

    def refresh(self) -> None:
        if self._comm_page is not None:
            fn = getattr(self._comm_page, "refresh", None)
            if callable(fn):
                fn()

    def _on_tab_changed(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._tab_ids):
            return
        if self._tab_ids[idx] == TAB_COMM:
            self._ensure_comm_page()

    def _ensure_comm_page(self) -> None:
        if self._comm_page is not None:
            return

        def _create() -> QWidget:
            from hmi.pages.config_page import ConfigPage

            page = ConfigPage(self.coord)
            self._comm_page = page
            lay = self._comm_host.layout()
            assert lay is not None
            self._comm_placeholder.setParent(None)
            lay.addWidget(page)
            return page

        run_load_task(
            self,
            i18n.tr("load.progress.comm"),
            i18n.tr("load.progress.build_ui"),
            _create,
        )
