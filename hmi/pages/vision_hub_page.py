"""视觉总页：工作区子页签 + 懒加载深度学习工作台 / 视觉方案。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.coordinator import Coordinator
from hmi import i18n
from hmi.load_progress import run_load_task
from hmi.style import apply_page_chrome
from hmi.pages.vision_workspace import (
    TAB_CAMERA_ROI,
    TAB_CHESSBOARD,
    TAB_DETECT,
    TAB_HANDEYE,
    TAB_PARAMS,
    VisionWorkspace,
    scroll_tab_body,
)

TAB_TRAIN = "采图训练"
TAB_SCHEME = "视觉方案"

# 兼容旧 goto / 帮助文案用的别名
_TAB_ALIASES: dict[str, str] = {
    "视觉采图": TAB_TRAIN,
    "采图": TAB_TRAIN,
    "训练": TAB_TRAIN,
    "深度学习": TAB_TRAIN,
    "ROI": TAB_CAMERA_ROI,
    "内参": TAB_CHESSBOARD,
    "棋盘格": TAB_CHESSBOARD,
    "手眼": TAB_HANDEYE,
    "检测": TAB_DETECT,
    "YOLO": TAB_DETECT,
    "参数": TAB_PARAMS,
    "视觉参数": TAB_PARAMS,
    "方案": TAB_SCHEME,
    "流程图": TAB_SCHEME,
}


class VisionHubPage(QWidget):
    """视觉总页：共享预览 + ROI / 内参 / 手眼 / 检测 / 采图训练 / 视觉方案。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.coord = coord
        self.ctx = coord.ctx
        self.workspace = VisionWorkspace(coord)
        self._train_page: QWidget | None = None
        self._scheme_page: QWidget | None = None
        self._train_host = QWidget()
        train_lay = QVBoxLayout(self._train_host)
        train_lay.setContentsMargins(0, 0, 0, 0)
        self._train_placeholder = QLabel("首次打开本页签时加载深度学习工作台…")
        self._train_placeholder.setAlignment(Qt.AlignCenter)
        self._train_placeholder.setStyleSheet("color:#7f8c8d;padding:24px;")
        train_lay.addWidget(self._train_placeholder)

        self._scheme_host = QWidget()
        scheme_lay = QVBoxLayout(self._scheme_host)
        scheme_lay.setContentsMargins(0, 0, 0, 0)
        self._scheme_placeholder = QLabel("首次打开本页签时加载视觉方案…")
        self._scheme_placeholder.setAlignment(Qt.AlignCenter)
        self._scheme_placeholder.setStyleSheet("color:#7f8c8d;padding:24px;")
        scheme_lay.addWidget(self._scheme_placeholder)

        self.tabs = self.workspace.inner_tabs
        self.tabs.blockSignals(True)
        self.tabs.addTab(scroll_tab_body(self._train_host), TAB_TRAIN)
        self.tabs.addTab(scroll_tab_body(self._scheme_host), TAB_SCHEME)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.workspace, 1)

        if hasattr(self.workspace, "btn_cam_win"):
            self.workspace.btn_cam_win.clicked.connect(self._open_cam_win)

        apply_page_chrome(self)

    def retranslate_ui(self) -> None:
        self.workspace.retranslate_ui()

    def _cam_id(self) -> str:
        """供相机监控避让：委托工作区当前相机。"""
        return self.workspace._cam_id()

    def select_tab(self, name: str) -> bool:
        """按子页签名切换；支持若干别名。"""
        target = _TAB_ALIASES.get(name, name)
        if self.workspace.select_tab(target):
            return True
        return self.workspace.select_tab(name)

    def refresh(self) -> None:
        self.workspace.refresh()
        for page in (self._train_page, self._scheme_page):
            if page is None:
                continue
            fn = getattr(page, "refresh", None)
            if callable(fn):
                fn()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        idx = self.tabs.currentIndex()
        if idx < 0:
            return
        title = self.tabs.tabText(idx)
        if title == TAB_TRAIN:
            if self._train_page is None:
                QTimer.singleShot(0, self._ensure_train_page)
            else:
                fn = getattr(self._train_page, "refresh", None)
                if callable(fn):
                    QTimer.singleShot(30, fn)
        elif title == TAB_SCHEME:
            if self._scheme_page is None:
                QTimer.singleShot(0, self._ensure_scheme_page)

    def _on_tab_changed(self, idx: int) -> None:
        if idx < 0:
            return
        title = self.tabs.tabText(idx)
        if title == TAB_TRAIN:
            self._ensure_train_page()
        elif title == TAB_SCHEME:
            self._ensure_scheme_page()

    def _ensure_train_page(self) -> None:
        """首次切入「采图训练」再构造五步工作台。"""
        if self._train_page is not None:
            return

        def _create() -> QWidget:
            from hmi.pages.dl_workbench import DlWorkbench

            page = DlWorkbench(self.coord)
            self._train_page = page
            lay = self._train_host.layout()
            assert lay is not None
            self._train_placeholder.setParent(None)
            lay.addWidget(page)
            self.workspace._restore_mid_split()
            return page

        run_load_task(
            self,
            i18n.tr("load.progress.train"),
            i18n.tr("load.progress.build_ui"),
            _create,
        )

    def _ensure_scheme_page(self) -> None:
        """首次切入「视觉方案」再构造流程图页。"""
        if self._scheme_page is not None:
            return

        def _create() -> QWidget:
            from hmi.pages.vision_scheme_page import VisionSchemePage

            page = VisionSchemePage(self.coord)
            self._scheme_page = page
            lay = self._scheme_host.layout()
            assert lay is not None
            self._scheme_placeholder.setParent(None)
            lay.addWidget(page)
            self.workspace._restore_mid_split()
            return page

        run_load_task(
            self,
            i18n.tr("load.progress.scheme"),
            i18n.tr("load.progress.build_ui"),
            _create,
        )

    def _open_cam_win(self) -> None:
        w = self.window()
        fn = getattr(w, "show_cam_monitor", None)
        if callable(fn):
            fn()
