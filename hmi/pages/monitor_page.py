"""运行监控页：按钮、三色灯、记忆、Station 状态、Mock IO。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.machine_state import RunMode
from core.memory import MEMORY_LABELS
from core.coordinator import Coordinator
from hmi.alarm_dialog import format_alarm_text
from hmi.style import apply_page_chrome, style_button, style_many


class MonitorPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._syncing_vel = False
        self._syncing_slot_ui = False

        root = QVBoxLayout(self)

        self.lbl_mobile = QLabel("")
        self.lbl_mobile.setWordWrap(True)
        self.lbl_mobile.setStyleSheet(
            "background:#1a5276;color:#ecf0f1;padding:8px;border-radius:4px;font-size:13px;"
        )
        root.addWidget(self.lbl_mobile)

        # 按钮行（两排，避免窄窗把「急停复位」等字挤没）
        btn_grid = QGridLayout()
        btn_grid.setHorizontalSpacing(8)
        btn_grid.setVerticalSpacing(6)
        self.btn_init = QPushButton("初始化")
        self.btn_start = QPushButton("启动")
        self.btn_pause = QPushButton("暂停")
        self.btn_stop = QPushButton("停止")
        self.btn_estop = QPushButton("急停")
        self.btn_reset_estop = QPushButton("急停复位")
        self.btn_alarm_reset = QPushButton("报警复位")
        self.btn_copy_alarm = QPushButton("复制报警")
        style_many(
            [
                (self.btn_init, "primary"),
                (self.btn_start, "success"),
                (self.btn_pause, "warn"),
                (self.btn_stop, "danger"),
                (self.btn_estop, "danger"),
                (self.btn_reset_estop, "warn"),
                (self.btn_alarm_reset, "primary"),
                (self.btn_copy_alarm, "neutral"),
            ]
        )
        self.btn_estop.setStyleSheet(
            self.btn_estop.styleSheet()
            + "QPushButton{font-size:15px;min-height:40px;}"
        )
        for i, b in enumerate(
            (
                self.btn_init,
                self.btn_start,
                self.btn_pause,
                self.btn_stop,
                self.btn_estop,
                self.btn_reset_estop,
                self.btn_alarm_reset,
                self.btn_copy_alarm,
            )
        ):
            btn_grid.addWidget(b, i // 4, i % 4)
        root.addLayout(btn_grid)

        # 初始化完成标识（必须完成初始化后才能启动）
        self.lbl_init_flag = QTextEdit()
        self.lbl_init_flag.setReadOnly(True)
        self.lbl_init_flag.setFrameShape(QFrame.Shape.NoFrame)
        self.lbl_init_flag.setMinimumHeight(72)
        self.lbl_init_flag.setMaximumHeight(140)
        self.lbl_init_flag.setToolTip("报警全文可选中复制，或点「复制报警」（不弹窗）")
        root.addWidget(self.lbl_init_flag)
        self._refresh_init_flag()

        self.btn_init.clicked.connect(self._on_init)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_pause.clicked.connect(self.coord.cmd_pause)
        self.btn_stop.clicked.connect(self.coord.cmd_stop)
        self.btn_estop.clicked.connect(self.coord.cmd_estop)
        self.btn_reset_estop.clicked.connect(self.coord.cmd_reset_estop)
        self.btn_alarm_reset.clicked.connect(self._on_alarm_reset)
        self.btn_copy_alarm.clicked.connect(self._on_copy_alarm)

        # 模式
        mode_row = QHBoxLayout()
        self.btn_mode_auto = QPushButton("模式:自动")
        self.btn_mode_step = QPushButton("模式:单步")
        self.btn_mode_manual = QPushButton("模式:手动")
        style_many(
            [
                (self.btn_mode_auto, "success"),
                (self.btn_mode_step, "warn"),
                (self.btn_mode_manual, "neutral"),
            ]
        )
        mode_row.addWidget(self.btn_mode_auto)
        mode_row.addWidget(self.btn_mode_step)
        mode_row.addWidget(self.btn_mode_manual)
        root.addLayout(mode_row)
        self.btn_mode_auto.clicked.connect(lambda: self.ctx.machine.set_mode(RunMode.AUTO))
        self.btn_mode_step.clicked.connect(lambda: self.ctx.machine.set_mode(RunMode.SINGLE_STEP))
        self.btn_mode_manual.clicked.connect(lambda: self.ctx.machine.set_mode(RunMode.MANUAL))

        # 单步快捷 + 空跑程序
        step_row = QHBoxLayout()
        self.btn_step_next = QPushButton("单步：下一步")
        self.btn_step_next.setToolTip(
            "切到单步模式；给忙站发 StepPulse（条件满足才跳步）。"
            "初始化中则推进 InitStepPulse。细调请用「工位调试」页。"
        )
        style_button(self.btn_step_next, "warn")
        self.btn_step_next.clicked.connect(self._on_step_next)
        self.btn_dry_prog = QPushButton("启动空跑程序")
        self.btn_dry_prog.setToolTip(
            "一键启用空跑屏蔽（光电/压机Mock、先压后转时序；不改相机模拟）并切自动模式；"
            "仍需「初始化」→「启动」后连续空跑。"
        )
        style_button(self.btn_dry_prog, "success")
        self.btn_dry_prog.clicked.connect(self._on_start_dry_program)
        step_row.addWidget(self.btn_step_next)
        step_row.addWidget(self.btn_dry_prog)
        root.addLayout(step_row)

        # 三色灯（大圆灯）+ 模式
        light_row = QHBoxLayout()
        light_row.setSpacing(12)
        self.lbl_mode = QLabel("模式: -")
        self.lbl_mode.setStyleSheet("font-size:15px;font-weight:bold;")
        self.light_r = QLabel("红")
        self.light_y = QLabel("黄")
        self.light_g = QLabel("绿")
        for w in (self.light_r, self.light_y, self.light_g):
            w.setAlignment(Qt.AlignCenter)
            w.setFixedSize(56, 56)
            w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        light_row.addWidget(self.light_r)
        light_row.addWidget(self.light_y)
        light_row.addWidget(self.light_g)
        light_row.addSpacing(16)
        light_row.addWidget(self.lbl_mode)
        light_row.addStretch(1)
        root.addLayout(light_row)
        self.lbl_state = QLabel("状态: -")
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root.addWidget(self.lbl_state)
        self.lbl_auto_clear = QLabel("")
        self.lbl_auto_clear.setWordWrap(True)
        self.lbl_auto_clear.setVisible(False)
        self.lbl_auto_clear.setStyleSheet(
            "background:#fef9e7;color:#7d6608;padding:6px 8px;border-radius:4px;"
        )
        root.addWidget(self.lbl_auto_clear)

        # —— 当前槽号 + 记忆：首屏最显眼 ——
        hero = QGroupBox("当前槽号 / 记忆（自动运行中记忆锁定）")
        self.mem_box = hero
        hero_lay = QVBoxLayout(hero)

        slot_row = QHBoxLayout()
        self.lbl_hero_place = QLabel("放料槽\n#—")
        self.lbl_hero_pick = QLabel("取料槽\n#—")
        for lb in (self.lbl_hero_place, self.lbl_hero_pick):
            lb.setAlignment(Qt.AlignCenter)
            lb.setWordWrap(True)
            lb.setMinimumHeight(80)
            lb.setStyleSheet(
                "background:#1a5276;color:#ecf0f1;padding:10px;border-radius:6px;"
                "font-size:22px;font-weight:bold;"
            )
            slot_row.addWidget(lb, 1)
        hero_lay.addLayout(slot_row)
        self.lbl_hero_slot_meta = QLabel("顺序 —")
        self.lbl_hero_slot_meta.setAlignment(Qt.AlignCenter)
        self.lbl_hero_slot_meta.setStyleSheet(
            "background:#273746;color:#f7dc6f;padding:6px;border-radius:4px;font-size:14px;font-weight:bold;"
        )
        hero_lay.addWidget(self.lbl_hero_slot_meta)

        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("槽号顺序"))
        self.cmb_mon_seq = QComboBox()
        self.cmb_mon_seq.addItem("12341 正序", "12341")
        self.cmb_mon_seq.addItem("43214 反序", "43214")
        fs0 = (self.ctx.cfg.get("press") or {}).get("four_slot") or {}
        seq0 = str(fs0.get("slot_sequence", "12341") or "12341")
        self.cmb_mon_seq.setCurrentIndex(
            max(0, self.cmb_mon_seq.findData("43214" if seq0 in ("43214", "reverse", "反序") else "12341"))
        )
        self.cmb_mon_seq.setMinimumWidth(140)
        self.cmb_mon_seq.currentIndexChanged.connect(self._on_monitor_seq_changed)
        edit_row.addWidget(self.cmb_mon_seq)
        edit_row.addWidget(QLabel("放料槽"))
        self.sp_mon_place = QSpinBox()
        self.sp_mon_place.setRange(1, 4)
        self.sp_mon_place.setValue(int(self.ctx.press.place_slot))
        self.sp_mon_place.setMinimumWidth(64)
        self.sp_mon_place.valueChanged.connect(lambda _v: self._on_monitor_slot_spin("place"))
        edit_row.addWidget(self.sp_mon_place)
        edit_row.addWidget(QLabel("取料槽"))
        self.sp_mon_pick = QSpinBox()
        self.sp_mon_pick.setRange(1, 4)
        self.sp_mon_pick.setValue(int(self.ctx.press.pick_slot))
        self.sp_mon_pick.setMinimumWidth(64)
        self.sp_mon_pick.valueChanged.connect(lambda _v: self._on_monitor_slot_spin("pick"))
        edit_row.addWidget(self.sp_mon_pick)
        edit_row.addStretch(1)
        hero_lay.addLayout(edit_row)
        edit_row2 = QHBoxLayout()
        self.chk_mon_slot_lock = QCheckBox("锁定手动槽号")
        self.chk_mon_slot_lock.setChecked(bool(self.ctx.press.manual_slot_lock))
        self.chk_mon_slot_lock.toggled.connect(self._on_monitor_slot_lock)
        edit_row2.addWidget(self.chk_mon_slot_lock)
        self.btn_mon_slot_apply = QPushButton("应用槽号")
        style_button(self.btn_mon_slot_apply, "warn")
        self.btn_mon_slot_apply.clicked.connect(self._apply_monitor_slots)
        edit_row2.addWidget(self.btn_mon_slot_apply)
        edit_row2.addStretch(1)
        hero_lay.addLayout(edit_row2)
        self.lbl_slot_edit_tip = QLabel(
            "停止/暂停后可改：改放料槽则取料槽按顺序联动，改取料槽则放料槽联动。"
        )
        self.lbl_slot_edit_tip.setStyleSheet("color:#555;")
        hero_lay.addWidget(self.lbl_slot_edit_tip)

        mem_grid = QGridLayout()
        mem_grid.setHorizontalSpacing(12)
        mem_grid.setVerticalSpacing(8)
        self.mem_checks = {}
        self.mem_lamps = {}
        for i in range(1, 11):
            lamp = QLabel(f"M{i}")
            lamp.setAlignment(Qt.AlignCenter)
            lamp.setFixedWidth(72)
            lamp.setMinimumHeight(32)
            lamp.setStyleSheet(
                "background:#555;color:#ccc;padding:4px;border-radius:3px;font-weight:bold;"
            )
            cb = QCheckBox(MEMORY_LABELS[i])
            cb.setToolTip(f"Mem[{i}] {MEMORY_LABELS[i]}")
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            cb.toggled.connect(lambda on, idx=i: self._mem_toggled(idx, on))
            self.mem_checks[i] = cb
            self.mem_lamps[i] = lamp
            col = 0 if i <= 5 else 2
            row = (i - 1) % 5
            mem_grid.addWidget(lamp, row, col)
            mem_grid.addWidget(cb, row, col + 1)
        mem_grid.setColumnStretch(1, 1)
        mem_grid.setColumnStretch(3, 1)
        hero_lay.addLayout(mem_grid)
        root.addWidget(hero)

        link_box = QGroupBox("设备连接（Mock=模拟就绪；真机断线将自动重连）")
        link_lay = QVBoxLayout(link_box)
        self.lbl_link_warn = QLabel("")
        self.lbl_link_warn.setWordWrap(True)
        self.lbl_link_warn.setVisible(False)
        link_lay.addWidget(self.lbl_link_warn)
        self._link_grid = QGridLayout()
        self._link_labels: dict[str, QLabel] = {}
        for i, row in enumerate(self.ctx.device_link_snapshot()):
            lb = QLabel()
            lb.setMinimumWidth(150)
            self._link_labels[row["name"]] = lb
            self._link_grid.addWidget(lb, i // 2, i % 2)
        link_lay.addLayout(self._link_grid)
        root.addWidget(link_box)
        self._refresh_link_panel()

        # CT / UPH 速览（详细直方图见「产量统计」页）
        prod = QHBoxLayout()
        self.lbl_ct = QLabel("CT: -- s")
        self.lbl_uph = QLabel("UPH: --")
        self.lbl_uph_avg = QLabel("UPH均: --")
        self.lbl_hour_cnt = QLabel("本小时: 0")
        self.lbl_total_cnt = QLabel("总产量: 0")
        for w in (self.lbl_ct, self.lbl_uph, self.lbl_uph_avg, self.lbl_hour_cnt, self.lbl_total_cnt):
            w.setStyleSheet("background:#34495e;color:#ecf0f1;padding:6px 10px;border-radius:4px;")
            prod.addWidget(w)
        root.addLayout(prod)

        # ---------- 机器人速度条（下发法奥 SetSpeed，示教器「运行速度%」同步）----------
        # 速度/全局平滑靠上：避免被 Mem/Mock 挤出首屏（用户常找不到）
        vel_box = QGroupBox("机器人速度（%）")
        vel_lay = QVBoxLayout(vel_box)

        row1 = QHBoxLayout()
        self.lbl_vel1 = QLabel("上料机器人 30%")
        self.lbl_vel1.setMinimumWidth(148)
        self.sld_vel1 = QSlider(Qt.Horizontal)
        self.sld_vel1.setRange(1, 100)
        self.sld_vel1.setValue(int(round(float(self.ctx.robot1.vel))))
        self.sld_vel1.setTickPosition(QSlider.TicksBelow)
        self.sld_vel1.setTickInterval(10)
        row1.addWidget(self.lbl_vel1, 1)
        row1.addWidget(self.sld_vel1, 4)
        vel_lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_vel2 = QLabel("下料机器人 30%")
        self.lbl_vel2.setMinimumWidth(148)
        self.sld_vel2 = QSlider(Qt.Horizontal)
        self.sld_vel2.setRange(1, 100)
        self.sld_vel2.setValue(int(round(float(self.ctx.robot2.vel))))
        self.sld_vel2.setTickPosition(QSlider.TicksBelow)
        self.sld_vel2.setTickInterval(10)
        row2.addWidget(self.lbl_vel2, 1)
        row2.addWidget(self.sld_vel2, 4)
        vel_lay.addLayout(row2)

        row_both = QHBoxLayout()
        self.lbl_vel_both = QLabel("两臂同步 30%")
        self.lbl_vel_both.setMinimumWidth(148)
        self.sld_vel_both = QSlider(Qt.Horizontal)
        self.sld_vel_both.setRange(1, 100)
        both0 = int(round((float(self.ctx.robot1.vel) + float(self.ctx.robot2.vel)) / 2))
        self.sld_vel_both.setValue(both0)
        self.sld_vel_both.setTickPosition(QSlider.TicksBelow)
        self.sld_vel_both.setTickInterval(10)
        row_both.addWidget(self.lbl_vel_both, 1)
        row_both.addWidget(self.sld_vel_both, 4)
        vel_lay.addLayout(row_both)

        self.sld_vel1.valueChanged.connect(lambda v: self._on_vel_changed("robot1", v))
        self.sld_vel2.valueChanged.connect(lambda v: self._on_vel_changed("robot2", v))
        self.sld_vel_both.valueChanged.connect(self._on_vel_both_changed)
        self.sld_vel1.sliderReleased.connect(lambda: self._save_vel("robot1"))
        self.sld_vel2.sliderReleased.connect(lambda: self._save_vel("robot2"))
        self.sld_vel_both.sliderReleased.connect(self._save_vel_both)
        self._update_vel_labels()
        root.addWidget(vel_box)

        # 路径平滑（全局总开关 + 默认 T/R）
        blend_box = QGroupBox("路径平滑（全局总开关 + 默认 blendT/blendR）")
        bl = QVBoxLayout(blend_box)
        self.chk_blend = QCheckBox("启用路径平滑")
        m0 = self.ctx.cfg.get("motion") if isinstance(self.ctx.cfg.get("motion"), dict) else {}
        self.chk_blend.setChecked(bool(m0.get("blend_enable", False)))
        bl.addWidget(self.chk_blend)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("MoveJ平滑时间 blendT(ms)"))
        self.sp_blend_t = QSpinBox()
        self.sp_blend_t.setRange(0, 500)
        self.sp_blend_t.setValue(int(round(float(m0.get("blend_t_ms", 100)))))
        self.sp_blend_t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        brow.addWidget(self.sp_blend_t)
        brow.addWidget(QLabel("MoveL平滑半径 blendR(mm)"))
        self.sp_blend_r = QDoubleSpinBox()
        self.sp_blend_r.setRange(0, 1000)
        self.sp_blend_r.setDecimals(1)
        self.sp_blend_r.setValue(float(m0.get("blend_r_mm", 30)))
        self.sp_blend_r.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        brow.addWidget(self.sp_blend_r)
        bl.addLayout(brow)
        brow2 = QHBoxLayout()
        brow2.addWidget(QLabel("衔接提前量(s)"))
        self.sp_blend_delay = QDoubleSpinBox()
        self.sp_blend_delay.setRange(0.02, 1.0)
        self.sp_blend_delay.setDecimals(3)
        self.sp_blend_delay.setSingleStep(0.02)
        self.sp_blend_delay.setValue(float(m0.get("blend_queue_delay_s", 0.08)))
        self.sp_blend_delay.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        brow2.addWidget(self.sp_blend_delay)
        self.btn_blend_apply = QPushButton("应用并保存")
        style_button(self.btn_blend_apply, "success")
        self.btn_blend_apply.clicked.connect(self._apply_blend)
        brow2.addWidget(self.btn_blend_apply)
        bl.addLayout(brow2)
        for w in (self.sp_blend_t, self.sp_blend_r, self.sp_blend_delay):
            w.wheelEvent = lambda e: e.ignore()  # type: ignore
        root.addWidget(blend_box)

        # 夹爪手动：速度 + 开合按键 + 完成状态灯（自动连续运行时锁定按键）
        grip_box = QGroupBox(
            "夹爪手动（停止/暂停/单步时可操作；等张开完成/夹紧完成，不再固定延时）"
        )
        gl = QGridLayout(grip_box)
        self.lbl_g1 = QLabel("上料夹爪: -")
        self.lbl_g2 = QLabel("下料夹爪: -")
        gl.addWidget(self.lbl_g1, 0, 0, 1, 4)
        gl.addWidget(self.lbl_g2, 0, 4, 1, 4)

        self.lamp_g1_open = QLabel("张开完成")
        self.lamp_g1_close = QLabel("夹紧完成")
        self.lamp_g2_open = QLabel("张开完成")
        self.lamp_g2_close = QLabel("夹紧完成")
        for lb in (self.lamp_g1_open, self.lamp_g1_close, self.lamp_g2_open, self.lamp_g2_close):
            lb.setAlignment(Qt.AlignCenter)
            lb.setMinimumWidth(88)
        gl.addWidget(self.lamp_g1_open, 1, 0)
        gl.addWidget(self.lamp_g1_close, 1, 1)
        gl.addWidget(self.lamp_g2_open, 1, 4)
        gl.addWidget(self.lamp_g2_close, 1, 5)

        self.btn_g1_open = QPushButton("上料张开")
        self.btn_g1_close = QPushButton("上料夹紧")
        self.btn_g2_open = QPushButton("下料张开")
        self.btn_g2_close = QPushButton("下料夹紧")
        style_many(
            [
                (self.btn_g1_open, "success"),
                (self.btn_g1_close, "warn"),
                (self.btn_g2_open, "success"),
                (self.btn_g2_close, "warn"),
            ]
        )
        self.btn_g1_open.clicked.connect(lambda: self._grip_cmd(1, open_=True))
        self.btn_g1_close.clicked.connect(lambda: self._grip_cmd(1, open_=False))
        self.btn_g2_open.clicked.connect(lambda: self._grip_cmd(2, open_=True))
        self.btn_g2_close.clicked.connect(lambda: self._grip_cmd(2, open_=False))
        gl.addWidget(self.btn_g1_open, 2, 0)
        gl.addWidget(self.btn_g1_close, 2, 1)
        gl.addWidget(self.btn_g2_open, 2, 4)
        gl.addWidget(self.btn_g2_close, 2, 5)

        gl.addWidget(QLabel("上料张开速度"), 3, 0)
        self.sp_g1_open_spd = QDoubleSpinBox()
        self.sp_g1_open_spd.setRange(1.0, 200.0)
        self.sp_g1_open_spd.setDecimals(1)
        self.sp_g1_open_spd.setSingleStep(5.0)
        self.sp_g1_open_spd.setValue(float(self.ctx.gripper1.open_speed))
        gl.addWidget(self.sp_g1_open_spd, 3, 1)
        gl.addWidget(QLabel("上料夹紧速度"), 3, 2)
        self.sp_g1_close_spd = QDoubleSpinBox()
        self.sp_g1_close_spd.setRange(1.0, 200.0)
        self.sp_g1_close_spd.setDecimals(1)
        self.sp_g1_close_spd.setSingleStep(5.0)
        self.sp_g1_close_spd.setValue(float(self.ctx.gripper1.close_speed))
        gl.addWidget(self.sp_g1_close_spd, 3, 3)

        gl.addWidget(QLabel("下料张开速度"), 4, 0)
        self.sp_g2_open_spd = QDoubleSpinBox()
        self.sp_g2_open_spd.setRange(1.0, 200.0)
        self.sp_g2_open_spd.setDecimals(1)
        self.sp_g2_open_spd.setSingleStep(5.0)
        self.sp_g2_open_spd.setValue(float(self.ctx.gripper2.open_speed))
        gl.addWidget(self.sp_g2_open_spd, 4, 1)
        gl.addWidget(QLabel("下料夹紧速度"), 4, 2)
        self.sp_g2_close_spd = QDoubleSpinBox()
        self.sp_g2_close_spd.setRange(1.0, 200.0)
        self.sp_g2_close_spd.setDecimals(1)
        self.sp_g2_close_spd.setSingleStep(5.0)
        self.sp_g2_close_spd.setValue(float(self.ctx.gripper2.close_speed))
        gl.addWidget(self.sp_g2_close_spd, 4, 3)

        for sp in (
            self.sp_g1_open_spd,
            self.sp_g1_close_spd,
            self.sp_g2_open_spd,
            self.sp_g2_close_spd,
        ):
            sp.wheelEvent = lambda e: e.ignore()  # type: ignore
            sp.valueChanged.connect(self._on_grip_speed_changed)

        self.btn_grip_spd_save = QPushButton("保存夹爪速度到 yaml")
        style_button(self.btn_grip_spd_save, "primary")
        self.btn_grip_spd_save.clicked.connect(self._save_grip_speeds)
        gl.addWidget(self.btn_grip_spd_save, 5, 0, 1, 4)
        root.addWidget(grip_box)

        # 压鞋机 / 转盘手动（现场点检；自动跑 Station6 时勿同时猛点）
        press_box = QGroupBox(
            "压鞋机/转盘手动（真机写 Modbus；自动跑 Station6 时请先停止）"
        )
        pl = QGridLayout(press_box)
        self.lbl_press_manual = QLabel("压鞋机: -")
        self.lbl_press_manual.setWordWrap(True)
        self.lbl_press_manual.setStyleSheet(
            "background:#273746;color:#ecf0f1;padding:8px;border-radius:4px;font-weight:bold;"
        )
        self.btn_press_rot_on = QPushButton("启动旋转鞋槽")
        self.btn_press_rot_off = QPushButton("停止旋转")
        self.btn_press_start = QPushButton("启动压鞋")
        self.btn_press_stop = QPushButton("停止压鞋")
        self.btn_press_done_sim = QPushButton("置旋转/压鞋完成")
        self.btn_press_done_sim.setToolTip(
            "Mock：置 rotate_done/press_done=True 并清命令；真机仅作调试提示，请看 PLC 到位信号"
        )
        style_many(
            [
                (self.btn_press_rot_on, "warn"),
                (self.btn_press_rot_off, "neutral"),
                (self.btn_press_start, "warn"),
                (self.btn_press_stop, "neutral"),
                (self.btn_press_done_sim, "success"),
            ]
        )
        self.btn_press_rot_on.clicked.connect(lambda: self._press_cmd("rotate", True))
        self.btn_press_rot_off.clicked.connect(lambda: self._press_cmd("rotate", False))
        self.btn_press_start.clicked.connect(lambda: self._press_cmd("press", True))
        self.btn_press_stop.clicked.connect(lambda: self._press_cmd("press", False))
        self.btn_press_done_sim.clicked.connect(self._on_press_force_done)
        pl.addWidget(self.lbl_press_manual, 0, 0, 1, 4)
        pl.addWidget(self.btn_press_rot_on, 1, 0)
        pl.addWidget(self.btn_press_rot_off, 1, 1)
        pl.addWidget(self.btn_press_start, 1, 2)
        pl.addWidget(self.btn_press_stop, 1, 3)
        pl.addWidget(self.btn_press_done_sim, 2, 0, 1, 2)
        root.addWidget(press_box)

        # Station 状态
        st_box = QGroupBox("Station 状态")
        st_layout = QVBoxLayout(st_box)
        self.st_labels = {}
        for s in self.coord.stations:
            lb = QLabel(s.name)
            lb.setWordWrap(True)
            self.st_labels[s.name] = lb
            st_layout.addWidget(lb)
        root.addWidget(st_box)

        # Mock / 空跑信号（详细总控在「空跑联调」页）
        mock = QGroupBox("屏蔽信号快控（完整空跑请用「空跑联调」页）")
        mg = QGridLayout(mock)

        self.lbl_dry = QLabel("空跑: -")
        self.lbl_dry.setWordWrap(True)
        self.lbl_dry.setStyleSheet(
            "background:#d6eaf8;color:#1a5276;padding:8px;border-radius:4px;font-weight:bold;"
        )
        mg.addWidget(self.lbl_dry, 0, 0, 1, 3)

        self.btn_dry_on = QPushButton("一键启用空跑屏蔽")
        style_button(self.btn_dry_on, "success")
        self.btn_dry_on.clicked.connect(self._on_dry_run_on)
        self.btn_dry_off = QPushButton("关闭空跑")
        style_button(self.btn_dry_off, "neutral")
        self.btn_dry_off.clicked.connect(self._on_dry_run_off)
        mg.addWidget(self.btn_dry_on, 1, 0)
        mg.addWidget(self.btn_dry_off, 1, 1)
        # 与顶部「启动空跑程序」同义入口（便于滚到本区时操作）
        self.btn_dry_prog2 = QPushButton("启动空跑程序（屏蔽+自动模式）")
        style_button(self.btn_dry_prog2, "accent")
        self.btn_dry_prog2.clicked.connect(self._on_start_dry_program)
        mg.addWidget(self.btn_dry_prog2, 1, 2)

        self.chk_belt_force = QCheckBox("光电用模拟（真机臂时勾选才能点下面按钮）")
        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        force_on = self.ctx.robot1.use_mock or (
            belt_di in getattr(self.ctx.robot1, "_di_force_mock", set())
        )
        self.chk_belt_force.setChecked(force_on)
        self.chk_belt_force.toggled.connect(self._belt_force_toggled)
        self.chk_belt = QCheckBox("上料皮带光电 DI（电平保持）")
        self.chk_belt.toggled.connect(self._belt_toggled)
        self.btn_belt_on = QPushButton("模拟光电感应到位")
        self.btn_belt_on.setToolTip("置光电 DI=True，触发 Station1 皮带拍照（需 Mem[1]=False）")
        self.btn_belt_on.clicked.connect(lambda: self._set_belt_sensor(True))
        self.btn_belt_off = QPushButton("模拟光电离开(无鞋)")
        self.btn_belt_off.setToolTip("置光电 DI=False，模拟两只鞋都取走后电平变低")
        self.btn_belt_off.clicked.connect(lambda: self._set_belt_sensor(False))
        style_many([(self.btn_belt_on, "success"), (self.btn_belt_off, "neutral")])
        self.lbl_belt = QLabel("光电: -")
        self.lbl_belt.setWordWrap(True)

        self.chk_estop = QCheckBox("物理急停 DI")
        self.chk_estop.toggled.connect(self.ctx.io.set_estop_mock)
        self.chk_rotate_done = QCheckBox("Mock旋转到位")
        self.chk_rotate_done.setChecked(True)
        self.chk_rotate_done.toggled.connect(self.ctx.press.set_rotate_done_mock)
        self.chk_press_done = QCheckBox("Mock压鞋完成")
        self.chk_press_done.setChecked(True)
        self.chk_press_done.toggled.connect(self.ctx.press.set_press_done_mock)
        self.btn_rot_done = QPushButton("模拟压鞋机旋转完成")
        self.btn_rot_done.setToolTip("空跑已开自动旋转时一般不用点；卡住时手动置到位")
        self.btn_rot_done.clicked.connect(self._on_sim_rotate_done)
        style_button(self.btn_rot_done, "warn")
        self.lbl_press = QLabel("压鞋机: -")
        self.lbl_press.setWordWrap(True)

        mg.addWidget(self.chk_belt_force, 2, 0, 1, 2)
        mg.addWidget(self.chk_belt, 3, 0)
        mg.addWidget(self.chk_estop, 3, 1)
        mg.addWidget(self.btn_belt_on, 4, 0)
        mg.addWidget(self.btn_belt_off, 4, 1)
        mg.addWidget(self.lbl_belt, 5, 0, 1, 2)
        mg.addWidget(self.chk_rotate_done, 6, 0)
        mg.addWidget(self.chk_press_done, 6, 1)
        mg.addWidget(self.btn_rot_done, 7, 0)
        self.chk_place_mat = QCheckBox("Mock放料槽有料（仅cam3模拟）")
        self.chk_place_mat.setToolTip(
            "空跑开启且「放料自动跟手」时会被周期改成空槽。\n"
            "仅当相机3为 Mock 时生效。"
        )
        self.chk_place_mat.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_place_has_material", bool(on))
        )
        self.chk_place_left = QCheckBox("Mock放料槽=左鞋槽（仅cam3模拟）")
        self.chk_place_left.setChecked(True)
        self.chk_place_left.setToolTip(
            "空跑开启时会自动跟手中鞋左右。\n"
            "仅当相机3为 Mock 时生效。"
        )
        self.chk_place_left.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_place_is_left", bool(on))
        )
        self.ctx.vision.mock_place_has_material = bool(self.chk_place_mat.isChecked())
        self.ctx.vision.mock_place_is_left = bool(self.chk_place_left.isChecked())
        self.chk_pick_mat = QCheckBox("Mock取料槽有料（仅cam4模拟）")
        self.chk_pick_mat.setChecked(False)
        self.chk_pick_mat.setToolTip(
            "空跑时：待转(Mem3)强制无料；转完后自动有料。\n"
            "不要一直勾着有料，否则 Mem6 会挡住 Station6。"
        )
        self.chk_pick_mat.toggled.connect(
            lambda on: setattr(self.ctx.vision, "mock_pick_has_material", bool(on))
        )
        self.ctx.vision.mock_pick_has_material = bool(self.chk_pick_mat.isChecked())
        self.lbl_shoe_match = QLabel("方向联锁: -")
        self.lbl_shoe_match.setWordWrap(True)
        self.lbl_belt_mock = QLabel("屏蔽皮带: -")
        self.lbl_belt_mock.setWordWrap(True)
        mg.addWidget(self.chk_place_mat, 7, 1)
        mg.addWidget(self.chk_place_left, 8, 0)
        mg.addWidget(self.chk_pick_mat, 8, 1)
        mg.addWidget(self.lbl_press, 9, 0, 1, 2)
        mg.addWidget(self.lbl_shoe_match, 10, 0, 1, 2)
        mg.addWidget(self.lbl_belt_mock, 11, 0, 1, 2)
        self.btn_fault_r1 = QPushButton("模拟上料臂报警（测停机）")
        self.btn_fault_r1.clicked.connect(
            lambda: self.ctx.robot1.inject_fault_mock("模拟：上料机器人伺服/运动报警")
        )
        self.btn_fault_r2 = QPushButton("模拟下料臂报警（测停机）")
        self.btn_fault_r2.clicked.connect(
            lambda: self.ctx.robot2.inject_fault_mock("模拟：下料机器人伺服/运动报警")
        )
        style_many([(self.btn_fault_r1, "danger"), (self.btn_fault_r2, "danger")])
        mg.addWidget(self.btn_fault_r1, 12, 0)
        mg.addWidget(self.btn_fault_r2, 12, 1)
        mg.setColumnStretch(0, 1)
        mg.setColumnStretch(1, 1)
        root.addWidget(mock)
        self._syncing_press_chk = False
        self._syncing_belt_chk = False
        apply_page_chrome(self)

    def _update_vel_labels(self) -> None:
        v1 = int(round(float(self.ctx.robot1.vel)))
        v2 = int(round(float(self.ctx.robot2.vel)))
        self.lbl_vel1.setText(f"上料机器人 {v1}%")
        self.lbl_vel2.setText(f"下料机器人 {v2}%")
        self.lbl_vel_both.setText(f"两臂同步 {int(round((v1 + v2) / 2))}%")

    def _on_init(self) -> None:
        err = self.coord.cmd_init()
        if err:
            QMessageBox.warning(self, "无法初始化", err)
        self._refresh_init_flag()

    def _on_start(self) -> None:
        err = self.coord.cmd_start()
        if err:
            QMessageBox.warning(self, "无法启动", err)
        self._refresh_init_flag()

    def _refresh_init_flag(self) -> None:
        """醒目显示：未初始化 / 进行中 / 已完成可启动。"""
        from core.machine_state import MachineState

        m = self.ctx.machine
        gvl = self.ctx.gvl
        state = m.state
        init_done = bool(gvl.Main.InitDone or m.init_ok)
        link_err = self.ctx.require_all_linked()

        base = (
            "font-size:18px;font-weight:bold;padding:12px 16px;border-radius:8px;"
            "border:2px solid %s;"
        )

        if state == MachineState.ESTOP:
            text = "急停中 — 请先「急停复位」，再初始化"
            css = base % "#922b21" + "background:#f5b7b1;color:#641e16;"
            start_tip = "急停中，无法启动"
            start_ok = False
        elif state == MachineState.ALARM:
            text = "报警中 — 设备连齐后点「报警复位」，再重新初始化"
            if self.ctx.alarms.active:
                text += f"\n[{self.ctx.alarms.active.code}] {self.ctx.alarms.active.message}"
            css = base % "#922b21" + "background:#f5b7b1;color:#641e16;"
            start_tip = "报警中，无法启动"
            start_ok = False
        elif state == MachineState.INITIALIZING or gvl.Main.Initializing:
            step = int(gvl.Main.Init_Auto or 0)
            text = f"初始化进行中…（步 {step}）— 完成后才能启动"
            css = base % "#b9770e" + "background:#fdebd0;color:#6e2c00;"
            start_tip = "初始化尚未完成，请等待"
            start_ok = False
        elif init_done and state in (
            MachineState.READY,
            MachineState.RUNNING,
            MachineState.PAUSED,
            MachineState.STOPPED,
        ):
            if state == MachineState.RUNNING:
                text = "初始化完成 · 运行中"
            elif state == MachineState.PAUSED:
                text = "初始化完成 · 已暂停 — 可再点「启动」继续"
            elif state == MachineState.STOPPED:
                text = "初始化完成 · 已停止 — 可再点「启动」"
            else:
                text = "✓ 初始化完成 — 可以点「启动」"
            if link_err:
                text += f"\n⚠ {link_err}"
                css = base % "#b9770e" + "background:#fdebd0;color:#6e2c00;"
                start_ok = False
                start_tip = link_err
            else:
                css = base % "#145a32" + "background:#d5f5e3;color:#145a32;"
                start_ok = state != MachineState.RUNNING
                start_tip = "可以启动" if start_ok else "已在运行"
        else:
            # IDLE 或其它未完成
            text = "未初始化 — 请先点「初始化」，完成后再「启动」"
            if self.ctx.init_message and self.ctx.init_message not in ("", "初始化完成"):
                text += f"\n{self.ctx.init_message}"
            if link_err:
                text += f"\n⚠ {link_err}"
            css = base % "#7f8c8d" + "background:#e5e8e8;color:#1c2833;"
            start_ok = False
            start_tip = "请先完成初始化"

        self.lbl_init_flag.setPlainText(text)
        self.lbl_init_flag.setStyleSheet(f"QTextEdit{{{css}}}")
        self.lbl_init_flag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_start.setToolTip(start_tip)
        # 启动按钮文案/亮度：未就绪时灰显但仍可点（弹窗说明原因）
        from hmi.style import style_button

        if state == MachineState.PAUSED and start_ok:
            self.btn_start.setText("启动(继续)")
            style_button(self.btn_start, "success")
        elif start_ok:
            self.btn_start.setText("启动 ✓")
            style_button(self.btn_start, "success")
        else:
            self.btn_start.setText("启动")
            style_button(self.btn_start, "neutral")
        self.btn_start.setEnabled(True)

    def _on_copy_alarm(self) -> None:
        """复制当前活动报警（无弹窗）。"""
        a = self.ctx.alarms.active
        if a:
            text = format_alarm_text(a.code, a.station, a.step, a.message)
        else:
            text = (self.lbl_init_flag.toPlainText() or "").strip()
        if not text:
            self.btn_copy_alarm.setText("无报警")
        else:
            QGuiApplication.clipboard().setText(text)
            self.btn_copy_alarm.setText("已复制")
        QTimer.singleShot(1600, lambda: self.btn_copy_alarm.setText("复制报警"))

    def _on_alarm_reset(self) -> None:
        tips = self.coord.cmd_alarm_reset() or []
        text = "\n".join(str(t) for t in tips) if tips else "已复位"
        failed = any(
            ("失败" in str(t))
            or ("仍有故障" in str(t))
            or ("未连接" in str(t))
            or ("返回" in str(t) and "ResetAllError" in str(t))
            for t in tips
        )
        if failed:
            QMessageBox.warning(self, "报警复位", text)
        else:
            QMessageBox.information(self, "报警复位", text)

    def _apply_blend(self) -> None:
        enable = bool(self.chk_blend.isChecked())
        bt = int(self.sp_blend_t.value())
        br = float(self.sp_blend_r.value())
        delay = float(self.sp_blend_delay.value())
        motion = self.ctx.cfg.setdefault("motion", {})
        if not isinstance(motion, dict):
            motion = {}
            self.ctx.cfg["motion"] = motion
        motion["blend_enable"] = enable
        motion["blend_t_ms"] = bt
        motion["blend_r_mm"] = br
        motion["blend_queue_delay_s"] = delay
        self.ctx.robot1.set_blend(
            enable=enable, blend_t_ms=bt, blend_r_mm=br, queue_delay_s=delay
        )
        self.ctx.robot2.set_blend(
            enable=enable, blend_t_ms=bt, blend_r_mm=br, queue_delay_s=delay
        )
        save_config(self.ctx.cfg)
        QMessageBox.information(
            self,
            "路径平滑",
            f"已{'开启' if enable else '关闭'}总开关\n"
            f"blendT={bt}ms  blendR={br}mm  衔接提前={delay}s（全局默认）\n"
            "点位页可为单点覆盖 T/R；取放终点仍强制到位。",
        )

    def _grip_cmd(self, which: int, *, open_: bool) -> None:
        """监视页夹爪手动：阻塞等张开/夹紧完成。"""
        if self._grip_auto_locked():
            QMessageBox.information(
                self,
                "夹爪",
                "自动连续运行中请先暂停/停止，再手动开合夹爪。",
            )
            return
        g = self.ctx.gripper1 if which == 1 else self.ctx.gripper2
        rk = "robot1" if which == 1 else "robot2"
        name = "上料" if which == 1 else "下料"
        try:
            # 发令前同步当前速度旋钮
            self._apply_grip_speeds_from_ui()
            ok = g.open_claw() if open_ else g.close_claw()
            if open_:
                self.ctx.set_robot_holding_shoe(rk, False)
            if not ok:
                QMessageBox.warning(
                    self,
                    "夹爪",
                    f"{name}夹爪{'张开' if open_ else '夹紧'}失败: {g.last_error or '无反馈确认'}",
                )
            self._refresh_grip_labels()
        except Exception as e:
            QMessageBox.warning(self, "夹爪", f"{name}夹爪操作失败: {e}")

    def _grip_auto_locked(self) -> bool:
        from core.machine_state import MachineState, RunMode

        m = self.ctx.machine
        return m.state == MachineState.RUNNING and m.mode == RunMode.AUTO

    def _apply_grip_speeds_from_ui(self) -> None:
        self.ctx.gripper1.set_speeds(
            float(self.sp_g1_open_spd.value()),
            float(self.sp_g1_close_spd.value()),
        )
        self.ctx.gripper2.set_speeds(
            float(self.sp_g2_open_spd.value()),
            float(self.sp_g2_close_spd.value()),
        )

    def _on_grip_speed_changed(self, *_args) -> None:
        self._apply_grip_speeds_from_ui()

    def _save_grip_speeds(self) -> None:
        self._apply_grip_speeds_from_ui()
        g1 = self.ctx.cfg.setdefault("grippers", {}).setdefault("gripper1", {})
        g2 = self.ctx.cfg.setdefault("grippers", {}).setdefault("gripper2", {})
        g1["open_speed"] = float(self.ctx.gripper1.open_speed)
        g1["close_speed"] = float(self.ctx.gripper1.close_speed)
        g2["open_speed"] = float(self.ctx.gripper2.open_speed)
        g2["close_speed"] = float(self.ctx.gripper2.close_speed)
        save_config(self.ctx.cfg)
        QMessageBox.information(self, "已保存", "夹爪开合速度已写入 config/default.yaml")

    def _set_grip_lamp(self, label: QLabel, on: bool, color: str) -> None:
        if on:
            label.setStyleSheet(
                f"background:{color};color:#111;padding:6px 8px;border-radius:4px;font-weight:bold;"
            )
        else:
            label.setStyleSheet(
                "background:#444;color:#aaa;padding:6px 8px;border-radius:4px;"
            )

    def _refresh_grip_labels(self) -> None:
        if not hasattr(self, "lbl_g1"):
            return
        g1, g2 = self.ctx.gripper1, self.ctx.gripper2
        busy1 = "动作中…" if g1.busy else ("张开" if not g1.closed else "夹紧")
        busy2 = "动作中…" if g2.busy else ("张开" if not g2.closed else "夹紧")
        self.lbl_g1.setText(
            f"上料夹爪: {busy1} | spd开={g1.open_speed:.0f}/关={g1.close_speed:.0f}"
            + ("" if g1.last_ok else f" | 错:{g1.last_error}")
        )
        self.lbl_g2.setText(
            f"下料夹爪: {busy2} | spd开={g2.open_speed:.0f}/关={g2.close_speed:.0f}"
            + ("" if g2.last_ok else f" | 错:{g2.last_error}")
        )
        if hasattr(self, "lamp_g1_open"):
            self._set_grip_lamp(self.lamp_g1_open, g1.open_done, "#2ecc71")
            self._set_grip_lamp(self.lamp_g1_close, g1.close_done, "#f39c12")
            self._set_grip_lamp(self.lamp_g2_open, g2.open_done, "#2ecc71")
            self._set_grip_lamp(self.lamp_g2_close, g2.close_done, "#f39c12")
        locked = self._grip_auto_locked()
        self.btn_g1_open.setEnabled(not locked and not g1.busy)
        self.btn_g1_close.setEnabled(not locked and not g1.busy)
        self.btn_g2_open.setEnabled(not locked and not g2.busy)
        self.btn_g2_close.setEnabled(not locked and not g2.busy)

    def _slot_ui_editable(self) -> bool:
        """非自动运行：槽号、顺序可改。"""
        return bool(self.ctx.machine.memory_editable)

    def _on_monitor_slot_lock(self, on: bool) -> None:
        if self._syncing_slot_ui:
            return
        if not self._slot_ui_editable():
            return
        self.ctx.press.manual_slot_lock = bool(on)
        self._refresh_hero_slots()

    def _on_monitor_slot_spin(self, which: str = "") -> None:
        """改一侧槽号，按顺序自动改另一侧。"""
        if self._syncing_slot_ui:
            return
        if not self._slot_ui_editable():
            return
        p = self.ctx.press
        if which == "place":
            p.place_slot = int(self.sp_mon_place.value())
            p.pair_from_place()
        else:
            p.pick_slot = int(self.sp_mon_pick.value())
            p.pair_from_pick()
        p.manual_slot_lock = bool(self.chk_mon_slot_lock.isChecked())
        self._syncing_slot_ui = True
        self.sp_mon_place.setValue(int(p.place_slot))
        self.sp_mon_pick.setValue(int(p.pick_slot))
        self._syncing_slot_ui = False
        self._refresh_hero_slots()

    def _apply_monitor_slots(self) -> None:
        if not self._slot_ui_editable():
            QMessageBox.information(self, "槽号", "自动运行中不可改槽号，请先暂停或停止。")
            return
        self.ctx.press.set_current_slots(
            pick=int(self.sp_mon_pick.value()),
            place=int(self.sp_mon_place.value()),
            lock=bool(self.chk_mon_slot_lock.isChecked()),
            derive_place=False,
        )
        press = self.ctx.cfg.setdefault("press", {})
        fs = press.setdefault("four_slot", {})
        fs["mock_pick_slot"] = int(self.sp_mon_pick.value())
        fs["mock_place_slot"] = int(self.sp_mon_place.value())
        fs["slot_sequence"] = str(self.cmb_mon_seq.currentData() or "12341")
        self.ctx.press.cfg = press
        save_config(self.ctx.cfg)
        self._refresh_hero_slots()

    def _on_monitor_seq_changed(self, *_args) -> None:
        if getattr(self, "_syncing_slot_ui", False):
            return
        if not self._slot_ui_editable():
            return
        press = self.ctx.cfg.setdefault("press", {})
        fs = press.setdefault("four_slot", {})
        fs["slot_sequence"] = str(self.cmb_mon_seq.currentData() or "12341")
        self.ctx.press.cfg = press
        self.ctx.press.pair_from_pick()
        self._syncing_slot_ui = True
        self.sp_mon_place.setValue(int(self.ctx.press.place_slot))
        self.sp_mon_pick.setValue(int(self.ctx.press.pick_slot))
        self._syncing_slot_ui = False
        save_config(self.ctx.cfg)
        self._refresh_hero_slots()

    def _press_cmd(self, kind: str, on: bool) -> None:
        """压鞋机手动：rotate / press。"""
        from core.machine_state import MachineState

        if self.ctx.machine.state == MachineState.RUNNING:
            r = QMessageBox.question(
                self,
                "确认",
                "当前正在自动运行，手动操作压鞋机可能与 Station6 抢令。\n仍要继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            if kind == "rotate":
                if on:
                    self.ctx.press.set_rotate(True)
                else:
                    self.ctx.press.set_rotate(False)
            else:
                if on:
                    self.ctx.press.set_start_press(True)
                else:
                    self.ctx.press.set_start_press(False)
            self._refresh_press_manual_label()
        except Exception as e:
            QMessageBox.warning(self, "压鞋机", f"操作失败: {e}")

    def _on_press_force_done(self) -> None:
        try:
            if self.ctx.press.use_mock:
                self.ctx.press.simulate_rotate_done()
            else:
                # 真机：只关输出命令，到位信号仍由 PLC 反馈
                self.ctx.press.set_rotate(False)
                self.ctx.press.set_start_press(False)
                self.ctx.press.refresh_inputs()
                QMessageBox.information(
                    self,
                    "压鞋机",
                    "已关闭旋转/压鞋命令。\n真机「到位」请看 PLC 反馈；"
                    "若 Station6 卡住，确认 addr_rotate_done / addr_press_done。",
                )
            self._refresh_press_manual_label()
        except Exception as e:
            QMessageBox.warning(self, "压鞋机", str(e))

    def _refresh_press_manual_label(self) -> None:
        if not hasattr(self, "lbl_press_manual"):
            return
        try:
            self.ctx.press.refresh_inputs()
        except Exception:
            pass
        p = self.ctx.press.snapshot()
        lock = " [手动锁定]" if p.get("manual_slot_lock") else ""
        seq = p.get("slot_sequence", "12341")
        auto = "自算" if p.get("auto_compute_slots") else "读PLC"
        self.lbl_press_manual.setText(
            f"顺序{seq}（{auto}）　放料#{p.get('place_slot','?')}　"
            f"取料#{p.get('pick_slot','?')}{lock}  "
            f"旋转到位={p['rotate_done']}  压合完成={p['press_done']}  "
            f"取料槽可取={p.get('pick_ready')}  |  "
            f"旋令={p['cmd_rotate']}  压令={p['cmd_start_press']}"
            f"{'  (Mock)' if self.ctx.press.use_mock else ''}"
        )
        if hasattr(self, "sp_mon_place"):
            self._syncing_slot_ui = True
            if not self.sp_mon_place.hasFocus():
                self.sp_mon_place.setValue(int(p.get("place_slot") or 1))
            if not self.sp_mon_pick.hasFocus():
                self.sp_mon_pick.setValue(int(p.get("pick_slot") or 1))
            self.chk_mon_slot_lock.blockSignals(True)
            self.chk_mon_slot_lock.setChecked(bool(p.get("manual_slot_lock")))
            self.chk_mon_slot_lock.blockSignals(False)
            self._syncing_slot_ui = False

    def _refresh_hero_slots(self, snap: dict | None = None) -> None:
        if not hasattr(self, "lbl_hero_place"):
            return
        p = snap if snap is not None else self.ctx.press.snapshot()
        place = int(p.get("place_slot") or 0)
        pick = int(p.get("pick_slot") or 0)
        seq = str(p.get("slot_sequence") or "12341")
        auto = "自算槽号" if p.get("auto_compute_slots") else "读PLC槽号"
        lock = "手动锁定" if p.get("manual_slot_lock") else auto
        ready = "可取" if p.get("pick_ready") else "未完成"
        self.lbl_hero_place.setText(f"放料槽（左口）\n#{place}")
        self.lbl_hero_pick.setText(f"取料槽（右口）\n#{pick}")
        self.lbl_hero_slot_meta.setText(
            f"顺序 {seq} ｜ {lock} ｜ 旋转到位={p.get('rotate_done')}  "
            f"压合={p.get('press_done')}  取料槽{ready}"
        )
        if hasattr(self, "cmb_mon_seq") and not self.cmb_mon_seq.hasFocus():
            self._syncing_slot_ui = True
            want = "43214" if seq in ("43214", "reverse", "反序") else "12341"
            idx = self.cmb_mon_seq.findData(want)
            if idx >= 0 and self.cmb_mon_seq.currentIndex() != idx:
                self.cmb_mon_seq.setCurrentIndex(idx)
            self._syncing_slot_ui = False

    def _on_vel_changed(self, which: str, value: int) -> None:
        if self._syncing_vel:
            return
        if which == "robot1":
            self.ctx.robot1.set_vel(value)
            self.ctx.cfg["robots"]["robot1"]["vel"] = float(value)
        else:
            self.ctx.robot2.set_vel(value)
            self.ctx.cfg["robots"]["robot2"]["vel"] = float(value)
        self._update_vel_labels()

    def _on_vel_both_changed(self, value: int) -> None:
        if self._syncing_vel:
            return
        self._syncing_vel = True
        self.sld_vel1.setValue(value)
        self.sld_vel2.setValue(value)
        self._syncing_vel = False
        self.ctx.robot1.set_vel(value)
        self.ctx.robot2.set_vel(value)
        self.ctx.cfg["robots"]["robot1"]["vel"] = float(value)
        self.ctx.cfg["robots"]["robot2"]["vel"] = float(value)
        self._update_vel_labels()

    def _save_vel(self, which: str) -> None:
        save_config(self.ctx.cfg)
        log_msg = self.ctx.cfg["robots"][which]["vel"]
        # 轻量提示：标签旁瞬时显示已保存（避免弹窗打断操作）
        if which == "robot1":
            self.lbl_vel1.setText(f"上料机器人 {int(log_msg)}%（已保存）")
        else:
            self.lbl_vel2.setText(f"下料机器人 {int(log_msg)}%（已保存）")

    def _save_vel_both(self) -> None:
        save_config(self.ctx.cfg)
        self.lbl_vel_both.setText(f"两臂同步 {self.sld_vel_both.value()}%（已保存）")

    def _on_step_next(self) -> None:
        """单步推进：忙站发 StepPulse；无忙站时仅推进初始化。"""
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        pulsed = 0
        for st in self.coord.stations:
            if st.active_auto_step() is not None:
                st.operator_run_current()
                pulsed += 1
        if pulsed == 0:
            self.ctx.gvl.Main.InitStepPulse = True
            self.ctx.request_step_go()
    def _on_start_dry_program(self) -> None:
        """一键空跑程序：开屏蔽 + 自动模式；用户再初始化/启动。"""
        self.ctx.dry_run.enable()
        self.ctx.machine.set_mode(RunMode.AUTO)
        QMessageBox.information(
            self,
            "空跑程序已就绪",
            "已启用空跑屏蔽（光电/压机Mock；Station6 先压后转自动完成；相机模拟仍按通信配置），"
            "并切到「自动」模式。\n\n"
            "请按：初始化 → 启动。\n"
            "若要逐步验证：模式切「单步」后点「单步：下一步」，"
            "或到「工位调试」页武装后推进。",
        )

    def _on_dry_run_on(self) -> None:
        self.ctx.dry_run.enable()
        QMessageBox.information(
            self,
            "空跑已启用",
            "已启用空跑屏蔽（光电/相机/放料跟手/取料时序/压机先压后转）。\n"
            "细节可到「空跑联调」页调整。\n"
            "请「初始化」→「启动」验证。",
        )

    def _on_dry_run_off(self) -> None:
        self.ctx.dry_run.disable()

    def _belt_di_id(self) -> int:
        return int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))

    def _belt_force_toggled(self, on: bool) -> None:
        """真机臂：勾选后光电走 HMI 模拟；取消则读真机 GetDI。"""
        self.ctx.robot1.set_di_force_mock(self._belt_di_id(), bool(on))
        self.ctx.cfg["robots"]["robot1"]["di_belt_use_mock"] = bool(on)

    def _belt_toggled(self, on: bool) -> None:
        if self._syncing_belt_chk:
            return
        if not self.chk_belt_force.isChecked() and not self.ctx.robot1.use_mock:
            # 未开强制模拟时，勾选框只显示真机状态，不写缓存
            return
        self.ctx.robot1.set_di_mock(self._belt_di_id(), bool(on))

    def _set_belt_sensor(self, value: bool) -> None:
        """模拟光电到位/离开。"""
        if not self.ctx.robot1.use_mock and not self.chk_belt_force.isChecked():
            self.chk_belt_force.setChecked(True)  # 自动打开强制模拟
        self.ctx.robot1.set_di_mock(self._belt_di_id(), bool(value))
        self._syncing_belt_chk = True
        self.chk_belt.blockSignals(True)
        self.chk_belt.setChecked(bool(value))
        self.chk_belt.blockSignals(False)
        self._syncing_belt_chk = False

    def _on_sim_rotate_done(self) -> None:
        self.ctx.press.simulate_rotate_done()
        # 立刻刷新勾选显示
        self._syncing_press_chk = True
        self.chk_rotate_done.setChecked(True)
        self.chk_press_done.setChecked(bool(self.ctx.press.press_done))
        self._syncing_press_chk = False

    def _mem_toggled(self, idx: int, on: bool) -> None:
        if not self.ctx.machine.memory_editable:
            return
        self.ctx.memory[idx] = bool(on)

    def _set_light(self, label: QLabel, on: bool, color: str):
        # 大圆灯：亮=高亮+描边；灭=深灰底仍可辨认颜色字
        if on:
            label.setStyleSheet(
                f"background:{color};color:#111;font-size:18px;font-weight:bold;"
                f"border:3px solid #111;border-radius:28px;"
            )
        else:
            label.setStyleSheet(
                "background:#2c3e50;color:#7f8c8d;font-size:16px;font-weight:bold;"
                "border:3px solid #566573;border-radius:28px;"
            )

    def _refresh_link_panel(self) -> None:
        rows = self.ctx.device_link_snapshot()
        missing = [r for r in rows if (not r["mock"]) and (not r["ok"])]
        for r in rows:
            lb = self._link_labels.get(r["name"])
            if lb is None:
                lb = QLabel()
                n = len(self._link_labels)
                self._link_labels[r["name"]] = lb
                self._link_grid.addWidget(lb, n // 2, n % 2)
            tip = f"{r['endpoint']}"
            if r.get("error"):
                tip += f"\n{r['error']}"
            if r["mock"]:
                text = f"{r['name']}\n模拟"
                css = "background:#5d6d7e;color:#fff;padding:6px 8px;border-radius:5px;font-weight:bold;"
            elif r.get("opening"):
                text = f"{r['name']}\n正在连接…"
                css = "background:#b9770e;color:#fff;padding:6px 8px;border-radius:5px;font-weight:bold;"
            elif r["ok"]:
                text = f"{r['name']}\n已连接"
                css = "background:#1a7a37;color:#fff;padding:6px 8px;border-radius:5px;font-weight:bold;"
            else:
                text = f"{r['name']}\n{r['status']}"
                css = "background:#c0392b;color:#fff;padding:6px 8px;border-radius:5px;font-weight:bold;"
            lb.setText(text)
            lb.setToolTip(tip)
            lb.setStyleSheet(css)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setWordWrap(True)
            lb.setMinimumHeight(48)
        if missing:
            names = "、".join(f"{r['name']}({r['endpoint']})" for r in missing)
            self.lbl_link_warn.setText(
                f"⚠ 有设备未连接：{names}\n"
                "非 Mock 设备将按间隔自动重连；请检查网线/IP/电源，或在「通信配置」改为 Mock。"
            )
            self.lbl_link_warn.setStyleSheet(
                "background:#fdebd0;color:#922b21;padding:8px;border-radius:4px;font-weight:bold;"
            )
            self.lbl_link_warn.setVisible(True)
        else:
            self.lbl_link_warn.setVisible(False)

    def refresh(self) -> None:
        mw = getattr(self.coord, "mobile_web", None)
        if mw is not None:
            tok = getattr(mw, "token", "") or ""
            tip = f"手机监控: {mw.access_url()}"
            if tok:
                tip += f"  口令={tok}"
            tip += "  （手机与工控机同一局域网；关闭改 yaml system.mobile_web.enabled=false）"
            self.lbl_mobile.setText(tip)
        else:
            self.lbl_mobile.setText(
                "手机监控未启用。在 config/default.yaml 设 system.mobile_web.enabled: true 后重启。"
            )

        snap = self.ctx.machine.snapshot()
        init_tag = "已初始化" if (snap.get("init_ok") or self.ctx.gvl.Main.InitDone) else "未初始化"
        self.lbl_state.setText(
            f"状态: {snap['state']}  |  {init_tag}  |  {self.ctx.init_message or '-'}"
        )
        self.lbl_mode.setText(f"模式: {snap['mode']}")
        tips = []
        for r in (self.ctx.robot1, self.ctx.robot2):
            detail = getattr(r, "last_auto_cleared", "") or ""
            at = float(getattr(r, "last_auto_cleared_at", 0) or 0)
            if detail and at and (time.time() - at) < 60.0:
                tips.append(f"{r.name}: {detail}")
        if tips:
            self.lbl_auto_clear.setText(
                "示教器瞬态报警已自动消除（详情也在「报警」页标[瞬态]）\n" + "\n".join(tips)
            )
            self.lbl_auto_clear.setVisible(True)
        else:
            self.lbl_auto_clear.setVisible(False)
        self._refresh_grip_labels()
        self._refresh_press_manual_label()
        self._refresh_hero_slots()
        lights = self.ctx.lights.snapshot()
        self._set_light(self.light_r, lights["red"], "#ff2d2d")
        self._set_light(self.light_y, lights["yellow"], "#ffd400")
        self._set_light(self.light_g, lights["green"], "#19e05a")
        self._refresh_init_flag()
        self._refresh_link_panel()

        ps = self.ctx.production.snapshot()
        self.lbl_ct.setText(f"CT: {ps['last_ct_s']:.2f} s" if ps["last_ct_s"] > 0 else "CT: -- s")
        self.lbl_uph.setText(f"UPH: {ps['uph_instant']:.1f}" if ps["uph_instant"] > 0 else "UPH: --")
        self.lbl_uph_avg.setText(f"UPH均: {ps['uph_avg']:.1f}" if ps["uph_avg"] > 0 else "UPH均: --")
        self.lbl_hour_cnt.setText(f"本小时: {ps['hour_count']}")
        self.lbl_total_cnt.setText(f"总产量: {ps['total']}")

        editable = self.ctx.machine.memory_editable
        if editable:
            self.mem_box.setTitle("当前槽号 / 记忆（可改槽号、顺序、记忆）")
            self.lbl_slot_edit_tip.setText("停止/暂停后可改槽号和顺序，改完点「应用槽号」。")
        else:
            self.mem_box.setTitle("当前槽号 / 记忆（自动运行中锁定）")
            self.lbl_slot_edit_tip.setText("自动运行中槽号与顺序锁定，暂停或停止后可改。")
        for w in (
            self.cmb_mon_seq,
            self.sp_mon_place,
            self.sp_mon_pick,
            self.chk_mon_slot_lock,
            self.btn_mon_slot_apply,
        ):
            w.setEnabled(editable)

        mem = self.ctx.memory.snapshot()
        for i, cb in self.mem_checks.items():
            cb.setEnabled(editable)
            want = bool(mem.get(i, False))
            if cb.isChecked() != want:
                cb.blockSignals(True)
                cb.setChecked(want)
                cb.blockSignals(False)
            lamp = self.mem_lamps.get(i)
            if lamp:
                if want:
                    lamp.setText(f"M{i}  ON")
                    lamp.setStyleSheet(
                        "background:#27ae60;color:#111;padding:4px;border-radius:3px;"
                        "font-weight:bold;font-size:13px;"
                    )
                else:
                    lamp.setText(f"M{i}")
                    lamp.setStyleSheet(
                        "background:#555;color:#ccc;padding:4px;border-radius:3px;font-size:13px;"
                    )

        for s in self.coord.stations:
            self.st_labels[s.name].setText(
                f"{s.name}: {s.status_text()} | {s.current_step_name()}"
            )

        belt_on = bool(self.ctx.robot1.get_di(self._belt_di_id()))
        self.lbl_belt.setText(f"光电 DI[{self._belt_di_id()}]: {'到位 True' if belt_on else '无鞋 False'}")
        if not self._syncing_belt_chk and self.chk_belt.isChecked() != belt_on:
            self._syncing_belt_chk = True
            self.chk_belt.blockSignals(True)
            self.chk_belt.setChecked(belt_on)
            self.chk_belt.blockSignals(False)
            self._syncing_belt_chk = False

        self.lbl_belt_mock.setText(self.ctx.vision.belt_mock_status_text())
        dry_on = bool(self.ctx.dry_run.enabled)
        self.lbl_dry.setText(
            ("空跑屏蔽：开 — " if dry_on else "空跑屏蔽：关 — ")
            + " | ".join(self.ctx.dry_run.status_lines()[1:3])
        )
        self.lbl_dry.setStyleSheet(
            (
                "background:#d5f5e3;color:#145a32;padding:8px;border-radius:4px;font-weight:bold;"
                if dry_on
                else "background:#fadbd8;color:#7b241c;padding:8px;border-radius:4px;font-weight:bold;"
            )
        )
        # 空跑 tick 会改 Mock 勾选，刷新时同步显示
        v = self.ctx.vision
        if self.chk_place_mat.isChecked() != bool(v.mock_place_has_material):
            self.chk_place_mat.blockSignals(True)
            self.chk_place_mat.setChecked(bool(v.mock_place_has_material))
            self.chk_place_mat.blockSignals(False)
        if self.chk_place_left.isChecked() != bool(v.mock_place_is_left):
            self.chk_place_left.blockSignals(True)
            self.chk_place_left.setChecked(bool(v.mock_place_is_left))
            self.chk_place_left.blockSignals(False)
        if self.chk_pick_mat.isChecked() != bool(v.mock_pick_has_material):
            self.chk_pick_mat.blockSignals(True)
            self.chk_pick_mat.setChecked(bool(v.mock_pick_has_material))
            self.chk_pick_mat.blockSignals(False)
        m8, m9, m10 = bool(mem.get(8)), bool(mem.get(9)), bool(mem.get(10))
        m2, m3, m4 = bool(mem.get(2)), bool(mem.get(3)), bool(mem.get(4))
        from devices.pose_utils import is_left_shoe_flag

        # 规则：左鞋→左槽、右鞋→右槽，且空槽才可放料（勿用带后缀的字符串做 == 判断）
        hand_is_left: bool | None
        if m8 and not m9:
            hand_is_left = True
        elif m9 and not m8:
            hand_is_left = False
        elif m2:
            snap0 = getattr(self.ctx.gvl, "BeltPickSnapshot", None) or self.ctx.gvl.PickPose
            hand_is_left = is_left_shoe_flag(snap0.get("is_left_shoe", True))
        else:
            hand_is_left = None

        if hand_is_left is True:
            hand_txt = "左鞋"
        elif hand_is_left is False:
            hand_txt = "右鞋"
        else:
            hand_txt = "无鞋"
        snap = getattr(self.ctx.gvl, "BeltPickSnapshot", None)
        if isinstance(snap, dict) and m2:
            hand_txt += f"(Y={float(snap.get('y', 0)):.1f})"

        slot_is_left = bool(self.ctx.vision.mock_place_is_left)
        slot_txt = "左鞋槽" if slot_is_left else "右鞋槽"
        slot_empty = not bool(self.ctx.vision.mock_place_has_material)
        rule = "规则:左鞋→左槽 / 右鞋→右槽，且空槽才可放"

        if hand_is_left is None:
            match_txt = (
                f"{rule}\n手中无鞋 / Mock{slot_txt} | "
                f"Mem3={int(m3)} Mem4={int(m4)} Mem10={int(m10)}"
            )
            css = "background:#e5e8e8;color:#1c2833;padding:6px;border-radius:4px;"
        elif not slot_empty:
            match_txt = (
                f"{rule}\n手中{hand_txt} / Mock{slot_txt}【有料】→ 不可放料（转盘带压）"
            )
            css = "background:#fdebd0;color:#6e2c00;padding:6px;border-radius:4px;font-weight:bold;"
        elif hand_is_left == slot_is_left:
            match_txt = (
                f"{rule}\n手中{hand_txt} ↔ Mock{slot_txt}【空槽·对应正确】→ 允许放料"
            )
            css = "background:#d5f5e3;color:#145a32;padding:6px;border-radius:4px;font-weight:bold;"
        else:
            match_txt = (
                f"{rule}\n手中{hand_txt} ↔ Mock{slot_txt}【空槽·左右不对应】→ 禁止放料(Mem10)，"
                f"只转不压 | Mem3={int(m3)} Mem4={int(m4)} Mem10={int(m10)}"
            )
            css = "background:#f5b7b1;color:#641e16;padding:6px;border-radius:4px;font-weight:bold;"
        last_dec = getattr(self.ctx.gvl, "_last_place_decision", "") or ""
        last_m10 = getattr(self.ctx.gvl, "_last_place_mem10", None)
        if last_dec:
            match_txt = (
                f"{match_txt}\n上次Station3: {last_dec}"
                + (f"（判定时Mem10={int(last_m10)}）" if last_m10 is not None else "")
            )
        self.lbl_shoe_match.setText(f"方向联锁: {match_txt}")
        self.lbl_shoe_match.setStyleSheet(css)

        p = self.ctx.press.snapshot()
        lock = "锁定" if p.get("manual_slot_lock") else "自动"
        seq = p.get("slot_sequence", "12341")
        self.lbl_press.setText(
            f"压鞋机: 顺序{seq} 放料#{p.get('place_slot')} 取料#{p.get('pick_slot')}({lock}) "
            f"旋转到位={p['rotate_done']} 压合={p['press_done']} 可取={p.get('pick_ready')} "
            f"旋令={p['cmd_rotate']} 压令={p['cmd_start_press']}"
        )
        # 同步 Mock 勾选（程序改信号时）
        if not self._syncing_press_chk:
            self._syncing_press_chk = True
            if self.chk_rotate_done.isChecked() != p["rotate_done"]:
                self.chk_rotate_done.blockSignals(True)
                self.chk_rotate_done.setChecked(p["rotate_done"])
                self.chk_rotate_done.blockSignals(False)
            if self.chk_press_done.isChecked() != p["press_done"]:
                self.chk_press_done.blockSignals(True)
                self.chk_press_done.setChecked(p["press_done"])
                self.chk_press_done.blockSignals(False)
            self._syncing_press_chk = False
