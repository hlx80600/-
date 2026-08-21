"""空跑联调：一键打开联调所需 Mock，保证握手能空跑。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from core.dry_run_shield import DEFAULT_AUTO_PRESS_S, DEFAULT_AUTO_ROTATE_S
from hmi.style import apply_page_chrome, style_button


class DryRunPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._syncing = False

        root = QVBoxLayout(self)

        # —— 总开关 ——
        box_main = QGroupBox("空跑总控")
        ml = QVBoxLayout(box_main)
        row = QHBoxLayout()
        self.chk_enable = QCheckBox("启用空跑屏蔽（运行中自动维持信号）")
        self.chk_enable.toggled.connect(self._on_enable_toggled)
        row.addWidget(self.chk_enable)
        btn_on = QPushButton("一键启用空跑")
        style_button(btn_on, "success")
        btn_on.clicked.connect(self._one_click_on)
        btn_off = QPushButton("关闭空跑")
        style_button(btn_off, "neutral")
        btn_off.clicked.connect(self._one_click_off)
        row.addWidget(btn_on)
        row.addWidget(btn_off)
        ml.addLayout(row)

        form = QFormLayout()
        self.sp_press = QDoubleSpinBox()
        self.sp_press.setRange(0.2, 30.0)
        self.sp_press.setDecimals(1)
        self.sp_press.setSingleStep(0.5)
        self.sp_press.setSuffix(" s")
        self.sp_press.setValue(float(self.ctx.dry_run.auto_press_s or DEFAULT_AUTO_PRESS_S))
        self.sp_press.valueChanged.connect(self._on_press_changed)
        form.addRow("压机 Mock 自动压鞋完成延时", self.sp_press)
        self.sp_rot = QDoubleSpinBox()
        self.sp_rot.setRange(0.2, 30.0)
        self.sp_rot.setDecimals(1)
        self.sp_rot.setSingleStep(0.5)
        self.sp_rot.setSuffix(" s")
        self.sp_rot.setValue(float(self.ctx.dry_run.auto_rotate_s or DEFAULT_AUTO_ROTATE_S))
        self.sp_rot.valueChanged.connect(self._on_rot_changed)
        form.addRow("压机 Mock 自动旋转完成延时", self.sp_rot)
        self.chk_belt = QCheckBox("空跑时保持皮带光电=有料")
        self.chk_belt.setChecked(True)
        self.chk_belt.toggled.connect(self._on_opts)
        self.chk_place = QCheckBox("放料槽自动跟手（空槽+左右匹配）")
        self.chk_place.setChecked(True)
        self.chk_place.toggled.connect(self._on_opts)
        self.chk_pick = QCheckBox("取料槽自动有料时序（待转无料 / 转完有料）")
        self.chk_pick.setChecked(True)
        self.chk_pick.toggled.connect(self._on_opts)
        ml.addLayout(form)
        ml.addWidget(self.chk_belt)
        ml.addWidget(self.chk_place)
        ml.addWidget(self.chk_pick)
        btn_save = QPushButton("保存空跑选项到 yaml")
        style_button(btn_save, "primary")
        btn_save.clicked.connect(self._save_cfg)
        ml.addWidget(btn_save)
        root.addWidget(box_main)

        # —— 手动覆盖（空跑开时仍可临时改，下一周期可能被自动盖掉）——
        box_man = QGroupBox("手动信号（调试覆盖；空跑自动项开启时会被周期改写）")
        mg = QVBoxLayout(box_man)
        r1 = QHBoxLayout()
        self.btn_belt_on = QPushButton("光电有料")
        self.btn_belt_off = QPushButton("光电无料")
        style_button(self.btn_belt_on, "success")
        style_button(self.btn_belt_off, "neutral")
        self.btn_belt_on.clicked.connect(lambda: self._set_belt(True))
        self.btn_belt_off.clicked.connect(lambda: self._set_belt(False))
        r1.addWidget(self.btn_belt_on)
        r1.addWidget(self.btn_belt_off)
        mg.addLayout(r1)

        self.chk_place_mat = QCheckBox("放料槽有料")
        self.chk_place_left = QCheckBox("放料槽=左鞋槽")
        self.chk_place_left.setChecked(True)
        self.chk_pick_mat = QCheckBox("取料槽有料")
        self.chk_place_mat.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_place_has_material", bool(on))
        )
        self.chk_place_left.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_place_is_left", bool(on))
        )
        self.chk_pick_mat.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_pick_has_material", bool(on))
        )
        mg.addWidget(self.chk_place_mat)
        mg.addWidget(self.chk_place_left)
        mg.addWidget(self.chk_pick_mat)

        r2 = QHBoxLayout()
        self.btn_press = QPushButton("手动：压鞋完成")
        style_button(self.btn_press, "warn")
        self.btn_press.clicked.connect(self.ctx.press.simulate_press_done)
        self.btn_rot = QPushButton("手动：压机旋转完成")
        style_button(self.btn_rot, "warn")
        self.btn_rot.clicked.connect(self.ctx.press.simulate_rotate_done)
        r2.addWidget(self.btn_press)
        r2.addWidget(self.btn_rot)
        mg.addLayout(r2)
        root.addWidget(box_man)

        self.lbl = QLabel("-")
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet(
            "background:#eef2f5;padding:10px;border-radius:4px;font-family:monospace;"
        )
        root.addWidget(self.lbl)

        apply_page_chrome(self)
        self._sync_from_ctx()

    def _on_enable_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        if on:
            self._apply_opts()
            self.ctx.dry_run.enable()
        else:
            self.ctx.dry_run.disable()
        self._refresh_lbl()

    def _one_click_on(self) -> None:
        self._apply_opts()
        self.ctx.dry_run.enable()
        self._syncing = True
        self.chk_enable.setChecked(True)
        self._syncing = False
        self._refresh_lbl()
        QMessageBox.information(
            self,
            "空跑已启用",
            "已打开空跑屏蔽。\n请到监视页「初始化」→「启动」验证流程。",
        )

    def _one_click_off(self) -> None:
        self.ctx.dry_run.disable()
        self._syncing = True
        self.chk_enable.setChecked(False)
        self._syncing = False
        self._refresh_lbl()

    def _on_press_changed(self, v: float) -> None:
        self.ctx.dry_run.auto_press_s = float(v)
        if self.ctx.dry_run.enabled:
            self.ctx.cfg.setdefault("press", {})["mock_auto_press_done_s"] = float(v)

    def _on_rot_changed(self, v: float) -> None:
        self.ctx.dry_run.auto_rotate_s = float(v)
        if self.ctx.dry_run.enabled:
            self.ctx.cfg.setdefault("press", {})["mock_auto_rotate_done_s"] = float(v)

    def _on_opts(self) -> None:
        if self._syncing:
            return
        self._apply_opts()

    def _apply_opts(self) -> None:
        d = self.ctx.dry_run
        d.keep_belt_on = bool(self.chk_belt.isChecked())
        d.auto_place_match = bool(self.chk_place.isChecked())
        d.auto_pick_slot = bool(self.chk_pick.isChecked())
        d.auto_press_s = float(self.sp_press.value())
        d.auto_rotate_s = float(self.sp_rot.value())

    def _save_cfg(self) -> None:
        self._apply_opts()
        dry = self.ctx.cfg.setdefault("system", {}).setdefault("dry_run", {})
        dry["enabled"] = bool(self.ctx.dry_run.enabled)
        dry["auto_press_s"] = float(self.ctx.dry_run.auto_press_s)
        dry["auto_rotate_s"] = float(self.ctx.dry_run.auto_rotate_s)
        dry["keep_belt_on"] = bool(self.ctx.dry_run.keep_belt_on)
        dry["auto_place_match"] = bool(self.ctx.dry_run.auto_place_match)
        dry["auto_pick_slot"] = bool(self.ctx.dry_run.auto_pick_slot)
        save_config(self.ctx.cfg)
        QMessageBox.information(self, "已保存", "空跑选项已写入 config/default.yaml")

    def _set_belt(self, on: bool) -> None:
        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        self.ctx.robot1.set_di_force_mock(belt_di, True)
        self.ctx.robot1.set_di_mock(belt_di, bool(on))
        self._refresh_lbl()

    def _sync_from_ctx(self) -> None:
        d = self.ctx.dry_run
        self._syncing = True
        self.chk_enable.setChecked(bool(d.enabled))
        self.chk_belt.setChecked(bool(d.keep_belt_on))
        self.chk_place.setChecked(bool(d.auto_place_match))
        self.chk_pick.setChecked(bool(d.auto_pick_slot))
        self.sp_press.setValue(float(d.auto_press_s or DEFAULT_AUTO_PRESS_S))
        self.sp_rot.setValue(float(d.auto_rotate_s or DEFAULT_AUTO_ROTATE_S))
        self.chk_place_mat.setChecked(bool(self.ctx.vision.mock_place_has_material))
        self.chk_place_left.setChecked(bool(self.ctx.vision.mock_place_is_left))
        self.chk_pick_mat.setChecked(bool(self.ctx.vision.mock_pick_has_material))
        self._syncing = False
        self._refresh_lbl()

    def _refresh_lbl(self) -> None:
        self.lbl.setText("\n".join(self.ctx.dry_run.status_lines()))

    def refresh(self) -> None:
        if self._syncing:
            return
        # 同步手动勾选显示（空跑 tick 可能改过）
        v = self.ctx.vision
        self._syncing = True
        if self.chk_place_mat.isChecked() != bool(v.mock_place_has_material):
            self.chk_place_mat.setChecked(bool(v.mock_place_has_material))
        if self.chk_place_left.isChecked() != bool(v.mock_place_is_left):
            self.chk_place_left.setChecked(bool(v.mock_place_is_left))
        if self.chk_pick_mat.isChecked() != bool(v.mock_pick_has_material):
            self.chk_pick_mat.setChecked(bool(v.mock_pick_has_material))
        if self.chk_enable.isChecked() != bool(self.ctx.dry_run.enabled):
            self.chk_enable.setChecked(bool(self.ctx.dry_run.enabled))
        self._syncing = False
        self._refresh_lbl()
