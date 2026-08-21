"""Qt 显示控件：监控窗原图 / 结果图。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from .frames import CAM_TITLES

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore


def bgr_to_pixmap(img, *, max_side: int = 720) -> QPixmap | None:
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
    def __init__(self, placeholder: str = "无图", *, max_side: int = 720):
        super().__init__(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(220, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "background:#1c2833;color:#bdc3c7;border:1px solid #5d6d7e;border-radius:3px;"
        )
        self._pix: QPixmap | None = None
        self._max_side = int(max_side)

    def set_bgr(self, img) -> None:
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
            Qt.TransformationMode.FastTransformation,
        )
        super().setPixmap(scaled)


class CamPane(QGroupBox):
    def __init__(self, cam_id: str):
        super().__init__(CAM_TITLES.get(cam_id, cam_id))
        self.cam_id = cam_id
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        # 监控窗八路同刷：略缩小解码尺寸，减轻 UI 卡顿
        self.raw_view = FrameView("原图", max_side=640)
        self.vis_view = FrameView("计算结果", max_side=640)
        self.lbl_raw = QLabel("原图")
        self.lbl_vis = QLabel("计算结果")
        for lab in (self.lbl_raw, self.lbl_vis):
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#1a5276;font-size:12px;")
        left.addWidget(QLabel("原图"))
        left.addWidget(self.raw_view, 1)
        left.addWidget(self.lbl_raw)
        right.addWidget(QLabel("计算结果"))
        right.addWidget(self.vis_view, 1)
        right.addWidget(self.lbl_vis)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        lay.addLayout(row)

    def show_raw(self, img, text: str) -> None:
        if img is not None:
            self.raw_view.set_bgr(img)
        self.lbl_raw.setText(text)

    def show_vis(self, img, text: str) -> None:
        if img is not None:
            self.vis_view.set_bgr(img)
        self.lbl_vis.setText(text)
