"""精简视觉方案流程图：图像源 → ROI → 深度学习 → 手眼 → 位姿输出。

仅用于调试预览，不改 GVL。产线 Station 仍走现有链路。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi.pages.points_page import NoWheelComboBox
from hmi.style import apply_page_chrome, style_many
from vision import model_store as mstore
from vision import roi as roi_mod

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
SCHEME_DIR = ROOT / "config" / "vision_schemes"

NODE_TYPES: dict[str, tuple[str, str]] = {
    "image_source": ("图像源", "#1a5276"),
    "roi": ("ROI", "#117a65"),
    "deep_learn": ("深度学习", "#6c3483"),
    "handeye": ("手眼", "#b9770e"),
    "pose_out": ("输出位姿", "#1a7a37"),
}

DEFAULT_SCHEME: dict[str, Any] = {
    "name": "belt_preset",
    "readonly_hint": "产线默认链路只读预设：cam1 → ROI → 皮带OBB槽位 → 手眼 → 位姿。Station 暂不读本文件。",
    "nodes": [
        {"id": "src", "type": "image_source", "cam": "cam1", "x": 40, "y": 80},
        {"id": "roi", "type": "roi", "cam": "cam1", "x": 220, "y": 80},
        {"id": "dl", "type": "deep_learn", "slot": "shoe_obb", "x": 400, "y": 80},
        {"id": "he", "type": "handeye", "cam": "cam1", "x": 580, "y": 80},
        {"id": "out", "type": "pose_out", "x": 760, "y": 80},
    ],
    "edges": [["src", "roi"], ["roi", "dl"], ["dl", "he"], ["he", "out"]],
}


class _NodeItem(QGraphicsRectItem):
    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(QRectF(0, 0, 150, 64))
        self.data = data
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        color = NODE_TYPES.get(str(data.get("type")), ("节点", "#5d6d7e"))[1]
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#1c2833"), 2))
        self.setPos(float(data.get("x") or 40), float(data.get("y") or 80))
        title = NODE_TYPES.get(str(data.get("type")), ("节点", ""))[0]
        extra = str(data.get("cam") or data.get("slot") or "")
        self._label = QGraphicsSimpleTextItem(f"{title}\n{extra}", self)
        self._label.setBrush(QBrush(QColor("#ffffff")))
        self._label.setFont(QFont("Noto Sans CJK SC", 10, QFont.Weight.Bold))
        self._label.setPos(10, 12)

    def refresh_label(self) -> None:
        title = NODE_TYPES.get(str(self.data.get("type")), ("节点", ""))[0]
        extra = str(self.data.get("cam") or self.data.get("slot") or "")
        self._label.setText(f"{title}\n{extra}")

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.data["x"] = int(self.pos().x())
            self.data["y"] = int(self.pos().y())
        return super().itemChange(change, value)


class _SchemeView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(220)
        self._scene = QGraphicsScene(0, 0, 980, 260)
        self.setScene(self._scene)
        self.nodes: list[_NodeItem] = []

    def load_nodes(self, rows: list[dict[str, Any]]) -> None:
        self._scene.clear()
        self.nodes = []
        for row in rows:
            item = _NodeItem(row)
            self._scene.addItem(item)
            self.nodes.append(item)
        self._draw_edges()

    def _draw_edges(self) -> None:
        for item in list(self._scene.items()):
            if not isinstance(item, _NodeItem) and item.zValue() < 0:
                self._scene.removeItem(item)
        pen = QPen(QColor("#5d6d7e"), 2)
        ordered = sorted(self.nodes, key=lambda n: n.pos().x())
        for a, b in zip(ordered, ordered[1:]):
            p1 = a.sceneBoundingRect().center() + QPointF(a.rect().width() / 2 - 8, 0)
            p2 = b.sceneBoundingRect().center() + QPointF(-b.rect().width() / 2 + 8, 0)
            line = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
            line.setZValue(-1)

    def selected_node(self) -> _NodeItem | None:
        for n in self.nodes:
            if n.isSelected():
                return n
        return None

    def collect(self) -> list[dict[str, Any]]:
        self._draw_edges()
        return [n.data for n in self.nodes]


class VisionSchemePage(QWidget):
    """第二期：拖几个现有工具节点，运行一次只预览。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._path = SCHEME_DIR / "belt_preset.yaml"

        root = QVBoxLayout(self)
        tip = QLabel(
            "调试用流程图，不改产线 GVL。节点只有我们真正有的工具："
            "图像源 / ROI / 深度学习 / 手眼 / 输出位姿。确认稳定前 Station 不读方案。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#1a5276;font-weight:bold;")
        root.addWidget(tip)

        bar = QHBoxLayout()
        self.ed_name = QLineEdit("belt_preset")
        self.cmb_add = NoWheelComboBox()
        for tid, (label, _) in NODE_TYPES.items():
            self.cmb_add.addItem(label, tid)
        b_add = QPushButton("添加节点")
        b_del = QPushButton("删除选中")
        b_load = QPushButton("打开")
        b_save = QPushButton("保存")
        b_run = QPushButton("运行一次")
        b_preset = QPushButton("载入产线预设")
        style_many(
            [
                (b_add, "success"),
                (b_del, "danger"),
                (b_load, "neutral"),
                (b_save, "primary"),
                (b_run, "motion"),
                (b_preset, "warn"),
            ]
        )
        b_add.clicked.connect(self._add_node)
        b_del.clicked.connect(self._del_node)
        b_load.clicked.connect(self._open)
        b_save.clicked.connect(self._save)
        b_run.clicked.connect(self._run_once)
        b_preset.clicked.connect(self._load_preset)
        bar.addWidget(QLabel("方案名"), 0)
        bar.addWidget(self.ed_name, 0)
        bar.addWidget(self.cmb_add, 0)
        for b in (b_add, b_del, b_load, b_save, b_preset, b_run):
            bar.addWidget(b, 0)
        bar.addStretch(1)
        root.addLayout(bar)

        prop = QHBoxLayout()
        self.cmb_cam = NoWheelComboBox()
        for cam in ("cam1", "cam2", "cam3", "cam4"):
            self.cmb_cam.addItem(cam, cam)
        self.cmb_slot = NoWheelComboBox()
        for sid, meta in mstore.SLOTS.items():
            self.cmb_slot.addItem(str(meta.get("label") or sid), sid)
        self.cmb_cam.currentIndexChanged.connect(self._apply_props)
        self.cmb_slot.currentIndexChanged.connect(self._apply_props)
        prop.addWidget(QLabel("选中节点相机"), 0)
        prop.addWidget(self.cmb_cam, 0)
        prop.addWidget(QLabel("深度学习槽位"), 0)
        prop.addWidget(self.cmb_slot, 0)
        prop.addStretch(1)
        root.addLayout(prop)

        self.view = _SchemeView()
        root.addWidget(self.view, 0)
        self.lbl_out = QLabel("运行结果：尚未运行")
        self.lbl_out.setWordWrap(True)
        root.addWidget(self.lbl_out)
        self.lbl_img = QLabel("运行一次后这里显示叠图")
        self.lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img.setMinimumHeight(220)
        self.lbl_img.setStyleSheet("background:#222;color:#aaa;")
        root.addWidget(self.lbl_img, 1)

        apply_page_chrome(self)
        SCHEME_DIR.mkdir(parents=True, exist_ok=True)
        if self._path.is_file():
            self._load_file(self._path)
        else:
            self._load_preset()
        self.view._scene.selectionChanged.connect(self._on_sel)

    def refresh(self) -> None:
        return

    def _on_sel(self) -> None:
        n = self.view.selected_node()
        if n is None:
            return
        cam = str(n.data.get("cam") or "cam1")
        i = self.cmb_cam.findData(cam)
        if i >= 0:
            self.cmb_cam.blockSignals(True)
            self.cmb_cam.setCurrentIndex(i)
            self.cmb_cam.blockSignals(False)
        slot = str(n.data.get("slot") or "")
        j = self.cmb_slot.findData(slot)
        if j >= 0:
            self.cmb_slot.blockSignals(True)
            self.cmb_slot.setCurrentIndex(j)
            self.cmb_slot.blockSignals(False)

    def _apply_props(self) -> None:
        n = self.view.selected_node()
        if n is None:
            return
        n.data["cam"] = str(self.cmb_cam.currentData() or "cam1")
        if str(n.data.get("type")) == "deep_learn":
            n.data["slot"] = str(self.cmb_slot.currentData() or "shoe_obb")
        n.refresh_label()

    def _add_node(self) -> None:
        tid = str(self.cmb_add.currentData() or "image_source")
        x = 40 + 170 * len(self.view.nodes)
        row: dict[str, Any] = {
            "id": f"{tid}_{len(self.view.nodes)+1}",
            "type": tid,
            "x": x,
            "y": 80,
        }
        if tid in ("image_source", "roi", "handeye"):
            row["cam"] = str(self.cmb_cam.currentData() or "cam1")
        if tid == "deep_learn":
            row["slot"] = str(self.cmb_slot.currentData() or "shoe_obb")
        self.view.nodes.append(_NodeItem(row))
        self.view._scene.addItem(self.view.nodes[-1])
        self.view._draw_edges()

    def _del_node(self) -> None:
        n = self.view.selected_node()
        if n is None:
            return
        self.view.nodes = [x for x in self.view.nodes if x is not n]
        self.view._scene.removeItem(n)
        self.view._draw_edges()

    def _scheme_dict(self) -> dict[str, Any]:
        nodes = self.view.collect()
        ordered = sorted(nodes, key=lambda d: int(d.get("x") or 0))
        edges = []
        for a, b in zip(ordered, ordered[1:]):
            edges.append([a.get("id"), b.get("id")])
        return {"name": self.ed_name.text().strip() or "scheme", "nodes": nodes, "edges": edges}

    def _load_preset(self) -> None:
        self.ed_name.setText(str(DEFAULT_SCHEME["name"]))
        self.view.load_nodes([dict(n) for n in DEFAULT_SCHEME["nodes"]])
        self.lbl_out.setText(str(DEFAULT_SCHEME.get("readonly_hint") or "已载入产线预设（只预览）"))

    def _load_file(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            QMessageBox.warning(self, "打开方案", str(e))
            return
        if not isinstance(data, dict):
            return
        self.ed_name.setText(str(data.get("name") or path.stem))
        nodes = list(data.get("nodes") or [])
        self.view.load_nodes([dict(n) for n in nodes if isinstance(n, dict)])
        self._path = path
        self.lbl_out.setText(f"已打开 {path}")

    def _open(self) -> None:
        SCHEME_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "打开视觉方案", str(SCHEME_DIR), "YAML (*.yaml *.yml)"
        )
        if path:
            self._load_file(Path(path))

    def _save(self) -> None:
        SCHEME_DIR.mkdir(parents=True, exist_ok=True)
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in self.ed_name.text()) or "scheme"
        path = SCHEME_DIR / f"{name}.yaml"
        path.write_text(
            yaml.safe_dump(self._scheme_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._path = path
        self.lbl_out.setText(f"已保存 {path}（未写入 GVL）")

    def _run_once(self) -> None:
        """按从左到右节点跑一遍预览，不写 PickPose / GVL。"""
        nodes = sorted(self.view.collect(), key=lambda d: int(d.get("x") or 0))
        cam = "cam1"
        slot = "shoe_obb"
        use_roi = False
        use_dl = False
        use_he = False
        for n in nodes:
            t = str(n.get("type") or "")
            if t == "image_source":
                cam = str(n.get("cam") or cam)
            elif t == "roi":
                use_roi = True
                cam = str(n.get("cam") or cam)
            elif t == "deep_learn":
                use_dl = True
                slot = str(n.get("slot") or slot)
            elif t == "handeye":
                use_he = True
                cam = str(n.get("cam") or cam)
        lines: list[str] = [f"相机={cam}  深度学习槽={slot}  ROI={use_roi}  手眼={use_he}"]
        try:
            from vision.cls_crop import grab_slot_image

            img = grab_slot_image(self.ctx, cam)
        except Exception as e:
            QMessageBox.warning(self, "运行一次", f"取图失败: {e}")
            return
        if use_roi:
            box = roi_mod.load_roi(cam)
            img = roi_mod.crop_roi(img, box)
            lines.append(f"已裁 ROI {box}")
        if use_dl:
            rows = mstore.list_slot_weights(slot)
            if not rows:
                lines.append("没有权重，跳过深度学习")
            else:
                try:
                    from vision.ultralytics_runner import overlay_predict_bgr

                    img = overlay_predict_bgr(img, rows[0][1])
                    lines.append(f"已推理 {rows[0][1].name}")
                except Exception as e:
                    lines.append(f"推理失败（可先装 ultralytics）: {e}")
        if use_he and cam == "cam1":
            try:
                r = self.ctx.vision.photo_belt_pick(
                    float(self.ctx.gvl.PickPose.get("z", 120)),
                    float(self.ctx.gvl.PickPose.get("rx", -178)),
                    float(self.ctx.gvl.PickPose.get("ry", -2)),
                    persist=False,
                )
                lines.append(
                    f"手眼预览（未写 GVL） ok={r.ok} X={r.x:.1f} Y={r.y:.1f} Z={r.z:.1f} "
                    f"Rz={r.rz:.1f} {r.message}"
                )
            except TypeError:
                try:
                    r = self.ctx.vision.photo_belt_pick(
                        float(self.ctx.gvl.PickPose.get("z", 120)),
                        float(self.ctx.gvl.PickPose.get("rx", -178)),
                        float(self.ctx.gvl.PickPose.get("ry", -2)),
                    )
                    lines.append(f"手眼预览 ok={r.ok}（请勿把本页当产线写入） {r.message}")
                except Exception as e:
                    lines.append(f"手眼预览失败: {e}")
            except Exception as e:
                lines.append(f"手眼预览失败: {e}")
        self.lbl_out.setText("\n".join(lines))
        if cv2 is not None and img is not None:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(qimg).scaled(
                640,
                400,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_img.setPixmap(pix)
            self.lbl_img.setText("")
