"""四路相机监控：独立窗口，可与调试页同时显示。

可视化能力来自 visualize_module（取流 / 画框 / Qt 控件 / 实时推演）。
- 原图：后台 LiveGrabber 连续取流
- 计算结果：LiveComputeLoop 用缓存帧推演（不抢 grab），忙则跳过
- 「结果跟原图」：右侧用最新原图叠加上次推演文字，画面跟得上、不卡顿
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from core.camera_config import preview_interval_ms
from hmi.clock_label import ClockLabel
from hmi.logo_label import BAR_PX, LogoLabel, apply_window_icon
from hmi.style import apply_page_chrome, style_many
from visualize_module import viz

_frames = viz.activate_frames()
_qt = viz.activate_qt_views()
CAM_IDS = _frames.CAM_IDS
CAM_TITLES = _frames.CAM_TITLES
copy_bgr = _frames.copy_bgr
draw_roi = _frames.draw_roi
annotate_bgr = _frames.annotate_bgr
CamPane = _qt.CamPane

# 监控窗 UI：跟相机目标帧率（上限见 system.hmi.preview_max_fps）
_UI_MIN_INTERVAL = 1.0 / 60.0
_UI_VIS_INTERVAL = 1.0 / 20.0


def _app_is_active() -> bool:
    app = QApplication.instance()
    if app is None:
        return True
    return app.applicationState() == Qt.ApplicationState.ApplicationActive


def _vision_debug_cam() -> str:
    """主界面正在看「视觉」总页时，返回其当前相机 id；否则空串。"""
    try:
        from PySide6.QtWidgets import QApplication

        from hmi.tab_titles import T

        app = QApplication.instance()
        if app is None:
            return ""
        main = None
        for w in app.topLevelWidgets():
            if hasattr(w, "vision") and hasattr(w, "tabs") and hasattr(w, "cam_win"):
                main = w
                break
        if main is None:
            return ""
        # 左侧导航当前页 id
        nav_id = ""
        nav = getattr(main, "nav", None)
        nav_ids = getattr(main, "_nav_ids", None)
        if nav is not None and nav_ids:
            row = int(nav.currentRow())
            if 0 <= row < len(nav_ids):
                nav_id = str(nav_ids[row])
        elif callable(getattr(main, "_current_nav_id", None)):
            nav_id = str(main._current_nav_id())
        if nav_id != T.VISION:
            return ""
        vision = getattr(main, "vision", None)
        if vision is None:
            return ""
        # Hub 委托 workspace；旧 VisionPage 自身有 _cam_id
        fn = getattr(vision, "_cam_id", None)
        if callable(fn):
            return str(fn())
        ws = getattr(vision, "workspace", None)
        fn2 = getattr(ws, "_cam_id", None) if ws is not None else None
        return str(fn2()) if callable(fn2) else ""
    except Exception:
        return ""


class VisionMonitorPage(QWidget):
    _compute_done = Signal(str, str)

    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._raw_shown_ts: dict[str, float] = {}
        self._vis_shown_ts: dict[str, float] = {}
        self._raw_paint_wall: dict[str, float] = {}
        self._vis_paint_wall: dict[str, float] = {}
        self._overlay_raw_ts: dict[str, float] = {}
        self._pending: list[str] = []
        self._grabber = viz.activate_live_grabber(
            self.ctx.vision,
            self.ctx.cameras,
            skip_cam_fn=_vision_debug_cam,
            cam_ids=CAM_IDS,
        )
        self._compute = viz.activate_live_compute(
            self.ctx.vision,
            cam_ids=CAM_IDS,
            on_done=lambda cid, msg: self._compute_done.emit(cid, msg or ""),
        )

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.lbl_logo = LogoLabel(side=BAR_PX)
        bar.addWidget(self.lbl_logo, 0)
        self.chk_live = QCheckBox("刷新原图")
        self.chk_live.setChecked(True)
        self.chk_live.setToolTip("后台连续取流；关掉则停止抢相机")
        self.chk_live.toggled.connect(lambda _on: self._sync_workers())
        self.chk_compute = QCheckBox("实时推演")
        self.chk_compute.setChecked(True)
        self.chk_compute.setToolTip(
            "后台用最新缓存帧跑算法，不抢 grab；算不过来就跳过，保证原图流畅"
        )
        self.chk_compute.toggled.connect(lambda _on: self._sync_workers())
        self.chk_overlay = QCheckBox("结果跟原图")
        self.chk_overlay.setChecked(True)
        self.chk_overlay.setToolTip(
            "右侧用最新原图叠加上次推演文字/状态；画面跟原图同频，数字随推演更新"
        )
        self.btn_once = QPushButton("立即推演全部")
        self.btn_one = QPushButton("推演下一台")
        self.lbl_run = QLabel("推演：—")
        self.lbl_run.setStyleSheet("color:#1a5276;font-weight:bold;")
        style_many([(self.btn_once, "motion"), (self.btn_one, "primary")])
        self.btn_once.clicked.connect(self._compute_all)
        self.btn_one.clicked.connect(self._compute_next)
        bar.addWidget(self.chk_live)
        bar.addWidget(self.chk_compute)
        bar.addWidget(self.chk_overlay)
        bar.addWidget(self.btn_once)
        bar.addWidget(self.btn_one)
        bar.addWidget(self.lbl_run, 1)
        self.lbl_clock = ClockLabel()
        bar.addWidget(self.lbl_clock, 0)
        root.addLayout(bar)

        tip = QLabel(
            "实时推演 = 缓存帧算法（不掉原图帧）；结果跟原图 = 右侧跟拍叠加上次计算结果。"
            "cam1 显示中心→鞋头距离，以及抓鞋前TCP(工具1)/抓鞋后TCP(工具2) 变更参数。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#5d6d7e;font-size:12px;")
        root.addWidget(tip)

        grid = QGridLayout()
        self.panes: dict[str, CamPane] = {}
        for i, cid in enumerate(CAM_IDS):
            pane = CamPane(cid)
            self.panes[cid] = pane
            grid.addWidget(pane, i // 2, i % 2)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid, 1)

        apply_page_chrome(self)
        self._compute_done.connect(self._on_compute_done)

        self._app_active = True
        # CoarseTimer：失焦/切窗时不把 UI 线程打满
        self._ui_timer = QTimer(self)
        self._ui_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._ui_timer.timeout.connect(self.refresh)
        self._sync_ui_timer_interval()

    def set_app_active(self, active: bool) -> None:
        """主窗通知：程序是否前台。失焦时降频并停取流，减轻点其他窗口卡顿。"""
        self._app_active = bool(active)
        self._sync_ui_timer_interval()
        if not self._app_active:
            self._stop_workers()
            if self._ui_timer.isActive():
                # 仍保留很慢的 UI 心跳，避免完全僵死
                pass
        else:
            self._sync_workers()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._app_active = _app_is_active()
        self._sync_workers()
        if not self._ui_timer.isActive():
            self._ui_timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:
        self._ui_timer.stop()
        self._stop_workers()
        super().hideEvent(event)

    def _sync_ui_timer_interval(self) -> None:
        fps = 30
        for cid in CAM_IDS:
            cam = self.ctx.cameras.get(cid)
            if cam is not None:
                fps = max(fps, int(getattr(cam, "target_fps", 0) or 30))
        inactive = not self._app_active or not _app_is_active()
        self._ui_timer.setInterval(
            preview_interval_ms(self.ctx.cfg, fps, inactive=inactive)
        )

    def _want_live_grab(self) -> bool:
        return bool(
            self.isVisible()
            and self.chk_live.isChecked()
            and self._app_active
            and _app_is_active()
        )

    def _want_live_compute(self) -> bool:
        return bool(
            self.isVisible()
            and self.chk_compute.isChecked()
            and self._app_active
            and _app_is_active()
        )

    def _sync_workers(self) -> None:
        self._grabber.set_enabled(self._want_live_grab())
        self._compute.set_enabled(self._want_live_compute())
        self.lbl_run.setText(self._compute.status_text())

    def _stop_workers(self) -> None:
        self._grabber.stop()
        self._compute.stop()

    def _sync_live_grabber(self) -> None:
        self._sync_workers()

    def _stop_live_grabber(self) -> None:
        self._stop_workers()

    def _compute_all(self) -> None:
        self._pending = list(CAM_IDS)
        self._kick_manual()

    def _compute_next(self) -> None:
        if not self._pending:
            self._pending = list(CAM_IDS)
        self._kick_manual()

    def _kick_manual(self) -> None:
        while self._pending:
            cid = self._pending.pop(0)
            if self._compute.kick_once(cid):
                self.lbl_run.setText(f"推演中：{CAM_TITLES.get(cid, cid)} …")
                return
        self.lbl_run.setText(self._compute.status_text())

    def _on_compute_done(self, cid: str, msg: str) -> None:
        self._paint_vis(cid, force=True)
        if self._pending:
            self._kick_manual()
        else:
            self.lbl_run.setText(self._compute.status_text())

    def _paint_raw(self, cid: str, *, force: bool = False) -> None:
        vis = self.ctx.vision
        pane = self.panes[cid]
        cam = self.ctx.cameras.get(cid)
        mock = vis.cam_is_mock(cid) if cam is not None else True
        raw = vis.last_raw.get(cid)
        if raw is None and cam is not None:
            raw = getattr(cam, "last_color", None)
        ts = float(vis.last_raw_ts.get(cid) or 0.0)
        now = time.time()
        if raw is None:
            pane.lbl_raw.setText(
                f"原图 无图 | {'模拟' if mock else '真机'} "
                f"{(getattr(cam, 'last_error', '') or '') if cam else ''}".strip()
            )
            return
        if not force and ts > 0 and self._raw_shown_ts.get(cid) == ts:
            age = now - ts
            pane.lbl_raw.setText(f"原图  {'模拟' if mock else '真机'}  {age:.1f}s前")
            return
        if not force and (now - self._raw_paint_wall.get(cid, 0.0)) < _UI_MIN_INTERVAL:
            return
        shown = copy_bgr(raw)
        if shown is not None:
            shown = draw_roi(shown, cid)
        age = now - ts if ts else 0.0
        pane.show_raw(shown, f"原图  {'模拟' if mock else '真机'}  {age:.1f}s前")
        self._raw_paint_wall[cid] = now
        if ts > 0:
            self._raw_shown_ts[cid] = ts

    def _paint_vis(self, cid: str, *, force: bool = False) -> None:
        vis = self.ctx.vision
        pane = self.panes[cid]
        meta = vis.last_vis_meta.get(cid) or {}
        msg = str(meta.get("message") or "")
        ok = meta.get("ok")
        ts = float(meta.get("ts") or 0.0)
        now = time.time()
        flag = "OK" if ok else ("FAIL" if ok is False else "-")
        age = now - ts if ts else 0.0
        # cam1：突出中心→鞋头距离与抓鞋前/后 TCP
        tcp_hint = ""
        if cid == "cam1":
            dist = meta.get("center_toe_dist_mm")
            before = meta.get("tcp_before_grasp")
            after = meta.get("tcp_after_grasp")
            off = meta.get("toe_offset_in_grasp_tcp")
            parts = []
            if dist is not None:
                parts.append(f"中心→鞋头 {float(dist):.1f}mm")
            if isinstance(off, (list, tuple)) and len(off) >= 3:
                parts.append(
                    f"offset=[{float(off[0]):.1f},{float(off[1]):.1f},{float(off[2]):.1f}]"
                )
            if isinstance(before, (list, tuple)) and len(before) >= 3:
                parts.append(
                    "抓鞋前TCP=["
                    + ",".join(f"{float(v):.1f}" for v in list(before)[:6])
                    + "]"
                )
            if isinstance(after, (list, tuple)) and len(after) >= 3:
                parts.append(
                    "抓鞋后TCP=["
                    + ",".join(f"{float(v):.1f}" for v in list(after)[:6])
                    + "]"
                )
            if parts:
                tcp_hint = "  |  " + "  ".join(parts[:2])
                if len(parts) > 2:
                    tcp_hint += "\n" + "  ".join(parts[2:])
        label = f"结果 {flag}  {age:.1f}s前  {msg.splitlines()[0] if msg else ''}{tcp_hint}"

        # 结果跟原图：用最新原图叠文字，画面流畅；数字随 meta 更新
        if self.chk_overlay.isChecked():
            raw = vis.peek_raw(cid) if hasattr(vis, "peek_raw") else vis.last_raw.get(cid)
            raw_ts = float(vis.last_raw_ts.get(cid) or 0.0)
            if raw is None:
                pane.lbl_vis.setText("计算结果 无原图可叠加（先开「刷新原图」）")
                return
            need_img = force or self._overlay_raw_ts.get(cid) != raw_ts
            need_lbl = force or self._vis_shown_ts.get(cid) != ts
            if not need_img and not need_lbl:
                pane.lbl_vis.setText(label)
                return
            if not force and (now - self._vis_paint_wall.get(cid, 0.0)) < _UI_VIS_INTERVAL:
                if need_lbl:
                    pane.lbl_vis.setText(label)
                    if ts > 0:
                        self._vis_shown_ts[cid] = ts
                return
            ascii_lines = list(meta.get("ascii_lines") or [])
            if not ascii_lines:
                ascii_lines = [str(msg or "")[:60]] if msg else ["waiting…"]
            shown = annotate_bgr(
                raw,
                ascii_lines[:7],
                ok=bool(ok) if ok is not None else True,
                cam_id=cid,
                kind="LIVE",
            )
            pane.show_vis(shown, label)
            self._vis_paint_wall[cid] = now
            self._overlay_raw_ts[cid] = raw_ts
            if ts > 0:
                self._vis_shown_ts[cid] = ts
            return

        img = vis.last_vis.get(cid)
        if img is None:
            pane.lbl_vis.setText("计算结果 尚未推演（勾选「实时推演」或点立即推演）")
            return
        if not force and ts > 0 and self._vis_shown_ts.get(cid) == ts:
            pane.lbl_vis.setText(label)
            return
        if not force and (now - self._vis_paint_wall.get(cid, 0.0)) < _UI_VIS_INTERVAL:
            return
        pane.show_vis(img, label)
        self._vis_paint_wall[cid] = now
        if ts > 0:
            self._vis_shown_ts[cid] = ts

    def refresh(self) -> None:
        if not self.isVisible():
            self._stop_workers()
            return
        # 失焦：只维持低频率，不刷图、不抢相机
        if (not self._app_active) or (not _app_is_active()):
            self._sync_ui_timer_interval()
            self._stop_workers()
            return
        self._sync_ui_timer_interval()
        self._sync_workers()
        for cid in CAM_IDS:
            self._paint_raw(cid)
            self._paint_vis(cid)


class VisionMonitorWindow(QMainWindow):
    """独立相机监控窗：关窗只隐藏，主程序「相机监控窗口」可再打开。"""

    def __init__(self, coord: Coordinator, parent: QWidget | None = None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle("相机监控")
        apply_window_icon(self)
        self.setMinimumSize(880, 560)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #eef1f4; color: #1c2833; }
            """
        )
        self.page = VisionMonitorPage(coord)
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.chk_top = QCheckBox("窗口置顶")
        self.chk_top.setToolTip("勾选后始终浮在主界面之上，方便一边点调试一边看图")
        self.chk_top.toggled.connect(self._on_stay_top)
        top.addWidget(self.chk_top)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self.page, 1)
        self.setCentralWidget(wrap)

    def _on_stay_top(self, on: bool) -> None:
        from PySide6.QtCore import Qt

        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        if self.isHidden():
            return
        self.show()

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        # 显示后确保取流与 UI 定时器启动
        self.page._sync_workers()
        if not self.page._ui_timer.isActive():
            self.page._ui_timer.start()
        self.page.refresh()

    def shutdown(self) -> None:
        self.page._ui_timer.stop()
        self.page._stop_workers()
        self._allow_close = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            self.page._ui_timer.stop()
            self.page._stop_workers()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.page._ui_timer.stop()
        self.page._stop_workers()

    def refresh(self) -> None:
        """主窗偶发调用：隐藏/最小化时停取流；显示时不抢刷（由 page 定时器负责）。"""
        if not self.isVisible() or self.isMinimized():
            self.page._ui_timer.stop()
            self.page._stop_workers()
            return
        if not self.page._ui_timer.isActive():
            self.page._ui_timer.start()
        self.page._sync_workers()
