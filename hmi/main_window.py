"""主窗口：分页 HMI（适应屏幕 + 页内滚动 + 防滚轮误触）。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi.alarm_dialog import show_copyable_alarm
from hmi.pages.alarm_page import AlarmPage
from hmi.pages.config_page import ConfigPage
from hmi.pages.dry_run_page import DryRunPage
from hmi.pages.monitor_page import MonitorPage
from hmi.pages.motion_steps_page import MotionStepsPage
from hmi.pages.payload_page import PayloadPage
from hmi.pages.points_page import PointsPage
from hmi.pages.press_io_page import PressIoPage
from hmi.pages.production_page import ProductionPage
from hmi.pages.shield_pick_page import ShieldPickPage
from hmi.pages.step_debug_page import StepDebugPage
from hmi.pages.help_page import HelpPage
from hmi.pages.vision_monitor_page import VisionMonitorWindow
from hmi.pages.vision_page import VisionPage
from hmi.pages.zero_to_pick_page import ZeroToPickPage
from hmi.scroll_util import MONITOR_WHEEL_SCALE, harden_wheel, wrap_in_scroll
from hmi.style import style_button
from hmi.tab_titles import T


_APP_QSS = """
QMainWindow, QWidget {
    background: #eef1f4;
    color: #1c2833;
}
QTabWidget::pane {
    border: 1px solid #c5d0dc;
    border-radius: 6px;
    background: #f7f9fb;
    top: -1px;
}
QTabBar {
    font-size: 13px;
    font-weight: bold;
}
QTabBar::tab {
    background: #d5dde6;
    color: #2c3e50;
    padding: 8px 12px;
    margin-right: 3px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    min-height: 26px;
}
QTabBar::tab:selected {
    background: #1a5276;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    background: #aeb9c5;
}
QTabBar::scroller {
    width: 40px;
}
QTabBar QToolButton {
    background: #1a5276;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    min-width: 22px;
    min-height: 22px;
    padding: 2px;
}
QTabBar QToolButton:hover {
    background: #2471a3;
}
QScrollBar:vertical {
    width: 14px;
    background: #e8eef3;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #7f8c8d;
    min-height: 40px;
    border-radius: 6px;
}
QScrollBar::handle:vertical:hover {
    background: #5d6d7e;
}
QScrollBar:horizontal {
    height: 14px;
    background: #e8eef3;
}
QScrollBar::handle:horizontal {
    background: #7f8c8d;
    min-width: 40px;
    border-radius: 6px;
}
QStatusBar {
    background: #dce3ea;
}
"""


class MainWindow(QMainWindow):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self.setWindowTitle("莆田鞋厂四槽机器控制程序")
        self.setStyleSheet(_APP_QSS)
        self.setMinimumSize(800, 500)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)

        self.monitor = MonitorPage(coord)
        self.cam_win = VisionMonitorWindow(coord, parent=None)
        self.cam_monitor = self.cam_win.page
        self.production = ProductionPage(coord)
        self.step_page = StepDebugPage(coord)
        self.motion_steps = MotionStepsPage(coord)
        self.vision = VisionPage(coord)
        self.zero_pick = ZeroToPickPage(coord)
        self.points = PointsPage(coord)
        self.shield_pick = ShieldPickPage(coord)
        self.dry_run = DryRunPage(coord)
        self.payload = PayloadPage(coord)
        self.press_io = PressIoPage(coord)
        self.config = ConfigPage(coord)
        self.alarm = AlarmPage(coord)
        self.help = HelpPage(coord)
        self.vision.btn_cam_win.clicked.connect(self.show_cam_monitor)

        # 每页包一层滚动区：小屏可滑到底；大屏正常铺满
        self.tabs.addTab(wrap_in_scroll(self.monitor, wheel_scale=MONITOR_WHEEL_SCALE), T.MONITOR)
        self.tabs.addTab(wrap_in_scroll(self.production), T.PRODUCTION)
        self.tabs.addTab(wrap_in_scroll(self.step_page), T.STEP_DEBUG)
        self.tabs.addTab(wrap_in_scroll(self.motion_steps), T.MOTION)
        self.tabs.addTab(wrap_in_scroll(self.zero_pick), T.VISION_SETUP)
        self.tabs.addTab(wrap_in_scroll(self.vision), T.VISION)
        self.tabs.addTab(wrap_in_scroll(self.points), T.POINTS)
        self.tabs.addTab(wrap_in_scroll(self.shield_pick), T.SHIELD_PICK)
        self.tabs.addTab(wrap_in_scroll(self.dry_run), T.DRY_RUN)
        self.tabs.addTab(wrap_in_scroll(self.payload), T.PAYLOAD)
        self.tabs.addTab(wrap_in_scroll(self.press_io), T.PRESS_IO)
        self.tabs.addTab(wrap_in_scroll(self.config), T.CONFIG)
        self.tabs.addTab(wrap_in_scroll(self.alarm), T.ALARM)
        self.tabs.addTab(self.help, T.HELP)

        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(6, 6, 6, 6)
        cam_bar = QHBoxLayout()
        self.btn_cam_win = QPushButton(f"{T.CAM_MONITOR}窗口")
        self.btn_cam_win.setToolTip("独立窗口，可与本页调试同时显示。关掉后点这里再打开。")
        style_button(self.btn_cam_win, "motion")
        self.btn_cam_win.clicked.connect(self.show_cam_monitor)
        cam_bar.addWidget(self.btn_cam_win, 0)
        cam_bar.addStretch(1)
        lay.addLayout(cam_bar)
        lay.addWidget(self.tabs)
        self.setCentralWidget(wrap)

        # 控件可能晚建：切换页时再 harden 一次（如动态创建）
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(100)

        self._last_popup_code = None
        self._fitted = False
        # 临时尺寸；真正按屏幕适配在首次 showEvent
        self.resize(1100, 720)

    def _on_tab_changed(self, _idx: int) -> None:
        w = self.tabs.currentWidget()
        if w is not None:
            harden_wheel(w)

    def _fit_to_screen(self) -> None:
        """按可用桌面区域调整窗口，避免超出屏幕。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 820)
            self._fitted = True
            return
        geo = screen.availableGeometry()
        # 留边距给任务栏/窗口装饰；小屏尽量占满可用区
        w = min(1400, max(800, int(geo.width() * 0.96)))
        h = min(920, max(500, int(geo.height() * 0.92)))
        # 若可用高度更小，跟可用高度走
        w = min(w, geo.width())
        h = min(h, geo.height())
        self.resize(w, h)
        x = geo.x() + max(0, (geo.width() - w) // 2)
        y = geo.y() + max(0, (geo.height() - h) // 2)
        self.move(x, y)
        self._fitted = True

    def show_cam_monitor(self) -> None:
        self.cam_win.show_and_raise()

    def _tile_cam_window(self) -> None:
        """宽屏：主界面左、监控右并排；窄屏：监控叠在右上角。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.cam_win.resize(1000, 700)
            self.cam_win.show_and_raise()
            return
        geo = screen.availableGeometry()
        if geo.width() >= 1700:
            main_w = min(1180, max(920, int(geo.width() * 0.58)))
            cam_w = max(720, geo.width() - main_w)
            main_w = geo.width() - cam_w
            self.setGeometry(geo.x(), geo.y(), main_w, geo.height())
            self.cam_win.setGeometry(geo.x() + main_w, geo.y(), cam_w, geo.height())
        else:
            cw = min(1000, max(720, geo.width() - 48))
            ch = min(720, max(520, geo.height() - 48))
            self.cam_win.resize(cw, ch)
            self.cam_win.move(geo.x() + max(24, geo.width() - cw - 12), geo.y() + 24)
        self.cam_win.show_and_raise()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._fitted:
            self._fit_to_screen()
            self._tile_cam_window()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.cam_win.shutdown()
        super().closeEvent(event)

    def _refresh(self) -> None:
        self.monitor.refresh()
        self.cam_win.refresh()
        self.production.refresh()
        self.step_page.refresh()
        self.motion_steps.refresh()
        self.zero_pick.refresh()
        self.vision.refresh()
        self.points.refresh()
        self.shield_pick.refresh()
        self.dry_run.refresh()
        self.payload.refresh()
        self.press_io.refresh()
        self.alarm.refresh()

        popup = self.ctx.alarms.pop_popup()
        if popup and popup.code != "LINK":
            show_copyable_alarm(
                self,
                code=popup.code,
                station=popup.station,
                step=popup.step,
                message=popup.message,
                extra=(
                    "若含「路径：从…→…」请到「点位偏移」检查这两点或增加过渡点后用路径试跑。\n"
                    "复位后从失败步重试。"
                ),
            )
