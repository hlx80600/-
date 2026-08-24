"""视觉总页：左侧导航仅「视觉」一项；内含工作区子页签 + 懒加载采图训练。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.coordinator import Coordinator
from hmi.style import apply_page_chrome
from hmi.pages.vision_workspace import (
    TAB_CAMERA_ROI,
    TAB_CHESSBOARD,
    TAB_DETECT,
    TAB_HANDEYE,
    VisionWorkspace,
)

TAB_TRAIN = "采图训练"

# 兼容旧 goto / 帮助文案用的别名
_TAB_ALIASES: dict[str, str] = {
    "视觉采图": TAB_TRAIN,
    "采图": TAB_TRAIN,
    "训练": TAB_TRAIN,
    "ROI": TAB_CAMERA_ROI,
    "内参": TAB_CHESSBOARD,
    "棋盘格": TAB_CHESSBOARD,
    "手眼": TAB_HANDEYE,
    "检测": TAB_DETECT,
    "YOLO": TAB_DETECT,
}


class VisionHubPage(QWidget):
    """视觉总页：共享预览在上方，下方 QTabWidget 分 ROI / 内参 / 手眼 / 检测 / 采图训练。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self.workspace = VisionWorkspace(coord)
        self._train_page: QWidget | None = None
        self._train_host = QWidget()
        train_lay = QVBoxLayout(self._train_host)
        train_lay.setContentsMargins(0, 0, 0, 0)
        self._train_placeholder = QLabel("首次打开本页签时加载采图训练…")
        self._train_placeholder.setAlignment(Qt.AlignCenter)
        self._train_placeholder.setStyleSheet("color:#7f8c8d;padding:24px;")
        train_lay.addWidget(self._train_placeholder)

        # 把采图训练挂到工作区已有的 inner_tabs
        self.tabs = self.workspace.inner_tabs
        self.tabs.addTab(self._train_host, TAB_TRAIN)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.workspace, 1)

        # 工作区里的相机监控按钮 → 主窗入口
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
        if self._train_page is not None:
            fn = getattr(self._train_page, "refresh", None)
            if callable(fn):
                fn()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 工作区自有 showEvent 会启预览；此处确保已加载后补刷训练页进度
        if self._train_page is not None and self.tabs.tabText(self.tabs.currentIndex()) == TAB_TRAIN:
            fn = getattr(self._train_page, "refresh", None)
            if callable(fn):
                QTimer.singleShot(30, fn)

    def _on_tab_changed(self, idx: int) -> None:
        if idx < 0:
            return
        if self.tabs.tabText(idx) == TAB_TRAIN:
            self._ensure_train_page()

    def _ensure_train_page(self) -> None:
        """首次切入「采图训练」再构造 ZeroToPickPage，减轻首开卡顿。"""
        if self._train_page is not None:
            return
        from hmi.pages.zero_to_pick_page import ZeroToPickPage

        page = ZeroToPickPage(self.coord)
        self._train_page = page
        lay = self._train_host.layout()
        assert lay is not None
        self._train_placeholder.setParent(None)
        lay.addWidget(page)

    def _open_cam_win(self) -> None:
        w = self.window()
        fn = getattr(w, "show_cam_monitor", None)
        if callable(fn):
            fn()
