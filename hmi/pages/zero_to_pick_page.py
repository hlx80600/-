"""采图训练（「视觉」总页子页签）：模型、采图训练、内参/手眼写入 json、视觉取料试走。"""

from __future__ import annotations

import os
import shutil
import signal
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from devices.pose_utils import numeric_pose
from hmi.pages.obb_label_widget import ObbLabelPanel
from hmi.pages.cls_preview_widget import ClassifyPreviewPanel
from hmi.style import apply_page_chrome, style_button, style_many
from hmi.tab_titles import T
from vision import commission_actions as cact
from vision import model_store as mstore

ROOT = Path(__file__).resolve().parents[2]


class ZeroToPickPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._proc: QProcess | None = None
        self._proc_kind = ""
        self._aborted = False
        self._job = ""  # train | pip
        self._last_check = 0.0

        root = QVBoxLayout(self)

        tip = QLabel(
            "按①→⑤在本页做完：挂模型 → 采图/训练（分类）→ 内参与手眼写入 json → "
            f"测试皮带并 MoveL。预览点像素用上方预览区，或切到「手眼标定」页签。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#1a5276;font-weight:bold;")
        root.addWidget(tip)

        chk = QGroupBox("进度")
        chk_lay = QVBoxLayout(chk)
        self.lbl_check = QLabel("-")
        self.lbl_check.setWordWrap(True)
        self.lbl_check.setStyleSheet("font-weight:bold;color:#154360;")
        chk_lay.addWidget(self.lbl_check)
        row = QHBoxLayout()
        b_ref = QPushButton("刷新进度")
        b_vis = QPushButton("切到手眼标定")
        b_cam = QPushButton(f"打开{T.CAM_MONITOR}")
        style_many([(b_ref, "neutral"), (b_vis, "primary"), (b_cam, "motion")])
        b_ref.clicked.connect(self._refresh_checklist)
        b_vis.clicked.connect(self._goto_handeye_tab)
        b_cam.clicked.connect(self._open_cam_win)
        for b in (b_ref, b_vis, b_cam):
            row.addWidget(b, 0)
        row.addStretch(1)
        chk_lay.addLayout(row)
        root.addWidget(chk)

        # ① 环境与模型
        g1 = QGroupBox("① 环境与模型")
        l1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        b_stack = QPushButton("检查YOLO环境")
        b_link = QPushButton("挂接旧模型")
        b_pip = QPushButton("安装 ultralytics")
        b_models = QPushButton("打开 models/")
        style_many(
            [(b_stack, "neutral"), (b_link, "success"), (b_pip, "warn"), (b_models, "neutral")]
        )
        b_stack.clicked.connect(self._check_stack)
        b_link.clicked.connect(self._link_legacy)
        b_pip.clicked.connect(self._pip_ultra)
        b_models.clicked.connect(lambda: self._open_dir(ROOT / "models"))
        for b in (b_stack, b_link, b_pip, b_models):
            r1.addWidget(b, 0)
        r1.addStretch(1)
        l1.addLayout(r1)
        self.lbl_cuda = QLabel("")
        self.lbl_cuda.setWordWrap(True)
        self.lbl_cuda.setStyleSheet("color:#34495e;padding:2px 0;")
        l1.addWidget(self.lbl_cuda)
        r1b = QHBoxLayout()
        self.cmb_slot = QComboBox()
        for sid, meta in mstore.SLOTS.items():
            self.cmb_slot.addItem(meta["label"], sid)
        self.cmb_slot.currentIndexChanged.connect(self._on_slot)
        b_pick = QPushButton("选用已有.pt写入配置")
        b_copy = QPushButton("拷到 models/ 并写入")
        style_many([(b_pick, "primary"), (b_copy, "success")])
        b_pick.clicked.connect(lambda: self._install_pt(copy=False))
        b_copy.clicked.connect(lambda: self._install_pt(copy=True))
        r1b.addWidget(QLabel("槽位"), 0)
        r1b.addWidget(self.cmb_slot, 1)
        r1b.addWidget(b_pick, 0)
        r1b.addWidget(b_copy, 0)
        l1.addLayout(r1b)
        root.addWidget(g1)

        # ② 采图训练
        g2 = QGroupBox("② 采图 / 训练（点一次拍一张，不连拍）")
        l2 = QVBoxLayout(g2)
        self.lbl_job = QLabel("状态：空闲")
        self.lbl_job.setWordWrap(True)
        self.lbl_job.setStyleSheet(
            "background:#d5f5e3;color:#145a32;padding:8px;border-radius:4px;font-weight:bold;"
        )
        l2.addWidget(self.lbl_job)

        r2 = QHBoxLayout()
        self.cmb_cls = QComboBox()
        self.cmb_cls.currentIndexChanged.connect(self._on_cls)
        r2.addWidget(QLabel("类别"), 0)
        r2.addWidget(self.cmb_cls, 1)
        l2.addLayout(r2)

        r2c = QHBoxLayout()
        self.btn_lr = QPushButton("左右脚采图/训练")
        self.btn_cap_one = QPushButton("采图（拍1张）")
        b_ds = QPushButton("打开数据集")
        style_many(
            [(self.btn_lr, "primary"), (self.btn_cap_one, "success"), (b_ds, "neutral")]
        )
        self.btn_lr.clicked.connect(lambda: self._select_slot("shoe_lr"))
        self.btn_cap_one.clicked.connect(self._cap_one_clicked)
        b_ds.clicked.connect(self._open_dataset)
        r2c.addWidget(self.btn_lr, 0)
        r2c.addWidget(self.btn_cap_one, 0)
        r2c.addWidget(b_ds, 0)
        r2c.addStretch(1)
        l2.addLayout(r2c)

        r2d = QHBoxLayout()
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
            r2d.addWidget(b, 0)
        r2d.addStretch(1)
        l2.addLayout(r2d)

        self.lbl_ds = QLabel("-")
        self.lbl_ds.setWordWrap(True)
        l2.addWidget(self.lbl_ds)

        self.cls_preview = ClassifyPreviewPanel()
        self.cls_preview.status.connect(self._log)
        l2.addWidget(self.cls_preview)

        self.obb_panel = ObbLabelPanel()
        self.obb_panel.status.connect(self._log)
        l2.addWidget(self.obb_panel)

        r2b = QHBoxLayout()
        self.sp_ep = QSpinBox()
        self.sp_ep.setRange(1, 400)
        self.sp_ep.setValue(40)
        self.cmb_dev = QComboBox()
        # 显示文案给现场选；实际 device 存在 itemData
        self.cmb_dev.addItem("CPU（不加GPU）", "cpu")
        self.cmb_dev.addItem("GPU（CUDA:0）", "0")
        self.cmb_dev.addItem("双GPU（0,1）", "0,1")
        self.cmb_dev.setToolTip(
            "CPU：无独显也能训，较慢。\n"
            "GPU：需 NVIDIA 驱动 + CUDA 版 torch；点「检查YOLO环境」可看是否可用。"
        )
        self.btn_train = QPushButton("开始训练")
        self.btn_train_stop = QPushButton("终止训练")
        style_many([(self.btn_train, "success"), (self.btn_train_stop, "danger")])
        self.btn_train.clicked.connect(self._train)
        self.btn_train_stop.clicked.connect(self._stop_proc)
        r2b.addWidget(QLabel("epochs"), 0)
        r2b.addWidget(self.sp_ep, 0)
        r2b.addWidget(QLabel("训练设备"), 0)
        r2b.addWidget(self.cmb_dev, 0)
        r2b.addWidget(self.btn_train, 0)
        r2b.addWidget(self.btn_train_stop, 0)
        r2b.addStretch(1)
        l2.addLayout(r2b)
        root.addWidget(g2)

        # ③ 标定写入
        g3 = QGroupBox("③ cam1 内参 / ROI / 手眼 → shoe_vision_config.json")
        l3 = QVBoxLayout(g3)
        r3 = QHBoxLayout()
        b_k = QPushButton("棋盘格内参写入json")
        b_roi = QPushButton("绿框ROI写入json")
        b_he_px = QPushButton("记录：点选像素+TCP")
        b_he_c = QPushButton("记录：画面中心+TCP")
        style_many(
            [(b_k, "success"), (b_roi, "success"), (b_he_px, "warn"), (b_he_c, "warn")]
        )
        b_k.clicked.connect(self._write_k)
        b_roi.clicked.connect(self._write_roi)
        b_he_px.clicked.connect(lambda: self._record_he(False))
        b_he_c.clicked.connect(lambda: self._record_he(True))
        for b in (b_k, b_roi, b_he_px, b_he_c):
            r3.addWidget(b, 0)
        r3.addStretch(1)
        l3.addLayout(r3)
        r3b = QHBoxLayout()
        self.sp_z = QSpinBox()
        self.sp_z.setRange(50, 2000)
        self.sp_z.setValue(400)
        self.sp_z.setSuffix(" mm")
        b_solve = QPushButton("计算手眼4×4并写入json")
        style_button(b_solve, "success")
        b_solve.clicked.connect(self._solve_he)
        r3b.addWidget(QLabel("无深度时假定Z"), 0)
        r3b.addWidget(self.sp_z, 0)
        r3b.addWidget(b_solve, 0)
        r3b.addStretch(1)
        l3.addLayout(r3b)
        hint = QLabel(
            "采样：皮带放鞋 → ① 记录对准位姿 → 手动移开 → ② 拍照 → ③ 完成采样 → 按路径回夹验证。\n"
            "换位置至少 3 点；cam1 建议 ≥8 点。"
        )
        hint.setWordWrap(True)
        l3.addWidget(hint)
        root.addWidget(g3)

        # ④ 抓取
        g4 = QGroupBox("④ 皮带出图 → PickPose → 试走")
        l4 = QVBoxLayout(g4)
        r4 = QHBoxLayout()
        self.chk_live = QCheckBox("取消 cam1 Mock（改真机）")
        cam1 = self.ctx.cameras.get("cam1")
        self.chk_live.setChecked(bool(cam1) and not bool(cam1.use_mock))
        self.chk_live.toggled.connect(self._toggle_cam1_mock)
        r4.addWidget(self.chk_live, 0)
        r4.addStretch(1)
        l4.addLayout(r4)
        r4b = QHBoxLayout()
        b_test = QPushButton("测试皮带拍照")
        b_apply = QPushButton("写入PickPose")
        b_up = QPushButton("MoveL到取料上方")
        b_dn = QPushButton("MoveL到取料点")
        b_step = QPushButton(f"打开{T.STEP_DEBUG}")
        style_many(
            [
                (b_test, "motion"),
                (b_apply, "success"),
                (b_up, "motion"),
                (b_dn, "warn"),
                (b_step, "primary"),
            ]
        )
        b_test.clicked.connect(self._test_belt)
        b_apply.clicked.connect(self._apply_pick)
        b_up.clicked.connect(lambda: self._move_pick(True))
        b_dn.clicked.connect(lambda: self._move_pick(False))
        b_step.clicked.connect(lambda: self._goto_tab(T.STEP_DEBUG))
        for b in (b_test, b_apply, b_up, b_dn, b_step):
            r4b.addWidget(b, 0)
        r4b.addStretch(1)
        l4.addLayout(r4b)
        root.addWidget(g4)

        log_box = QGroupBox("过程记录")
        log_lay = QVBoxLayout(log_box)
        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setMinimumHeight(140)
        log_lay.addWidget(self.txt)
        root.addWidget(log_box, 1)

        apply_page_chrome(self)
        self._on_slot()
        self._set_status("空闲")
        self._refresh_checklist()
        self._refresh_cuda_label()
        self._log(
            "旧左右脚模型不能用时：点「左右脚采图/训练」→ 选左脚/右脚 → 采图"
            "（自动抠鞋，把鞋头转到朝上）→ 两类都采够再训练。"
        )

    def _log(self, text: str) -> None:
        cur = self.txt.toPlainText().strip()
        self.txt.setPlainText((cur + "\n\n" + text) if cur else text)
        self.txt.moveCursor(self.txt.textCursor().MoveOperation.End)

    def _err(self, title: str, e: Exception) -> None:
        QMessageBox.warning(self, title, str(e))
        self._log(f"{title}: {e}")

    def _slot_id(self) -> str:
        return str(self.cmb_slot.currentData() or "slot_check")

    def _cls_name(self) -> str:
        return str(self.cmb_cls.currentData() or "")

    def _on_slot(self, _i: int = 0) -> None:
        sid = self._slot_id()
        meta = mstore.SLOTS[sid]
        mstore.ensure_dirs(sid)
        keep = self._cls_name()
        self.cmb_cls.blockSignals(True)
        self.cmb_cls.clear()
        classes = list(meta.get("classes") or [])
        if classes:
            for c in classes:
                self.cmb_cls.addItem(mstore.class_label(c), c)
            self.cmb_cls.setEnabled(self._job not in ("train", "pip"))
            idx = self.cmb_cls.findData(keep)
            if idx >= 0:
                self.cmb_cls.setCurrentIndex(idx)
        else:
            self.cmb_cls.addItem("（找鞋/楦/压杆：不用选类，下面圈图）", "")
            self.cmb_cls.setEnabled(False)
        self.cmb_cls.blockSignals(False)
        self._refresh_counts()
        if hasattr(self, "cls_preview"):
            self.cls_preview.set_slot(sid)
            if meta.get("kind") == "cls":
                self.cls_preview.show_path(mstore.newest_train_image(sid, self._cls_name()))
        if hasattr(self, "obb_panel"):
            self.obb_panel.set_slot(sid)

    def _on_cls(self, _i: int = 0) -> None:
        sid = self._slot_id()
        if mstore.SLOTS[sid].get("kind") != "cls":
            return
        if hasattr(self, "cls_preview"):
            self.cls_preview.show_path(mstore.newest_train_image(sid, self._cls_name()))

    def _refresh_counts(self) -> None:
        sid = self._slot_id()
        meta = mstore.SLOTS[sid]
        self.lbl_ds.setText(
            f"{meta['label']}  相机={meta['cam']}  {mstore.dataset_counts(sid)}"
        )

    def _open_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            from subprocess import Popen

            Popen(["xdg-open", str(path)])
        except Exception as e:
            QMessageBox.information(self, "打开目录", f"{path}\n{e}")

    def _open_dataset(self) -> None:
        self._open_dir(mstore.dataset_dir(self._slot_id()))

    def _goto_tab(self, title: str) -> None:
        w = self.window()
        fn = getattr(w, "goto_page", None)
        if callable(fn):
            fn(title)
            return
        # 兼容旧 Tab 结构
        tabs = getattr(w, "tabs", None)
        if tabs is None:
            return
        if hasattr(tabs, "tabText"):
            for i in range(tabs.count()):
                if tabs.tabText(i) == title:
                    tabs.setCurrentIndex(i)
                    return

    def _goto_handeye_tab(self) -> None:
        """已在「视觉」总页内时切子页签；否则跳转导航。"""
        parent = self.parent()
        while parent is not None:
            fn = getattr(parent, "select_tab", None)
            if callable(fn) and fn("手眼标定"):
                return
            parent = parent.parent()
        w = self.window()
        fn = getattr(w, "goto_page", None)
        if callable(fn):
            fn(T.VISION, vision_tab="手眼标定")
            return
        self._goto_tab(T.VISION)

    def _open_cam_win(self) -> None:
        w = self.window()
        fn = getattr(w, "show_cam_monitor", None)
        if callable(fn):
            fn()

    def _refresh_checklist(self) -> None:
        try:
            lines = cact.checklist_lines(self.ctx)
            self.lbl_check.setText("   ".join(lines))
        except Exception as e:
            self.lbl_check.setText(str(e))
        self._refresh_counts()
        cam1 = self.ctx.cameras.get("cam1")
        want = bool(cam1) and not bool(cam1.use_mock)
        if self.chk_live.isChecked() != want:
            self.chk_live.blockSignals(True)
            self.chk_live.setChecked(want)
            self.chk_live.blockSignals(False)

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_check < 0.9:
            return
        self._last_check = now
        self._refresh_checklist()

    def _train_device(self) -> str:
        return mstore.normalize_train_device(str(self.cmb_dev.currentData() or "cpu"))

    def _train_device_label(self) -> str:
        return str(self.cmb_dev.currentText() or "CPU")

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
        # 无 CUDA 时仍可选 GPU（便于装好后再训），但默认保持 CPU
        if st.get("cuda") and self.cmb_dev.currentData() == "cpu":
            # 仅提示，不强行切 GPU，避免现场误选
            pass

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

    def _link_legacy(self) -> None:
        try:
            out = mstore.link_legacy_models()
            self._log(out)
            QMessageBox.information(self, "挂接旧模型", out[-800:] if len(out) > 800 else out)
        except Exception as e:
            self._err("挂接失败", e)
        self._refresh_checklist()

    def _pip_ultra(self) -> None:
        if self._busy():
            return
        box = QMessageBox(self)
        box.setWindowTitle("安装 ultralytics / torch")
        box.setText("选择安装 CPU 版还是 GPU 版 PyTorch（需联网，可能较久）。")
        box.setInformativeText(
            "CPU：通用，训练慢。\n"
            "GPU：需 NVIDIA 显卡；默认装 CUDA 12.4 wheel（cu124）。\n"
            "装完请点「检查YOLO环境」确认 CUDA。"
        )
        btn_cpu = box.addButton("安装 CPU 版", QMessageBox.ButtonRole.AcceptRole)
        btn_gpu = box.addButton("安装 GPU 版", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (btn_cpu, btn_gpu):
            return
        with_cuda = clicked is btn_gpu
        self._job = "pip"
        tip = "GPU(CUDA)" if with_cuda else "CPU"
        self._set_status(f"正在安装 ultralytics / torch（{tip}）…", kind="run")
        self._start_proc(mstore.pip_ultralytics_cmd(with_cuda=with_cuda), kind="pip")

    def _install_pt(self, *, copy: bool) -> None:
        sid = self._slot_id()
        start = str(ROOT / "models")
        path, _ = QFileDialog.getOpenFileName(self, "选择 .pt", start, "YOLO (*.pt)")
        if not path:
            return
        try:
            rel = mstore.bind_model(self.ctx, sid, Path(path), copy_to_default=copy)
            self._log(f"{mstore.SLOTS[sid]['label']} → {rel}")
            QMessageBox.information(self, "已写入", f"已指向 {rel}\n检测会重新加载模型。")
        except Exception as e:
            self._err("安装模型", e)
        self._refresh_checklist()

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
        training = self._job in ("train", "pip")
        self.btn_cap_one.setEnabled(not training)
        self.btn_train.setEnabled(not training)
        self.btn_train_stop.setEnabled(training)
        self.btn_del_last.setEnabled(not training)
        self.btn_del_cls.setEnabled(not training)
        self.btn_del_all.setEnabled(not training)
        self.cmb_slot.setEnabled(not training)
        self.cmb_dev.setEnabled(not training)
        self.sp_ep.setEnabled(not training)
        if mstore.SLOTS[self._slot_id()].get("classes"):
            self.cmb_cls.setEnabled(not training)
        if hasattr(self, "btn_lr"):
            self.btn_lr.setEnabled(not training)

    def _confirm(self, title: str, text: str) -> bool:
        r = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _select_slot(self, sid: str) -> None:
        idx = self.cmb_slot.findData(sid)
        if idx < 0:
            return
        self.cmb_slot.setCurrentIndex(idx)
        self._log(
            "已切到皮带-左右脚。取消 cam1 Mock 后：选左脚或右脚 → 采图。"
            "左右都采若干张（鞋头朝上）再点开始训练。"
        )

    def _cap_one(self, *, jump: bool = False) -> bool:
        sid = self._slot_id()
        cls = self._cls_name()
        meta = mstore.SLOTS[sid]
        if meta["kind"] == "cls" and not cls:
            QMessageBox.warning(self, "采图", "请先选类别")
            return False
        try:
            path = mstore.capture_to_slot(self.ctx, sid, cls, to_val=False)
            note = getattr(mstore.capture_to_slot, "last_note", "") or ""
            n = mstore.count_images(path.parent)
            if n % 5 == 0:
                if meta["kind"] == "cls":
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
            if hasattr(self, "cls_preview") and meta["kind"] == "cls":
                self.cls_preview.show_path(path)
            if jump and meta["kind"] == "obb" and hasattr(self, "obb_panel"):
                self.obb_panel.goto_last()
                self._set_status(f"已拍 1 张，请在下方圈图  {path.name}", kind="ok")
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

    def _del_last(self) -> None:
        if self._job:
            return
        path = mstore.newest_train_image(self._slot_id(), self._cls_name())
        if path is None:
            QMessageBox.information(self, "删除", "当前类没有可删的图")
            return
        if not self._confirm("删除最近一张", f"删除：\n{path}\n（val 同名也会删）"):
            return
        removed = mstore.delete_image_pair(path)
        self._log("已删除:\n" + "\n".join(str(p) for p in removed))
        self._refresh_counts()
        if hasattr(self, "cls_preview"):
            self.cls_preview.set_slot(self._slot_id())
        if hasattr(self, "obb_panel") and mstore.SLOTS[self._slot_id()].get("kind") == "obb":
            self.obb_panel.reload()
        self._set_status("已删除最近一张", kind="ok")

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
        if not self._confirm("删除本类全部图", f"将删除「{cls}」共 {n} 张（train+val）。确定？"):
            return
        got = mstore.delete_class_images(sid, self._cls_name())
        self._log(f"已删除本类 {got} 张")
        self._refresh_counts()
        if hasattr(self, "cls_preview"):
            self.cls_preview.set_slot(sid)
        if hasattr(self, "obb_panel"):
            self.obb_panel.reload()
        self._set_status(f"已删除本类 {got} 张", kind="ok")

    def _del_dataset(self) -> None:
        if self._job:
            return
        sid = self._slot_id()
        label = mstore.SLOTS[sid]["label"]
        if not self._confirm(
            "清空本任务数据集",
            f"将删除「{label}」下全部训练图（{mstore.dataset_dir(sid)}）。\n"
            "不删 models/ 里的 .pt。确定？",
        ):
            return
        n = mstore.delete_dataset_images(sid)
        self._log(f"已清空 {label} 共 {n} 张")
        self._refresh_counts()
        if hasattr(self, "cls_preview"):
            self.cls_preview.set_slot(sid)
        if hasattr(self, "obb_panel"):
            self.obb_panel.reload()
        self._set_status(f"已清空数据集 {n} 张", kind="ok")

    def _train(self) -> None:
        if self._job:
            QMessageBox.information(self, "忙", "请先停止当前训练或安装。")
            return
        sid = self._slot_id()
        meta = mstore.SLOTS[sid]
        device = self._train_device()
        device_label = self._train_device_label()
        if mstore.is_gpu_train_device(device):
            cuda = mstore.cuda_train_status()
            if not cuda.get("cuda"):
                r = QMessageBox.question(
                    self,
                    "GPU 不可用",
                    f"当前选了「{device_label}」，但本机 CUDA 不可用：\n"
                    f"{cuda.get('message', '')}\n\n"
                    "可改选「CPU（不加GPU）」，或先点「安装 ultralytics」选 GPU 版。\n"
                    "仍要用 GPU 参数启动训练吗？（多半会失败）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if r != QMessageBox.StandardButton.Yes:
                    return
        if meta.get("train") == "obb":
            from vision import obb_label as olab

            n_all = len(olab.list_slot_images(sid))
            n_lab = sum(1 for p in olab.list_slot_images(sid) if olab.is_labeled(p))
            if n_all == 0 or n_lab == 0:
                QMessageBox.warning(
                    self,
                    "还没圈图",
                    f"找鞋/楦/压杆训练需要圈旋转框。当前已圈 {n_lab}/{n_all}。\n"
                    "请在下方拖框圈目标后再训；或改用旧模型、不训。",
                )
                return
            if not self._confirm(
                "开始训练 OBB",
                f"已圈 {n_lab}/{n_all} 张。未圈的图不会参与训练。\n"
                f"epochs={int(self.sp_ep.value())}  训练设备={device_label}\n"
                "CPU 会较慢；有 GPU 请选「GPU（CUDA:0）」。开始？",
            ):
                return
        else:
            missing = []
            for c in meta.get("classes") or []:
                ntr = mstore.count_images(mstore.class_dir(sid, str(c), split="train"))
                if ntr < 1:
                    missing.append(mstore.class_label(c))
            if missing:
                QMessageBox.warning(
                    self,
                    "图不够",
                    "分类训练每个类至少 1 张。还缺：" + "、".join(missing) + "。",
                )
                return
            extra = ""
            if sid == "shoe_lr":
                extra = (
                    "\n请确认图里是鞋头朝上的鞋（可在预览里旋转）。"
                    "训完会写入 custom_鞋头朝上左右脚分类.pt。"
                )
            if not self._confirm(
                "开始训练",
                f"任务={meta['label']}\n{mstore.dataset_counts(sid)}\n"
                f"epochs={int(self.sp_ep.value())}  训练设备={device_label}"
                f"{extra}\n"
                "训练期间可点「终止训练」。开始？",
            ):
                return
        batch = mstore.default_train_batch(device)
        cmd = mstore.train_cmd(
            sid,
            epochs=int(self.sp_ep.value()),
            device=device,
            batch=batch,
        )
        self._log(f"开始训练: {' '.join(cmd)}  ({device_label}, batch={batch})")
        self._job = "train"
        self._set_status(
            f"训练中 {meta['label']}  epochs={int(self.sp_ep.value())}  {device_label}",
            kind="run",
        )
        self._start_proc(cmd, kind="train")

    def _busy(self) -> bool:
        if self._job:
            QMessageBox.information(self, "忙", "已有训练/安装在跑，请先停止或等结束。")
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
            QMessageBox.warning(self, "启动失败", "训练/安装进程没能启动，见下方记录。")

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
        sid = self._slot_id()
        if aborted:
            self._set_status("已终止训练/安装", kind="fail")
            self._log("已终止")
            QMessageBox.information(self, "已终止", "训练或安装已终止，权重未更新。")
            self._refresh_checklist()
            return
        if kind == "train":
            if code == 0:
                extra = ""
                try:
                    inst = ROOT / mstore.SLOTS[sid]["install"]
                    rel = mstore.bind_model(self.ctx, sid, inst, copy_to_default=False)
                    extra = f"\n已写入配置：{rel}"
                    self._log(f"已把配置指到 {rel}")
                except Exception as e:
                    extra = f"\n配置未自动写入：{e}"
                self._set_status("训练完成", kind="ok")
                self._log(f"训练完成 exit=0{extra}")
                QMessageBox.information(
                    self,
                    "训练完成",
                    f"任务={mstore.SLOTS[sid]['label']} 已结束。{extra}\n"
                    "请到视觉页测试该路检测。",
                )
            else:
                self._set_status(f"训练失败 exit={code}", kind="fail")
                self._log(f"训练失败 exit={code}")
                QMessageBox.warning(
                    self,
                    "训练失败",
                    f"exit={code}。常见原因：未装 ultralytics、图太少、缺两类文件夹。\n"
                    "详情见下方过程记录。",
                )
        elif kind == "pip":
            if code == 0:
                self._set_status("安装完成", kind="ok")
                self._refresh_cuda_label()
                QMessageBox.information(
                    self,
                    "安装完成",
                    "ultralytics / torch 已安装。请再点「检查YOLO环境」确认是否 CUDA 可用，"
                    "再在「训练设备」里选 CPU 或 GPU。",
                )
            else:
                self._set_status(f"安装失败 exit={code}", kind="fail")
                QMessageBox.warning(self, "安装失败", f"pip exit={code}，见下方记录。")
        else:
            self._set_status(f"进程结束 exit={code}", kind="ok" if code == 0 else "fail")
        self._refresh_checklist()

    def _stop_proc(self) -> None:
        if self._proc is None or self._proc.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "终止", "当前没有训练或安装在跑。")
            return
        if not self._confirm("终止训练", "将结束当前训练/安装。已跑完的 epoch 不会自动装成模型。确定？"):
            return
        self._aborted = True
        pid = int(self._proc.processId() or 0)
        self._kill_tree(pid, signal.SIGTERM)
        self._proc.terminate()
        QTimer.singleShot(2500, self._kill_if_running)
        self._log("正在终止训练…")
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
            self._log("已强制结束进程")

    def _write_k(self) -> None:
        try:
            self._log(cact.write_intrinsics_from_calib(self.ctx, "cam1"))
        except Exception as e:
            self._err("写内参", e)
        self._refresh_checklist()

    def _write_roi(self) -> None:
        try:
            self._log(cact.write_roi_ratio_from_file(self.ctx, "cam1"))
        except Exception as e:
            self._err("写ROI", e)

    def _record_he(self, center: bool) -> None:
        try:
            self._log(cact.record_handeye_sample(self.ctx, "cam1", use_center=center))
        except Exception as e:
            self._err("手眼采样", e)
        self._refresh_checklist()

    def _solve_he(self) -> None:
        try:
            msg = cact.solve_handeye_and_write(
                self.ctx, "cam1", assumed_z_mm=float(self.sp_z.value())
            )
            self._log(msg)
            QMessageBox.information(self, "手眼", msg)
        except Exception as e:
            self._err("求解手眼", e)
        self._refresh_checklist()

    def _toggle_cam1_mock(self, live: bool) -> None:
        on_mock = not bool(live)
        self.ctx.vision.set_cam_mock("cam1", on_mock)
        self.ctx.cfg.setdefault("cameras", {}).setdefault("cam1", {})["use_mock"] = on_mock
        try:
            from core.config_loader import save_config

            save_config(self.ctx.cfg)
        except Exception:
            pass
        self._log(f"cam1 → {'真机' if live else '模拟'}")

    def _test_belt(self) -> None:
        try:
            r = self.ctx.vision.photo_belt_pick(
                float(self.ctx.gvl.PickPose.get("z", 120)),
                float(self.ctx.gvl.PickPose.get("rx", -178)),
                float(self.ctx.gvl.PickPose.get("ry", -2)),
            )
            self._log(
                f"皮带拍照 ok={r.ok}  X={r.x:.1f} Y={r.y:.1f} Z={r.z:.1f} Rz={r.rz:.1f}\n"
                f"左右={'左' if r.is_left_shoe else '右'}  source={r.source}\n{r.message}"
            )
        except Exception as e:
            self._err("皮带拍照", e)

    def _apply_pick(self) -> None:
        try:
            _r, msg = cact.apply_belt_pick(self.ctx)
            self._log(msg)
        except Exception as e:
            self._err("写入PickPose", e)
        self._refresh_checklist()

    def _move_pick(self, above: bool) -> None:
        if self.ctx.machine.state.name == "RUNNING":
            QMessageBox.warning(self, "禁止", "自动运行中禁止点动，请先停止。")
            return
        try:
            target = cact.pick_above_pose(self.ctx) if above else numeric_pose(self.ctx.gvl.PickPose)
        except Exception as e:
            self._err("目标位姿", e)
            return
        title = "MoveL 视觉取料上方" if above else "MoveL 视觉取料点"
        xyz = ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
        ans = QMessageBox.question(
            self,
            title,
            f"{xyz}\n\n请确认周边安全、夹爪状态。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self._log(cact.move_robot1_to_pick(self.ctx, above=above))
        except Exception as e:
            self._err("MoveL", e)
