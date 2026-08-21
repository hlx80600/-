"""圈图：拖框定义鞋/楦/压杆，可旋转，存 YOLO-OBB。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hmi.style import style_button, style_many
from vision import obb_label
from vision import model_store as mstore

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore


class _Canvas(QLabel):
    boxes_edited = Signal()

    def __init__(self) -> None:
        super().__init__("采图后在这里圈鞋")
        self.setMinimumHeight(280)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background:#222;color:#aaa;")
        self.setMouseTracking(True)
        self._bgr = None
        self._img_w = 0
        self._img_h = 0
        self._dx = self._dy = self._dw = self._dh = 0
        self.boxes: list[dict] = []
        self.sel = -1
        self._drag = False
        self._p0 = (0.0, 0.0)
        self._p1 = (0.0, 0.0)

    def set_bgr(self, bgr) -> None:
        self._bgr = bgr
        if bgr is None:
            self._img_w = self._img_h = 0
            self.clear()
            self.setText("没有图像")
            return
        self._img_h, self._img_w = int(bgr.shape[0]), int(bgr.shape[1])
        self._paint()

    def _geom(self) -> None:
        pix = self.pixmap()
        if pix is None or self._img_w <= 0:
            self._dx = self._dy = self._dw = self._dh = 0
            return
        self._dw, self._dh = pix.width(), pix.height()
        self._dx = max(0, (self.width() - self._dw) // 2)
        self._dy = max(0, (self.height() - self._dh) // 2)

    def _to_img(self, pos) -> tuple[float, float] | None:
        if self._dw <= 0 or self._img_w <= 0:
            return None
        x = (pos.x() - self._dx) * self._img_w / self._dw
        y = (pos.y() - self._dy) * self._img_h / self._dh
        if x < 0 or y < 0 or x >= self._img_w or y >= self._img_h:
            return None
        return float(x), float(y)

    def _to_disp(self, x: float, y: float) -> QPointF:
        if self._dw <= 0 or self._img_w <= 0:
            return QPointF(x, y)
        return QPointF(
            self._dx + x * self._dw / self._img_w,
            self._dy + y * self._dh / self._img_h,
        )

    def _paint(self) -> None:
        if cv2 is None or self._bgr is None:
            return
        vis = self._bgr.copy()
        rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
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
        if self._bgr is None or self._dw <= 0:
            return
        p = QPainter(self)
        for i, b in enumerate(self.boxes):
            pts = obb_label.rotated_corners(
                b["cx"], b["cy"], b["w"], b["h"], b.get("angle_deg") or 0.0
            )
            poly = QPolygonF([self._to_disp(x, y) for x, y in pts])
            color = QColor("#f4d03f") if i == self.sel else QColor("#2ecc71")
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(poly)
        if self._drag:
            xa, ya = self._p0
            xb, yb = self._p1
            p.setPen(QPen(QColor("#5dade2"), 2, Qt.DashLine))
            p.drawRect(
                self._to_disp(min(xa, xb), min(ya, yb)).x(),
                self._to_disp(min(xa, xb), min(ya, yb)).y(),
                abs(xb - xa) * self._dw / self._img_w,
                abs(yb - ya) * self._dh / self._img_h,
            )
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is None:
            return
        if event.button() == Qt.RightButton:
            hit = self._hit(pt)
            if hit >= 0:
                del self.boxes[hit]
                self.sel = -1
                self.boxes_edited.emit()
                self.update()
            return
        if event.button() == Qt.LeftButton:
            hit = self._hit(pt)
            if hit >= 0:
                self.sel = hit
                self.boxes_edited.emit()
                self.update()
                return
            self._drag = True
            self._p0 = self._p1 = pt
            self.setFocus()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag:
            return
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is not None:
            self._p1 = pt
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self._drag or event.button() != Qt.LeftButton:
            return
        self._drag = False
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is not None:
            self._p1 = pt
        box = obb_label.box_from_drag(*self._p0, *self._p1, cls_id=0)
        if box["w"] >= 8 and box["h"] >= 8:
            self.boxes.append(box)
            self.sel = len(self.boxes) - 1
            self.boxes_edited.emit()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.sel < 0 or self.sel >= len(self.boxes):
            event.ignore()
            return
        delta = 3.0 if event.angleDelta().y() > 0 else -3.0
        b = self.boxes[self.sel]
        b["angle_deg"] = float(b.get("angle_deg") or 0.0) + delta
        self.boxes_edited.emit()
        self.update()
        event.accept()

    def _hit(self, pt: tuple[float, float]) -> int:
        x, y = pt
        for i in range(len(self.boxes) - 1, -1, -1):
            b = self.boxes[i]
            hw, hh = b["w"] / 2.0, b["h"] / 2.0
            if abs(x - b["cx"]) <= hw * 1.15 and abs(y - b["cy"]) <= hh * 1.15:
                return i
        return -1


class ObbLabelPanel(QWidget):
    status = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._slot = "shoe_obb"
        self._files: list[Path] = []
        self._idx = -1
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#1a5276;font-weight:bold;")
        root.addWidget(self.lbl_hint)

        self.canvas = _Canvas()
        self.canvas.boxes_edited.connect(self._on_edit)
        root.addWidget(self.canvas)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("上一张")
        self.btn_next = QPushButton("下一张")
        self.btn_save = QPushButton("保存本图圈框")
        self.btn_del = QPushButton("删除选中框")
        self.btn_clr = QPushButton("清空本图框")
        style_many(
            [
                (self.btn_prev, "neutral"),
                (self.btn_next, "primary"),
                (self.btn_save, "success"),
                (self.btn_del, "warn"),
                (self.btn_clr, "danger"),
            ]
        )
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_save.clicked.connect(self.save_current)
        self.btn_del.clicked.connect(self._del_sel)
        self.btn_clr.clicked.connect(self._clear)
        for b in (self.btn_prev, self.btn_next, self.btn_save, self.btn_del, self.btn_clr):
            nav.addWidget(b, 0)
        nav.addStretch(1)
        root.addLayout(nav)

        row = QHBoxLayout()
        self.chk_unlab = QCheckBox("只看未圈")
        self.chk_unlab.toggled.connect(self.reload)
        self.sp_ang = QDoubleSpinBox()
        self.sp_ang.setRange(-180.0, 180.0)
        self.sp_ang.setDecimals(1)
        self.sp_ang.setSuffix(" °")
        self.sp_ang.valueChanged.connect(self._ang_changed)
        self.lbl_file = QLabel("-")
        self.lbl_file.setWordWrap(True)
        row.addWidget(self.chk_unlab, 0)
        row.addWidget(QLabel("选中框旋转"), 0)
        row.addWidget(self.sp_ang, 0)
        row.addWidget(self.lbl_file, 1)
        root.addLayout(row)

        self._obb_widgets = [
            self.canvas,
            self.btn_prev,
            self.btn_next,
            self.btn_save,
            self.btn_del,
            self.btn_clr,
            self.chk_unlab,
            self.sp_ang,
            self.lbl_file,
        ]

    def set_slot(self, slot_id: str) -> None:
        self.save_current(quiet=True)
        self._slot = slot_id
        meta = mstore.SLOTS.get(slot_id) or {}
        if meta.get("kind") != "obb":
            self.hide()
            return
        self.show()
        names = meta.get("names") or {0: "obj"}
        name = names.get(0, "目标")
        self.lbl_hint.setText(
            f"找{name}必须圈图：在图上拖矩形框住目标；滚轮或右侧角度可旋转。"
            "右键框=删除。用旧模型可先不圈、不训。"
        )
        for w in self._obb_widgets:
            w.setVisible(True)
        self.reload()

    def reload(self) -> None:
        if mstore.SLOTS.get(self._slot, {}).get("kind") != "obb":
            return
        files = obb_label.list_slot_images(self._slot)
        if self.chk_unlab.isChecked():
            files = [p for p in files if not obb_label.is_labeled(p)]
        keep = None
        if 0 <= self._idx < len(self._files):
            keep = self._files[self._idx]
        self._files = files
        self._idx = 0
        if keep is not None:
            for i, p in enumerate(self._files):
                if p == keep:
                    self._idx = i
                    break
        if not self._files:
            self._idx = -1
            self.canvas.set_bgr(None)
            self.canvas.setText("还没有图。请先采图，再回来圈。")
            self.lbl_file.setText("0/0")
            return
        self._show(self._idx)

    def goto_last(self) -> None:
        self.chk_unlab.setChecked(False)
        self.reload()
        if self._files:
            self._show(len(self._files) - 1)

    def _show(self, idx: int) -> None:
        if not self._files:
            return
        self._idx = max(0, min(idx, len(self._files) - 1))
        path = self._files[self._idx]
        bgr = None
        if cv2 is not None:
            bgr = cv2.imread(str(path))
        self.canvas.set_bgr(bgr)
        h, w = (int(bgr.shape[0]), int(bgr.shape[1])) if bgr is not None else (1, 1)
        self.canvas.boxes = obb_label.load_boxes(path, w, h)
        self.canvas.sel = 0 if self.canvas.boxes else -1
        self._dirty = False
        self._sync_ang()
        nlab = sum(1 for p in obb_label.list_slot_images(self._slot) if obb_label.is_labeled(p))
        nall = len(obb_label.list_slot_images(self._slot))
        mark = "已圈" if obb_label.is_labeled(path) else "未圈"
        self.lbl_file.setText(
            f"{self._idx + 1}/{len(self._files)}  {mark}  全任务已圈 {nlab}/{nall}  {path.name}"
        )

    def _on_edit(self) -> None:
        self._dirty = True
        self._sync_ang()

    def _sync_ang(self) -> None:
        self.sp_ang.blockSignals(True)
        if 0 <= self.canvas.sel < len(self.canvas.boxes):
            self.sp_ang.setValue(float(self.canvas.boxes[self.canvas.sel].get("angle_deg") or 0.0))
        else:
            self.sp_ang.setValue(0.0)
        self.sp_ang.blockSignals(False)

    def _ang_changed(self, val: float) -> None:
        if 0 <= self.canvas.sel < len(self.canvas.boxes):
            self.canvas.boxes[self.canvas.sel]["angle_deg"] = float(val)
            self._dirty = True
            self.canvas.update()

    def save_current(self, quiet: bool = False) -> bool:
        if mstore.SLOTS.get(self._slot, {}).get("kind") != "obb":
            return True
        if self._idx < 0 or self._idx >= len(self._files):
            return True
        path = self._files[self._idx]
        bgr = self.canvas._bgr
        if bgr is None:
            return True
        h, w = int(bgr.shape[0]), int(bgr.shape[1])
        obb_label.save_boxes(path, list(self.canvas.boxes), w, h)
        self._dirty = False
        if not quiet:
            self.status.emit(f"已保存圈框 {path.name}  框数={len(self.canvas.boxes)}")
        self._show(self._idx)
        return True

    def _step(self, d: int) -> None:
        self.save_current(quiet=True)
        if not self._files:
            self.reload()
            return
        self._show(self._idx + d)

    def _del_sel(self) -> None:
        if 0 <= self.canvas.sel < len(self.canvas.boxes):
            del self.canvas.boxes[self.canvas.sel]
            self.canvas.sel = min(self.canvas.sel, len(self.canvas.boxes) - 1)
            self._dirty = True
            self.canvas.update()
            self._sync_ang()

    def _clear(self) -> None:
        self.canvas.boxes = []
        self.canvas.sel = -1
        self._dirty = True
        self.canvas.update()
