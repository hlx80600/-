"""分类采图预览：左右脚可旋转/框裁，使训练图与推理时的鞋头朝上抠图一致。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from hmi.style import style_many
from vision import model_store as mstore

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore


class _Canvas(QLabel):
    def __init__(self) -> None:
        super().__init__("先采图，这里显示最近一张")
        self.setMinimumHeight(220)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#222;color:#aaa;")
        self.setMouseTracking(True)
        self._bgr = None
        self._img_w = 0
        self._img_h = 0
        self._dx = self._dy = self._dw = self._dh = 0
        self._p0 = None
        self._p1 = None
        self._placing = False
        self.allow_box = False

    def set_bgr(self, bgr) -> None:
        self._bgr = None if bgr is None else bgr.copy()
        self.clear_box()
        if self._bgr is None:
            self._img_w = self._img_h = 0
            self.clear()
            self.setText("没有图像")
            return
        self._img_h, self._img_w = int(self._bgr.shape[0]), int(self._bgr.shape[1])
        self._paint()

    def clear_box(self) -> None:
        self._p0 = None
        self._p1 = None
        self._placing = False
        self.update()

    def box(self) -> tuple[int, int, int, int] | None:
        r = self._rect()
        if r is None:
            return None
        xa, ya, xb, yb = r
        if xb - xa < 8 or yb - ya < 8:
            return None
        return xa, ya, xb, yb

    def _rect(self) -> tuple[int, int, int, int] | None:
        if self._p0 is None or self._p1 is None:
            return None
        x0, y0 = self._p0
        x1, y1 = self._p1
        xa, xb = sorted((int(round(x0)), int(round(x1))))
        ya, yb = sorted((int(round(y0)), int(round(y1))))
        xa = max(0, min(xa, max(0, self._img_w - 1)))
        xb = max(0, min(xb, self._img_w))
        ya = max(0, min(ya, max(0, self._img_h - 1)))
        yb = max(0, min(yb, self._img_h))
        return xa, ya, xb, yb

    def _geom(self) -> None:
        pix = self.pixmap()
        if pix is None or self._img_w <= 0:
            self._dx = self._dy = self._dw = self._dh = 0
            return
        self._dw, self._dh = pix.width(), pix.height()
        self._dx = max(0, (self.width() - self._dw) // 2)
        self._dy = max(0, (self.height() - self._dh) // 2)

    def _to_img(self, pos, *, clamp: bool = False) -> tuple[float, float] | None:
        if self._dw <= 0 or self._img_w <= 0:
            return None
        x = (pos.x() - self._dx) * self._img_w / self._dw
        y = (pos.y() - self._dy) * self._img_h / self._dh
        if clamp:
            x = min(max(x, 0.0), float(max(0, self._img_w - 1)))
            y = min(max(y, 0.0), float(max(0, self._img_h - 1)))
            return float(x), float(y)
        if x < 0 or y < 0 or x >= self._img_w or y >= self._img_h:
            return None
        return float(x), float(y)

    def _paint(self) -> None:
        if cv2 is None or self._bgr is None:
            return
        rgb = cv2.cvtColor(self._bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(pix)
        self._geom()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._bgr is not None:
            self._paint()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        r = self._rect()
        if r is None or self._dw <= 0 or self._img_w <= 0:
            return
        xa, ya, xb, yb = r
        p = QPainter(self)
        color = QColor("#f4d03f") if self._placing else QColor("#2ecc71")
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.NoBrush)
        rect = QRectF(
            QPointF(
                self._dx + xa * self._dw / self._img_w,
                self._dy + ya * self._dh / self._img_h,
            ),
            QPointF(
                self._dx + xb * self._dw / self._img_w,
                self._dy + yb * self._dh / self._img_h,
            ),
        )
        p.drawRect(rect)

    def _pos(self, event: QMouseEvent):
        return event.position() if hasattr(event, "position") else event.pos()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.allow_box:
            return
        if event.button() == Qt.RightButton:
            self.clear_box()
            return
        if event.button() != Qt.LeftButton:
            return
        pt = self._to_img(self._pos(event), clamp=True)
        if pt is None:
            return
        if self._placing:
            self._p1 = pt
            self._placing = False
            self.update()
            return
        if self.box() is not None:
            return
        self._p0 = pt
        self._p1 = pt
        self._placing = True
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._placing:
            return
        pt = self._to_img(self._pos(event), clamp=True)
        if pt is None:
            return
        self._p1 = pt
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.allow_box or event.button() != Qt.LeftButton or not self._placing:
            return
        pt = self._to_img(self._pos(event), clamp=True)
        if pt is not None:
            self._p1 = pt
        if self.box() is not None:
            self._placing = False
        self.update()


class ClassifyPreviewPanel(QWidget):
    status = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._slot = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#1a5276;font-weight:bold;")
        root.addWidget(self.lbl_hint)
        self.canvas = _Canvas()
        root.addWidget(self.canvas)
        row = QHBoxLayout()
        self.btn_ccw = QPushButton("左转90°")
        self.btn_cw = QPushButton("右转90°")
        self.btn_180 = QPushButton("转180°")
        self.btn_crop = QPushButton("按框裁切")
        self.btn_clear = QPushButton("取消框选")
        style_many(
            [
                (self.btn_ccw, "neutral"),
                (self.btn_cw, "neutral"),
                (self.btn_180, "neutral"),
                (self.btn_crop, "success"),
                (self.btn_clear, "warn"),
            ]
        )
        self.btn_ccw.clicked.connect(lambda: self._rotate(cv2.ROTATE_90_COUNTERCLOCKWISE if cv2 else 0))
        self.btn_cw.clicked.connect(lambda: self._rotate(cv2.ROTATE_90_CLOCKWISE if cv2 else 0))
        self.btn_180.clicked.connect(lambda: self._rotate(cv2.ROTATE_180 if cv2 else 0))
        self.btn_crop.clicked.connect(self._crop)
        self.btn_clear.clicked.connect(self._clear_box)
        for b in (self.btn_ccw, self.btn_cw, self.btn_180, self.btn_crop, self.btn_clear):
            row.addWidget(b, 0)
        row.addStretch(1)
        root.addLayout(row)
        self._edit = [self.btn_ccw, self.btn_cw, self.btn_180, self.btn_crop, self.btn_clear]

    def set_slot(self, slot_id: str) -> None:
        self._slot = slot_id
        meta = mstore.SLOTS.get(slot_id) or {}
        if meta.get("kind") != "cls":
            self.hide()
            return
        self.show()
        shoe_lr = slot_id == "shoe_lr"
        self.canvas.allow_box = shoe_lr
        for w in self._edit:
            w.setVisible(shoe_lr)
        if shoe_lr:
            self.lbl_hint.setText(
                "左右脚：选「左脚」或「右脚」后采图。能检出鞋会自动抠图。"
                "请把鞋头转到朝上。检不出时：图上点一下定点，再点一下收框，然后「按框裁切」。"
                "「取消框选」或右键可去掉当前框。不用画YOLO旋转框。"
            )
        else:
            self.lbl_hint.setText(
                "分类任务：选上面的类别后点采图，整张图就是训练样本，不用圈框。"
            )
        path = mstore.newest_train_image(slot_id, "")
        self.show_path(path)

    def show_path(self, path: Path | None) -> None:
        self._path = Path(path) if path else None
        if self._path is None or not self._path.is_file() or cv2 is None:
            self.canvas.set_bgr(None)
            return
        img = cv2.imread(str(self._path))
        self.canvas.set_bgr(img)

    def _write(self, img) -> None:
        if self._path is None:
            self.status.emit("没有可改的图，请先采图")
            return
        mstore.save_bgr(self._path, img)
        val = mstore.val_twin(self._path)
        if val is not None and val.is_file():
            mstore.save_bgr(val, img)
        self.canvas.set_bgr(img)
        self.status.emit(f"已更新 {self._path.name}")

    def _rotate(self, flag) -> None:
        if cv2 is None or self.canvas._bgr is None:
            return
        self._write(cv2.rotate(self.canvas._bgr, flag))

    def _clear_box(self) -> None:
        self.canvas.clear_box()
        self.status.emit("已取消框选")

    def _crop(self) -> None:
        box = self.canvas.box()
        img = self.canvas._bgr
        if box is None or img is None:
            self.status.emit("请先点两下画出框，再裁切")
            return
        xa, ya, xb, yb = box
        crop = img[ya:yb, xa:xb]
        if crop is None or crop.size == 0:
            self.status.emit("裁切区域无效")
            return
        self._write(crop)
