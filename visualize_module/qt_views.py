"""Qt 显示控件：监控窗原图 / 结果 / 深度（分页，一格只铺一张大图）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
)

from .frames import CAM_TITLES

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

PAGE_RAW = "raw"
PAGE_VIS = "vis"
PAGE_DEPTH = "depth"
_PAGE_INDEX = {PAGE_RAW: 0, PAGE_VIS: 1, PAGE_DEPTH: 2}


def bgr_to_pixmap(img: Any, *, max_side: int = 1280) -> QPixmap | None:
    if img is None or cv2 is None:
        return None
    try:
        h, w = int(img.shape[0]), int(img.shape[1])
        if h <= 0 or w <= 0:
            return None
        view = img
        m = max(h, w)
        if m > max_side:
            scale = float(max_side) / float(m)
            view = cv2.resize(
                img,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        rgb = cv2.cvtColor(view, cv2.COLOR_BGR2RGB)
    except Exception:
        return None
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class FrameView(QLabel):
    def __init__(self, placeholder: str = "无图", *, max_side: int = 1280) -> None:
        super().__init__(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "background:#1c2833;color:#bdc3c7;border:1px solid #5d6d7e;border-radius:3px;"
        )
        self._pix: QPixmap | None = None
        self._max_side = int(max_side)

    def set_bgr(self, img: Any) -> None:
        pix = bgr_to_pixmap(img, max_side=self._max_side)
        if pix is None:
            self._pix = None
            return
        self._pix = pix
        self._rescale()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pix is None or self._pix.isNull():
            return
        scaled = self._pix.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)


class CamPane(QGroupBox):
    """一路相机：原图 / 结果 / 深度三页叠放，由监控窗统一切页。"""

    def __init__(self, cam_id: str) -> None:
        super().__init__(CAM_TITLES.get(cam_id, cam_id))
        self.cam_id = cam_id
        self._page = PAGE_RAW
        self._depth_ok = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setProperty("hmi_fill", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 4)
        lay.setSpacing(4)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.raw_view = FrameView("原图")
        self.vis_view = FrameView("计算结果")
        self.depth_view = FrameView("深度")
        self.stack.addWidget(self.raw_view)
        self.stack.addWidget(self.vis_view)
        self.stack.addWidget(self.depth_view)
        lay.addWidget(self.stack, 1)
        self.lbl_raw = QLabel("原图")
        self.lbl_vis = QLabel("计算结果")
        self.lbl_depth = QLabel("深度")
        for lab in (self.lbl_raw, self.lbl_vis, self.lbl_depth):
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#1a5276;font-size:12px;")
            lay.addWidget(lab)
        self._sync_caption()

    def set_page(self, page: str) -> None:
        """切到原图 / 结果 / 深度；本格铺满这一张。"""
        key = page if page in _PAGE_INDEX else PAGE_RAW
        self._page = key
        self.stack.setCurrentIndex(_PAGE_INDEX[key])
        self._sync_caption()

    def set_depth_visible(self, on: bool) -> None:
        """该路是否配置了深度输出（无深度时深度页仍显示占位图）。"""
        self._depth_ok = bool(on)
        self._sync_caption()

    def show_raw(self, img: Any, text: str) -> None:
        if img is not None:
            self.raw_view.set_bgr(img)
        self.lbl_raw.setText(text)
        self._sync_caption()

    def show_depth(self, img: Any, text: str) -> None:
        if img is not None:
            self.depth_view.set_bgr(img)
        self.lbl_depth.setText(text)
        self._sync_caption()

    def show_vis(self, img: Any, text: str) -> None:
        if img is not None:
            self.vis_view.set_bgr(img)
        self.lbl_vis.setText(text)
        self._sync_caption()

    def _sync_caption(self) -> None:
        self.lbl_raw.setVisible(self._page == PAGE_RAW)
        self.lbl_vis.setVisible(self._page == PAGE_VIS)
        self.lbl_depth.setVisible(self._page == PAGE_DEPTH)
