"""主窗口：左侧导航 + 右侧页面（适应屏幕 + 页内滚动 + 防滚轮误触）。

不再用顶部 Tab 翻页箭头：页多时在左侧列表直接点选。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi import i18n
from hmi.i18n import fonts as i18n_fonts
from hmi.alarm_dialog import show_copyable_alarm
from hmi.load_progress import run_with_progress
from hmi.pages.monitor_page import MonitorPage
from hmi.scroll_util import MONITOR_WHEEL_SCALE, harden_wheel, wrap_in_scroll
from hmi.style import style_button
from hmi.tab_titles import T, nav_title


_APP_QSS_BASE = """
QMainWindow, QWidget {{
    background: #eef1f4;
    color: #1c2833;
    font-family: "{ff}";
}}
QPushButton, QLabel, QCheckBox, QRadioButton, QGroupBox, QTabWidget, QTabBar,
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit,
QListWidget, QTableWidget, QHeaderView, QMenu, QToolTip {{
    font-family: "{ff}";
}}
QListWidget#navList {{
    background: #1a5276;
    color: #ecf0f1;
    border: none;
    outline: none;
    font-size: 14px;
    font-weight: bold;
    font-family: "{ff}";
    padding: 6px 0;
}}
QListWidget#navList::item {{
    padding: 12px 14px;
    margin: 2px 6px;
    border-radius: 5px;
    min-height: 22px;
}}
QListWidget#navList::item:selected {{
    background: #f7f9fb;
    color: #1a5276;
}}
QListWidget#navList::item:hover:!selected {{
    background: #2471a3;
    color: #ffffff;
}}
QLabel#navTitle {{
    background: #154360;
    color: #ffffff;
    font-size: 15px;
    font-weight: bold;
    font-family: "{ff}";
    padding: 12px 10px;
}}
QFrame#contentFrame, QWidget#contentHost {{
    background: #f7f9fb;
    border: 1px solid #c5d0dc;
    border-radius: 6px;
}}
QScrollBar:vertical {{
    width: 14px;
    background: #e8eef3;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #7f8c8d;
    min-height: 40px;
    border-radius: 6px;
}}
QScrollBar::handle:vertical:hover {{
    background: #5d6d7e;
}}
QScrollBar:horizontal {{
    height: 14px;
    background: #e8eef3;
}}
QScrollBar::handle:horizontal {{
    background: #7f8c8d;
    min-width: 40px;
    border-radius: 6px;
}}
QStatusBar {{
    background: #dce3ea;
    font-family: "{ff}";
}}
"""


def _build_app_qss(font_family: str) -> str:
    ff = font_family.replace("\\", "\\\\").replace('"', '\\"')
    return _APP_QSS_BASE.format(ff=ff)


def _create_page(title: str, coord: Coordinator) -> QWidget:
    """按需 import + 构造页面，避免启动时加载全部 HMI。"""
    if title == T.PRODUCTION:
        from hmi.pages.production_page import ProductionPage

        return ProductionPage(coord)
    if title == T.STEP_DEBUG:
        from hmi.pages.step_debug_page import StepDebugPage

        return StepDebugPage(coord)
    if title == T.MOTION:
        from hmi.pages.motion_steps_page import MotionStepsPage

        return MotionStepsPage(coord)
    if title == T.VISION:
        from hmi.pages.vision_hub_page import VisionHubPage

        return VisionHubPage(coord)
    if title == T.POINTS:
        from hmi.pages.points_page import PointsPage

        return PointsPage(coord)
    if title == T.SHIELD_PICK:
        from hmi.pages.shield_pick_page import ShieldPickPage

        return ShieldPickPage(coord)
    if title == T.DRY_RUN:
        from hmi.pages.dry_run_page import DryRunPage

        return DryRunPage(coord)
    if title == T.PAYLOAD:
        from hmi.pages.payload_page import PayloadPage

        return PayloadPage(coord)
    if title == T.PRESS_IO:
        from hmi.pages.press_io_page import PressIoPage

        return PressIoPage(coord)
    if title == T.GRIPPER:
        from hmi.pages.gripper_debug_page import GripperDebugPage

        return GripperDebugPage(coord)
    if title == T.SETTINGS:
        from hmi.pages.settings_hub_page import SettingsHubPage

        return SettingsHubPage(coord)
    if title == T.CONFIG:
        from hmi.pages.settings_hub_page import SettingsHubPage

        page = SettingsHubPage(coord)
        page.select_tab("communication")
        return page
    if title == T.ALARM:
        from hmi.pages.alarm_page import AlarmPage

        return AlarmPage(coord)
    if title == T.HELP:
        from hmi.pages.help_page import HelpPage

        return HelpPage(coord)
    raise KeyError(f"未知页面: {title}")


# (标题, 是否包滚动层, 滚轮倍率或 None)
_NAV_SPEC: list[tuple[str, bool, float | None]] = [
    (T.MONITOR, True, MONITOR_WHEEL_SCALE),
    (T.PRODUCTION, True, None),
    (T.STEP_DEBUG, True, None),
    (T.MOTION, True, None),
    (T.VISION, True, None),
    (T.POINTS, True, None),
    (T.SHIELD_PICK, True, None),
    (T.DRY_RUN, True, None),
    (T.PAYLOAD, True, None),
    (T.PRESS_IO, True, None),
    (T.GRIPPER, True, None),
    (T.SETTINGS, True, None),
    (T.ALARM, True, None),
    (T.HELP, False, None),
]


class MainWindow(QMainWindow):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        font, family = i18n.apply_ui_font()
        self.setStyleSheet(_build_app_qss(family))
        self.setMinimumSize(800, 500)

        self._page_cache: dict[str, QWidget] = {}
        self._loaded_indices: set[int] = set()
        self._cam_win = None
        self.cam_monitor = None
        self._jog_win = None
        self._nav_ids = [spec[0] for spec in _NAV_SPEC]

        # 启动只建「运行监控」，其余页首次点开再加载
        self.monitor = MonitorPage(coord)
        self._page_cache[T.MONITOR] = self.monitor
        self._loaded_indices.add(0)

        # —— 左侧导航 ——
        nav_wrap = QWidget()
        nav_wrap.setFixedWidth(168)
        nav_lay = QVBoxLayout(nav_wrap)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(0)
        self.lbl_nav_title = QLabel()
        self.lbl_nav_title.setObjectName("navTitle")
        self.lbl_nav_title.setAlignment(Qt.AlignCenter)
        nav_lay.addWidget(self.lbl_nav_title)

        self.nav = QListWidget()
        self.nav.setObjectName("navList")
        self.nav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setSpacing(1)
        for nav_id in self._nav_ids:
            QListWidgetItem(nav_title(nav_id), self.nav)
        nav_lay.addWidget(self.nav, 1)

        # —— 右侧内容 ——
        self.stack = QStackedWidget()
        self.stack.setObjectName("contentHost")
        for idx, (title, wrap, wheel) in enumerate(_NAV_SPEC):
            if idx == 0:
                self.stack.addWidget(
                    wrap_in_scroll(self.monitor, wheel_scale=wheel or MONITOR_WHEEL_SCALE)
                )
            else:
                ph = QWidget()
                ph.setProperty("_lazy_placeholder", True)
                self.stack.addWidget(ph)

        # 兼容旧代码：self.tabs.currentChanged / setCurrentIndex / currentWidget
        self.tabs = self.stack

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 6, 6, 6)
        cam_bar = QHBoxLayout()
        self.btn_cam_win = QPushButton()
        self.btn_cam_win.setToolTip("")
        style_button(self.btn_cam_win, "motion")
        self.btn_cam_win.clicked.connect(self.show_cam_monitor)
        cam_bar.addWidget(self.btn_cam_win, 0)
        self.btn_jog_win = QPushButton()
        self.btn_jog_win.setToolTip("")
        style_button(self.btn_jog_win, "accent")
        self.btn_jog_win.clicked.connect(lambda: self.show_jog_pendant())
        cam_bar.addWidget(self.btn_jog_win, 0)
        self.lbl_page = QLabel()
        self.lbl_page.setStyleSheet("font-size:16px;font-weight:bold;color:#1a5276;")
        cam_bar.addWidget(self.lbl_page, 0)
        cam_bar.addStretch(1)
        right_lay.addLayout(cam_bar)
        right_lay.addWidget(self.stack, 1)
        self._content_host = right

        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(nav_wrap, 0)
        lay.addWidget(right, 1)
        self.setCentralWidget(wrap)

        self._refresh_tick = 0
        titles_refresh = [t for t in self._nav_ids if t != T.HELP]

        def _lazy_call(title: str, method: str):
            def _fn() -> None:
                page = self._page_cache.get(title)
                if page is None:
                    return
                fn = getattr(page, method, None)
                if callable(fn):
                    fn()
            return _fn

        self._page_refreshers = {t: _lazy_call(t, "refresh") for t in titles_refresh}
        self._page_fast_refreshers = {
            T.MONITOR: _lazy_call(T.MONITOR, "refresh_fast"),
            T.STEP_DEBUG: _lazy_call(T.STEP_DEBUG, "refresh_fast"),
            T.POINTS: _lazy_call(T.POINTS, "refresh_fast"),
            T.SHIELD_PICK: _lazy_call(T.SHIELD_PICK, "refresh_fast"),
        }

        hmi_cfg = (self.ctx.cfg.get("system") or {}).get("hmi") or {}
        self._fast_ms_active = max(16, int(hmi_cfg.get("refresh_fast_ms", 33)))
        self._slow_ms_active = max(50, int(hmi_cfg.get("refresh_slow_ms", 100)))
        self._inactive_ms = max(100, int(hmi_cfg.get("refresh_inactive_ms", 250)))
        self._app_active = True

        self._fast_timer = QTimer(self)
        # CoarseTimer：避免 PreciseTimer 占满 UI 线程导致切窗/点击卡顿
        self._fast_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._fast_timer.setInterval(self._fast_ms_active)
        self._fast_timer.timeout.connect(self._fast_refresh)

        self._slow_timer = QTimer(self)
        self._slow_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._slow_timer.setInterval(self._slow_ms_active)
        self._slow_timer.timeout.connect(self._slow_refresh)

        self._timers_started = False

        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(0)
        self.stack.setCurrentIndex(0)
        self.nav.blockSignals(False)

        i18n.add_listener(self.retranslate_ui)
        self.retranslate_ui()

        self._last_popup_code = None
        self._fitted = False
        self.resize(1100, 720)

    def _load_page_if_needed(self, idx: int) -> None:
        if idx in self._loaded_indices:
            return
        if idx < 0 or idx >= len(_NAV_SPEC):
            return
        title, wrap, wheel = _NAV_SPEC[idx]
        page_name = nav_title(title)
        built: dict[str, QWidget] = {}

        def _build_page() -> QWidget:
            inner = _create_page(title, self.coord)
            built["inner"] = inner
            if title == T.STEP_DEBUG:
                self.step_page = inner
            elif title == T.VISION:
                self.vision = inner
            elif title == T.POINTS:
                self.points = inner
            elif title == T.SHIELD_PICK:
                self.shield_pick = inner
            elif title == T.SETTINGS:
                self.settings = inner
            self._page_cache[title] = inner
            return inner

        def _layout_page() -> QWidget:
            inner = built.get("inner")
            if inner is None:
                inner = _build_page()
            if wrap:
                widget = wrap_in_scroll(
                    inner,
                    wheel_scale=wheel if wheel is not None else None,
                )
            else:
                widget = inner
            old = self.stack.widget(idx)
            self.stack.removeWidget(old)
            old.deleteLater()
            self.stack.insertWidget(idx, widget)
            self._loaded_indices.add(idx)
            harden_wheel(widget)
            return widget

        run_with_progress(
            self,
            i18n.tr("load.progress.page", name=page_name),
            [
                (25, i18n.tr("load.progress.build_ui"), _build_page),
                (75, i18n.tr("load.progress.layout"), _layout_page),
            ],
        )

    def _ensure_cam_win(self):
        if self._cam_win is not None:
            return self._cam_win
        from hmi.load_progress import run_load_task

        def _create() -> QWidget:
            from hmi.pages.vision_monitor_page import VisionMonitorWindow

            win = VisionMonitorWindow(self.coord, parent=None)
            self._cam_win = win
            self.cam_monitor = win.page
            font, _ = i18n.apply_ui_font()
            i18n_fonts.apply_font_to_widget(self._cam_win, font)
            return win

        return run_load_task(
            self,
            i18n.tr("nav.cam_monitor"),
            i18n.tr("load.progress.cam_monitor"),
            _create,
        )

    @property
    def cam_win(self):
        return self._ensure_cam_win()

    def _on_app_state_changed(self, state) -> None:
        """点到其他程序窗口时降频刷新，避免 UI 线程占满导致系统卡顿。"""
        active = state == Qt.ApplicationState.ApplicationActive
        self._app_active = active
        if not self._timers_started:
            return
        if active:
            self._fast_timer.setInterval(self._fast_ms_active)
            self._slow_timer.setInterval(self._slow_ms_active)
        else:
            self._fast_timer.setInterval(self._inactive_ms)
            self._slow_timer.setInterval(max(self._inactive_ms, 400))
        try:
            cam = self._cam_win
            if cam is not None and hasattr(cam, "page"):
                cam.page.set_app_active(active)
        except Exception:
            pass

    def _on_nav_changed(self, idx: int) -> None:
        if idx < 0:
            return
        nav_id = self._nav_ids[idx] if 0 <= idx < len(self._nav_ids) else ""
        self._load_page_if_needed(idx)
        self.stack.setCurrentIndex(idx)
        if 0 <= idx < len(self._nav_ids):
            self.lbl_page.setText(nav_title(self._nav_ids[idx]))
        w = self.stack.currentWidget()
        if w is not None:
            harden_wheel(w)
        if nav_id != T.VISION:
            self._refresh_visible_page(force=True)

    def retranslate_ui(self) -> None:
        """语言切换后刷新导航、字体与已加载页。"""
        font, family = i18n.apply_ui_font()
        self.setStyleSheet(_build_app_qss(family))
        i18n_fonts.apply_font_to_widget(self, font)
        self.setWindowTitle(i18n.tr("app.title"))
        self.lbl_nav_title.setText(i18n.tr("nav.title"))
        self.btn_cam_win.setText(i18n.tr("nav.cam_monitor_btn"))
        self.btn_cam_win.setToolTip(i18n.tr("nav.cam_monitor_tip"))
        self.btn_jog_win.setText(i18n.tr("nav.jog_btn"))
        self.btn_jog_win.setToolTip(i18n.tr("nav.jog_tip"))
        row = int(self.nav.currentRow())
        for i, nav_id in enumerate(self._nav_ids):
            item = self.nav.item(i)
            if item is not None:
                item.setText(nav_title(nav_id))
        if 0 <= row < len(self._nav_ids):
            self.lbl_page.setText(nav_title(self._nav_ids[row]))
        for page in self._page_cache.values():
            i18n_fonts.apply_font_to_widget(page, font)
            fn = getattr(page, "retranslate_ui", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        if self._cam_win is not None:
            i18n_fonts.apply_font_to_widget(self._cam_win, font)
        if self._jog_win is not None:
            i18n_fonts.apply_font_to_widget(self._jog_win, font)
            fn_jog = getattr(self._jog_win, "retranslate_ui", None)
            if callable(fn_jog):
                fn_jog()

    def apply_hmi_refresh_settings(self) -> None:
        """设置页保存后更新定时器间隔。"""
        hmi_cfg = (self.ctx.cfg.get("system") or {}).get("hmi") or {}
        self._fast_ms_active = max(16, int(hmi_cfg.get("refresh_fast_ms", 33)))
        self._slow_ms_active = max(50, int(hmi_cfg.get("refresh_slow_ms", 100)))
        self._inactive_ms = max(100, int(hmi_cfg.get("refresh_inactive_ms", 250)))
        if not self._timers_started:
            return
        if self._app_active:
            self._fast_timer.setInterval(self._fast_ms_active)
            self._slow_timer.setInterval(self._slow_ms_active)
        else:
            self._fast_timer.setInterval(self._inactive_ms)
            self._slow_timer.setInterval(max(self._inactive_ms, 400))

    def _current_nav_id(self) -> str:
        row = int(self.nav.currentRow())
        if 0 <= row < len(self._nav_ids):
            return self._nav_ids[row]
        return ""

    def _refresh_visible_page(self, *, force: bool = False) -> None:
        nav_id = self._current_nav_id()
        fn = self._page_refreshers.get(nav_id)
        if fn is None:
            return
        # 部分页可降频：非 force 时按 tick 隔拍刷新（视觉页自有预览定时器，不降频）
        slow_pages = {T.VISION, T.POINTS, T.PAYLOAD, T.MOTION}
        if (not force) and nav_id in slow_pages and (self._refresh_tick % 2):
            return
        try:
            fn()
        except Exception:
            pass

    def _fast_refresh(self) -> None:
        nav_id = self._current_nav_id()
        fn = self._page_fast_refreshers.get(nav_id)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    def _slow_refresh(self) -> None:
        self._refresh_tick = (self._refresh_tick + 1) & 0xFFFF

        self._refresh_visible_page()

        try:
            cam = self._cam_win
            if cam is not None and (cam.isMinimized() or (not cam.isVisible())):
                if self._refresh_tick % 10 == 0:
                    cam.refresh()
            elif self._cam_win is None and self._refresh_tick % 20 == 0:
                pass
        except Exception:
            pass

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

    def _refresh(self) -> None:
        """兼容旧调用：等同慢刷。"""
        self._slow_refresh()

    def _fit_to_screen(self) -> None:
        """按可用桌面区域调整窗口，避免超出屏幕。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 820)
            self._fitted = True
            return
        geo = screen.availableGeometry()
        w = min(1400, max(800, int(geo.width() * 0.96)))
        h = min(920, max(500, int(geo.height() * 0.92)))
        w = min(w, geo.width())
        h = min(h, geo.height())
        self.resize(w, h)
        x = geo.x() + max(0, (geo.width() - w) // 2)
        y = geo.y() + max(0, (geo.height() - h) // 2)
        self.move(x, y)
        self._fitted = True

    def show_cam_monitor(self) -> None:
        """用户主动打开相机监控；启动时不自动弹出。"""
        self._tile_cam_window()

    def _tile_cam_window(self) -> None:
        """宽屏：主界面左、监控右并排；窄屏：监控叠在右上角。"""
        cam = self._ensure_cam_win()
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            cam.resize(1000, 700)
            cam.show_and_raise()
            return
        geo = screen.availableGeometry()
        if geo.width() >= 1700:
            main_w = min(1180, max(920, int(geo.width() * 0.58)))
            cam_w = max(720, geo.width() - main_w)
            main_w = geo.width() - cam_w
            self.setGeometry(geo.x(), geo.y(), main_w, geo.height())
            cam.setGeometry(geo.x() + main_w, geo.y(), cam_w, geo.height())
        else:
            cw = min(1000, max(720, geo.width() - 48))
            ch = min(720, max(520, geo.height() - 48))
            cam.resize(cw, ch)
            cam.move(geo.x() + max(24, geo.width() - cw - 12), geo.y() + 24)
        cam.show_and_raise()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._fitted:
            self._fit_to_screen()
        if not self._timers_started:
            self._timers_started = True
            self._fast_timer.start()
            self._slow_timer.start()
            self._refresh_visible_page(force=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._fast_timer.stop()
        self._slow_timer.stop()
        for robot in (self.ctx.robot1, self.ctx.robot2):
            try:
                robot.stop_jog(immediate=True)
            except Exception:
                pass
        if self._cam_win is not None:
            self._cam_win.shutdown()
        if self._jog_win is not None:
            self._jog_win.shutdown()
        super().closeEvent(event)

    def show_jog_pendant(self, robot_key: str | None = None) -> None:
        """打开独立示教器（不切走当前主界面页）。"""
        win = self._ensure_jog_win()
        if robot_key:
            win.panel.select_robot(str(robot_key))
        self._place_jog_window()

    def _ensure_jog_win(self):
        if self._jog_win is not None:
            return self._jog_win
        from hmi.pages.jog_pendant import JogPendantWindow

        win = JogPendantWindow(self.coord, parent=None)
        self._jog_win = win
        font, _ = i18n.apply_ui_font()
        i18n_fonts.apply_font_to_widget(win, font)
        return win

    def _place_jog_window(self) -> None:
        """打开示教器：记住上次大小位置，不再每次重置。"""
        win = self._ensure_jog_win()
        win.show_and_raise()

    def goto_page(self, title: str, *, vision_tab: str | None = None) -> bool:
        """按 nav id / 旧中文标题 / 显示名跳转。"""
        raw = str(title or "").strip()
        nav_id = raw
        if raw in (T.JOG, "jog", "点动示教", "示教器"):
            self.show_jog_pendant()
            return True
        snap_names = ("运行快照", "历史快照", "视觉log", "快照", "snaps", "Run snaps")
        if raw in snap_names or str(vision_tab or "") in snap_names:
            for i, nid in enumerate(self._nav_ids):
                if nid == T.ALARM:
                    self.nav.setCurrentRow(i)
                    page = self._page_cache.get(T.ALARM)
                    fn = getattr(page, "select_tab", None) if page is not None else None
                    if callable(fn):
                        fn("运行快照")
                    return True
            return False
        if raw in (T.VISION, T.VISION_SETUP, "视觉采图", "视觉调试", "vision", "vision_setup"):
            nav_id = T.VISION
        elif raw in (T.CONFIG, "通信配置", "config", "communication"):
            nav_id = T.SETTINGS
            settings_tab = vision_tab or "communication"
            for i, nid in enumerate(self._nav_ids):
                if nid == nav_id:
                    self.nav.setCurrentRow(i)
                    page = self._page_cache.get(T.SETTINGS)
                    fn = getattr(page, "select_tab", None) if page is not None else None
                    if callable(fn):
                        fn(settings_tab)
                    return True
            return False
        elif raw == T.SETTINGS or raw in ("设置", "settings"):
            nav_id = T.SETTINGS
        else:
            for nid in self._nav_ids:
                if raw == nid or raw == nav_title(nid):
                    nav_id = nid
                    break
        for i, nid in enumerate(self._nav_ids):
            if nid == nav_id:
                self.nav.setCurrentRow(i)
                tab = vision_tab
                if tab is None and raw in (T.VISION_SETUP, "视觉采图"):
                    tab = "采图训练"
                if tab and nav_id == T.VISION:
                    page = self._page_cache.get(T.VISION)
                    fn = getattr(page, "select_tab", None) if page is not None else None
                    if callable(fn):
                        fn(tab)
                elif tab and nav_id == T.SETTINGS:
                    page = self._page_cache.get(T.SETTINGS)
                    fn = getattr(page, "select_tab", None) if page is not None else None
                    if callable(fn):
                        fn(tab)
                return True
        return False
