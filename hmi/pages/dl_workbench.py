"""海康式五步深度学习工作台：采集 → 标注 → 训练 → 验证 → 模型。

训练走 Ultralytics 子进程，不阻塞 PLC 主循环。手眼 / 试走仍用视觉工作区其它页签。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi.pages.cls_preview_widget import ClassifyPreviewPanel
from hmi.pages.label_canvas import LabelCanvasPanel
from hmi.pages.points_page import NoWheelComboBox
from hmi.pages.train_curve import TrainCurveWidget
from hmi.style import apply_page_chrome, style_many
from hmi.tab_titles import T
from vision import model_store as mstore
from vision import ultralytics_hparams as uhp

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
STEPS = ("采集", "标注", "训练", "验证", "模型")
TASK_CN = {
    "classify": "分类",
    "detect": "检测",
    "obb": "旋转框",
    "segment": "分割",
}


def _bgr_to_pixmap(bgr, max_w: int = 520, max_h: int = 360) -> QPixmap | None:
    if cv2 is None or bgr is None:
        return None
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
    pix = QPixmap.fromImage(qimg)
    return pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _model_name(task: str, size: str) -> str:
    suffix = {"classify": "-cls", "detect": "", "obb": "-obb", "segment": "-seg"}.get(task, "")
    return f"yolov8{size}{suffix}.pt"


class _NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建自定义工程")
        lay = QFormLayout(self)
        self.ed_name = QLineEdit()
        self.cmb_task = NoWheelComboBox()
        for tid, label in TASK_CN.items():
            self.cmb_task.addItem(label, tid)
        self.cmb_cam = NoWheelComboBox()
        for cam in ("cam1", "cam2", "cam3", "cam4"):
            self.cmb_cam.addItem(cam, cam)
        self.ed_cls = QLineEdit("obj")
        self.ed_cls.setPlaceholderText("逗号分隔，如 left,right")
        lay.addRow("名称", self.ed_name)
        lay.addRow("任务", self.cmb_task)
        lay.addRow("相机", self.cmb_cam)
        lay.addRow("类别", self.ed_cls)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def values(self) -> tuple[str, str, str, list[str]]:
        name = self.ed_name.text().strip() or "custom"
        task = str(self.cmb_task.currentData() or "detect")
        cam = str(self.cmb_cam.currentData() or "cam1")
        classes = [c.strip() for c in self.ed_cls.text().split(",") if c.strip()]
        if task != "classify" and not classes:
            classes = ["obj"]
        return name, task, cam, classes


class DlWorkbench(QWidget):
    """五步工作台，替换原「采图训练」页内容。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._proc: QProcess | None = None
        self._proc_kind = ""
        self._aborted = False
        self._job = ""
        self._last_check = 0.0
        self._burst_left = 0
        self._burst_timer = QTimer(self)
        self._burst_timer.timeout.connect(self._burst_tick)
        self._curve_timer = QTimer(self)
        self._curve_timer.setInterval(2000)
        self._curve_timer.timeout.connect(self._refresh_curve)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        tip = QLabel(
            "采集 → 标注 → 训练 → 验证 → 模型。训练用 Ultralytics，不卡住自动流程。"
            "手眼 / 内参 / 皮带试走请切上方「棋盘格 / 手眼 / 检测测试」。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#1a5276;font-weight:bold;")
        root.addWidget(tip)

        head = QHBoxLayout()
        self.cmb_slot = NoWheelComboBox()
        self.cmb_slot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_slot.setMaximumWidth(280)
        self._reload_slots()
        self.cmb_slot.currentIndexChanged.connect(self._on_slot)
        b_new = QPushButton("新建工程")
        b_hand = QPushButton("手眼标定")
        b_det = QPushButton("检测测试")
        b_cam = QPushButton(f"打开{T.CAM_MONITOR}")
        style_many(
            [(b_new, "success"), (b_hand, "primary"), (b_det, "motion"), (b_cam, "neutral")]
        )
        b_new.clicked.connect(self._new_project)
        b_hand.clicked.connect(lambda: self._goto_workspace_tab("手眼标定"))
        b_det.clicked.connect(lambda: self._goto_workspace_tab("检测测试"))
        b_cam.clicked.connect(self._open_cam_win)
        head.addWidget(QLabel("工程槽位"), 0)
        head.addWidget(self.cmb_slot, 0)
        head.addWidget(b_new, 0)
        head.addStretch(1)
        head.addWidget(b_hand, 0)
        head.addWidget(b_det, 0)
        head.addWidget(b_cam, 0)
        root.addLayout(head)

        self.lbl_meta = QLabel("-")
        self.lbl_meta.setWordWrap(True)
        root.addWidget(self.lbl_meta)

        self.lbl_obb_risk = QLabel(
            "皮带找鞋若仍走 ultralytics_obb360：标准 yolov8-obb 启用前必须在「验证」用 cam1 实图确认。"
        )
        self.lbl_obb_risk.setWordWrap(True)
        self.lbl_obb_risk.setStyleSheet("color:#922b21;font-weight:bold;")
        root.addWidget(self.lbl_obb_risk)

        step_row = QHBoxLayout()
        self._step_group = QButtonGroup(self)
        self._step_group.setExclusive(True)
        self._step_btns: list[QPushButton] = []
        for i, name in enumerate(STEPS):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setMinimumHeight(40)
            btn.clicked.connect(lambda _=False, idx=i: self._set_step(idx))
            self._step_group.addButton(btn, i)
            self._step_btns.append(btn)
            step_row.addWidget(btn, 1)
        root.addLayout(step_row)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_capture())
        self.stack.addWidget(self._build_label())
        self.stack.addWidget(self._build_train())
        self.stack.addWidget(self._build_val())
        self.stack.addWidget(self._build_model())
        root.addWidget(self.stack, 1)

        self.lbl_job = QLabel("状态：空闲")
        self.lbl_job.setWordWrap(True)
        self._set_status("空闲")
        root.addWidget(self.lbl_job)

        log_box = QGroupBox("过程记录")
        log_lay = QVBoxLayout(log_box)
        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setMinimumHeight(90)
        self.txt.setMaximumHeight(160)
        log_lay.addWidget(self.txt)
        root.addWidget(log_box, 0)

        apply_page_chrome(self)
        sc1 = QShortcut(QKeySequence("1"), self)
        sc1.activated.connect(lambda: self._class_hotkey(0))
        sc2 = QShortcut(QKeySequence("2"), self)
        sc2.activated.connect(lambda: self._class_hotkey(1))
        self._set_step(0)
        self._on_slot()
        self._refresh_cuda_label()
        self._log("深度学习工作台已加载。先选槽位，再按五步走。")

    # —— 壳 ——
    def _reload_slots(self) -> None:
        keep = str(self.cmb_slot.currentData() or "") if self.cmb_slot.count() else ""
        self.cmb_slot.blockSignals(True)
        self.cmb_slot.clear()
        for sid, meta in mstore.SLOTS.items():
            tag = meta.get("label") or sid
            if meta.get("custom"):
                tag = f"{tag}（自定义）"
            self.cmb_slot.addItem(str(tag), sid)
        if keep:
            idx = self.cmb_slot.findData(keep)
            if idx >= 0:
                self.cmb_slot.setCurrentIndex(idx)
        self.cmb_slot.blockSignals(False)

    def _slot_id(self) -> str:
        return str(self.cmb_slot.currentData() or "shoe_obb")

    def _meta(self) -> dict[str, Any]:
        return mstore.slot_meta(self._slot_id())

    def _task(self) -> str:
        return mstore.slot_task(self._slot_id())

    def _cls_name(self) -> str:
        return str(self.cmb_cls.currentData() or "")

    def _set_step(self, idx: int) -> None:
        idx = max(0, min(int(idx), len(STEPS) - 1))
        if idx == 1:
            self.label_panel.save_current(quiet=True)
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._step_btns):
            btn.setChecked(i == idx)
            role = "success" if i == idx else "neutral"
            style_many([(btn, role)])
        if idx == 1:
            self.label_panel.set_slot(self._slot_id())
            self.cls_preview.set_slot(self._slot_id())
        elif idx == 2:
            self._load_hparams_ui()
            self._refresh_curve()
        elif idx == 3:
            self._refresh_val_widgets()
        elif idx == 4:
            self._refresh_weights()

    def _on_slot(self, _i: int = 0) -> None:
        sid = self._slot_id()
        meta = self._meta()
        mstore.ensure_dirs(sid)
        task = self._task()
        cam = meta.get("cam") or "-"
        self.lbl_meta.setText(
            f"{meta.get('label', sid)}  任务={TASK_CN.get(task, task)}  相机={cam}  "
            f"{mstore.dataset_counts(sid)}"
        )
        self.lbl_obb_risk.setVisible(sid in ("shoe_obb", "last_obb"))
        keep = self._cls_name()
        self.cmb_cls.blockSignals(True)
        self.cmb_cls.clear()
        classes = list(meta.get("classes") or [])
        if classes:
            self.cmb_cls.show()
            self.lbl_cls_hint.hide()
            for c in classes:
                self.cmb_cls.addItem(mstore.class_label(c), c)
            idx = self.cmb_cls.findData(keep)
            if idx >= 0:
                self.cmb_cls.setCurrentIndex(idx)
        else:
            self.cmb_cls.hide()
            self.lbl_cls_hint.setText("本任务画框，没有分类文件夹；到「标注」圈目标。")
            self.lbl_cls_hint.show()
        self.cmb_cls.blockSignals(False)
        self._fill_class_buttons()
        self.cls_preview.set_slot(sid)
        if mstore.is_classify(sid):
            self.cls_preview.show_path(mstore.newest_train_image(sid, self._cls_name()))
            self.cls_preview.show()
            self.label_panel.hide()
        else:
            self.cls_preview.hide()
            self.label_panel.set_slot(sid)
        self._refresh_counts()
        self._show_last_thumb()
        self._load_hparams_ui()
        self._sync_prod_map()
        self._refresh_weights()
        self._set_busy_enabled()

    def _fill_class_buttons(self) -> None:
        while self.cls_btn_row.count():
            item = self.cls_btn_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        classes = list(self._meta().get("classes") or [])
        if not classes:
            return
        for i, c in enumerate(classes):
            btn = QPushButton(f"{i + 1} {mstore.class_label(c)}")
            style_many([(btn, "primary")])
            btn.clicked.connect(lambda _=False, name=str(c): self._select_class(name))
            self.cls_btn_row.addWidget(btn, 0)
        self.cls_btn_row.addStretch(1)

    def _select_class(self, name: str) -> None:
        idx = self.cmb_cls.findData(name)
        if idx >= 0:
            self.cmb_cls.setCurrentIndex(idx)

    def _class_hotkey(self, index: int) -> None:
        if self.cmb_cls.count() > index:
            self.cmb_cls.setCurrentIndex(index)

    def _refresh_counts(self) -> None:
        sid = self._slot_id()
        self.lbl_ds.setText(mstore.dataset_counts(sid))
        self.lbl_meta.setText(
            f"{self._meta().get('label', sid)}  任务={TASK_CN.get(self._task(), self._task())}  "
            f"相机={self._meta().get('cam')}  {mstore.dataset_counts(sid)}"
        )

    def _show_last_thumb(self) -> None:
        path = mstore.newest_train_image(self._slot_id(), self._cls_name())
        if path is None or cv2 is None:
            self.lbl_thumb.setText("还没有样本图")
            self.lbl_thumb.setPixmap(QPixmap())
            return
        bgr = cv2.imread(str(path))
        pix = _bgr_to_pixmap(bgr)
        if pix is None:
            self.lbl_thumb.setText(path.name)
            return
        self.lbl_thumb.setPixmap(pix)
        self.lbl_thumb.setText("")

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_check < 0.9:
            return
        self._last_check = now
        self._refresh_counts()
        if self._job == "train":
            self._refresh_curve()

    # —— 采集 ——
    def _build_capture(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.cmb_cls = NoWheelComboBox()
        self.cmb_cls.setMinimumContentsLength(6)
        self.cmb_cls.currentIndexChanged.connect(self._on_cls)
        self.lbl_cls_hint = QLabel()
        self.lbl_cls_hint.setWordWrap(True)
        row.addWidget(QLabel("类别"), 0)
        row.addWidget(self.cmb_cls, 0)
        row.addWidget(self.lbl_cls_hint, 1)
        lay.addLayout(row)
        self.cls_btn_row = QHBoxLayout()
        lay.addLayout(self.cls_btn_row)

        r2 = QHBoxLayout()
        self.btn_cap_one = QPushButton("拍 1 张")
        self.btn_burst = QPushButton("连拍")
        self.sp_burst_n = QSpinBox()
        self.sp_burst_n.setRange(2, 80)
        self.sp_burst_n.setValue(10)
        self.sp_burst_ms = QDoubleSpinBox()
        self.sp_burst_ms.setRange(0.2, 8.0)
        self.sp_burst_ms.setValue(0.8)
        self.sp_burst_ms.setSuffix(" s")
        self.chk_val = QCheckBox("写入 val")
        style_many([(self.btn_cap_one, "success"), (self.btn_burst, "primary")])
        self.btn_cap_one.clicked.connect(self._cap_one_clicked)
        self.btn_burst.clicked.connect(self._burst_start)
        r2.addWidget(self.btn_cap_one, 0)
        r2.addWidget(self.btn_burst, 0)
        r2.addWidget(QLabel("张数"), 0)
        r2.addWidget(self.sp_burst_n, 0)
        r2.addWidget(QLabel("间隔"), 0)
        r2.addWidget(self.sp_burst_ms, 0)
        r2.addWidget(self.chk_val, 0)
        r2.addStretch(1)
        lay.addLayout(r2)

        r3 = QHBoxLayout()
        b_imp = QPushButton("从文件夹导入")
        b_snap = QPushButton("从失败快照导入")
        b_files = QPushButton("选图片导入")
        b_ds = QPushButton("打开数据集")
        style_many(
            [(b_imp, "primary"), (b_snap, "warn"), (b_files, "neutral"), (b_ds, "neutral")]
        )
        b_imp.clicked.connect(self._import_folder)
        b_snap.clicked.connect(self._import_snaps)
        b_files.clicked.connect(self._import_files)
        b_ds.clicked.connect(lambda: self._open_dir(mstore.dataset_dir(self._slot_id())))
        for b in (b_imp, b_snap, b_files, b_ds):
            r3.addWidget(b, 0)
        r3.addStretch(1)
        lay.addLayout(r3)

        r4 = QHBoxLayout()
        self.btn_del_last = QPushButton("删除最近一张")
        self.btn_del_cls = QPushButton("删除本类全部图")
        self.btn_del_all = QPushButton("清空本任务数据集")
        style_many(
            [
                (self.btn_del_last, "warn"),
                (self.btn_del_cls, "danger"),
                (self.btn_del_all, "danger"),
            ]
        )
        self.btn_del_last.clicked.connect(self._del_last)
        self.btn_del_cls.clicked.connect(self._del_class)
        self.btn_del_all.clicked.connect(self._del_dataset)
        for b in (self.btn_del_last, self.btn_del_cls, self.btn_del_all):
            r4.addWidget(b, 0)
        r4.addStretch(1)
        lay.addLayout(r4)

        self.lbl_ds = QLabel("-")
        self.lbl_ds.setWordWrap(True)
        lay.addWidget(self.lbl_ds)
        self.lbl_thumb = QLabel("还没有样本图")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setMinimumHeight(180)
        self.lbl_thumb.setStyleSheet("background:#222;color:#aaa;")
        lay.addWidget(self.lbl_thumb, 1)
        return page

    def _on_cls(self, _i: int = 0) -> None:
        if mstore.is_classify(self._slot_id()):
            self.cls_preview.show_path(mstore.newest_train_image(self._slot_id(), self._cls_name()))
        self._show_last_thumb()

    def _cap_one(self, *, jump: bool = False) -> bool:
        sid = self._slot_id()
        cls = self._cls_name()
        if mstore.is_classify(sid) and not cls:
            QMessageBox.warning(self, "采图", "请先选类别（或按 1/2）")
            return False
        try:
            path = mstore.capture_to_slot(
                self.ctx, sid, cls, to_val=bool(self.chk_val.isChecked())
            )
            note = getattr(mstore.capture_to_slot, "last_note", "") or ""
            n = mstore.count_images(path.parent)
            if n % 5 == 0 and not self.chk_val.isChecked():
                if mstore.is_classify(sid):
                    val = path.parent.parent.parent / "val" / path.parent.name
                else:
                    val = mstore.class_dir(sid, "", split="val")
                val.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, val / path.name)
            msg = f"已采 {path.name}"
            if cls:
                msg += f"  类={mstore.class_label(cls)}"
            if note:
                msg += f"\n{note}"
            self._log(msg)
            self._refresh_counts()
            self._show_last_thumb()
            if mstore.is_classify(sid):
                self.cls_preview.show_path(path)
            if jump and not mstore.is_classify(sid):
                self._set_step(1)
                self.label_panel.goto_last()
                self._set_status(f"已拍 1 张，请圈图  {path.name}", kind="ok")
            else:
                extra = "  请把鞋头转到朝上" if sid == "shoe_lr" else ""
                self._set_status(f"已拍 1 张  {path.name}{extra}", kind="ok")
            return True
        except Exception as e:
            self._err("采图", e)
            return False

    def _cap_one_clicked(self) -> None:
        if self._job:
            QMessageBox.information(self, "忙", "请先停止当前训练或安装。")
            return
        self._cap_one(jump=True)

    def _burst_start(self) -> None:
        if self._job or self._burst_left > 0:
            QMessageBox.information(self, "忙", "已有任务在跑。")
            return
        self._burst_left = int(self.sp_burst_n.value())
        self._set_status(f"连拍剩余 {self._burst_left}", kind="run")
        self._burst_tick()
        self._burst_timer.start(int(self.sp_burst_ms.value() * 1000))

    def _burst_tick(self) -> None:
        if self._burst_left <= 0:
            self._burst_timer.stop()
            self._set_status("连拍结束", kind="ok")
            return
        ok = self._cap_one(jump=False)
        self._burst_left -= 1
        if not ok:
            self._burst_left = 0
            self._burst_timer.stop()
            return
        if self._burst_left <= 0:
            self._burst_timer.stop()
            self._set_status("连拍结束", kind="ok")
        else:
            self._set_status(f"连拍剩余 {self._burst_left}", kind="run")

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹", str(ROOT))
        if not folder:
            return
        try:
            n = mstore.import_images_folder(self._slot_id(), Path(folder), self._cls_name())
            self._log(f"导入 {n} 张 ← {folder}")
        except Exception as e:
            self._err("导入", e)
        self._refresh_counts()
        self._show_last_thumb()

    def _import_snaps(self) -> None:
        start = ROOT / "logs" / "vision_snaps"
        start.mkdir(parents=True, exist_ok=True)
        folder = QFileDialog.getExistingDirectory(self, "从运行快照导入", str(start))
        if not folder:
            return
        try:
            n = mstore.import_images_folder(self._slot_id(), Path(folder), self._cls_name())
            self._log(f"从快照导入 {n} 张")
        except Exception as e:
            self._err("导入快照", e)
        self._refresh_counts()
        self._show_last_thumb()

    def _import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", str(ROOT / "logs" / "vision_snaps"), "图像 (*.jpg *.jpeg *.png *.bmp)"
        )
        if not paths:
            return
        try:
            n = mstore.import_image_files(self._slot_id(), [Path(p) for p in paths], self._cls_name())
            self._log(f"导入 {n} 张文件")
        except Exception as e:
            self._err("导入文件", e)
        self._refresh_counts()
        self._show_last_thumb()

    def _del_last(self) -> None:
        if self._job:
            return
        path = mstore.newest_train_image(self._slot_id(), self._cls_name())
        if path is None:
            QMessageBox.information(self, "删除", "当前类没有可删的图")
            return
        if not self._confirm("删除最近一张", f"删除：\n{path}"):
            return
        removed = mstore.delete_image_pair(path)
        self._log("已删除:\n" + "\n".join(str(p) for p in removed))
        self._after_dataset_change()

    def _del_class(self) -> None:
        if self._job:
            return
        sid = self._slot_id()
        cls = self._cls_name() or "全部图"
        n = mstore.count_images(mstore.class_dir(sid, self._cls_name(), split="train"))
        n += mstore.count_images(mstore.class_dir(sid, self._cls_name(), split="val"))
        if n <= 0:
            QMessageBox.information(self, "删除", "本类没有图")
            return
        if not self._confirm("删除本类全部图", f"将删除「{cls}」共 {n} 张。确定？"):
            return
        got = mstore.delete_class_images(sid, self._cls_name())
        self._log(f"已删除本类 {got} 张")
        self._after_dataset_change()

    def _del_dataset(self) -> None:
        if self._job:
            return
        sid = self._slot_id()
        label = self._meta()["label"]
        if not self._confirm("清空本任务数据集", f"将删除「{label}」全部训练图。不删 .pt。确定？"):
            return
        n = mstore.delete_dataset_images(sid)
        self._log(f"已清空 {label} 共 {n} 张")
        self._after_dataset_change()

    def _after_dataset_change(self) -> None:
        self._refresh_counts()
        self._show_last_thumb()
        self.cls_preview.set_slot(self._slot_id())
        if not mstore.is_classify(self._slot_id()):
            self.label_panel.reload()

    # —— 标注 ——
    def _build_label(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.cls_preview = ClassifyPreviewPanel()
        self.cls_preview.status.connect(self._log)
        lay.addWidget(self.cls_preview)
        self.label_panel = LabelCanvasPanel()
        self.label_panel.status.connect(self._log)
        lay.addWidget(self.label_panel, 1)
        return page

    # —— 训练 ——
    def _build_train(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.lbl_task = QLabel("-")
        self.cmb_size = NoWheelComboBox()
        for sz in ("n", "s", "m"):
            self.cmb_size.addItem(f"{sz}（yolov8{sz}）", sz)
        self.chk_ft = QCheckBox("从当前生产权重微调")
        self.chk_resume = QCheckBox("从 last.pt 继续")
        self.sp_ep = QSpinBox()
        self.sp_ep.setRange(1, 400)
        self.sp_ep.setValue(40)
        self.sp_imgsz = QSpinBox()
        self.sp_imgsz.setRange(32, 1280)
        self.sp_imgsz.setValue(640)
        self.sp_batch = QSpinBox()
        self.sp_batch.setRange(1, 64)
        self.sp_batch.setValue(8)
        self.cmb_dev = NoWheelComboBox()
        self.cmb_dev.addItem("CPU（不加GPU）", "cpu")
        self.cmb_dev.addItem("GPU（CUDA:0）", "0")
        self.cmb_dev.addItem("双GPU（0,1）", "0,1")
        self.sp_pat = QSpinBox()
        self.sp_pat.setRange(0, 200)
        self.sp_pat.setValue(20)
        form.addRow("任务", self.lbl_task)
        form.addRow("模型规格", self.cmb_size)
        form.addRow("", self.chk_ft)
        form.addRow("", self.chk_resume)
        form.addRow("迭代次数 epochs", self.sp_ep)
        form.addRow("图像尺寸 imgsz", self.sp_imgsz)
        form.addRow("批次 batch", self.sp_batch)
        form.addRow("训练设备", self.cmb_dev)
        form.addRow("早停 patience", self.sp_pat)
        lay.addLayout(form)
        self.lbl_cuda = QLabel("")
        self.lbl_cuda.setWordWrap(True)
        lay.addWidget(self.lbl_cuda)

        self.chk_adv = QCheckBox("显示高级参数")
        self.chk_adv.toggled.connect(self._toggle_adv)
        lay.addWidget(self.chk_adv)
        self.adv = QWidget()
        adv = QFormLayout(self.adv)
        self.sp_lr0 = QDoubleSpinBox()
        self.sp_lr0.setDecimals(5)
        self.sp_lr0.setRange(1e-6, 1.0)
        self.sp_lr0.setValue(0.01)
        self.sp_lrf = QDoubleSpinBox()
        self.sp_lrf.setDecimals(5)
        self.sp_lrf.setRange(1e-6, 1.0)
        self.sp_lrf.setValue(0.01)
        self.cmb_opt = NoWheelComboBox()
        for name in ("auto", "SGD", "Adam", "AdamW"):
            self.cmb_opt.addItem(name, name)
        self.chk_cos = QCheckBox("余弦学习率")
        self.sp_freeze = QSpinBox()
        self.sp_freeze.setRange(0, 200)
        self.sp_workers = QSpinBox()
        self.sp_workers.setRange(0, 16)
        self.sp_workers.setValue(2)
        self.sp_seed = QSpinBox()
        self.sp_seed.setRange(0, 99999)
        self.sp_hsv_h = QDoubleSpinBox()
        self.sp_hsv_s = QDoubleSpinBox()
        self.sp_hsv_v = QDoubleSpinBox()
        self.sp_deg = QDoubleSpinBox()
        self.sp_trans = QDoubleSpinBox()
        self.sp_scale = QDoubleSpinBox()
        self.sp_fliplr = QDoubleSpinBox()
        self.sp_flipud = QDoubleSpinBox()
        self.sp_mosaic = QDoubleSpinBox()
        self.sp_mixup = QDoubleSpinBox()
        self.sp_close_mosaic = QSpinBox()
        self.sp_close_mosaic.setRange(0, 100)
        for sp in (
            self.sp_hsv_h,
            self.sp_hsv_s,
            self.sp_hsv_v,
            self.sp_deg,
            self.sp_trans,
            self.sp_scale,
            self.sp_fliplr,
            self.sp_flipud,
            self.sp_mosaic,
            self.sp_mixup,
        ):
            sp.setRange(0.0, 1.0 if sp is not self.sp_deg else 180.0)
            sp.setDecimals(3)
        self.sp_deg.setRange(0.0, 180.0)
        adv.addRow("lr0", self.sp_lr0)
        adv.addRow("lrf", self.sp_lrf)
        adv.addRow("optimizer", self.cmb_opt)
        adv.addRow("", self.chk_cos)
        adv.addRow("freeze", self.sp_freeze)
        adv.addRow("workers", self.sp_workers)
        adv.addRow("seed", self.sp_seed)
        adv.addRow("hsv_h", self.sp_hsv_h)
        adv.addRow("hsv_s", self.sp_hsv_s)
        adv.addRow("hsv_v", self.sp_hsv_v)
        adv.addRow("degrees", self.sp_deg)
        adv.addRow("translate", self.sp_trans)
        adv.addRow("scale", self.sp_scale)
        adv.addRow("fliplr", self.sp_fliplr)
        adv.addRow("flipud", self.sp_flipud)
        adv.addRow("mosaic", self.sp_mosaic)
        adv.addRow("mixup", self.sp_mixup)
        adv.addRow("close_mosaic", self.sp_close_mosaic)
        self.adv.hide()
        lay.addWidget(self.adv)

        r = QHBoxLayout()
        self.btn_train = QPushButton("开始训练")
        self.btn_train_stop = QPushButton("终止训练")
        style_many([(self.btn_train, "success"), (self.btn_train_stop, "danger")])
        self.btn_train.clicked.connect(self._train)
        self.btn_train_stop.clicked.connect(self._stop_proc)
        r.addWidget(self.btn_train, 0)
        r.addWidget(self.btn_train_stop, 0)
        r.addStretch(1)
        lay.addLayout(r)
        self.curve = TrainCurveWidget()
        lay.addWidget(self.curve, 1)
        return page

    def _toggle_adv(self, on: bool) -> None:
        self.adv.setVisible(bool(on))

    def _load_hparams_ui(self) -> None:
        sid = self._slot_id()
        hp = uhp.load_hparams(sid, task=self._task())
        self.lbl_task.setText(TASK_CN.get(self._task(), self._task()))
        model = str(hp.get("model") or "")
        size = "n"
        for sz in ("n", "s", "m"):
            if f"yolov8{sz}" in model:
                size = sz
                break
        idx = self.cmb_size.findData(size)
        if idx >= 0:
            self.cmb_size.setCurrentIndex(idx)
        inst = ROOT / str(self._meta().get("install") or "")
        self.chk_ft.setChecked(bool(inst.is_file() and Path(model).name == inst.name))
        self.chk_resume.setChecked(bool(hp.get("resume")))
        self.sp_ep.setValue(int(hp.get("epochs") or 40))
        self.sp_imgsz.setValue(int(hp.get("imgsz") or 640))
        self.sp_batch.setValue(int(hp.get("batch") or 8))
        dev = str(hp.get("device") or "cpu")
        di = self.cmb_dev.findData(dev if dev != "0" else "0")
        if di < 0:
            di = 0
        self.cmb_dev.setCurrentIndex(di)
        self.sp_pat.setValue(int(hp.get("patience") or 20))
        self.sp_lr0.setValue(float(hp.get("lr0") or 0.01))
        self.sp_lrf.setValue(float(hp.get("lrf") or 0.01))
        oi = self.cmb_opt.findData(str(hp.get("optimizer") or "auto"))
        self.cmb_opt.setCurrentIndex(max(0, oi))
        self.chk_cos.setChecked(bool(hp.get("cos_lr")))
        self.sp_freeze.setValue(int(hp.get("freeze") or 0))
        self.sp_workers.setValue(int(hp.get("workers") or 2))
        self.sp_seed.setValue(int(hp.get("seed") or 0))
        self.sp_hsv_h.setValue(float(hp.get("hsv_h") or 0.015))
        self.sp_hsv_s.setValue(float(hp.get("hsv_s") or 0.7))
        self.sp_hsv_v.setValue(float(hp.get("hsv_v") or 0.4))
        self.sp_deg.setValue(float(hp.get("degrees") or 0.0))
        self.sp_trans.setValue(float(hp.get("translate") or 0.1))
        self.sp_scale.setValue(float(hp.get("scale") or 0.5))
        self.sp_fliplr.setValue(float(hp.get("fliplr") or 0.5))
        self.sp_flipud.setValue(float(hp.get("flipud") or 0.0))
        self.sp_mosaic.setValue(float(hp.get("mosaic") or 0.0))
        self.sp_mixup.setValue(float(hp.get("mixup") or 0.0))
        self.sp_close_mosaic.setValue(int(hp.get("close_mosaic") or 10))

    def _collect_hparams(self) -> dict[str, Any]:
        task = self._task()
        hp = uhp.load_hparams(self._slot_id(), task=task)
        size = str(self.cmb_size.currentData() or "n")
        model = _model_name(task, size)
        inst = ROOT / str(self._meta().get("install") or "")
        if self.chk_ft.isChecked() and inst.is_file():
            model = str(inst)
        hp.update(
            {
                "task": task,
                "model": model,
                "epochs": int(self.sp_ep.value()),
                "imgsz": int(self.sp_imgsz.value()),
                "batch": int(self.sp_batch.value()),
                "device": mstore.normalize_train_device(str(self.cmb_dev.currentData() or "cpu")),
                "patience": int(self.sp_pat.value()),
                "resume": bool(self.chk_resume.isChecked()),
                "pretrained": True,
                "lr0": float(self.sp_lr0.value()),
                "lrf": float(self.sp_lrf.value()),
                "optimizer": str(self.cmb_opt.currentData() or "auto"),
                "cos_lr": bool(self.chk_cos.isChecked()),
                "freeze": int(self.sp_freeze.value()),
                "workers": int(self.sp_workers.value()),
                "seed": int(self.sp_seed.value()),
                "hsv_h": float(self.sp_hsv_h.value()),
                "hsv_s": float(self.sp_hsv_s.value()),
                "hsv_v": float(self.sp_hsv_v.value()),
                "degrees": float(self.sp_deg.value()),
                "translate": float(self.sp_trans.value()),
                "scale": float(self.sp_scale.value()),
                "fliplr": float(self.sp_fliplr.value()),
                "flipud": float(self.sp_flipud.value()),
                "mosaic": float(self.sp_mosaic.value()),
                "mixup": float(self.sp_mixup.value()),
                "close_mosaic": int(self.sp_close_mosaic.value()),
                "plots": True,
            }
        )
        return hp

    def _train(self) -> None:
        if self._job:
            QMessageBox.information(self, "忙", "请先停止当前训练或安装。")
            return
        sid = self._slot_id()
        meta = self._meta()
        hp = self._collect_hparams()
        device = str(hp["device"])
        device_label = str(self.cmb_dev.currentText() or "CPU")
        if mstore.is_gpu_train_device(device):
            cuda = mstore.cuda_train_status()
            if not cuda.get("cuda"):
                r = QMessageBox.question(
                    self,
                    "GPU 不可用",
                    f"{cuda.get('message', '')}\n仍要用 GPU 启动吗？（多半会失败）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if r != QMessageBox.StandardButton.Yes:
                    return
        task = self._task()
        if task != "classify":
            from vision import label_io as lio

            n_all = len(lio.list_slot_images(sid))
            n_lab = sum(1 for p in lio.list_slot_images(sid) if lio.is_labeled(p))
            if n_all == 0 or n_lab == 0:
                QMessageBox.warning(
                    self, "还没圈图", f"当前已圈 {n_lab}/{n_all}。请到「标注」圈目标后再训。"
                )
                return
            if not self._confirm(
                "开始训练",
                f"已圈 {n_lab}/{n_all}。epochs={hp['epochs']}  设备={device_label}\n开始？",
            ):
                return
        else:
            missing = []
            for c in meta.get("classes") or []:
                ntr = mstore.count_images(mstore.class_dir(sid, str(c), split="train"))
                if ntr < 1:
                    missing.append(mstore.class_label(c))
            if missing:
                QMessageBox.warning(self, "图不够", "还缺：" + "、".join(missing))
                return
            if not self._confirm(
                "开始训练",
                f"任务={meta['label']}\n{mstore.dataset_counts(sid)}\n"
                f"epochs={hp['epochs']}  {device_label}\n开始？",
            ):
                return
        self.label_panel.save_current(quiet=True)
        cmd = mstore.train_cmd(
            sid,
            epochs=int(hp["epochs"]),
            device=device,
            batch=int(hp["batch"]),
            hparams=hp,
        )
        self._log(f"开始训练: {' '.join(cmd)}")
        self._job = "train"
        self._set_status(f"训练中 {meta['label']}  epochs={hp['epochs']}  {device_label}", kind="run")
        self._curve_timer.start()
        self._start_proc(cmd, kind="train")

    def _refresh_curve(self) -> None:
        self.curve.load_csv(mstore.results_csv_path(self._slot_id()))

    # —— 验证 ——
    def _build_val(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        r = QHBoxLayout()
        self.btn_pred = QPushButton("当前预览推理")
        self.btn_val = QPushButton("跑 val 集")
        self.btn_batch = QPushButton("文件夹批量")
        style_many(
            [(self.btn_pred, "motion"), (self.btn_val, "primary"), (self.btn_batch, "neutral")]
        )
        self.btn_pred.clicked.connect(self._predict_preview)
        self.btn_val.clicked.connect(self._run_val)
        self.btn_batch.clicked.connect(self._batch_folder)
        for b in (self.btn_pred, self.btn_val, self.btn_batch):
            r.addWidget(b, 0)
        r.addStretch(1)
        lay.addLayout(r)
        self.lbl_val = QLabel("验证指标：-")
        self.lbl_val.setWordWrap(True)
        lay.addWidget(self.lbl_val)
        self.lbl_pred = QLabel("推理叠图会显示在这里")
        self.lbl_pred.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pred.setMinimumHeight(200)
        self.lbl_pred.setStyleSheet("background:#222;color:#aaa;")
        lay.addWidget(self.lbl_pred, 1)
        self.lbl_cm = QLabel("混淆矩阵：训练/验证后若有图会显示")
        self.lbl_cm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cm.setMinimumHeight(140)
        lay.addWidget(self.lbl_cm)
        self.lst_batch = QListWidget()
        self.lst_batch.setMaximumHeight(100)
        self.lst_batch.itemClicked.connect(self._show_batch_item)
        lay.addWidget(self.lst_batch)
        return page

    def _refresh_val_widgets(self) -> None:
        path = ROOT / "runs" / "val_last.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                bits = [f"{k}={v}" for k, v in data.items() if k not in ("weights", "save_dir")]
                self.lbl_val.setText("验证指标：" + ("  ".join(bits) if bits else str(data)))
            except Exception:
                pass
        cm = mstore.confusion_matrix_path(self._slot_id())
        if cm is not None and cm.is_file():
            pix = QPixmap(str(cm)).scaled(
                420, 280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_cm.setPixmap(pix)
            self.lbl_cm.setText("")
        else:
            self.lbl_cm.setPixmap(QPixmap())
            self.lbl_cm.setText("尚无混淆矩阵图（先跑 val）")

    def _weights_for_val(self) -> Path | None:
        rows = mstore.list_slot_weights(self._slot_id())
        if not rows:
            return None
        return rows[0][1]

    def _predict_preview(self) -> None:
        if self._busy():
            return
        w = self._weights_for_val()
        if w is None:
            QMessageBox.warning(self, "验证", "没有权重。请先训练或到「模型」导入。")
            return
        try:
            from vision.cls_crop import grab_slot_image

            img = grab_slot_image(self.ctx, str(self._meta().get("cam") or "cam1"))
            tmp = mstore.dataset_dir(self._slot_id()) / "_preview.jpg"
            mstore.save_bgr(tmp, img)
        except Exception as e:
            self._err("取预览图", e)
            return
        out = ROOT / "runs" / "predict" / self._slot_id()
        cmd = [
            __import__("sys").executable,
            "-u",
            str(mstore.RUNNER),
            "predict",
            "--weights",
            str(w),
            "--source",
            str(tmp),
            "--out",
            str(out / "frame.jpg"),
        ]
        self._job = "predict"
        self._set_status("预览推理中…", kind="run")
        self._start_proc(cmd, kind="predict")

    def _run_val(self) -> None:
        if self._busy():
            return
        sid = self._slot_id()
        w = self._weights_for_val()
        cmd = mstore.val_cmd(sid, w)
        self._job = "val"
        self._set_status("正在 val…", kind="run")
        self._log(" ".join(cmd))
        self._start_proc(cmd, kind="val")

    def _batch_folder(self) -> None:
        if self._busy():
            return
        w = self._weights_for_val()
        if w is None:
            QMessageBox.warning(self, "批量", "没有权重")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择待测文件夹", str(ROOT))
        if not folder:
            return
        out = ROOT / "runs" / "predict" / self._slot_id()
        cmd = [
            __import__("sys").executable,
            "-u",
            str(mstore.RUNNER),
            "predict",
            "--weights",
            str(w),
            "--source",
            folder,
            "--out",
            str(out / "batch.jpg"),
        ]
        self._job = "predict"
        self._set_status("批量推理中…", kind="run")
        self._start_proc(cmd, kind="predict")

    def _show_pred_dir(self, folder: Path) -> None:
        self.lst_batch.clear()
        if not folder.is_dir():
            return
        files = sorted(folder.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0)
        imgs = [p for p in files if p.is_file() and p.suffix.lower() in mstore.IMAGE_EXTS]
        for p in imgs[-80:]:
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.lst_batch.addItem(item)
        if imgs:
            self._show_image_file(imgs[-1])

    def _show_batch_item(self, item: QListWidgetItem) -> None:
        path = Path(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        if path.is_file():
            self._show_image_file(path)

    def _show_image_file(self, path: Path) -> None:
        if cv2 is None:
            return
        bgr = cv2.imread(str(path))
        pix = _bgr_to_pixmap(bgr, 560, 400)
        if pix is not None:
            self.lbl_pred.setPixmap(pix)
            self.lbl_pred.setText("")

    # —— 模型 ——
    def _build_model(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        r0 = QHBoxLayout()
        b_stack = QPushButton("检查YOLO环境")
        b_link = QPushButton("挂接旧模型")
        b_one = QPushButton("一键从旧工程")
        b_pip = QPushButton("安装 ultralytics")
        b_models = QPushButton("打开 models/")
        style_many(
            [
                (b_stack, "neutral"),
                (b_link, "success"),
                (b_one, "success"),
                (b_pip, "warn"),
                (b_models, "neutral"),
            ]
        )
        b_stack.clicked.connect(self._check_stack)
        b_link.clicked.connect(self._link_legacy)
        b_one.clicked.connect(self._import_legacy_project)
        b_pip.clicked.connect(self._pip_ultra)
        b_models.clicked.connect(lambda: self._open_dir(ROOT / "models"))
        for b in (b_stack, b_link, b_one, b_pip, b_models):
            r0.addWidget(b, 0)
        r0.addStretch(1)
        lay.addLayout(r0)

        r1 = QHBoxLayout()
        b_pt = QPushButton("选用已有.pt写入配置")
        b_copy = QPushButton("拷到 models/ 并写入")
        b_iw = QPushButton("导入权重")
        b_id = QPushButton("导入数据集")
        b_ir = QPushButton("导入 runs")
        style_many(
            [
                (b_pt, "primary"),
                (b_copy, "success"),
                (b_iw, "neutral"),
                (b_id, "neutral"),
                (b_ir, "neutral"),
            ]
        )
        b_pt.clicked.connect(lambda: self._install_pt(copy=False))
        b_copy.clicked.connect(lambda: self._install_pt(copy=True))
        b_iw.clicked.connect(self._import_weights)
        b_id.clicked.connect(self._import_dataset)
        b_ir.clicked.connect(self._import_runs)
        for b in (b_pt, b_copy, b_iw, b_id, b_ir):
            r1.addWidget(b, 0)
        r1.addStretch(1)
        lay.addLayout(r1)

        self.lst_w = QListWidget()
        self.lst_w.setMinimumHeight(120)
        lay.addWidget(self.lst_w)

        r2 = QHBoxLayout()
        self.cmb_prod = NoWheelComboBox()
        for sid, meta in mstore.SLOTS.items():
            if sid in mstore.BUILTIN_SLOT_IDS:
                self.cmb_prod.addItem(str(meta["label"]), sid)
        self.btn_enable = QPushButton("启用到产线")
        self.btn_onnx = QPushButton("导出 ONNX")
        style_many([(self.btn_enable, "success"), (self.btn_onnx, "primary")])
        self.btn_enable.clicked.connect(self._enable_line)
        self.btn_onnx.clicked.connect(self._export_onnx)
        r2.addWidget(QLabel("映射产线槽位"), 0)
        r2.addWidget(self.cmb_prod, 0)
        r2.addWidget(self.btn_enable, 0)
        r2.addWidget(self.btn_onnx, 0)
        r2.addStretch(1)
        lay.addLayout(r2)
        note = QLabel(
            "导入权重/数据集/runs 不会改产线。确认「验证」叠图后再点「启用到产线」。"
            "自定义工程必须映射到现有 6 个产线槽位之一才会改 Station。"
            "皮带 OBB 若来自 ultralytics_obb360，必须用 cam1 实图验证。"
            "挂接旧模型只补缺失软链，不覆盖已有 custom_*.pt。"
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return page

    def _sync_prod_map(self) -> None:
        sid = self._slot_id()
        idx = self.cmb_prod.findData(sid if sid in mstore.BUILTIN_SLOT_IDS else "shoe_obb")
        if idx >= 0:
            self.cmb_prod.setCurrentIndex(idx)
        custom = bool(self._meta().get("custom"))
        self.cmb_prod.setEnabled(custom or True)

    def _refresh_weights(self) -> None:
        self.lst_w.clear()
        for label, path in mstore.list_slot_weights(self._slot_id()):
            item = QListWidgetItem(f"{label}  {mstore.relpath(path)}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.lst_w.addItem(item)
        if self.lst_w.count() == 0:
            self.lst_w.addItem("（还没有权重）")

    def _selected_weight(self) -> Path | None:
        item = self.lst_w.currentItem()
        if item is None:
            rows = mstore.list_slot_weights(self._slot_id())
            return rows[0][1] if rows else None
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            rows = mstore.list_slot_weights(self._slot_id())
            return rows[0][1] if rows else None
        p = Path(str(raw))
        return p if p.is_file() else None

    def _enable_line(self) -> None:
        src = self._selected_weight()
        if src is None:
            QMessageBox.warning(self, "启用", "请先选一个 .pt")
            return
        self._enable_weight(src)

    def _enable_weight(self, src: Path) -> bool:
        dest = str(self.cmb_prod.currentData() or self._slot_id())
        if dest not in mstore.BUILTIN_SLOT_IDS:
            QMessageBox.warning(self, "启用", "请映射到产线 6 槽之一")
            return False
        if dest in ("shoe_obb", "last_obb"):
            if not self._confirm(
                "皮带 OBB 风险",
                "若产线仍用 ultralytics_obb360，标准 yolov8-obb 可能对不上。"
                "请先在「验证」用 cam1 实图确认。仍要写入产线配置吗？",
            ):
                return False
        try:
            rel = mstore.bind_model(self.ctx, dest, src, copy_to_default=True)
            self._log(f"已启用 {mstore.SLOTS[dest]['label']} → {rel}")
            QMessageBox.information(self, "已写入", f"产线槽位已指向 {rel}")
        except Exception as e:
            self._err("启用到产线", e)
            self._refresh_weights()
            return False
        self._refresh_weights()
        return True

    def _ask_enable_now(self, src: Path) -> None:
        if not src.is_file():
            return
        if self._confirm(
            "立即启用到产线？",
            f"已导入：{src.name}\n"
            f"将写入映射槽位「{self.cmb_prod.currentText()}」。\n"
            "建议先到「验证」看叠图；若现在就改产线配置请选是。",
        ):
            self._enable_weight(src)

    def _pick_legacy_path(self, title: str) -> Path | None:
        default = mstore.DEFAULT_LEGACY_ROOT
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(
            f"默认旧工程：\n{default}\n\n"
            "可选工程根目录或其中的 models/。"
        )
        b_def = box.addButton("用默认路径", QMessageBox.ButtonRole.AcceptRole)
        b_pick = box.addButton("选择目录", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_def:
            return default
        if clicked is b_pick:
            start = str(default if default.exists() else ROOT)
            folder = QFileDialog.getExistingDirectory(self, "选择旧工程或 models/", start)
            return Path(folder) if folder else None
        return None

    def _export_onnx(self) -> None:
        if self._busy():
            return
        src = self._selected_weight()
        if src is None:
            QMessageBox.warning(self, "ONNX", "请先选权重")
            return
        cmd = mstore.export_cmd(src, fmt="onnx")
        self._job = "export"
        self._set_status("正在导出 ONNX…", kind="run")
        self._start_proc(cmd, kind="export")

    def _install_pt(self, *, copy: bool) -> None:
        sid = self._slot_id()
        path, _ = QFileDialog.getOpenFileName(self, "选择 .pt", str(ROOT / "models"), "YOLO (*.pt)")
        if not path:
            return
        try:
            rel = mstore.bind_model(self.ctx, sid, Path(path), copy_to_default=copy)
            self._log(f"{self._meta()['label']} → {rel}")
            QMessageBox.information(self, "已写入", f"已指向 {rel}")
        except Exception as e:
            self._err("安装模型", e)
        self._refresh_weights()

    def _import_weights(self) -> None:
        start = str(mstore.DEFAULT_LEGACY_ROOT) if mstore.DEFAULT_LEGACY_ROOT.exists() else str(ROOT)
        box = QMessageBox(self)
        box.setWindowTitle("导入权重")
        box.setText("选一个 .pt，或选含 best.pt / last.pt 的 runs 目录。不会自动改产线配置。")
        b_file = box.addButton("选 .pt 文件", QMessageBox.ButtonRole.AcceptRole)
        b_dir = box.addButton("选 runs 目录", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        src: Path | None = None
        if clicked is b_file:
            path, _ = QFileDialog.getOpenFileName(self, "导入权重", start, "YOLO (*.pt)")
            src = Path(path) if path else None
        elif clicked is b_dir:
            folder = QFileDialog.getExistingDirectory(self, "导入 runs / 权重目录", start)
            src = Path(folder) if folder else None
        if src is None:
            return
        try:
            msg = mstore.import_weights_bundle(self._slot_id(), src, copy=True)
            self._log(msg)
            self._refresh_weights()
            self._refresh_curve()
            inst = ROOT / str(self._meta().get("install") or "")
            if inst.is_file():
                self._ask_enable_now(inst)
        except Exception as e:
            self._err("导入权重", e)
        self._refresh_weights()

    def _import_dataset(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "导入 YOLO 数据集目录", str(ROOT))
        if not folder:
            return
        src = Path(folder)
        warn = mstore.dataset_class_warnings(self._slot_id(), src)
        if warn and not self._confirm("类名可能对不上", warn + "\n\n仍要导入（不自动改名）？"):
            return
        try:
            msg = mstore.import_dataset(self._slot_id(), src)
            self._log(msg)
            if warn:
                QMessageBox.information(self, "已导入（请核对类名）", msg[-1200:])
        except Exception as e:
            self._err("导入数据集", e)
        self._refresh_counts()

    def _import_runs(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "导入 Ultralytics runs 目录", str(ROOT / "runs"))
        if not folder:
            return
        try:
            msg = mstore.import_runs(self._slot_id(), Path(folder))
            self._log(msg)
            QMessageBox.information(
                self,
                "已导入 runs",
                msg + "\n可在「训练」勾选从 last.pt 继续，或到「验证」看叠图。",
            )
        except Exception as e:
            self._err("导入 runs", e)
        self._refresh_weights()
        self._refresh_curve()

    def _import_legacy_project(self) -> None:
        path = self._pick_legacy_path("一键从旧工程")
        if path is None:
            return
        try:
            info = mstore.peek_legacy_project(path)
        except Exception as e:
            self._err("旧工程", e)
            return
        ds = info.get("datasets")
        runs = info.get("runs")
        copy_ds = False
        copy_runs = False
        if isinstance(ds, Path):
            copy_ds = self._confirm(
                "拷入 datasets？",
                f"发现 {ds}\n将按槽位目录名拷进本仓库 datasets/<槽>/（不改产线配置）。",
            )
        if isinstance(runs, Path):
            copy_runs = self._confirm(
                "拷入 runs？",
                f"发现 {runs}\n将按槽位拷进 runs/<任务>/<槽>/，便于看曲线、从 last.pt 续训。",
            )
        try:
            msg = mstore.import_legacy_project(
                path, copy_datasets=copy_ds, copy_runs=copy_runs
            )
            self._log(msg)
            QMessageBox.information(
                self,
                "一键导入完成",
                "已挂接缺失的旧 .pt 软链（不覆盖 custom_*.pt）。\n"
                "请到「验证」用当前相机看叠图，再点「启用到产线」。\n\n"
                + msg[-900:],
            )
        except Exception as e:
            self._err("一键从旧工程", e)
        self._refresh_weights()
        self._refresh_counts()
        self._refresh_curve()

    def _link_legacy(self) -> None:
        path = self._pick_legacy_path("挂接旧模型")
        if path is None:
            return
        try:
            out = mstore.link_legacy_models(str(path), only_missing=True)
            self._log(out)
            QMessageBox.information(self, "挂接旧模型", out[-800:] if len(out) > 800 else out)
        except Exception as e:
            self._err("挂接失败", e)
        self._refresh_weights()

    def _check_stack(self) -> None:
        from vision.legacy_pipeline import listed_model_paths, stack_status

        st = stack_status()
        models = "\n".join(
            f"{'✓' if p.exists() else '✗'} {n}: {p}"
            for n, p in listed_model_paths(self.ctx.cfg.get("vision") or {})
        )
        cuda = mstore.cuda_train_status()
        self._refresh_cuda_label()
        self._log(f"{st.get('message','')}\n{cuda.get('message','')}\n{models}")

    def _new_project(self) -> None:
        dlg = _NewProjectDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, task, cam, classes = dlg.values()
        try:
            sid = mstore.add_custom_project(label=name, task=task, cam=cam, classes=classes)
            self._reload_slots()
            idx = self.cmb_slot.findData(sid)
            if idx >= 0:
                self.cmb_slot.setCurrentIndex(idx)
            self._log(f"已建自定义工程 {sid}，进产线请在「模型」映射到 6 槽之一。")
        except Exception as e:
            self._err("新建工程", e)

    def _pip_ultra(self) -> None:
        if self._busy():
            return
        box = QMessageBox(self)
        box.setWindowTitle("安装 ultralytics / torch")
        box.setText("选择安装 CPU 版还是 GPU 版 PyTorch（需联网）。")
        btn_cpu = box.addButton("安装 CPU 版", QMessageBox.ButtonRole.AcceptRole)
        btn_gpu = box.addButton("安装 GPU 版", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (btn_cpu, btn_gpu):
            return
        self._job = "pip"
        self._set_status("正在安装 ultralytics / torch…", kind="run")
        self._start_proc(mstore.pip_ultralytics_cmd(with_cuda=clicked is btn_gpu), kind="pip")

    def _refresh_cuda_label(self) -> None:
        st = mstore.cuda_train_status()
        msg = str(st.get("message") or "")
        if st.get("cuda"):
            color = "#145a32"
        elif st.get("torch_ok"):
            color = "#7e5103"
        else:
            color = "#922b21"
        self.lbl_cuda.setText(f"GPU训练环境：{msg}")
        self.lbl_cuda.setStyleSheet(f"color:{color};padding:2px 0;")

    # —— 进程 ——
    def _set_status(self, text: str, *, kind: str = "idle") -> None:
        colors = {
            "idle": ("#d5f5e3", "#145a32"),
            "run": ("#fdebd0", "#7e5103"),
            "ok": ("#d5f5e3", "#145a32"),
            "fail": ("#fadbd8", "#922b21"),
        }
        bg, fg = colors.get(kind, colors["idle"])
        self.lbl_job.setText(f"状态：{text}")
        self.lbl_job.setStyleSheet(
            f"background:{bg};color:{fg};padding:8px;border-radius:4px;font-weight:bold;"
        )
        self._set_busy_enabled()

    def _set_busy_enabled(self) -> None:
        training = self._job in ("train", "pip", "val", "export", "predict")
        for w in (
            self.btn_cap_one,
            self.btn_burst,
            self.btn_train,
            self.btn_del_last,
            self.btn_del_cls,
            self.btn_del_all,
            self.cmb_slot,
            self.cmb_dev,
            self.sp_ep,
            self.btn_pred,
            self.btn_val,
            self.btn_batch,
            self.btn_enable,
            self.btn_onnx,
        ):
            w.setEnabled(not training)
        self.btn_train_stop.setEnabled(training)
        if list(self._meta().get("classes") or []):
            self.cmb_cls.setEnabled(not training)

    def _busy(self) -> bool:
        if self._job:
            QMessageBox.information(self, "忙", "已有训练/验证/安装在跑，请先停止或等结束。")
            return True
        return False

    def _start_proc(self, cmd: list[str], *, kind: str) -> None:
        self._aborted = False
        self._proc_kind = kind
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self._proc.setProcessEnvironment(env)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_proc_out)
        self._proc.finished.connect(self._on_proc_done)
        self._proc.start(cmd[0], cmd[1:])
        if not self._proc.waitForStarted(5000):
            self._log("进程未能启动")
            self._proc = None
            self._job = ""
            self._set_status("启动失败", kind="fail")

    def _on_proc_out(self) -> None:
        if self._proc is None:
            return
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        text = raw.strip()
        if not text:
            return
        self._log(text)
        if self._job == "train":
            line = text.splitlines()[-1]
            if len(line) > 80:
                line = line[-80:]
            self.lbl_job.setText(f"状态：训练中  {line}")

    def _on_proc_done(self, code: int, _st) -> None:
        kind = self._proc_kind
        aborted = self._aborted
        self._proc = None
        self._proc_kind = ""
        self._job = ""
        self._curve_timer.stop()
        sid = self._slot_id()
        if aborted:
            self._set_status("已终止", kind="fail")
            self._log("已终止")
            return
        if kind == "train":
            self._refresh_curve()
            if code == 0:
                extra = ""
                try:
                    inst = ROOT / str(self._meta().get("install") or "")
                    if inst.is_file() and sid in mstore.BUILTIN_SLOT_IDS:
                        rel = mstore.bind_model(self.ctx, sid, inst, copy_to_default=False)
                        extra = f"\n已写入配置：{rel}"
                except Exception as e:
                    extra = f"\n配置未自动写入：{e}"
                self._set_status("训练完成", kind="ok")
                self._log(f"训练完成 exit=0{extra}")
                QMessageBox.information(self, "训练完成", f"{self._meta()['label']} 已结束。{extra}")
            else:
                self._set_status(f"训练失败 exit={code}", kind="fail")
                QMessageBox.warning(self, "训练失败", f"exit={code}，见下方记录。")
        elif kind == "pip":
            if code == 0:
                self._set_status("安装完成", kind="ok")
                self._refresh_cuda_label()
                QMessageBox.information(self, "安装完成", "请再点「检查YOLO环境」。")
            else:
                self._set_status(f"安装失败 exit={code}", kind="fail")
        elif kind == "val":
            self._refresh_val_widgets()
            self._set_status("验证结束" if code == 0 else f"val 失败 {code}", kind="ok" if code == 0 else "fail")
            self._set_step(3)
        elif kind == "predict":
            pred = ROOT / "runs" / "predict"
            hits = sorted(pred.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0)
            folder = hits[-1].parent if hits else pred
            self._show_pred_dir(folder)
            self._set_status("推理完成" if code == 0 else f"推理失败 {code}", kind="ok" if code == 0 else "fail")
            self._set_step(3)
        elif kind == "export":
            self._set_status("导出完成" if code == 0 else f"导出失败 {code}", kind="ok" if code == 0 else "fail")
        else:
            self._set_status(f"进程结束 exit={code}", kind="ok" if code == 0 else "fail")
        self._refresh_weights()
        self._set_busy_enabled()

    def _stop_proc(self) -> None:
        if self._proc is None or self._proc.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "终止", "当前没有任务在跑。")
            return
        if not self._confirm("终止", "将结束当前子进程。确定？"):
            return
        self._aborted = True
        pid = int(self._proc.processId() or 0)
        self._kill_tree(pid, signal.SIGTERM)
        self._proc.terminate()
        QTimer.singleShot(2500, self._kill_if_running)
        self._set_status("正在终止…", kind="run")

    def _kill_tree(self, pid: int, sig: int) -> None:
        if pid <= 0:
            return
        try:
            import subprocess

            name = "KILL" if int(sig) == int(signal.SIGKILL) else "TERM"
            subprocess.run(
                ["pkill", f"-{name}", "-P", str(pid)],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass
        try:
            os.kill(pid, sig)
        except Exception:
            pass

    def _kill_if_running(self) -> None:
        if self._proc is None:
            return
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            pid = int(self._proc.processId() or 0)
            self._kill_tree(pid, signal.SIGKILL)
            self._proc.kill()

    # —— 杂项 ——
    def _log(self, text: str) -> None:
        cur = self.txt.toPlainText().strip()
        self.txt.setPlainText((cur + "\n\n" + text) if cur else text)
        self.txt.moveCursor(self.txt.textCursor().MoveOperation.End)

    def _err(self, title: str, e: Exception) -> None:
        QMessageBox.warning(self, title, str(e))
        self._log(f"{title}: {e}")

    def _confirm(self, title: str, text: str) -> bool:
        r = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _open_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            from subprocess import Popen

            Popen(["xdg-open", str(path)])
        except Exception as e:
            QMessageBox.information(self, "打开目录", f"{path}\n{e}")

    def _open_cam_win(self) -> None:
        w = self.window()
        fn = getattr(w, "show_cam_monitor", None)
        if callable(fn):
            fn()

    def _goto_workspace_tab(self, name: str) -> None:
        parent = self.parent()
        while parent is not None:
            fn = getattr(parent, "select_tab", None)
            if callable(fn) and fn(name):
                return
            parent = parent.parent()
        w = self.window()
        fn = getattr(w, "goto_page", None)
        if callable(fn):
            fn(T.VISION, vision_tab=name)
