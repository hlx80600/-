"""视觉工作区：共享预览 / Mock / 相机状态，子页签装 ROI·内参·手眼·检测。

检测一律旧压鞋机 YOLO（皮带 OBB+手眼、槽分类、鞋头对位、压杆）。
棋盘格只用 OpenCV 做内参，不做形状模板匹配。
由 VisionHubPage 再挂「采图训练」页签。
「视觉参数」按上方相机下拉只显示这一路常用项。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import threading
import time

try:
    import cv2  # type: ignore
    import numpy as np
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QImage,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.camera_config import preview_interval_ms
from core.coordinator import Coordinator
from hmi import i18n
from hmi.style import apply_page_chrome, style_button, style_many
from hmi.scroll_util import disable_tab_bar_wheel
from hmi.tab_titles import T
from hmi.pages import vision_commission as vcomm
from vision import calib, roi
from vision.camera_orbbec import enumerate_devices_text
from vision.depth_vis import depth_stats_text
from vision.handeye_solve import enrich_sample, k_from_any, set_clicked_pixel
from vision.pixel_to_robot import samples_scale_text

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

# 子页签标题（供 Hub / goto 切换）
TAB_CAMERA_ROI = "相机与ROI"
TAB_CHESSBOARD = "棋盘格内参"
TAB_HANDEYE = "手眼标定"
TAB_PARAMS = "视觉参数"
TAB_DETECT = "检测测试"


_CAM_TITLES = {
    "cam1": "cam1 皮带上料（YOLO+手眼）",
    "cam2": "cam2 鞋头对位",
    "cam3": "cam3 放料槽有无鞋",
    "cam4": "cam4 取料槽/压杆",
}

_SNAP_DIR = Path(__file__).resolve().parents[2] / "config" / "vision_snaps"


def _spin(lo: int, hi: int, val: int, w: int = 88) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setValue(val)
    sp.setFixedWidth(w)
    return sp


def _pair(name: str, widget: QWidget, label_w: int = 56) -> QWidget:
    lay = QHBoxLayout()
    lay.setSpacing(4)
    lay.setContentsMargins(0, 0, 0, 0)
    lb = QLabel(f"{name}:")
    lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lb.setFixedWidth(label_w)
    lay.addWidget(lb)
    lay.addWidget(widget)
    wrap = QWidget()
    wrap.setLayout(lay)
    wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    return wrap


def _set_label_text(lbl: QLabel, text: str) -> None:
    """文案没变就不动，避免 word-wrap 高度抖动带动整页闪。"""
    if lbl.text() != text:
        lbl.setText(text)


# 预览叠图最长边；不要按控件瞬时宽高 resize，否则滚动条出现/消失会闭环闪烁
_PREVIEW_MAX_SIDE = 1280


def scroll_tab_body(inner: QWidget) -> QScrollArea:
    """子页签自己滚动，避免撑高整页从而改掉左侧预览相框。"""
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    sc.setWidget(inner)
    return sc


class WheelSplit(QSplitter):
    """竖向分隔：在格子上滚轮改上下高度，原图与深度互不抢对方相框。"""

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.orientation() != Qt.Orientation.Vertical or self.count() < 2:
            super().wheelEvent(event)
            return
        w0 = self.widget(0)
        w1 = self.widget(1)
        if w0 is None or w1 is None or not w1.isVisible():
            event.ignore()
            return
        sizes = self.sizes()
        if len(sizes) < 2:
            event.ignore()
            return
        total = int(sizes[0] + sizes[1])
        delta = int(event.angleDelta().y())
        if delta == 0:
            event.ignore()
            return
        step = 40 if delta > 0 else -40
        min0 = max(96, int(w0.minimumHeight()))
        min1 = max(72, int(w1.minimumHeight()))
        top = max(min0, min(total - min1, int(sizes[0]) + step))
        self.setSizes([top, total - top])
        event.accept()


class PreviewLabel(QLabel):
    """显示图像；可拖选 ROI。

    不用 QLabel.setPixmap 的 sizeHint（跟图走），否则滚动条一出一进，整页闪。
    """

    roi_dragged = Signal(int, int, int, int)  # x,y,w,h
    pixel_clicked = Signal(int, int)  # 图像像素

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#222;color:#aaa;")
        self.setMouseTracking(True)
        self._pix = QPixmap()
        self._placeholder = "预览"
        self._img_w = 0
        self._img_h = 0
        self._disp_x = 0
        self._disp_y = 0
        self._disp_w = 0
        self._disp_h = 0
        self._drag = False
        self._p0 = (0, 0)
        self._p1 = (0, 0)
        self.roi_select_mode = False
        self._fit_cache = QPixmap()
        self._fit_key: tuple[int, int, int] | None = None

    def sizeHint(self) -> QSize:
        return QSize(640, 320)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 240)

    def has_frame(self) -> bool:
        return not self._pix.isNull()

    def show_placeholder(self, text: str) -> None:
        self._placeholder = text or "预览"
        self._pix = QPixmap()
        self._fit_cache = QPixmap()
        self._fit_key = None
        self._img_w = self._img_h = 0
        self.update()

    def clear_frame(self) -> None:
        self.show_placeholder("预览")

    def set_frame(self, pix: QPixmap, orig_w: int, orig_h: int) -> None:
        """设置一帧；控件几何不变，只重绘。"""
        self._pix = pix
        self._fit_cache = QPixmap()
        self._fit_key = None
        self._img_w, self._img_h = int(orig_w), int(orig_h)
        self._placeholder = ""
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#222222"))
        if self._pix.isNull():
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter),
                self._placeholder or "预览",
            )
            return
        target = self.rect()
        key = (int(self._pix.cacheKey()), int(target.width()), int(target.height()))
        if self._fit_key != key or self._fit_cache.isNull():
            self._fit_cache = self._pix.scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._fit_key = key
        scaled = self._fit_cache
        x = target.x() + (target.width() - scaled.width()) // 2
        y = target.y() + (target.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        self._disp_x, self._disp_y = x, y
        self._disp_w, self._disp_h = scaled.width(), scaled.height()

    def _to_image(self, pos) -> tuple[int, int] | None:
        if self._disp_w <= 0 or self._disp_h <= 0 or self._img_w <= 0:
            return None
        xn = (float(pos.x()) - float(self._disp_x)) / float(self._disp_w)
        yn = (float(pos.y()) - float(self._disp_y)) / float(self._disp_h)
        if xn < 0.0 or yn < 0.0 or xn >= 1.0 or yn >= 1.0:
            return None
        x = int(xn * self._img_w)
        y = int(yn * self._img_h)
        if x < 0 or y < 0 or x >= self._img_w or y >= self._img_h:
            return None
        return x, y

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pt = self._to_image(event.position() if hasattr(event, "position") else event.pos())
            if pt is None:
                return
            if self.roi_select_mode:
                self._drag = True
                self._p0 = self._p1 = pt
            else:
                self.pixel_clicked.emit(pt[0], pt[1])
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag:
            pt = self._to_image(event.position() if hasattr(event, "position") else event.pos())
            if pt is not None:
                self._p1 = pt
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag and event.button() == Qt.MouseButton.LeftButton:
            self._drag = False
            pt = self._to_image(event.position() if hasattr(event, "position") else event.pos())
            if pt is not None:
                self._p1 = pt
            x0, y0 = self._p0
            x1, y1 = self._p1
            x, y = min(x0, x1), min(y0, y1)
            w, h = abs(x1 - x0), abs(y1 - y0)
            if w >= 4 and h >= 4:
                self.roi_dragged.emit(x, y, w, h)
        super().mouseReleaseEvent(event)


class VisionWorkspace(QWidget):
    """共享预览与标定状态；左侧上下对照原图/深度，右侧子页签拆 ROI/内参/手眼/检测。"""

    board_detect_done = Signal(int, bool, object, str, int, int)
    board_capture_done = Signal(int, bool, object, str)
    _commission_done = Signal(str, str)
    _enum_done = Signal(str)

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.coord = coord
        self.ctx = coord.ctx
        self._calib_images: list = []
        self._syncing_cam_mock = False
        self._freeze = False
        self._frozen_img = None
        self._frozen_depth = None
        self._last_bgr = None
        self._last_depth_vis = None
        self._overlay_toe = None  # (gx,gy,tx,ty,length_mm) 全图坐标
        self._crosshair = True
        self._handeye_samples: list = []
        self._pending_handeye_robot: dict | None = None
        self._handeye_retreat_done = False
        self._last_handeye_pick_tcp: dict[str, float] | None = None
        self._pending_pixel: tuple[int, int] | None = None
        self._roi_by_cam: dict[str, dict] = {}
        self._frame_size_by_cam: dict[str, tuple[int, int]] = {}
        self._roi_editing_cam = ""
        self._roi_applying = False
        self._roi_autofit: set[str] = set()
        self._board_token = 0
        self._board_busy = False
        self._board_session = False
        self._board_cancel = threading.Event()
        self._board_snap = None
        self._board_hold_vis = None
        self._board_hold_until = 0.0
        self._board_last_ok: bool | None = None
        self._commission_busy = False
        self._preview_pix_buf = None
        self._depth_pix_buf = None
        self._tab_builders: dict[str, object] = {}
        self._tabs_built: set[str] = set()
        self.board_detect_done.connect(
            self._on_board_detect_done, Qt.ConnectionType.QueuedConnection
        )
        self.board_capture_done.connect(
            self._on_board_capture_done, Qt.ConnectionType.QueuedConnection
        )
        self._commission_done.connect(
            self._on_commission_done, Qt.ConnectionType.QueuedConnection
        )
        self._enum_done.connect(self._on_enum_done, Qt.ConnectionType.QueuedConnection)

        self._tab_builders = {
            TAB_CAMERA_ROI: self._build_tab_camera_roi,
            TAB_CHESSBOARD: self._build_tab_chessboard,
            TAB_HANDEYE: self._build_tab_handeye,
            TAB_PARAMS: self._build_tab_params,
            TAB_DETECT: self._build_tab_detect,
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # —— 常驻：Mock + 状态 ——
        mock_box = QGroupBox("相机分路 Mock（勾选=模拟；取消=真机，立刻生效）")
        mock_lay = QVBoxLayout(mock_box)
        mock_lay.setSpacing(6)
        mock_row = QHBoxLayout()
        self.chk_cam_mock: dict[str, QCheckBox] = {}
        for key, title in _CAM_TITLES.items():
            cb = QCheckBox(title)
            cam = self.ctx.cameras.get(key)
            cb.setChecked(bool(cam.use_mock) if cam else True)
            cb.toggled.connect(lambda on, k=key: self._on_cam_mock(k, on))
            self.chk_cam_mock[key] = cb
            mock_row.addWidget(cb)
        mock_lay.addLayout(mock_row)

        self.lbl_stack = QLabel("-")
        self.lbl_stack.setWordWrap(True)
        self.lbl_stack.setStyleSheet("color:#6b3a00;font-weight:bold;")
        mock_lay.addWidget(self.lbl_stack)
        self._stack_progress = QProgressBar()
        self._stack_progress.setRange(0, 0)
        self._stack_progress.setFixedHeight(16)
        self._stack_progress.setTextVisible(False)
        self._stack_progress.hide()
        mock_lay.addWidget(self._stack_progress)
        root.addWidget(mock_box)

        # —— 常驻：相机 + 预览 ——
        top = QHBoxLayout()
        top.setSpacing(6)
        self.cmb_cam = QComboBox()
        self.cmb_cam.setMinimumWidth(200)
        for k in self.ctx.cameras.keys():
            self.cmb_cam.addItem(_CAM_TITLES.get(k, k), k)
        top.addWidget(_pair("相机", self.cmb_cam, 40), 0)
        self.chk_freeze = QCheckBox("冻结画面")
        self.chk_freeze.toggled.connect(self._on_freeze)
        self.chk_cross = QCheckBox("十字准星")
        self.chk_cross.setChecked(True)
        self.chk_cross.toggled.connect(lambda on: setattr(self, "_crosshair", bool(on)))
        self.chk_roi_drag = QCheckBox("在预览上拖框设检测区")
        self.chk_roi_drag.setToolTip("勾选后，在上方原图按住左键拖出一个矩形，作为检测区域 ROI")
        self.chk_roi_drag.toggled.connect(self._on_roi_mode)
        self.chk_show_depth = QCheckBox("输出深度图")
        self.chk_show_depth.setChecked(True)
        self.chk_show_depth.setToolTip(
            "勾选：本路预览下方深度格、监控「深度」页、运行快照都输出深度伪彩；"
            "取消则只出彩色（写入 yaml cameras.*.enable_depth）。"
        )
        self._syncing_depth_chk = False
        self.chk_show_depth.toggled.connect(self._on_output_depth)
        top.addWidget(self.chk_freeze, 0)
        top.addWidget(self.chk_cross, 0)
        top.addWidget(self.chk_roi_drag, 0)
        top.addWidget(self.chk_show_depth, 0)
        btn_snap = QPushButton("截图保存")
        btn_reopen = QPushButton("重开相机")
        style_many([(btn_snap, "primary"), (btn_reopen, "neutral")])
        btn_snap.clicked.connect(self._save_snap)
        btn_reopen.clicked.connect(self._reopen_cam)
        top.addWidget(btn_snap, 0)
        top.addWidget(btn_reopen, 0)
        top.addStretch(1)
        root.addLayout(top)

        self.preview = PreviewLabel()
        self.preview.roi_dragged.connect(self._on_roi_dragged)
        self.preview.pixel_clicked.connect(self._on_pixel_clicked)
        self.preview.setMinimumHeight(160)
        self.preview_depth = PreviewLabel()
        self.preview_depth.setMinimumHeight(96)
        self.preview_depth.show_placeholder("深度")
        self.split_preview = WheelSplit(Qt.Orientation.Vertical)
        self.split_preview.setChildrenCollapsible(False)
        self.split_preview.setHandleWidth(8)
        self.split_preview.setStyleSheet(
            "QSplitter::handle:vertical { background: #5d6d7e; border-radius: 2px; }"
        )
        self.split_preview.addWidget(self.preview)
        self.split_preview.addWidget(self.preview_depth)
        self.split_preview.setStretchFactor(0, 3)
        self.split_preview.setStretchFactor(1, 2)
        self.split_preview.setSizes([520, 280])
        self._preview_split_sizes = [520, 280]
        self.split_preview.splitterMoved.connect(self._on_preview_split_moved)

        self.lbl_img = QLabel("图像: -")
        self.lbl_img.setWordWrap(True)

        # —— 子页签（Hub 再追加「采图训练」）——
        # 四个标定页都是轻量表单，一次建完。不要先放「加载中…」再 removeTab：
        # QTabWidget.removeTab(当前页) 会把当前项甩到下一张（棋盘格内参），
        # 看起来像永远卡在「加载中…」，必须切走再点回来才会真正构建。
        self.inner_tabs = QTabWidget()
        self.inner_tabs.setDocumentMode(True)
        self.inner_tabs.setMinimumWidth(400)
        self.inner_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.inner_tabs.blockSignals(True)
        for tab_name in (TAB_CAMERA_ROI, TAB_CHESSBOARD, TAB_HANDEYE, TAB_PARAMS, TAB_DETECT):
            self.inner_tabs.addTab(scroll_tab_body(self._tab_builders[tab_name]()), tab_name)
            self._tabs_built.add(tab_name)
        self.inner_tabs.setCurrentIndex(0)
        self.inner_tabs.blockSignals(False)
        self.inner_tabs.currentChanged.connect(self._on_inner_tab_changed)
        disable_tab_bar_wheel(self.inner_tabs)

        prev_wrap = QWidget()
        prev_wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        prev_col = QVBoxLayout(prev_wrap)
        prev_col.setSpacing(4)
        prev_col.setContentsMargins(0, 0, 0, 0)
        prev_col.addWidget(self.split_preview, 1)
        prev_col.addWidget(self.lbl_img, 0)

        self.split_mid = QSplitter(Qt.Orientation.Horizontal)
        self.split_mid.setChildrenCollapsible(False)
        self.split_mid.setHandleWidth(8)
        self.split_mid.setStyleSheet(
            "QSplitter::handle:horizontal { background: #c5d0dc; border-radius: 2px; }"
        )
        self.split_mid.addWidget(prev_wrap)
        self.split_mid.addWidget(self.inner_tabs)
        self.split_mid.setStretchFactor(0, 3)
        self.split_mid.setStretchFactor(1, 4)
        self.split_mid.setSizes([720, 640])
        self._mid_split_sizes = [720, 640]
        self.split_mid.splitterMoved.connect(self._on_mid_split_moved)
        root.addWidget(self.split_mid, 1)

        apply_page_chrome(self)
        self.retranslate_ui()
        self.cmb_cam.currentIndexChanged.connect(self._on_cam_changed)
        self._roi_editing_cam = self._cam_id()
        saved = roi.load_roi(self._roi_editing_cam)
        if saved:
            self._roi_by_cam[self._roi_editing_cam] = saved
        self._apply_roi_for(self._roi_editing_cam)
        self._sync_bind_fields()
        # 重活延后，避免点开瞬间卡死。不定进度条：动画会带动整页重绘。
        self.lbl_stack.setText("YOLO 环境后台检查中（检测时才加载模型）…")
        self._stack_progress.hide()
        self.lbl_check.setText("检查清单加载中…")
        self._handeye_samples = []
        self._commission_tick = 0
        self._last_preview_paint = 0.0
        self._preview_skip_key = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._preview_timer.timeout.connect(self._tick_preview)
        self._sync_preview_timer_interval()
        QTimer.singleShot(80, self._deferred_page_ready)

    def retranslate_ui(self) -> None:
        if hasattr(self, "btn_cam_win"):
            self.btn_cam_win.setText(i18n.tr("nav.cam_monitor_btn"))

    def _ensure_inner_tab(self, name: str) -> None:
        """补建尚未构造的子页签。采图训练由 Hub 负责，这里没有 builder。

        替换页签时必须挡住信号并还原 currentIndex：removeTab 会把当前项改到下一张。
        """
        if name in self._tabs_built:
            return
        builder = self._tab_builders.get(name)
        if builder is None:
            return
        tab_index = -1
        for i in range(self.inner_tabs.count()):
            if self.inner_tabs.tabText(i) == name:
                tab_index = i
                break
        if tab_index < 0:
            return
        widget = scroll_tab_body(builder())
        tabs = self.inner_tabs
        tabs.blockSignals(True)
        current = tabs.currentIndex()
        old = tabs.widget(tab_index)
        tabs.removeTab(tab_index)
        tabs.insertTab(tab_index, widget, name)
        want = current if 0 <= current < tabs.count() else 0
        tabs.setCurrentIndex(want)
        tabs.blockSignals(False)
        if old is not None:
            old.deleteLater()
        self._tabs_built.add(name)

    def _ensure_visible_inner_tab(self) -> None:
        """当前可见子页签若仍是占位，立刻建好（不要求先切走再点回来）。"""
        idx = self.inner_tabs.currentIndex()
        if idx < 0:
            return
        self._ensure_inner_tab(self.inner_tabs.tabText(idx))

    def _on_inner_tab_changed(self, idx: int) -> None:
        if idx < 0:
            return
        self._ensure_inner_tab(self.inner_tabs.tabText(idx))
        self._restore_mid_split()

    def _on_preview_split_moved(self, _pos: int, _index: int) -> None:
        if self.preview_depth.isVisible():
            self._preview_split_sizes = self.split_preview.sizes()

    def _on_mid_split_moved(self, _pos: int, _index: int) -> None:
        self._mid_split_sizes = self.split_mid.sizes()

    def _restore_mid_split(self) -> None:
        """切页签后把左右分隔拉回原位，避免相框跟着表单高度变。"""
        sizes = list(getattr(self, "_mid_split_sizes", []) or [])
        if len(sizes) >= 2:
            QTimer.singleShot(0, lambda s=sizes: self.split_mid.setSizes(s))

    def select_tab(self, name: str) -> bool:
        """按页签标题切换；找不到返回 False。"""
        for i in range(self.inner_tabs.count()):
            if self.inner_tabs.tabText(i) == name:
                self._ensure_inner_tab(name)
                self.inner_tabs.setCurrentIndex(i)
                return True
        return False

    def _build_tab_camera_roi(self) -> QWidget:
        """相机绑定 / 本路状态 / ROI 控件。"""
        tab = QWidget()
        lay = QVBoxLayout(tab)

        comm_box = QGroupBox("本路状态（步骤说明见「使用说明」页）")
        comm_lay = QVBoxLayout(comm_box)
        self.lbl_hw = QLabel("-")
        self.lbl_hw.setWordWrap(True)
        comm_lay.addWidget(self.lbl_hw)
        bind = QHBoxLayout()
        self.ed_serial = QLineEdit()
        self.ed_serial.setPlaceholderText("Orbbec 序列号，优先于 index")
        self.ed_serial.setMinimumWidth(180)
        self.sp_index = _spin(0, 32, 0, 64)
        b_enum = QPushButton("枚举设备")
        b_bind = QPushButton("写入serial并重开")
        b_copy = QPushButton("复制本路状态")
        b_open_snap = QPushButton("截图目录")
        b_open_cal = QPushButton("标定目录")
        b_open_models = QPushButton("YOLO模型目录")
        self.btn_cam_win = QPushButton()
        style_many(
            [
                (b_enum, "motion"),
                (b_bind, "success"),
                (b_copy, "primary"),
                (b_open_snap, "neutral"),
                (b_open_cal, "neutral"),
                (b_open_models, "neutral"),
                (self.btn_cam_win, "motion"),
            ]
        )
        b_enum.clicked.connect(self._enum_devices)
        b_bind.clicked.connect(self._bind_serial_reopen)
        b_copy.clicked.connect(self._copy_cam_status)
        b_open_snap.clicked.connect(lambda: self._open_dir(_SNAP_DIR))
        b_open_cal.clicked.connect(lambda: self._open_dir(calib.CALIB_DIR))
        b_open_models.clicked.connect(lambda: self._open_dir(_MODELS_DIR))
        bind.addWidget(QLabel("serial:"), 0)
        bind.addWidget(self.ed_serial, 1)
        bind.addWidget(QLabel("index:"), 0)
        bind.addWidget(self.sp_index, 0)
        for b in (
            b_enum,
            b_bind,
            b_copy,
            b_open_snap,
            b_open_cal,
            b_open_models,
            self.btn_cam_win,
        ):
            bind.addWidget(b, 0)
        comm_lay.addLayout(bind)
        self.lbl_check = QLabel("-")
        self.lbl_check.setWordWrap(True)
        self.lbl_check.setStyleSheet("color:#1a5276;font-weight:bold;")
        comm_lay.addWidget(self.lbl_check)
        lay.addWidget(comm_box)

        self.roi_box = QGroupBox("检测区域 ROI（绿框）")
        roi_lay = QVBoxLayout(self.roi_box)
        roi_row = QHBoxLayout()
        roi_row.setSpacing(8)
        self.sp_x = _spin(0, 4000, 0)
        self.sp_y = _spin(0, 4000, 0)
        self.sp_w = _spin(1, 4000, 640)
        self.sp_h = _spin(1, 4000, 480)
        for name, tip, sp in (
            ("左X", "绿框左边位置", self.sp_x),
            ("上Y", "绿框上边位置", self.sp_y),
            ("宽W", "绿框宽度", self.sp_w),
            ("高H", "绿框高度", self.sp_h),
        ):
            sp.setToolTip(tip)
            sp.valueChanged.connect(self._on_roi_spin)
            roi_row.addWidget(_pair(name, sp, 36), 0)
        roi_lay.addLayout(roi_row)
        self.lbl_roi_status = QLabel("-")
        self.lbl_roi_status.setWordWrap(True)
        self.lbl_roi_status.setStyleSheet("color:#1a5276;font-weight:bold;")
        roi_lay.addWidget(self.lbl_roi_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_save_roi = QPushButton("写入配置（保存ROI）")
        btn_save_roi.setToolTip("只保存当前选中相机，文件为 config/roi/<相机>.json，不会改其他三路")
        btn_load_roi = QPushButton("读回配置")
        btn_load_roi.setToolTip("只读当前相机已保存的 ROI，不会读其他相机")
        btn_del_roi = QPushButton("清除本路ROI文件")
        btn_del_roi.setToolTip("删除 config/roi/<当前相机>.json，不影响其他相机")
        btn_full = QPushButton("铺满整张图")
        btn_full.setToolTip("把绿框设成整幅图像大小（从 0,0 到全图）")
        btn_center = QPushButton("中间半张图")
        btn_center.setToolTip("把绿框放到画面正中间，大小约为半幅宽高（常用起步）")
        style_many(
            [
                (btn_save_roi, "success"),
                (btn_load_roi, "neutral"),
                (btn_del_roi, "danger"),
                (btn_full, "warn"),
                (btn_center, "primary"),
            ]
        )
        btn_save_roi.clicked.connect(self._save_roi)
        btn_load_roi.clicked.connect(self._load_roi)
        btn_del_roi.clicked.connect(self._delete_roi_file)
        btn_full.clicked.connect(self._roi_full)
        btn_center.clicked.connect(self._roi_center)
        for b in (btn_save_roi, btn_load_roi, btn_del_roi, btn_full, btn_center):
            btn_row.addWidget(b, 0)
        btn_row.addStretch(1)
        roi_lay.addLayout(btn_row)

        nudge = QHBoxLayout()
        nudge.setSpacing(6)
        nudge.addWidget(QLabel("微调(每次10像素):"))
        for text, tip, dx, dy, dw, dh in (
            ("左移", "绿框整体向左移 10 像素", -10, 0, 0, 0),
            ("右移", "绿框整体向右移 10 像素", 10, 0, 0, 0),
            ("上移", "绿框整体向上移 10 像素", 0, -10, 0, 0),
            ("下移", "绿框整体向下移 10 像素", 0, 10, 0, 0),
            ("加宽", "绿框宽度 +10", 0, 0, 10, 0),
            ("变窄", "绿框宽度 -10", 0, 0, -10, 0),
            ("加高", "绿框高度 +10", 0, 0, 0, 10),
            ("变矮", "绿框高度 -10", 0, 0, 0, -10),
        ):
            b = QPushButton(text)
            b.setMinimumWidth(48)
            b.setToolTip(tip)
            style_button(b, "neutral")
            b.clicked.connect(lambda _=False, a=dx, b_=dy, c=dw, d=dh: self._nudge_roi(a, b_, c, d))
            nudge.addWidget(b, 0)
        nudge.addStretch(1)
        roi_lay.addLayout(nudge)
        lay.addWidget(self.roi_box)
        lay.addStretch(1)
        return tab

    def _build_tab_chessboard(self) -> QWidget:
        """棋盘格内参标定。"""
        tab = QWidget()
        lay = QVBoxLayout(tab)
        cal_box = QGroupBox("棋盘格内参标定")
        cal_lay = QVBoxLayout(cal_box)
        cal_row = QHBoxLayout()
        cal_row.setSpacing(8)
        self.sp_cols = _spin(3, 20, 9, 60)
        self.sp_rows = _spin(3, 20, 6, 60)
        self.sp_sq = QDoubleSpinBox()
        self.sp_sq.setRange(1.0, 100.0)
        self.sp_sq.setDecimals(2)
        self.sp_sq.setValue(20.0)
        self.sp_sq.setSuffix(" mm")
        self.sp_sq.setFixedWidth(100)
        board = self.ctx.cfg.get("vision", {}).get("chessboard", {})
        self.sp_cols.setValue(int(board.get("cols", 9)))
        self.sp_rows.setValue(int(board.get("rows", 6)))
        self.sp_sq.setValue(float(board.get("square_size_mm", 20)))
        for name, w in (("列cols", self.sp_cols), ("行rows", self.sp_rows), ("格边长", self.sp_sq)):
            cal_row.addWidget(_pair(name, w, 52), 0)
        cal_lay.addLayout(cal_row)
        cal_btn = QHBoxLayout()
        self.btn_detect = QPushButton("检测棋盘格")
        self.btn_cap = QPushButton("采集有效帧")
        b_clr = QPushButton("清空缓冲")
        b_run = QPushButton("计算并保存内参")
        b_save_board = QPushButton("保存棋盘参数到yaml")
        style_many(
            [
                (self.btn_detect, "motion"),
                (self.btn_cap, "primary"),
                (b_clr, "neutral"),
                (b_run, "success"),
                (b_save_board, "warn"),
            ]
        )
        self.btn_detect.clicked.connect(self._detect_board)
        self.btn_cap.clicked.connect(self._capture_calib)
        b_clr.clicked.connect(self._clear_calib)
        b_run.clicked.connect(self._run_calib)
        b_save_board.clicked.connect(self._save_board_cfg)
        for b in (self.btn_detect, self.btn_cap, b_clr, b_run, b_save_board):
            cal_btn.addWidget(b, 0)
        cal_btn.addStretch(1)
        cal_lay.addLayout(cal_btn)
        cal_clr = QHBoxLayout()
        b_del_intr = QPushButton("清除内参/棋盘文件")
        b_del_intr.setToolTip(
            "删除当前相机 config/calib/<相机>_intrinsics.json（含当时的列/行/边长与 K）。"
            "yaml 里填写的棋盘格尺寸不删。"
        )
        style_button(b_del_intr, "danger")
        b_del_intr.clicked.connect(self._delete_intrinsics_file)
        cal_clr.addWidget(b_del_intr, 0)
        cal_clr.addStretch(1)
        cal_lay.addLayout(cal_clr)
        self.lbl_calib = QLabel("-")
        self.lbl_calib.setWordWrap(True)
        cal_lay.addWidget(self.lbl_calib)
        lay.addWidget(cal_box)
        lay.addStretch(1)
        return tab

    def _build_tab_handeye(self) -> QWidget:
        """手眼采样 / 求解 / 写入 json。"""
        tab = QWidget()
        lay = QVBoxLayout(tab)
        tip = QLabel(
            "推荐流程：皮带放一只鞋 → ① 记录对准位姿 → 手动移出视野 → "
            "② 移开后拍照 → 点像素 → ③ 完成采样（自动写入 PickPose）→ "
            "④ 按路径回进入点/上方/取料点夹取验证，减小手眼误差。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#1a5276;font-weight:bold;")
        lay.addWidget(tip)

        he = QGroupBox("手眼采样与求解")
        he_lay = QVBoxLayout(he)
        row0 = QHBoxLayout()
        b_he_pose = QPushButton("① 记录机械臂位姿")
        b_he_retreat = QPushButton("② 移开后拍照")
        b_he_finish = QPushButton("③ 完成本点采样")
        b_he_pose.setToolTip("臂仍对准标定点时：读 TCP + 六关节角并暂存")
        b_he_retreat.setToolTip(
            "请先在示教器手动移开上料臂/夹爪，使标定点无遮挡且仍在视野内，再拍照"
        )
        b_he_finish.setToolTip("用①记录的位姿 + 移开后预览上点的像素写入采样")
        style_many([(b_he_pose, "warn"), (b_he_retreat, "motion"), (b_he_finish, "success")])
        b_he_pose.clicked.connect(self._record_handeye_robot_pose)
        b_he_retreat.clicked.connect(self._handeye_retreat_and_capture)
        b_he_finish.clicked.connect(self._finish_handeye_sample)
        row0.addWidget(b_he_pose)
        row0.addWidget(b_he_retreat)
        row0.addWidget(b_he_finish)
        row0.addStretch(1)
        he_lay.addLayout(row0)
        row_pick = QHBoxLayout()
        b_he_pickpose = QPushButton("写入本点PickPose")
        b_he_entry = QPushButton("回 pick_entry")
        b_he_above = QPushButton("到取料上方")
        b_he_pick = QPushButton("到取料点")
        b_he_repick = QPushButton("按路径回夹取")
        b_he_pickpose.setToolTip("将最近一次③完成的标定点 TCP 写入 PickPose")
        b_he_entry.setToolTip("MoveJ 到示教 pick_entry（需先完成③）")
        b_he_above.setToolTip("MoveL 到本点取料上方（PickPose+pick_above_offset）")
        b_he_pick.setToolTip("MoveL 到本点取料位（①记录的对准 TCP）")
        b_he_repick.setToolTip(
            "张爪 → pick_entry → 取料上方 → 取料点 → 夹紧；与 Station2 取料段一致"
        )
        style_many(
            [
                (b_he_pickpose, "primary"),
                (b_he_entry, "motion"),
                (b_he_above, "motion"),
                (b_he_pick, "motion"),
                (b_he_repick, "success"),
            ]
        )
        b_he_pickpose.clicked.connect(self._handeye_write_pick_pose)
        b_he_entry.clicked.connect(self._handeye_move_pick_entry)
        b_he_above.clicked.connect(lambda: self._handeye_move_pick_stage(above=True))
        b_he_pick.clicked.connect(lambda: self._handeye_move_pick_stage(above=False))
        b_he_repick.clicked.connect(self._handeye_repick_sequence)
        for b in (b_he_pickpose, b_he_entry, b_he_above, b_he_pick, b_he_repick):
            row_pick.addWidget(b, 0)
        row_pick.addStretch(1)
        he_lay.addLayout(row_pick)
        row1 = QHBoxLayout()
        b9 = QPushButton("保存手眼采样")
        b_solve = QPushButton("计算手眼4×4写入json")
        b_kjson = QPushButton("内参写入皮带json")
        b_roij = QPushButton("ROI写入皮带json")
        b_del_he_s = QPushButton("清除手眼采样")
        b_del_he = QPushButton("清除手眼矩阵")
        b_del_he_s.setToolTip("删除当前相机手眼采样文件并清空内存缓冲")
        b_del_he.setToolTip("删除当前相机 config/calib/<相机>_handeye.json")
        b_solve.setToolTip("用采样点求 T_cam2base，cam1 会写入 shoe_vision_config.json")
        style_many(
            [
                (b9, "success"),
                (b_solve, "success"),
                (b_kjson, "success"),
                (b_roij, "success"),
                (b_del_he_s, "danger"),
                (b_del_he, "danger"),
            ]
        )
        b9.clicked.connect(self._save_handeye_samples)
        b_solve.clicked.connect(self._solve_handeye)
        b_kjson.clicked.connect(self._write_k_json)
        b_roij.clicked.connect(self._write_roi_json)
        b_del_he_s.clicked.connect(self._delete_handeye_samples)
        b_del_he.clicked.connect(self._delete_handeye_file)
        for b in (b9, b_solve, b_kjson, b_roij):
            row1.addWidget(b, 0)
        row1.addStretch(1)
        he_lay.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(b_del_he_s, 0)
        row2.addWidget(b_del_he, 0)
        row2.addStretch(1)
        he_lay.addLayout(row2)
        self.lbl_handeye = QLabel("-")
        he_lay.addWidget(self.lbl_handeye)
        lay.addWidget(he)
        lay.addStretch(1)
        return tab

    def _build_tab_params(self) -> QWidget:
        """按当前相机显示检测阈值 / 压杆 ROI 等，一个保存按钮。"""
        from hmi.pages.vision_params_page import VisionParamsPage

        self.params_page = VisionParamsPage(self.coord, cam_id_fn=self._cam_id)
        return self.params_page

    def _build_tab_detect(self) -> QWidget:
        """YOLO 检测测试与结果日志。"""
        tab = QWidget()
        lay = QVBoxLayout(tab)

        yolo_box = QGroupBox("YOLO 模型（从旧压鞋机拷到本工程 models/）")
        yolo_lay = QVBoxLayout(yolo_box)
        self.lbl_models = QLabel("-")
        self.lbl_models.setWordWrap(True)
        self.lbl_models.setStyleSheet("color:#1a5276;")
        yolo_lay.addWidget(self.lbl_models)
        lay.addWidget(yolo_box)

        biz = QGroupBox("YOLO 测试")
        biz_lay = QVBoxLayout(biz)
        biz_row = QHBoxLayout()
        b4 = QPushButton("测试皮带拍照")
        b5 = QPushButton("测试放料槽")
        b6 = QPushButton("测试取料槽")
        b_slot = QPushButton("测试槽有无鞋")
        b_toe = QPushButton("测试鞋头对位")
        b_rod = QPushButton("测试压杆偏移")
        b7 = QPushButton("测试贴边引导")
        b_pick = QPushButton("视觉结果写入PickPose")
        b_ml = QPushButton("MoveL到取料上方")
        style_many(
            [
                (b4, "motion"),
                (b5, "motion"),
                (b6, "motion"),
                (b_slot, "motion"),
                (b_toe, "primary"),
                (b_rod, "primary"),
                (b7, "primary"),
                (b_pick, "success"),
                (b_ml, "motion"),
            ]
        )
        b4.clicked.connect(self._test_belt)
        b5.clicked.connect(self._test_place)
        b6.clicked.connect(self._test_pick)
        b_slot.clicked.connect(self._test_slot_yolo)
        b_toe.clicked.connect(self._test_toe_yolo)
        b_rod.clicked.connect(self._test_rod)
        b7.clicked.connect(self._test_guide)
        b_pick.clicked.connect(self._apply_pick_pose)
        b_ml.clicked.connect(self._move_pick_above)
        for b in (b4, b5, b6, b_slot, b_toe, b_rod):
            biz_row.addWidget(b, 0)
        biz_row.addStretch(1)
        biz_lay.addLayout(biz_row)
        biz_row2 = QHBoxLayout()
        for b in (b7, b_pick, b_ml):
            biz_row2.addWidget(b, 0)
        biz_row2.addStretch(1)
        biz_lay.addLayout(biz_row2)
        lay.addWidget(biz)

        res_box = QGroupBox("调试结果")
        res_lay = QVBoxLayout(res_box)
        self.txt_result = QTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setMinimumHeight(160)
        self.txt_result.setPlaceholderText("YOLO 测试结果会显示在这里…")
        res_lay.addWidget(self.txt_result)
        lay.addWidget(res_box, 1)
        return tab

    def _deferred_page_ready(self) -> None:
        """页面已显示后再刷清单/手眼，并启动预览。"""
        try:
            cid = self._cam_id()
            self._handeye_samples = calib.load_handeye_samples(cid)
            self._refresh_calib_status()
            self._refresh_handeye_lbl()
            self._refresh_commission(heavy=False)
            self._start_commission_heavy()
            threading.Thread(
                target=self._preload_rois_bg,
                daemon=True,
                name="vision-roi-preload",
            ).start()
        except Exception:
            pass
        self._sync_preview_timer_interval()
        if self.isVisible() and not self._preview_timer.isActive():
            self._preview_timer.start()
        if self.isVisible():
            self._tick_preview()

    def _preload_rois_bg(self) -> None:
        for cid in self.ctx.cameras.keys():
            if cid in self._roi_by_cam:
                continue
            saved = roi.load_roi(cid)
            if saved:
                self._roi_by_cam[cid] = saved

    def _start_commission_heavy(self, *, show_busy: bool = False) -> None:
        """stack_status / 模型列表在后台算，避免 import YOLO 卡 UI。

        周期性复检不要再亮进度条：show/hide 会改布局高度，整页跟着闪。
        """
        if self._commission_busy:
            return
        self._commission_busy = True
        if show_busy and hasattr(self, "_stack_progress"):
            self._stack_progress.show()
        ctx = self.ctx

        def _run() -> None:
            try:
                stack = vcomm.stack_line(ctx)
                models = vcomm.models_list_text(ctx)
            except Exception as e:
                stack = f"栈检查失败: {e}"
                models = ""
            self._commission_done.emit(stack, models)

        threading.Thread(target=_run, daemon=True, name="vision-commission").start()

    def _on_commission_done(self, stack: str, models: str) -> None:
        self._commission_busy = False
        if hasattr(self, "_stack_progress"):
            self._stack_progress.hide()
        if hasattr(self, "lbl_stack"):
            _set_label_text(self.lbl_stack, stack)
        if hasattr(self, "lbl_models"):
            _set_label_text(self.lbl_models, models)

    def _sync_preview_timer_interval(self) -> None:
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is not None and bool(getattr(cam, "use_mock", False)):
            # 模拟图是静态的，不必高频刷
            self._preview_timer.setInterval(400)
            return
        fps = int(getattr(cam, "target_fps", 0) or 8) if cam else 8
        hmi = (self.ctx.cfg.get("system") or {}).get("hmi") or {}
        debug_cap = max(5, int(hmi.get("vision_debug_max_fps", 10)))
        fps = min(max(1, fps), debug_cap)
        app = QApplication.instance()
        inactive = bool(
            app is not None
            and app.applicationState() != Qt.ApplicationState.ApplicationActive
        )
        self._preview_timer.setInterval(
            preview_interval_ms(self.ctx.cfg, fps, inactive=inactive)
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_visible_inner_tab()
        self._sync_preview_timer_interval()
        # 不在 show 当拍同步刷图；等 deferred / 定时器
        if not self._preview_timer.isActive():
            QTimer.singleShot(50, self._ensure_preview_timer)

    def _ensure_preview_timer(self) -> None:
        if self.isVisible() and not self._preview_timer.isActive():
            self._preview_timer.start()
            self._tick_preview()

    def hideEvent(self, event) -> None:
        self._preview_timer.stop()
        super().hideEvent(event)

    def _peek_preview_frame(self):
        """UI 只读缓存帧，绝不在主线程 grab（避免点开本页卡顿）。"""
        if self._freeze and self._frozen_img is not None:
            return self._frozen_img
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is None:
            return None
        return getattr(cam, "last_color", None)

    def _peek_depth_vis(self):
        """UI 只读深度伪彩缓存。"""
        if self._freeze and self._frozen_depth is not None:
            return self._frozen_depth
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is not None:
            dv = getattr(cam, "last_depth_vis", None)
            if dv is not None:
                return dv
        vis = getattr(self.ctx, "vision", None)
        if vis is None:
            return None
        cache = getattr(vis, "last_depth_vis", None)
        if not isinstance(cache, dict):
            return None
        return cache.get(self._cam_id())

    # ---------- helpers ----------
    def _log(self, text: str) -> None:
        te = getattr(self, "txt_result", None)
        if te is None:
            return
        te.setPlainText(text)

    def _append_log(self, text: str) -> None:
        te = getattr(self, "txt_result", None)
        if te is None:
            return
        cur = te.toPlainText().strip()
        te.setPlainText((cur + "\n" + text) if cur else text)

    def _on_cam_mock(self, cam_key: str, on: bool) -> None:
        if self._syncing_cam_mock:
            return
        self.ctx.vision.set_cam_mock(cam_key, bool(on))
        self.ctx.cfg.setdefault("cameras", {}).setdefault(cam_key, {})["use_mock"] = bool(on)
        self._last_bgr = None
        self._last_depth_vis = None
        self._preview_skip_key = None
        self._sync_preview_timer_interval()
        self._log(
            f"{cam_key} → {'模拟' if on else '真机'}（已切换，yaml 稍后写入）\n"
            + " ".join(
                f"{k}:{'模' if self.ctx.vision.cam_is_mock(k) else '真'}"
                for k in ("cam1", "cam2", "cam3", "cam4")
            )
        )
        QTimer.singleShot(0, self._tick_preview)
        QTimer.singleShot(0, lambda k=cam_key, flag=bool(on): self._after_cam_mock(k, flag))

    def _after_cam_mock(self, cam_key: str, on: bool) -> None:
        """勾选返回后再写盘/刷清单，避免和 Orbbec 抢 GIL。"""
        try:
            save_config(self.ctx.cfg)
        except Exception:
            pass
        self._refresh_commission(heavy=False)

    def _cam_id(self) -> str:
        d = self.cmb_cam.currentData()
        if d:
            return str(d)
        return "cam1"

    def _on_cam_changed(self, _t: int = 0) -> None:
        old = self._roi_editing_cam
        if old:
            self._stash_roi(old)
        self._roi_editing_cam = self._cam_id()
        self._apply_roi_for(self._roi_editing_cam)
        self._sync_bind_fields()
        self._last_bgr = None
        self._last_depth_vis = None
        self._preview_skip_key = None
        self.preview.clear_frame()
        self.preview_depth.clear_frame()
        self.preview_depth.show_placeholder("深度")
        self._refresh_commission()
        self._refresh_calib_status()
        self._overlay_toe = None
        self._handeye_samples = calib.load_handeye_samples(self._cam_id())
        self._refresh_handeye_lbl()
        self._sync_preview_timer_interval()
        page = getattr(self, "params_page", None)
        if page is not None:
            page.show_cam(self._cam_id())

    def _sync_bind_fields(self) -> None:
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is None or not hasattr(self, "ed_serial"):
            return
        self.ed_serial.blockSignals(True)
        self.sp_index.blockSignals(True)
        self.ed_serial.setText(str(cam.serial or ""))
        self.sp_index.setValue(int(cam.index or 0))
        self.ed_serial.blockSignals(False)
        self.sp_index.blockSignals(False)
        self._sync_depth_checkbox()

    def _sync_depth_checkbox(self) -> None:
        """预览勾选跟当前相机 cameras.*.enable_depth 对齐。"""
        chk = getattr(self, "chk_show_depth", None)
        if chk is None:
            return
        cam = self.ctx.cameras.get(self._cam_id())
        want = bool(getattr(cam, "enable_depth", True)) if cam is not None else True
        self._syncing_depth_chk = True
        if chk.isChecked() != want:
            chk.setChecked(want)
        self._syncing_depth_chk = False

    def _on_output_depth(self, on: bool) -> None:
        """勾选「输出深度图」：写入本路 yaml，必要时重开相机。"""
        if getattr(self, "_syncing_depth_chk", False):
            return
        cid = self._cam_id()
        cam = self.ctx.cameras.get(cid)
        want = bool(on)
        blk = self.ctx.cfg.setdefault("cameras", {}).setdefault(cid, {})
        blk["enable_depth"] = want
        try:
            save_config(self.ctx.cfg)
        except Exception:
            pass
        if cam is None:
            QTimer.singleShot(0, self._tick_preview)
            return
        cam.enable_depth = want
        self._preview_skip_key = None
        if not want:
            cam.last_depth = None
            cam.last_depth_vis = None
            cam.last_depth_stats = ""
            vis = getattr(self.ctx, "vision", None)
            if vis is not None and isinstance(getattr(vis, "last_depth_vis", None), dict):
                vis.last_depth_vis.pop(cid, None)
            self._last_depth_vis = None
            self._log(f"{cid} 已关闭深度图输出（只出彩色）")
            QTimer.singleShot(0, self._tick_preview)
            return
        if cam.use_mock:
            self._log(f"{cid} 已打开深度图输出")

            def _rebuild() -> None:
                try:
                    cam.rebuild_mock_frame()
                except Exception:
                    pass

            threading.Thread(
                target=_rebuild, daemon=True, name=f"mock-depth-{cid}"
            ).start()
            QTimer.singleShot(50, self._tick_preview)
            return
        need_reopen = not bool(getattr(cam, "_has_depth_stream", False))
        if need_reopen:
            self._log(f"{cid} 已打开深度图输出，正在后台重开以启用深度流…")
            cam.reopen_async()
        else:
            self._log(f"{cid} 已打开深度图输出")
        QTimer.singleShot(0, self._tick_preview)

    def _refresh_commission(self, *, heavy: bool = True) -> None:
        if not hasattr(self, "lbl_hw"):
            return
        cid = self._cam_id()
        cam = self.ctx.cameras.get(cid)
        _set_label_text(self.lbl_hw, vcomm.hardware_line(cam, cid))
        _set_label_text(self.lbl_check, vcomm.checklist_text(self.ctx, cid))
        if heavy:
            self._start_commission_heavy(show_busy=False)

    def _open_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            return
        QMessageBox.information(self, "打开目录", f"无法自动打开，请手动进入：\n{path}")

    def _enum_devices(self) -> None:
        """Orbbec Context 枚举可能卡住数秒，必须后台跑。"""
        self._log("正在后台枚举设备（不阻塞界面）…")

        def _run() -> None:
            try:
                text = enumerate_devices_text()
            except Exception as e:
                text = f"枚举失败: {e}"
            self._enum_done.emit(text)

        threading.Thread(target=_run, daemon=True, name="vision-enum").start()

    def _on_enum_done(self, text: str) -> None:
        self._log(text)
        self._refresh_commission(heavy=False)

    def _bind_serial_reopen(self) -> None:
        cid = self._cam_id()
        cam = self.ctx.cameras.get(cid)
        if cam is None:
            return
        sn = self.ed_serial.text().strip()
        idx = int(self.sp_index.value())
        blk = self.ctx.cfg.setdefault("cameras", {}).setdefault(cid, {})
        blk["serial"] = sn
        blk["index"] = idx
        blk["enable_depth"] = bool(self.chk_show_depth.isChecked())
        cam.serial = sn
        cam.index = idx
        cam.enable_depth = bool(self.chk_show_depth.isChecked())
        try:
            save_config(self.ctx.cfg)
        except Exception as e:
            QMessageBox.warning(self, "保存", f"yaml 写入失败: {e}")
            return
        self._log(f"{cid} 已写入 yaml serial={sn or '（空）'} index={idx}，正在重开…")
        self._reopen_cam()
        self._refresh_commission()

    def _copy_cam_status(self) -> None:
        text = vcomm.status_copy_text(self.ctx, self._cam_id())
        QApplication.clipboard().setText(text)
        self._log("本路状态已复制到剪贴板。")

    def _on_freeze(self, on: bool) -> None:
        self._freeze = bool(on)
        self._preview_skip_key = None
        if on and self._last_bgr is not None:
            self._frozen_img = self._last_bgr.copy()
            dv = self._peek_depth_vis()
            self._frozen_depth = dv.copy() if dv is not None else None
        if not on:
            self._frozen_img = None
            self._frozen_depth = None

    def _set_board_session(self, on: bool, *, keep_buffer: bool = True) -> None:
        self._board_session = bool(on)
        self._board_busy = False
        if not on:
            self._board_cancel.set()
        if on:
            self.btn_detect.setText("结束采集（点此退出）")
        else:
            self.btn_detect.setText("检测棋盘格")
            self._board_snap = None
            self._board_hold_vis = None
            self._board_hold_until = 0.0
        if not keep_buffer:
            self._calib_images.clear()
            self._refresh_calib_status()

    def _cancel_board_detect(self, resume_live: bool = True) -> None:
        self._board_cancel.set()
        self._board_token += 1
        n = len(self._calib_images)
        self._set_board_session(False, keep_buffer=True)
        if resume_live:
            self._freeze = False
            self._frozen_img = None
            self._frozen_depth = None
            self.chk_freeze.blockSignals(True)
            self.chk_freeze.setChecked(False)
            self.chk_freeze.blockSignals(False)
        self.btn_cap.setText("采集有效帧")
        self._log(f"已结束棋盘采集（缓冲仍保留 {n} 帧）。无效帧已丢弃，可再点「检测棋盘格」继续加帧。")

    def _hold_board_vis(self, vis, ok: bool, caption: str, *, hold_s: float = 2.0) -> None:
        """把带角点的结果钉在预览上几秒，避免被实时画面立刻盖掉。"""
        if vis is None or cv2 is None:
            return
        shown = vis.copy()
        color = (0, 200, 0) if ok else (0, 0, 255)
        cv2.putText(
            shown,
            caption[:80],
            (16, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            color,
            2,
        )
        self._board_hold_vis = shown
        self._board_hold_until = time.monotonic() + max(0.3, float(hold_s))
        self._board_last_ok = bool(ok)
        self._last_bgr = shown
        self._show_bgr(shown)

    def _detect_board(self) -> None:
        if self._board_session or self._board_busy:
            self._cancel_board_detect(resume_live=True)
            return
        img = self._grab_fresh()
        if img is None or cv2 is None:
            QMessageBox.warning(self, "检测", "无图像")
            return
        cols, rows, _ = self._board_params()
        snap = img.copy()
        self._board_snap = snap
        self._board_cancel.clear()
        self._board_token += 1
        token = self._board_token
        self._board_busy = True
        self.btn_detect.setText("检测中…点此取消")
        self._log(f"正在检测棋盘格 {cols}×{rows}…无效帧会在约 1.5 秒内跳过，预览不冻结。")

        def _run() -> None:
            try:
                ok, vis, msg, used = calib.draw_chessboard(
                    snap, cols, rows, cancel=self._board_cancel, timeout_s=1.5
                )
                uc, ur = int(used[0]), int(used[1])
            except Exception as e:
                ok, vis, msg, uc, ur = False, snap, f"检测异常: {e}", cols, rows
            self.board_detect_done.emit(token, bool(ok), vis, str(msg), uc, ur)

        threading.Thread(target=_run, daemon=True, name="chess-detect").start()

    def _on_board_detect_done(self, token: int, ok: bool, vis, msg: str, cols: int, rows: int) -> None:
        if token != self._board_token:
            return
        self._board_busy = False
        if not ok:
            self.btn_detect.setText("检测棋盘格")
            self._hold_board_vis(vis, False, "未识别", hold_s=0.8)
            self._log("✗ " + msg + "\n未采入缓冲。摆正后再点「检测棋盘格」。")
            return
        raw = self._board_snap if self._board_snap is not None else vis
        if raw is not None:
            self._calib_images.append(raw.copy())
        self._set_board_session(True, keep_buffer=True)
        self._refresh_calib_status()
        n = len(self._calib_images)
        self._hold_board_vis(vis, True, f"识别OK 有效帧 {n}")
        self._log(
            "✓ "
            + msg
            + f"\n已保留有效帧 {n}。预览会显示角点约 3 秒；换姿态后再点「采集有效帧」。"
            "\n点「结束采集」才退出，已采帧不会清空。"
        )

    def _capture_calib(self) -> None:
        if self._board_busy:
            self._log("上一帧还在检测，请稍后再点「采集有效帧」")
            return
        img = self._grab_fresh()
        if img is None:
            QMessageBox.warning(self, "采集", "无图像（预览占用相机，请再点一次采集）")
            return
        cols, rows, _ = self._board_params()
        snap = img.copy()
        self._board_snap = snap
        self._board_cancel.clear()
        self._board_token += 1
        token = self._board_token
        self._board_busy = True
        self.btn_cap.setText("采集中…")
        self._log(f"正在检查本帧棋盘格 {cols}×{rows}…无效帧约 1.5 秒内丢弃，不必退出采集。")

        def _run() -> None:
            try:
                found, vis, msg, used = calib.draw_chessboard(
                    snap, cols, rows, cancel=self._board_cancel, timeout_s=1.5
                )
            except Exception as e:
                found, vis, msg = False, snap, f"采集异常: {e}"
            self.board_capture_done.emit(token, bool(found), vis, str(msg))

        threading.Thread(target=_run, daemon=True, name="chess-capture").start()

    def _on_board_capture_done(self, token: int, ok: bool, vis, msg: str) -> None:
        if token != self._board_token:
            return
        self._board_busy = False
        self.btn_cap.setText("采集有效帧")
        if self._board_session:
            self.btn_detect.setText("结束采集（点此退出）")
        if not ok:
            self._hold_board_vis(vis, False, "本帧未识别", hold_s=0.8)
            self._log("✗ " + msg + "\n本帧未采入。采集会话仍在，摆正后再点「采集有效帧」。")
            return
        raw = self._board_snap if self._board_snap is not None else vis
        if raw is not None:
            self._calib_images.append(raw.copy())
        self._set_board_session(True, keep_buffer=True)
        self._refresh_calib_status()
        n = len(self._calib_images)
        self._hold_board_vis(vis, True, f"识别OK 有效帧 {n}")
        self._log(f"已采集有效标定帧: {n}（{msg}）\n角点已画在预览上；换姿态后再采。")

    def _on_roi_mode(self, on: bool) -> None:
        self.preview.roi_select_mode = bool(on)

    def _grab(self, wait_s: float = 0.0):
        if self._freeze and self._frozen_img is not None:
            return self._frozen_img
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is None:
            return None
        img = None
        try:
            if wait_s:
                img = cam.grab(wait_s=float(wait_s))
            else:
                img = cam.grab()
        except Exception:
            img = None
        # 相机监控也在取流时锁会被占用；回退最近一帧，避免预览闪「未出图」
        if img is None:
            img = getattr(cam, "last_color", None)
        return img

    def _grab_fresh(self):
        """标定采集必须拿实时画面，不用冻结/结果图。"""
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is None:
            return None
        img = cam.grab(wait_s=0.6)
        if img is None:
            img = getattr(cam, "last_color", None)
        return img

    def _reopen_cam(self) -> None:
        cam = self.ctx.cameras.get(self._cam_id())
        if cam is None:
            return
        self._preview_skip_key = None
        cam.reopen_async()
        self._log(
            f"{self._cam_id()} 正在后台重开 mock={cam.use_mock} "
            f"serial={cam.serial or '-'} index={cam.index}"
        )

    def _save_snap(self) -> None:
        img = self._grab()
        if img is None or cv2 is None:
            QMessageBox.warning(self, "截图", "无图像")
            return
        name = (
            f"{self._cam_id()}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
        )
        _SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path = _SNAP_DIR / name
        cv2.imwrite(str(path), img)
        dv = self._peek_depth_vis()
        if dv is not None:
            depth_path = _SNAP_DIR / name.replace(".png", "_depth.png")
            cv2.imwrite(str(depth_path), dv)
            self._log(f"截图已存: {path}\n深度: {depth_path}")
        else:
            self._log(f"截图已存: {path}")

    # ---------- ROI ----------
    def _roi_dict(self) -> dict:
        return {
            "x": int(self.sp_x.value()),
            "y": int(self.sp_y.value()),
            "w": int(self.sp_w.value()),
            "h": int(self.sp_h.value()),
        }

    def _preload_rois(self) -> None:
        for cid in self.ctx.cameras.keys():
            if cid in self._roi_by_cam:
                continue
            saved = roi.load_roi(cid)
            if saved:
                self._roi_by_cam[cid] = saved

    def _default_roi_for(self, cid: str) -> dict:
        size = self._frame_size_by_cam.get(cid)
        if size:
            return {"x": 0, "y": 0, "w": int(size[0]), "h": int(size[1])}
        return {"x": 0, "y": 0, "w": 640, "h": 480}

    def _stash_roi(self, cid: str) -> None:
        if not cid or self._roi_applying:
            return
        self._roi_by_cam[cid] = self._roi_dict()

    def _set_roi_spins(self, r: dict) -> None:
        self._roi_applying = True
        try:
            self.sp_x.setValue(int(r.get("x", 0)))
            self.sp_y.setValue(int(r.get("y", 0)))
            self.sp_w.setValue(max(1, int(r.get("w", 640))))
            self.sp_h.setValue(max(1, int(r.get("h", 480))))
        finally:
            self._roi_applying = False

    def _apply_roi_for(self, cid: str) -> None:
        r = self._roi_by_cam.get(cid) or roi.load_roi(cid)
        if r:
            self._roi_by_cam[cid] = dict(r)
        else:
            r = self._default_roi_for(cid)
        self._set_roi_spins(r)
        self._refresh_roi_caption()

    def _refresh_roi_caption(self) -> None:
        cid = self._cam_id()
        path = roi.roi_path(cid)
        disk = self._roi_by_cam.get(cid)
        if disk is None:
            disk = roi.load_roi(cid)
            if disk:
                self._roi_by_cam[cid] = dict(disk)
        cur = self._roi_dict()
        if hasattr(self, "roi_box"):
            self.roi_box.setTitle(f"检测区域 ROI — {cid}（四路独立，互不共用）")
        if not hasattr(self, "lbl_roi_status"):
            return
        if disk is None:
            self.lbl_roi_status.setText(
                f"{cid} 尚未写入文件（{path}）。本页编辑只属于这一路；点「写入配置」后自动拍照才用。"
            )
        elif disk == cur:
            self.lbl_roi_status.setText(f"{cid} 已保存 {path}  x={cur['x']} y={cur['y']} w={cur['w']} h={cur['h']}")
        else:
            self.lbl_roi_status.setText(
                f"{cid} 已改未保存 → {path}。当前框 x={cur['x']} y={cur['y']} w={cur['w']} h={cur['h']}"
            )

    def _note_frame_size(self, img) -> None:
        if img is None:
            return
        cid = self._cam_id()
        h, w = img.shape[:2]
        self._frame_size_by_cam[cid] = (int(w), int(h))
        if cid in self._roi_by_cam or roi.load_roi(cid) is not None:
            return
        if cid in self._roi_autofit:
            return
        self._roi_autofit.add(cid)
        self._set_roi_spins({"x": 0, "y": 0, "w": int(w), "h": int(h)})
        self._refresh_roi_caption()

    def _on_roi_spin(self, _v: int = 0) -> None:
        if self._roi_applying:
            return
        cid = self._roi_editing_cam or self._cam_id()
        self._stash_roi(cid)
        self._refresh_roi_caption()

    def _save_roi(self) -> None:
        cid = self._cam_id()
        cur = self._roi_dict()
        self._roi_by_cam[cid] = dict(cur)
        path = roi.save_roi(cid, **cur)
        self._refresh_roi_caption()
        self._log(f"{cid} ROI 已保存: {path}\n{cur}\n不会改其他相机的 ROI。")
        self._refresh_commission()

    def _delete_roi_file(self) -> None:
        cid = self._cam_id()
        path = roi.roi_path(cid)
        if not path.exists():
            QMessageBox.information(self, "ROI", f"{cid} 没有 ROI 文件：{path}")
            return
        if not self._confirm_delete("清除本路ROI", f"将删除：\n{path}\n其他相机不受影响。确定？"):
            return
        roi.delete_roi(cid)
        self._roi_by_cam.pop(cid, None)
        self._roi_autofit.discard(cid)
        self._apply_roi_for(cid)
        self._log(f"已删除 {cid} ROI: {path}")
        self._refresh_commission()

    def _load_roi(self) -> None:
        cid = self._cam_id()
        r = roi.load_roi(cid)
        if not r:
            QMessageBox.information(
                self,
                "ROI",
                f"{cid} 还没有保存过 ROI 文件：\n{roi.roi_path(cid)}\n\n"
                "请拖框或微调后点「写入配置」。不会使用其他相机的框。",
            )
            self._apply_roi_for(cid)
            return
        self._roi_by_cam[cid] = dict(r)
        self._set_roi_spins(r)
        self._refresh_roi_caption()
        self._log(f"{cid} 已从文件读回 ROI: {roi.roi_path(cid)}\n{r}")

    def _on_roi_dragged(self, x: int, y: int, w: int, h: int) -> None:
        self._set_roi_spins({"x": x, "y": y, "w": w, "h": h})
        cid = self._roi_editing_cam or self._cam_id()
        self._stash_roi(cid)
        self._refresh_roi_caption()
        self._log(f"{cid} 拖选 ROI → x={x} y={y} w={w} h={h}（记得点保存ROI，只写入这一路）")

    def _nudge_roi(self, dx: int, dy: int, dw: int, dh: int) -> None:
        self._set_roi_spins(
            {
                "x": max(0, self.sp_x.value() + dx),
                "y": max(0, self.sp_y.value() + dy),
                "w": max(1, self.sp_w.value() + dw),
                "h": max(1, self.sp_h.value() + dh),
            }
        )
        self._stash_roi(self._roi_editing_cam or self._cam_id())
        self._refresh_roi_caption()

    def _roi_full(self) -> None:
        img = self._grab()
        cid = self._cam_id()
        if img is None:
            size = self._frame_size_by_cam.get(cid)
            if not size:
                self._set_roi_spins({"x": 0, "y": 0, "w": 640, "h": 480})
                self._stash_roi(cid)
                self._refresh_roi_caption()
                return
            w, h = size
        else:
            h, w = img.shape[:2]
            self._frame_size_by_cam[cid] = (int(w), int(h))
        self._set_roi_spins({"x": 0, "y": 0, "w": int(w), "h": int(h)})
        self._stash_roi(cid)
        self._refresh_roi_caption()

    def _roi_center(self) -> None:
        img = self._grab()
        if img is None:
            return
        cid = self._cam_id()
        h, w = img.shape[:2]
        self._frame_size_by_cam[cid] = (int(w), int(h))
        rw, rh = max(1, w // 2), max(1, h // 2)
        self._set_roi_spins(
            {
                "x": max(0, (w - rw) // 2),
                "y": max(0, (h - rh) // 2),
                "w": rw,
                "h": rh,
            }
        )
        self._stash_roi(cid)
        self._refresh_roi_caption()

    def _on_pixel_clicked(self, x: int, y: int) -> None:
        self._pending_pixel = (x, y)
        set_clicked_pixel(self._cam_id(), x, y)
        if self._pending_handeye_robot and self._handeye_retreat_done:
            self._log(
                f"已选像素点 ({x}, {y}) — 点「③ 完成本点采样」"
                "（位姿来自移开前记录，像素来自移开后拍照）"
            )
        else:
            self._log(f"已选像素点 ({x}, {y})")
        self._refresh_handeye_lbl()

    # ---------- calib ----------
    def _board_params(self) -> tuple[int, int, float]:
        return int(self.sp_cols.value()), int(self.sp_rows.value()), float(self.sp_sq.value())

    def _refresh_calib_status(self) -> None:
        if not hasattr(self, "lbl_calib"):
            return
        cid = self._cam_id()
        sess = "采集中" if getattr(self, "_board_session", False) else "未在采集"
        _set_label_text(
            self.lbl_calib,
            f"{calib.calib_status_text(cid)} | {calib.handeye_status_text(cid)} | "
            f"缓冲帧={len(self._calib_images)} | {sess}",
        )

    def _clear_calib(self) -> None:
        self._calib_images.clear()
        self._refresh_calib_status()
        self._log("标定缓冲已清空")

    def _run_calib(self) -> None:
        cols, rows, sq = self._board_params()
        try:
            data = calib.calibrate_intrinsics(self._calib_images, cols, rows, sq)
            path = calib.save_calib(self._cam_id(), data)
            self._calib_images.clear()
            self._set_board_session(False, keep_buffer=True)
            self._refresh_calib_status()
            QMessageBox.information(
                self,
                "标定完成",
                f"RMS={data['rms']:.4f}\n有效帧={data.get('n_frames')}\n{path}",
            )
            self._log(f"内参已保存 RMS={data['rms']:.4f}\n{path}")
            self._refresh_commission()
        except Exception as e:
            QMessageBox.critical(self, "标定失败", str(e))

    def _save_board_cfg(self) -> None:
        cols, rows, sq = self._board_params()
        board = self.ctx.cfg.setdefault("vision", {}).setdefault("chessboard", {})
        board["cols"] = cols
        board["rows"] = rows
        board["square_size_mm"] = sq
        save_config(self.ctx.cfg)
        self._log(f"棋盘参数已写入 yaml: cols={cols} rows={rows} square={sq}mm")

    def _confirm_delete(self, title: str, text: str) -> bool:
        r = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _delete_intrinsics_file(self) -> None:
        cid = self._cam_id()
        path = calib.calib_path(cid)
        if not path.exists():
            QMessageBox.information(self, "内参", f"没有文件：{path}")
            return
        if not self._confirm_delete(
            "清除内参文件",
            f"将删除当前相机已保存的棋盘内参（含当时列/行/边长）：\n{path}\n\n"
            "yaml 里的棋盘格尺寸不会改。确定？",
        ):
            return
        calib.delete_calib(cid)
        self._refresh_calib_status()
        self._log(f"已删除内参文件: {path}")
        self._refresh_commission()

    def _delete_handeye_samples(self) -> None:
        cid = self._cam_id()
        path = calib.handeye_samples_path(cid)
        n_mem = len(self._handeye_samples)
        if not path.exists() and n_mem == 0:
            QMessageBox.information(self, "手眼采样", "没有已保存采样，内存缓冲也是空的")
            return
        extra = f"\n内存缓冲还有 {n_mem} 点，会一并清空。" if n_mem else ""
        msg = (
            f"将删除当前相机手眼采样文件：\n{path if path.exists() else '（磁盘无文件）'}"
            f"{extra}\n\n不影响内参文件。确定？"
        )
        if not self._confirm_delete("清除手眼采样", msg):
            return
        calib.delete_handeye_samples(cid)
        self._handeye_samples.clear()
        self._pending_handeye_robot = None
        self._handeye_retreat_done = False
        self._pending_pixel = None
        self._pending_pixel = None
        self._refresh_handeye_lbl()
        self._refresh_calib_status()
        self._log(f"已清除手眼采样（文件+缓冲）: {path}")
        self._refresh_commission()

    def _delete_handeye_file(self) -> None:
        cid = self._cam_id()
        path = calib.handeye_path(cid)
        if not path.exists():
            QMessageBox.information(self, "手眼矩阵", f"没有文件：{path}")
            return
        if not self._confirm_delete(
            "清除手眼矩阵",
            f"将删除当前相机手眼矩阵文件：\n{path}\n\n采样点文件不会删。确定？",
        ):
            return
        calib.delete_handeye(cid)
        self._refresh_handeye_lbl()
        self._refresh_calib_status()
        self._log(f"已删除手眼矩阵: {path}")
        self._refresh_commission()

    # ---------- business ----------
    def _test_belt(self) -> None:
        r = self.ctx.vision.photo_belt_pick(120, 180, 0)
        mock = self.ctx.vision.cam_is_mock("cam1")
        dbg = getattr(self.ctx.vision, "last_belt_debug", None) or {}
        length = float(getattr(r, "shoe_length_mm", 0.0) or dbg.get("length_mm") or 0.0)
        off = getattr(r, "toe_offset_in_grasp_tcp", None)
        off_s = off if off is not None else dbg.get("offset")
        grasp = dbg.get("grasp_uv")
        toe = dbg.get("toe_uv")
        if (
            self._cam_id() == "cam1"
            and isinstance(grasp, (list, tuple))
            and isinstance(toe, (list, tuple))
            and len(grasp) >= 2
            and len(toe) >= 2
        ):
            self._overlay_toe = (
                float(grasp[0]),
                float(grasp[1]),
                float(toe[0]),
                float(toe[1]),
                length,
            )
        else:
            self._overlay_toe = None
        gxy = dbg.get("grasp_xy")
        txy = dbg.get("toe_xy")
        xy_txt = ""
        if isinstance(gxy, (list, tuple)) and isinstance(txy, (list, tuple)) and len(gxy) >= 2 and len(txy) >= 2:
            xy_txt = (
                f"  抓取基座XY=({float(gxy[0]):.1f},{float(gxy[1]):.1f})  "
                f"鞋头基座XY=({float(txy[0]):.1f},{float(txy[1]):.1f})\n"
                f"  换算={dbg.get('method') or '-'}\n"
            )
        self._log(
            f"【皮带拍照 cam1={'模拟' if mock else '真机'}】\n"
            f"  ok={r.ok}\n"
            f"  X={r.x:.2f}  Y={r.y:.2f}  Z={r.z:.2f}\n"
            f"  Rx={r.rx:.2f}  Ry={r.ry:.2f}  Rz={r.rz:.2f}\n"
            f"  左右={'左鞋' if r.is_left_shoe else '右鞋'}\n"
            f"  鞋长={length:.1f}mm（示教器基座XY）  鞋头偏移={off_s}\n"
            f"{xy_txt}"
            f"  source={r.source}\n"
            f"  备注: {r.message}"
        )

    def _test_slot_yolo(self) -> None:
        cid = self._cam_id()
        key = cid if cid in ("cam3", "cam4") else "cam3"
        occ, msg, conf = self.ctx.vision.test_slot_classify(key)
        state = "失败" if occ is None else ("有鞋" if occ else "空槽")
        self._log(
            f"【槽有无鞋 {key}】\n"
            f"  结果={state}  conf={conf:.2f}\n"
            f"  备注: {msg}"
        )

    def _test_toe_yolo(self) -> None:
        vis = self.ctx.cfg.get("vision") or {}
        key = str((vis.get("toe_align") or {}).get("camera") or "cam2")
        label, msg = self.ctx.vision.test_toe_align_label(key)
        self._log(
            f"【鞋头对位 {key}】\n"
            f"  标签={label or '-'}\n"
            f"  备注: {msg}\n"
            f"  约定：0=到位，1=继续向前（与旧程序 classify 一致）"
        )

    def _test_rod(self) -> None:
        ok, dx, dy, dz, _vis, msg = self.ctx.vision.test_rod_offset()
        off = self.ctx.vision.last_pick_xy_offset_mm
        self._log(
            f"【压杆偏移 cam4】\n"
            f"  ok={ok}  dx={dx:.2f}  dy={dy:.2f}  dz={dz:.2f} mm\n"
            f"  已缓存给 Station5={off}\n"
            f"  备注: {msg}"
        )
        if ok:
            self.ctx.vision.last_pick_xy_offset_mm = [dx, dy, dz]

    def _test_place(self) -> None:
        mock = self.ctx.vision.cam_is_mock("cam3")
        r = self.ctx.vision.photo_place_slot()
        side = (
            "左鞋槽"
            if r.is_left_slot
            else ("右鞋槽" if r.is_left_slot is False else "未知")
        )
        extra = ""
        if mock:
            extra = (
                f"\n  Mock开关: 有料={self.ctx.vision.mock_place_has_material}"
                f" 左槽={self.ctx.vision.mock_place_is_left}"
            )
        self._log(
            f"【放料槽 cam3={'模拟' if mock else '真机'}】\n"
            f"  ok={r.ok} 有料={r.has_material} 槽向={side}\n"
            f"  备注: {r.message}{extra}"
        )

    def _test_pick(self) -> None:
        mock = self.ctx.vision.cam_is_mock("cam4")
        r = self.ctx.vision.photo_pick_slot()
        self._log(
            f"【取料槽 cam4={'模拟' if mock else '真机'}】\n"
            f"  ok={r.ok} 有料={r.has_material}\n"
            f"  备注: {r.message}"
        )

    def _test_guide(self) -> None:
        mock = self.ctx.vision.cam_is_mock("cam2")
        g = self.ctx.vision.guide_place_edge()
        self._log(
            f"【鞋头对位/引导 cam2={'模拟' if mock else '真机'} method={self.ctx.vision.method()}】\n"
            f"  ok={g.ok} aligned={g.aligned}\n"
            f"  dx={g.dx:.2f} dy={g.dy:.2f} drz={g.drz:.2f}\n"
            f"  备注: {g.message}"
        )

    def _refresh_handeye_lbl(self) -> None:
        if not hasattr(self, "lbl_handeye"):
            return
        n = len(self._handeye_samples)
        px = self._pending_pixel
        pend = self._pending_handeye_robot
        if pend:
            tcp = pend.get("tcp") or {}
            pose_s = (
                f"已记录TCP z={float(tcp.get('z', 0)):.1f}"
                f"{'·已移开拍照' if self._handeye_retreat_done else '·待移开拍照'}"
            )
        else:
            pose_s = "未记录位姿"
        pick_s = ""
        if self._last_handeye_pick_tcp:
            t = self._last_handeye_pick_tcp
            pick_s = f" | 本点Pick X={float(t.get('x', 0)):.0f}"
        _set_label_text(
            self.lbl_handeye,
            f"{calib.handeye_status_text(self._cam_id())} | "
            f"采样缓冲={n} | {pose_s} | 像素={px if px else '未点选'}{pick_s}",
        )

    def _record_handeye_robot_pose(self) -> None:
        from vision import commission_actions as cact

        try:
            rec = cact.read_handeye_robot_pose(self.ctx)
        except Exception as e:
            QMessageBox.warning(self, "手眼", str(e))
            return
        self._pending_handeye_robot = rec
        self._handeye_retreat_done = False
        self._pending_pixel = None
        tcp = rec["tcp"]
        joints = rec.get("joints") or []
        j_s = ", ".join(f"{x:.2f}" for x in joints[:6])
        self._refresh_handeye_lbl()
        self._log(
            "① 已记录机械臂位姿\n"
            "  请在示教器手动移开臂/夹爪（标定点仍在相机视野内），再点「② 移开后拍照」\n"
            f"  TCP x={tcp['x']:.2f} y={tcp['y']:.2f} z={tcp['z']:.2f} "
            f"rz={tcp['rz']:.2f}\n"
            f"  J=[{j_s}]"
        )

    def _handeye_retreat_and_capture(self) -> None:
        from vision import commission_actions as cact

        if self._pending_handeye_robot is None:
            QMessageBox.information(
                self,
                "手眼",
                "请先把上料臂 TCP 对准标定点，再点「① 记录机械臂位姿」。",
            )
            return
        ans = QMessageBox.question(
            self,
            "手眼",
            "请确认已在示教器上手动将上料臂/夹爪移出相机视野，"
            "且标定点仍清晰可见。\n\n移开完成后点「是」开始拍照。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        cid = self._cam_id()
        try:
            img, _depth, msg = cact.handeye_retreat_and_capture(self.ctx, cid)
        except Exception as e:
            QMessageBox.warning(self, "手眼", str(e))
            self._log(f"移开拍照失败: {e}")
            return
        self._handeye_retreat_done = True
        self._pending_pixel = None
        self._last_bgr = img
        self._show_bgr(img)
        self._refresh_handeye_lbl()
        self._log(msg)

    def _finish_handeye_sample(self) -> None:
        from vision import commission_actions as cact

        if self._pending_handeye_robot is None:
            QMessageBox.information(self, "手眼", "请先点「① 记录机械臂位姿」")
            return
        if not self._handeye_retreat_done:
            QMessageBox.information(self, "手眼", "请先点「② 移开后拍照」")
            return
        if self._pending_pixel is None:
            QMessageBox.information(
                self,
                "手眼",
                "请在上方原图点击标定点像素（移开后无遮挡的画面）",
            )
            return
        u, v = self._pending_pixel
        try:
            msg = cact.append_handeye_sample(
                self.ctx,
                self._cam_id(),
                pixel_u=u,
                pixel_v=v,
                robot_record=self._pending_handeye_robot,
                img=self._last_bgr,
            )
        except Exception as e:
            QMessageBox.warning(self, "手眼", str(e))
            return
        self._handeye_samples = calib.load_handeye_samples(self._cam_id())
        tcp_saved = dict(self._pending_handeye_robot["tcp"])
        self._last_handeye_pick_tcp = tcp_saved
        try:
            pp_msg = cact.write_pick_pose_from_tcp(self.ctx, tcp_saved)
        except Exception:
            pp_msg = ""
        self._pending_handeye_robot = None
        self._handeye_retreat_done = False
        self._pending_pixel = None
        self._refresh_handeye_lbl()
        full_msg = msg + (f"\n{pp_msg}" if pp_msg else "")
        self._log(full_msg + "\n可点「按路径回夹取」验证取料误差。")

    def _handeye_tcp_for_repick(self) -> dict[str, float] | None:
        if self._last_handeye_pick_tcp:
            return dict(self._last_handeye_pick_tcp)
        try:
            from devices.pose_utils import numeric_pose

            pp = numeric_pose(self.ctx.gvl.PickPose)
            if abs(float(pp.get("x", 0))) > 1e-3 or abs(float(pp.get("y", 0))) > 1e-3:
                return pp
        except Exception:
            pass
        return None

    def _handeye_confirm_motion(self, title: str, detail: str) -> bool:
        if self.ctx.machine.state.name == "RUNNING":
            QMessageBox.warning(self, "禁止", "自动运行中禁止点动，请先停止。")
            return False
        ans = QMessageBox.question(
            self,
            title,
            f"{detail}\n\n请确认周边安全。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _handeye_write_pick_pose(self) -> None:
        from vision import commission_actions as cact

        tcp = self._handeye_tcp_for_repick()
        if tcp is None:
            QMessageBox.information(self, "手眼", "请先完成「③ 完成本点采样」以记录取料位")
            return
        try:
            self._log(cact.write_pick_pose_from_tcp(self.ctx, tcp))
        except Exception as e:
            QMessageBox.warning(self, "PickPose", str(e))

    def _handeye_move_pick_entry(self) -> None:
        from vision import commission_actions as cact

        if not self._handeye_confirm_motion("回 pick_entry", "MoveJ → 示教进入点 pick_entry"):
            return
        try:
            self._log(cact.move_robot1_pick_entry(self.ctx))
        except Exception as e:
            QMessageBox.critical(self, "运动", str(e))

    def _handeye_move_pick_stage(self, *, above: bool) -> None:
        from vision import commission_actions as cact

        tcp = self._handeye_tcp_for_repick()
        if tcp is None:
            QMessageBox.information(self, "手眼", "请先完成「③ 完成本点采样」")
            return
        label = "取料上方" if above else "取料点"
        if not self._handeye_confirm_motion(f"到{label}", f"MoveL → 本标定点{label}"):
            return
        try:
            self._log(cact.move_robot1_to_handeye_tcp(self.ctx, tcp, above=above))
        except Exception as e:
            QMessageBox.critical(self, "运动", str(e))

    def _handeye_repick_sequence(self) -> None:
        from vision import commission_actions as cact

        tcp = self._handeye_tcp_for_repick()
        if tcp is None:
            QMessageBox.information(self, "手眼", "请先完成「③ 完成本点采样」")
            return
        if not self._handeye_confirm_motion(
            "按路径回夹取",
            "张爪 → pick_entry → 取料上方 → 取料点 → 夹紧\n"
            "（与自动取料路径一致，用于验证手眼精度）",
        ):
            return
        try:
            self._log(cact.move_robot1_handeye_repick(self.ctx, tcp))
        except Exception as e:
            QMessageBox.critical(self, "运动", str(e))

    def _add_handeye_sample(self) -> None:
        if self._pending_pixel is None:
            QMessageBox.information(self, "手眼", "请先在预览上点击一个像素点")
            return
        try:
            pose = self.ctx.robot1.get_actual_tcp_pose()
        except Exception as e:
            QMessageBox.warning(self, "手眼", f"读上料臂 TCP 失败: {e}")
            return
        u, v = self._pending_pixel
        sample = {
            "pixel_u": int(u),
            "pixel_v": int(v),
            "tcp": {k: float(pose.get(k, 0)) for k in ("x", "y", "z", "rx", "ry", "rz")},
            "camera": self._cam_id(),
            "time": datetime.now().isoformat(timespec="seconds"),
        }
        cam = self.ctx.cameras.get(self._cam_id())
        img = self._last_bgr
        if img is not None:
            sample["image_w"] = int(img.shape[1])
            sample["image_h"] = int(img.shape[0])
            color_hw = (int(img.shape[0]), int(img.shape[1]))
        else:
            color_hw = None
        from vision import shoe_cfg

        k = k_from_any(
            calib.load_calib(self._cam_id()),
            shoe_cfg.load(self.ctx.cfg.get("vision") or {}),
        )
        depth = getattr(cam, "last_depth", None) if cam is not None else None
        sample = enrich_sample(sample, depth=depth, color_hw=color_hw, k=k)
        self._handeye_samples.append(sample)
        calib.save_handeye_samples(self._cam_id(), self._handeye_samples)
        self._refresh_handeye_lbl()
        z = sample.get("depth_mm")
        z_s = f"{float(z):.0f}mm" if z else "无深度"
        self._log(
            f"手眼采样 +1（共{len(self._handeye_samples)}）\n"
            f"  像素=({u},{v}) 深度={z_s}\n"
            f"  TCP x={sample['tcp']['x']:.2f} y={sample['tcp']['y']:.2f} "
            f"z={sample['tcp']['z']:.2f} rz={sample['tcp']['rz']:.2f}"
        )

    def _save_handeye_samples(self) -> None:
        if not self._handeye_samples:
            QMessageBox.information(self, "手眼", "没有采样点")
            return
        path = calib.save_handeye_samples(self._cam_id(), self._handeye_samples)
        extra = ""
        if self._cam_id() == "cam1":
            extra = "\n" + samples_scale_text("cam1")
        self._log(f"手眼采样已保存: {path}\n共 {len(self._handeye_samples)} 点{extra}")
        self._refresh_handeye_lbl()
        self._refresh_commission()

    def _solve_handeye(self) -> None:
        from vision import commission_actions as cact

        cid = self._cam_id()
        try:
            msg = cact.solve_handeye_and_write(
                self.ctx, cid, extra_samples=list(self._handeye_samples)
            )
            self._log(msg)
            QMessageBox.information(self, "手眼", msg)
        except Exception as e:
            QMessageBox.warning(self, "手眼", str(e))
            self._log(f"求解手眼: {e}")
        self._refresh_handeye_lbl()
        self._refresh_commission()

    def _write_k_json(self) -> None:
        from vision import commission_actions as cact

        try:
            self._log(cact.write_intrinsics_from_calib(self.ctx, self._cam_id()))
        except Exception as e:
            QMessageBox.warning(self, "内参", str(e))
            self._log(str(e))

    def _write_roi_json(self) -> None:
        from vision import commission_actions as cact

        try:
            self._log(cact.write_roi_ratio_from_file(self.ctx, self._cam_id()))
        except Exception as e:
            QMessageBox.warning(self, "ROI", str(e))
            self._log(str(e))

    def _apply_pick_pose(self) -> None:
        from vision import commission_actions as cact

        try:
            _r, msg = cact.apply_belt_pick(self.ctx)
            self._log(msg)
        except Exception as e:
            QMessageBox.warning(self, "PickPose", str(e))
            self._log(str(e))

    def _move_pick_above(self) -> None:
        from vision import commission_actions as cact

        if self.ctx.machine.state.name == "RUNNING":
            QMessageBox.warning(self, "禁止", "自动运行中禁止点动，请先停止。")
            return
        try:
            target = cact.pick_above_pose(self.ctx)
        except Exception as e:
            QMessageBox.warning(self, "MoveL", str(e))
            return
        xyz = ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
        ans = QMessageBox.question(
            self,
            "MoveL 视觉取料上方",
            f"{xyz}\n\n请确认周边安全。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self._log(cact.move_robot1_to_pick(self.ctx, above=True))
        except Exception as e:
            QMessageBox.critical(self, "MoveL", str(e))

    # ---------- display ----------
    def _preview_scale(self, ow: int, oh: int) -> float:
        """叠图缩放：按固定最长边，不读控件瞬时尺寸。"""
        long_side = max(int(ow), int(oh), 1)
        return min(float(_PREVIEW_MAX_SIDE) / float(long_side), 1.0)

    def _resize_for_preview(self, img):
        oh, ow = int(img.shape[0]), int(img.shape[1])
        scale = self._preview_scale(ow, oh)
        if scale >= 0.98:
            return img.copy(), scale, ow, oh
        vw = max(1, int(round(ow * scale)))
        vh = max(1, int(round(oh * scale)))
        view = cv2.resize(img, (vw, vh), interpolation=cv2.INTER_AREA)
        return view, scale, ow, oh

    def _draw_preview_overlays(self, vis, scale: float) -> None:
        x = int(self.sp_x.value() * scale)
        y = int(self.sp_y.value() * scale)
        w = max(1, int(self.sp_w.value() * scale))
        h = max(1, int(self.sp_h.value() * scale))
        cid = self._cam_id()
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), max(1, int(round(2 * scale))))
        cv2.putText(
            vis,
            f"ROI {cid}",
            (x + 4, max(16, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.35, 0.5 * scale),
            (0, 255, 0),
            1,
        )
        if self._crosshair:
            ih, iw = vis.shape[:2]
            cv2.line(vis, (iw // 2, 0), (iw // 2, ih), (80, 80, 255), 1)
            cv2.line(vis, (0, ih // 2), (iw, ih // 2), (80, 80, 255), 1)
        if self._overlay_toe is not None and cid == "cam1":
            gx, gy, tx, ty, ln = self._overlay_toe
            p0 = (int(gx * scale), int(gy * scale))
            p1 = (int(tx * scale), int(ty * scale))
            cv2.circle(vis, p0, max(3, int(round(6 * scale))), (0, 255, 255), 2)
            cv2.circle(vis, p1, max(3, int(round(6 * scale))), (0, 0, 255), 2)
            cv2.arrowedLine(vis, p0, p1, (0, 0, 255), max(1, int(round(2 * scale))), tipLength=0.12)
            cv2.putText(
                vis,
                f"L={ln:.0f}mm",
                (p1[0] + 8, max(16, p1[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                max(0.4, 0.55 * scale),
                (0, 0, 255),
                1,
            )
        if self._pending_pixel is not None:
            pu, pv = self._pending_pixel
            cv2.drawMarker(
                vis,
                (int(pu * scale), int(pv * scale)),
                (255, 0, 255),
                cv2.MARKER_CROSS,
                max(8, int(round(16 * scale))),
                max(1, int(round(2 * scale))),
            )

    def _bgr_to_pixmap(self, vis: Any, buf_attr: str) -> QPixmap | None:
        """BGR → QPixmap；buf_attr 指向本页缓存数组，避免每帧新分配。"""
        if cv2 is None or vis is None:
            return None
        oh, ow = int(vis.shape[0]), int(vis.shape[1])
        if oh <= 0 or ow <= 0:
            return None
        rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
        h_, w_, ch = rgb.shape
        if np is not None:
            buf = getattr(self, buf_attr)
            if buf is None or buf.shape != rgb.shape:
                buf = np.ascontiguousarray(rgb)
                setattr(self, buf_attr, buf)
            else:
                np.copyto(buf, rgb)
            qimg = QImage(buf.data, w_, h_, ch * w_, QImage.Format_RGB888).copy()
        else:
            qimg = QImage(rgb.data, w_, h_, ch * w_, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qimg)

    def _show_bgr(self, vis: Any, *, orig_wh: tuple[int, int] | None = None) -> None:
        pix = self._bgr_to_pixmap(vis, "_preview_pix_buf")
        if pix is None:
            return
        oh, ow = int(vis.shape[0]), int(vis.shape[1])
        orig_ow, orig_oh = orig_wh if orig_wh is not None else (ow, oh)
        self.preview.set_frame(pix, orig_ow, orig_oh)

    def _show_depth_bgr(self, vis: Any) -> None:
        pix = self._bgr_to_pixmap(vis, "_depth_pix_buf")
        if pix is None:
            return
        oh, ow = int(vis.shape[0]), int(vis.shape[1])
        self.preview_depth.set_frame(pix, ow, oh)

    def _set_depth_pane_visible(self, on: bool) -> None:
        """显示或收起下方深度格；收起时记住分隔位置。"""
        want = bool(on)
        if self.preview_depth.isVisible() == want:
            return
        if not want:
            self._preview_split_sizes = self.split_preview.sizes()
            self.preview_depth.hide()
            return
        self.preview_depth.show()
        sizes = list(getattr(self, "_preview_split_sizes", []) or [])
        if len(sizes) >= 2 and sum(sizes) > 80:
            self.split_preview.setSizes(sizes)
        else:
            self.split_preview.setSizes([3, 2])

    def _update_depth_preview(self, show_depth: bool, dv: Any, cam_now: Any) -> str:
        """刷新下方深度格。返回状态栏短文案。"""
        self._set_depth_pane_visible(show_depth)
        if not show_depth:
            return ""
        if dv is not None and not self._freeze:
            self._last_depth_vis = dv
        stats = str(getattr(cam_now, "last_depth_stats", "") or "") if cam_now else ""
        if not stats:
            depth_mm = getattr(cam_now, "last_depth", None) if cam_now is not None else None
            stats = depth_stats_text(depth_mm)
        if dv is not None:
            self._show_depth_bgr(dv)
        elif self._last_depth_vis is not None and self._freeze:
            self._show_depth_bgr(self._last_depth_vis)
        else:
            self.preview_depth.show_placeholder(stats or "深度 无图")
        if self._freeze and not stats:
            return " | 深度:冻结"
        return " | " + (stats or "深度:无")

    def refresh(self) -> None:
        """状态标签刷新（主窗定时器）；预览由 _preview_timer 驱动。"""
        if not self.isVisible():
            return
        self._syncing_cam_mock = True
        for key, cb in self.chk_cam_mock.items():
            want = self.ctx.vision.cam_is_mock(key)
            if cb.isChecked() != want:
                cb.setChecked(want)
        self._syncing_cam_mock = False
        self._commission_tick = int(getattr(self, "_commission_tick", 0)) + 1
        if self._commission_tick % 10 != 0:
            return
        self._refresh_calib_status()
        self._refresh_handeye_lbl()
        self._refresh_commission(heavy=(self._commission_tick % 40 == 0))

    def _tick_preview(self) -> None:
        if not self.isVisible():
            return
        app = QApplication.instance()
        if (
            app is not None
            and app.applicationState() != Qt.ApplicationState.ApplicationActive
        ):
            return
        if cv2 is None:
            self.preview.show_placeholder("请安装 opencv-python 后查看预览")
            self.preview_depth.show_placeholder("深度")
            return
        holding = (
            self._board_hold_vis is not None
            and time.monotonic() < float(self._board_hold_until)
        )
        x, y, w, h = self.sp_x.value(), self.sp_y.value(), self.sp_w.value(), self.sp_h.value()
        show_depth = bool(
            getattr(self, "chk_show_depth", None) and self.chk_show_depth.isChecked()
        )
        cam_now = self.ctx.cameras.get(self._cam_id())
        if cam_now is not None and not bool(getattr(cam_now, "enable_depth", True)):
            show_depth = False
        if holding:
            img = self._board_hold_vis
        else:
            img = self._peek_preview_frame()
            if img is None:
                img = self._last_bgr
        dv = self._peek_depth_vis() if show_depth else None
        skip_key = (
            id(img) if img is not None else 0,
            id(dv) if dv is not None else 0,
            bool(show_depth),
            bool(self._freeze),
            bool(holding),
            bool(self._board_busy),
            int(x),
            int(y),
            int(w),
            int(h),
            bool(self._crosshair),
            self._pending_pixel,
            self._overlay_toe,
            bool(self._board_session),
            len(self._calib_images),
            self._board_last_ok,
            self._cam_id(),
        )
        if (
            skip_key == getattr(self, "_preview_skip_key", None)
            and self.preview.has_frame()
            and not self._board_busy
        ):
            return
        now = time.monotonic()
        min_dt = 1.0 / 8.0
        if (now - float(getattr(self, "_last_preview_paint", 0.0))) < min_dt:
            return
        self._last_preview_paint = now
        self._preview_skip_key = skip_key
        orig_ow = orig_oh = 0
        if holding:
            vis, scale, orig_ow, orig_oh = self._resize_for_preview(self._board_hold_vis)
        elif self._board_busy:
            if img is None:
                self._update_depth_preview(show_depth, dv, cam_now)
                return
            if not self._freeze:
                self._last_bgr = img
            vis, scale, orig_ow, orig_oh = self._resize_for_preview(img)
            cv2.putText(
                vis,
                "DETECTING...",
                (16, 36),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 200, 255),
                2,
            )
        else:
            if img is None:
                cam = cam_now
                if cam is not None and cam.opening:
                    hint = "正在连接真机…"
                elif cam is not None and not cam.use_mock:
                    hint = f"真机未出图 {cam.last_error or ''}".strip()
                else:
                    hint = "模拟"
                if not self.preview.has_frame():
                    self.preview.show_placeholder(f"{self._cam_id()} 无图（{hint}）")
                    _set_label_text(self.lbl_img, "图像: 无")
                self._update_depth_preview(show_depth, dv, cam_now)
                return
            if not self._freeze:
                self._last_bgr = img
            self._note_frame_size(img)
            vis, scale, orig_ow, orig_oh = self._resize_for_preview(img)
            self._draw_preview_overlays(vis, scale)
        last = self._board_last_ok
        last_txt = ""
        if last is True:
            last_txt = " | 上一帧:已识别"
        elif last is False:
            last_txt = " | 上一帧:未识别"
        depth_txt = self._update_depth_preview(show_depth, dv, cam_now)
        _set_label_text(
            self.lbl_img,
            f"图像: {orig_ow}×{orig_oh} | {self._cam_id()} "
            f"{'模拟' if self.ctx.vision.cam_is_mock(self._cam_id()) else '真机'} | "
            f"ROI[{self._cam_id()}]=({x},{y},{w},{h})"
            + (" | 冻结" if self._freeze else "")
            + (" | 查看识别结果" if holding else "")
            + (f" | 标定采集中 有效帧={len(self._calib_images)}" if self._board_session else "")
            + last_txt
            + depth_txt,
        )
        self._show_bgr(vis, orig_wh=(orig_ow, orig_oh))
