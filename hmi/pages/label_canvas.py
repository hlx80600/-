"""统一标注画布：分类不使用；检测矩形 / OBB 旋转框 / 分割多边形。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hmi.style import style_many
from vision import label_io
from vision import model_store as mstore
from vision import obb_label

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore


class _Canvas(QLabel):
    shapes_edited = Signal()

    def __init__(self) -> None:
        super().__init__("采图后在这里标注")
        self.setMinimumHeight(280)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background:#222;color:#aaa;")
        self.setMouseTracking(True)
        self._bgr = None
        self._img_w = 0
        self._img_h = 0
        self._dx = self._dy = self._dw = self._dh = 0
        self.shapes: list[dict] = []
        self.sel = -1
        self.task = "obb"
        self.cls_id = 0
        self._drag = False
        self._p0 = (0.0, 0.0)
        self._p1 = (0.0, 0.0)
        self._poly: list[tuple[float, float]] = []
        self.undo_stack: list[list[dict]] = []

    def push_undo(self) -> None:
        self.undo_stack.append(deepcopy(self.shapes))
        self.undo_stack = self.undo_stack[-40:]

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.shapes = self.undo_stack.pop()
        self.sel = min(self.sel, len(self.shapes) - 1)
        self._poly = []
        self.shapes_edited.emit()
        self.update()
        return True

    def set_bgr(self, bgr) -> None:
        self._bgr = bgr
        self._poly = []
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
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pix)
        self._geom()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._bgr is not None:
            self._paint()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._bgr is None or self._dw <= 0:
            return
        p = QPainter(self)
        for i, sh in enumerate(self.shapes):
            color = QColor("#f4d03f") if i == self.sel else QColor("#2ecc71")
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            kind = str(sh.get("kind") or self.task)
            if kind == "seg" and sh.get("points"):
                poly = QPolygonF([self._to_disp(x, y) for x, y in sh["points"]])
                p.drawPolygon(poly)
            else:
                pts = obb_label.rotated_corners(
                    float(sh["cx"]),
                    float(sh["cy"]),
                    float(sh["w"]),
                    float(sh["h"]),
                    float(sh.get("angle_deg") or 0.0),
                )
                p.drawPolygon(QPolygonF([self._to_disp(x, y) for x, y in pts]))
        if self.task == "segment" and self._poly:
            p.setPen(QPen(QColor("#5dade2"), 2, Qt.PenStyle.DashLine))
            poly = QPolygonF([self._to_disp(x, y) for x, y in self._poly])
            p.drawPolyline(poly)
        elif self._drag:
            xa, ya = self._p0
            xb, yb = self._p1
            p.setPen(QPen(QColor("#5dade2"), 2, Qt.PenStyle.DashLine))
            p.drawRect(
                self._to_disp(min(xa, xb), min(ya, yb)).x(),
                self._to_disp(min(xa, xb), min(ya, yb)).y(),
                abs(xb - xa) * self._dw / self._img_w,
                abs(yb - ya) * self._dh / self._img_h,
            )
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self.task == "segment" and self._poly:
                self._poly.pop()
                self.update()
                return
            hit = self._hit(pt)
            if hit >= 0:
                self.push_undo()
                del self.shapes[hit]
                self.sel = -1
                self.shapes_edited.emit()
                self.update()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self.task == "segment":
                self._poly.append(pt)
                self.update()
                return
            hit = self._hit(pt)
            if hit >= 0:
                self.sel = hit
                self.shapes_edited.emit()
                self.update()
                return
            self._drag = True
            self._p0 = self._p1 = pt
            self.setFocus()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._drag:
            return
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is not None:
            self._p1 = pt
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.task == "segment" or not self._drag or event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = False
        pt = self._to_img(event.position() if hasattr(event, "position") else event.pos())
        if pt is not None:
            self._p1 = pt
        box = obb_label.box_from_drag(*self._p0, *self._p1, cls_id=self.cls_id)
        if box["w"] >= 8 and box["h"] >= 8:
            self.push_undo()
            box["kind"] = "detect" if self.task == "detect" else "obb"
            box["points"] = []
            self.shapes.append(box)
            self.sel = len(self.shapes) - 1
            self.shapes_edited.emit()
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.task == "segment" and len(self._poly) >= 3:
            self.finish_polygon()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def finish_polygon(self) -> None:
        if len(self._poly) < 3:
            return
        self.push_undo()
        xs = [p[0] for p in self._poly]
        ys = [p[1] for p in self._poly]
        self.shapes.append(
            {
                "kind": "seg",
                "cls": self.cls_id,
                "cx": (min(xs) + max(xs)) / 2.0,
                "cy": (min(ys) + max(ys)) / 2.0,
                "w": max(4.0, max(xs) - min(xs)),
                "h": max(4.0, max(ys) - min(ys)),
                "angle_deg": 0.0,
                "points": list(self._poly),
            }
        )
        self._poly = []
        self.sel = len(self.shapes) - 1
        self.shapes_edited.emit()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.task != "obb" or self.sel < 0 or self.sel >= len(self.shapes):
            event.ignore()
            return
        if not self.undo_stack or self.undo_stack[-1] is not self.shapes:
            self.push_undo()
        delta = 3.0 if event.angleDelta().y() > 0 else -3.0
        b = self.shapes[self.sel]
        b["angle_deg"] = float(b.get("angle_deg") or 0.0) + delta
        self.shapes_edited.emit()
        self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_polygon()
            event.accept()
            return
        if key == Qt.Key.Key_Delete and 0 <= self.sel < len(self.shapes):
            self.push_undo()
            del self.shapes[self.sel]
            self.sel = min(self.sel, len(self.shapes) - 1)
            self.shapes_edited.emit()
            self.update()
            event.accept()
            return
        if key == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.undo()
            event.accept()
            return
        super().keyPressEvent(event)

    def _hit(self, pt: tuple[float, float]) -> int:
        x, y = pt
        for i in range(len(self.shapes) - 1, -1, -1):
            sh = self.shapes[i]
            hw, hh = float(sh.get("w") or 0) / 2.0, float(sh.get("h") or 0) / 2.0
            if abs(x - float(sh.get("cx") or 0)) <= hw * 1.15 and abs(
                y - float(sh.get("cy") or 0)
            ) <= hh * 1.15:
                return i
        return -1


class LabelCanvasPanel(QWidget):
    """检测 / OBB / 分割标注；分类任务时隐藏。"""

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
        self.canvas.shapes_edited.connect(self._on_edit)
        root.addWidget(self.canvas, 1)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("上一张")
        self.btn_next = QPushButton("下一张")
        self.btn_save = QPushButton("保存本图")
        self.btn_undo = QPushButton("撤销")
        self.btn_del = QPushButton("删除选中")
        self.btn_clr = QPushButton("清空本图")
        self.btn_poly = QPushButton("闭合多边形")
        style_many(
            [
                (self.btn_prev, "neutral"),
                (self.btn_next, "primary"),
                (self.btn_save, "success"),
                (self.btn_undo, "warn"),
                (self.btn_del, "warn"),
                (self.btn_clr, "danger"),
                (self.btn_poly, "motion"),
            ]
        )
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_save.clicked.connect(self.save_current)
        self.btn_undo.clicked.connect(self._undo)
        self.btn_del.clicked.connect(self._del_sel)
        self.btn_clr.clicked.connect(self._clear)
        self.btn_poly.clicked.connect(self.canvas.finish_polygon)
        for b in (
            self.btn_prev,
            self.btn_next,
            self.btn_save,
            self.btn_undo,
            self.btn_del,
            self.btn_clr,
            self.btn_poly,
        ):
            nav.addWidget(b, 0)
        nav.addStretch(1)
        root.addLayout(nav)

        row = QHBoxLayout()
        self.chk_unlab = QCheckBox("只看未标")
        self.chk_unlab.toggled.connect(self.reload)
        self.cmb_cls = QComboBox()
        self.cmb_cls.currentIndexChanged.connect(self._cls_changed)
        self.sp_ang = QDoubleSpinBox()
        self.sp_ang.setRange(-180.0, 180.0)
        self.sp_ang.setDecimals(1)
        self.sp_ang.setSuffix(" °")
        self.sp_ang.valueChanged.connect(self._ang_changed)
        self.lbl_file = QLabel("-")
        self.lbl_file.setWordWrap(True)
        row.addWidget(self.chk_unlab, 0)
        row.addWidget(QLabel("类别"), 0)
        row.addWidget(self.cmb_cls, 0)
        row.addWidget(QLabel("旋转"), 0)
        row.addWidget(self.sp_ang, 0)
        row.addWidget(self.lbl_file, 1)
        root.addLayout(row)

    def _task(self) -> str:
        return mstore.slot_task(self._slot)

    def set_slot(self, slot_id: str) -> None:
        self.save_current(quiet=True)
        self._slot = slot_id
        task = self._task()
        if task == "classify":
            self.hide()
            return
        self.show()
        meta = mstore.slot_meta(slot_id)
        names = meta.get("names") or {}
        classes = list(meta.get("classes") or [])
        self.cmb_cls.blockSignals(True)
        self.cmb_cls.clear()
        if names:
            keys: list[int] = []
            for raw in names.keys():
                try:
                    keys.append(int(raw))
                except (TypeError, ValueError):
                    continue
            for k in sorted(set(keys)):
                label = names.get(k)
                if label is None:
                    label = names.get(str(k), str(k))
                self.cmb_cls.addItem(str(label), k)
        elif classes:
            for i, c in enumerate(classes):
                self.cmb_cls.addItem(mstore.class_label(c), i)
        else:
            self.cmb_cls.addItem("目标", 0)
        self.cmb_cls.blockSignals(False)
        self.canvas.task = task
        self.canvas.cls_id = int(self.cmb_cls.currentData() or 0)
        self.btn_poly.setVisible(task == "segment")
        self.sp_ang.setEnabled(task == "obb")
        if task == "obb":
            tip = "拖矩形圈目标，滚轮旋转。右键删除框。"
        elif task == "detect":
            tip = "拖矩形圈目标（轴对齐检测框）。右键删除。"
        else:
            tip = "左键加点，双击或「闭合多边形」完成一圈。右键撤销一点。"
        self.lbl_hint.setText(tip)
        self.reload()

    def reload(self) -> None:
        if self._task() == "classify":
            return
        files = label_io.list_slot_images(self._slot)
        if self.chk_unlab.isChecked():
            files = [p for p in files if not label_io.is_labeled(p)]
        keep = self._files[self._idx] if 0 <= self._idx < len(self._files) else None
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
            self.canvas.setText("还没有图。请先到「采集」拍图或导入。")
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
        bgr = cv2.imread(str(path)) if cv2 is not None else None
        self.canvas.set_bgr(bgr)
        self.canvas.undo_stack = []
        h, w = (int(bgr.shape[0]), int(bgr.shape[1])) if bgr is not None else (1, 1)
        self.canvas.shapes = label_io.load_shapes(path, w, h, task=self._task())
        self.canvas.sel = 0 if self.canvas.shapes else -1
        self._dirty = False
        self._sync_ang()
        self._sync_cls_combo()
        nall = len(label_io.list_slot_images(self._slot))
        nlab = sum(1 for p in label_io.list_slot_images(self._slot) if label_io.is_labeled(p))
        mark = "已标" if label_io.is_labeled(path) else "未标"
        self.lbl_file.setText(
            f"{self._idx + 1}/{len(self._files)}  {mark}  已标 {nlab}/{nall}  {path.name}"
        )

    def _on_edit(self) -> None:
        self._dirty = True
        self._sync_ang()
        self._sync_cls_combo()

    def _sync_cls_combo(self) -> None:
        if 0 <= self.canvas.sel < len(self.canvas.shapes):
            cid = int(self.canvas.shapes[self.canvas.sel].get("cls") or 0)
            idx = self.cmb_cls.findData(cid)
            if idx >= 0:
                self.cmb_cls.blockSignals(True)
                self.cmb_cls.setCurrentIndex(idx)
                self.cmb_cls.blockSignals(False)
            self.canvas.cls_id = cid

    def _cls_changed(self, _i: int = 0) -> None:
        self.canvas.cls_id = int(self.cmb_cls.currentData() or 0)
        if 0 <= self.canvas.sel < len(self.canvas.shapes):
            self.canvas.shapes[self.canvas.sel]["cls"] = self.canvas.cls_id
            self._dirty = True

    def _sync_ang(self) -> None:
        self.sp_ang.blockSignals(True)
        if 0 <= self.canvas.sel < len(self.canvas.shapes):
            self.sp_ang.setValue(float(self.canvas.shapes[self.canvas.sel].get("angle_deg") or 0.0))
        else:
            self.sp_ang.setValue(0.0)
        self.sp_ang.blockSignals(False)

    def _ang_changed(self, val: float) -> None:
        if 0 <= self.canvas.sel < len(self.canvas.shapes):
            self.canvas.shapes[self.canvas.sel]["angle_deg"] = float(val)
            self._dirty = True
            self.canvas.update()

    def save_current(self, quiet: bool = False) -> bool:
        if self._task() == "classify":
            return True
        if self._idx < 0 or self._idx >= len(self._files):
            return True
        path = self._files[self._idx]
        bgr = self.canvas._bgr
        if bgr is None:
            return True
        h, w = int(bgr.shape[0]), int(bgr.shape[1])
        label_io.save_shapes(path, list(self.canvas.shapes), w, h, task=self._task())
        self._dirty = False
        if not quiet:
            self.status.emit(f"已保存 {path.name}  目标数={len(self.canvas.shapes)}")
        self._show(self._idx)
        return True

    def _step(self, d: int) -> None:
        self.save_current(quiet=True)
        if not self._files:
            self.reload()
            return
        self._show(self._idx + d)

    def _del_sel(self) -> None:
        if 0 <= self.canvas.sel < len(self.canvas.shapes):
            self.canvas.push_undo()
            del self.canvas.shapes[self.canvas.sel]
            self.canvas.sel = min(self.canvas.sel, len(self.canvas.shapes) - 1)
            self._dirty = True
            self.canvas.update()
            self._sync_ang()
            self._sync_cls_combo()

    def _clear(self) -> None:
        self.canvas.push_undo()
        self.canvas.shapes = []
        self.canvas.sel = -1
        self.canvas._poly = []
        self._dirty = True
        self.canvas.update()

    def _undo(self) -> None:
        if self.canvas.undo():
            self._dirty = True
            self._sync_ang()
            self._sync_cls_combo()
            self.status.emit("已撤销")
