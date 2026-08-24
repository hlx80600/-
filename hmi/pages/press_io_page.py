"""压机信号：公共口 + 槽1~4 独立地址可改；RX/TX 实时可视。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from devices.press_modbus import WORK_STATUS_NAMES
from hmi.style import apply_page_chrome, style_button

SLOT_ADDR_ROWS = [
    ("addr_shoe_placed", "放鞋完成"),
    ("addr_motor_start", "压杆电机启动"),
    ("addr_motor_done", "压杆电机完成"),
    ("addr_move_distance", "压杆距离D"),
    ("addr_slot_up", "槽上升/压合"),
    ("addr_rod_aligned", "压杆回正X"),
    ("addr_rod_in_pos", "压杆到位X"),
    ("addr_base_down", "大座下到位X"),
    ("addr_rod_home", "压杆原点X"),
    ("addr_work_status", "工作状态D"),
    ("addr_estop", "急停M"),
    ("addr_rod_forward", "压杆前进"),
    ("addr_rod_back", "压杆后退"),
    ("addr_rod_go_home", "压杆回原"),
    ("addr_press_up", "大座上升"),
    ("addr_press_down", "大座下降"),
]


def _hex_spin(val: int) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(0, 0xFFFF)
    sp.setDisplayIntegerBase(16)
    sp.setPrefix("0x")
    sp.setValue(int(val) & 0xFFFF)
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore
    return sp


class PressIoPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._syncing = False
        self.slot_spins: dict[int, dict[str, QSpinBox]] = {}
        self.lbl_slot_live: dict[int, QLabel] = {}

        root = QVBoxLayout(self)

        box_live = QGroupBox("接收信号 RX（实时）")
        vl = QVBoxLayout(box_live)
        self.lbl_slots = QLabel("-")
        self.lbl_slots.setStyleSheet(
            "background:#273746;color:#ecf0f1;padding:12px;border-radius:4px;"
            "font-size:18px;font-weight:bold;"
        )
        self.lbl_slots.setAlignment(Qt.AlignCenter)
        vl.addWidget(self.lbl_slots)

        # 当前槽号：显示 + 可手动改
        slot_row = QHBoxLayout()
        slot_row.addWidget(QLabel("当前放料槽号(左口)"))
        self.sp_cur_place = QSpinBox()
        self.sp_cur_place.setRange(1, 4)
        self.sp_cur_place.setValue(int(self.ctx.press.place_slot))
        self.sp_cur_place.setMinimumWidth(70)
        self.sp_cur_place.valueChanged.connect(lambda _v: self._on_io_slot_spin("place"))
        slot_row.addWidget(self.sp_cur_place)
        slot_row.addWidget(QLabel("当前取料槽号(右口)"))
        self.sp_cur_pick = QSpinBox()
        self.sp_cur_pick.setRange(1, 4)
        self.sp_cur_pick.setValue(int(self.ctx.press.pick_slot))
        self.sp_cur_pick.setMinimumWidth(70)
        self.sp_cur_pick.valueChanged.connect(lambda _v: self._on_io_slot_spin("pick"))
        slot_row.addWidget(self.sp_cur_pick)
        self.chk_slot_lock = QCheckBox("锁定手动槽号")
        self.chk_slot_lock.setToolTip(
            "勾选后旋转到位仍可按顺序推进；周期刷新不再改你设的槽号。\n"
            "压杆发令→放料槽；取料完成判断→取料槽。"
        )
        self.chk_slot_lock.setChecked(bool(self.ctx.press.manual_slot_lock))
        self.chk_slot_lock.toggled.connect(self._on_io_slot_lock)
        slot_row.addWidget(self.chk_slot_lock)
        btn_apply_slot = QPushButton("应用槽号")
        style_button(btn_apply_slot, "warn")
        btn_apply_slot.clicked.connect(self._apply_current_slots)
        slot_row.addWidget(btn_apply_slot)
        btn_derive = QPushButton("取料→推算放料")
        style_button(btn_derive, "primary")
        btn_derive.setToolTip("用取料槽号顺时针推算放料槽号后应用")
        btn_derive.clicked.connect(self._apply_pick_derive_place)
        slot_row.addWidget(btn_derive)
        slot_row.addStretch(1)
        vl.addLayout(slot_row)

        g = QGridLayout()
        self.lamp = {}
        for i, (key, title) in enumerate(
            [
                ("power_ok", "上电完成"),
                ("rotate_done", "旋转完成"),
                ("press_done", "压合完成"),
                ("pick_ready", "右口可取"),
                ("host_control", "上位机控制"),
            ]
        ):
            lb = QLabel(title)
            lb.setAlignment(Qt.AlignCenter)
            lb.setMinimumWidth(90)
            self.lamp[key] = lb
            g.addWidget(lb, 0, i)
        vl.addLayout(g)
        self.lbl_place = QLabel("-")
        self.lbl_pick = QLabel("-")
        self.lbl_place.setWordWrap(True)
        self.lbl_pick.setWordWrap(True)
        vl.addWidget(self.lbl_place)
        vl.addWidget(self.lbl_pick)
        for i in range(1, 5):
            lb = QLabel(f"槽{i}: -")
            lb.setWordWrap(True)
            self.lbl_slot_live[i] = lb
            vl.addWidget(lb)
        root.addWidget(box_live)

        box_tx = QGroupBox("发送信号 TX（最近写入）")
        self.lbl_tx = QLabel("-")
        self.lbl_tx.setWordWrap(True)
        self.lbl_tx.setStyleSheet(
            "background:#eef2f5;padding:8px;border-radius:4px;font-family:monospace;"
        )
        txl = QVBoxLayout(box_tx)
        txl.addWidget(self.lbl_tx)
        root.addWidget(box_tx)

        press = self.ctx.cfg.get("press") or {}
        fs = press.get("four_slot") or {}
        op = press.get("opening") or {}

        box_map = QGroupBox("开口约定与槽号")
        fm = QFormLayout(box_map)
        self.chk_4 = QCheckBox("启用四槽逻辑")
        self.chk_4.setChecked(bool(fs.get("enabled", True)))
        self.cmb_seq = QComboBox()
        self.cmb_seq.addItem("12341 正序（1→2→3→4→1）", "12341")
        self.cmb_seq.addItem("43214 反序（4→3→2→1→4）", "43214")
        cur_seq = str(fs.get("slot_sequence", "12341") or "12341")
        idx = self.cmb_seq.findData("43214" if cur_seq in ("43214", "reverse", "反序") else "12341")
        self.cmb_seq.setCurrentIndex(max(0, idx))
        self.chk_auto_slot = QCheckBox("自动运行自行计算槽号（旋转到位后按顺序推进）")
        self.chk_auto_slot.setChecked(bool(fs.get("auto_compute_slots", True)))
        self.chk_derive = QCheckBox("由取料口推算放料口（随正/反序变化）")
        self.chk_derive.setChecked(bool(fs.get("derive_place_from_pick", True)))
        self.cmb_place_open = QComboBox()
        self.cmb_place_open.addItems(["left", "right"])
        self.cmb_place_open.setCurrentText(str(op.get("place", "left")))
        self.cmb_pick_open = QComboBox()
        self.cmb_pick_open.addItems(["right", "left"])
        self.cmb_pick_open.setCurrentText(str(op.get("pick", "right")))
        self.sp_addr_pick = _hex_spin(int(fs.get("addr_pick_slot", 0x2100) or 0))
        self.sp_addr_place = _hex_spin(int(fs.get("addr_place_slot", 0x2101) or 0))
        fm.addRow(self.chk_4)
        fm.addRow("槽号顺序", self.cmb_seq)
        fm.addRow(self.chk_auto_slot)
        fm.addRow(self.chk_derive)
        fm.addRow("放料口物理开口", self.cmb_place_open)
        fm.addRow("取料口物理开口", self.cmb_pick_open)
        fm.addRow("PLC 右口槽号寄存器", self.sp_addr_pick)
        fm.addRow("PLC 左口槽号寄存器", self.sp_addr_place)
        root.addWidget(box_map)
        self.cmb_seq.currentIndexChanged.connect(self._apply_seq_live)
        self.chk_auto_slot.toggled.connect(self._apply_seq_live)

        box_g = QGroupBox("公共口地址")
        fg = QFormLayout(box_g)
        self.sp_host = _hex_spin(int(press.get("addr_host_control", 0xA11) or 0))
        self.sp_cmd_rot = _hex_spin(int(press.get("addr_cmd_rotate", 10) or 0))
        self.sp_rot_done = _hex_spin(int(press.get("addr_rotate_done", 1) or 0))
        self.sp_cmd_press = _hex_spin(int(press.get("addr_cmd_start_press", 11) or 0))
        self.sp_press_done = _hex_spin(int(press.get("addr_press_done", 2) or 0))
        self.sp_power = _hex_spin(int(press.get("addr_power_ok", 0) or 0))
        fg.addRow("上位机控制", self.sp_host)
        fg.addRow("旋转命令 / 完成", self._pair(self.sp_cmd_rot, self.sp_rot_done))
        fg.addRow("压合命令 / 完成", self._pair(self.sp_cmd_press, self.sp_press_done))
        fg.addRow("上电完成", self.sp_power)
        root.addWidget(box_g)

        tabs = QTabWidget()
        slots_cfg = press.get("slots") or {}
        for i in range(1, 5):
            sc = slots_cfg.get(i) or slots_cfg.get(str(i)) or {}
            page = QWidget()
            fl = QFormLayout(page)
            spins: dict[str, QSpinBox] = {}
            for key, lab in SLOT_ADDR_ROWS:
                sp = _hex_spin(int(sc.get(key, 0) or 0))
                spins[key] = sp
                fl.addRow(lab, sp)
            self.slot_spins[i] = spins
            tabs.addTab(page, f"槽{i}")
        root.addWidget(tabs)

        box_man = QGroupBox("手动（停止/暂停时操作）")
        mg = QGridLayout(box_man)
        self.cmb_man_slot = QComboBox()
        self.cmb_man_slot.addItems(["1", "2", "3", "4"])
        mg.addWidget(QLabel("操作槽号"), 0, 0)
        mg.addWidget(self.cmb_man_slot, 0, 1)
        btns = [
            ("压杆前进", lambda: self._rod("forward", True)),
            ("压杆停", lambda: self._rod("forward", False)),
            ("压杆后退", lambda: self._rod("back", True)),
            ("压杆回原", lambda: self._rod("home", True)),
            ("大座升", lambda: self._base(True)),
            ("大座降", lambda: self._base(False)),
            ("左口当前槽开始压合", self._start_place),
            ("模拟旋转完成", self.ctx.press.simulate_rotate_done),
            ("模拟压合完成", self.ctx.press.simulate_press_done),
        ]
        for i, (name, fn) in enumerate(btns):
            b = QPushButton(name)
            style_button(b, "warn" if "模拟" in name else "motion")
            b.clicked.connect(fn)
            mg.addWidget(b, 1 + i // 3, i % 3)
        root.addWidget(box_man)

        row = QHBoxLayout()
        btn_save = QPushButton("保存地址到 yaml 并应用")
        style_button(btn_save, "success")
        btn_save.clicked.connect(self._save)
        btn_recon = QPushButton("重连压机")
        style_button(btn_recon, "primary")
        btn_recon.clicked.connect(self._reconnect)
        row.addWidget(btn_save)
        row.addWidget(btn_recon)
        root.addLayout(row)

        apply_page_chrome(self)

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(a)
        lay.addWidget(b)
        return w

    def _apply_seq_live(self, *_args) -> None:
        """顺序/自算选项即时生效（仍建议点保存写入 yaml）。"""
        press = self.ctx.cfg.setdefault("press", {})
        fs = press.setdefault("four_slot", {})
        fs["slot_sequence"] = str(self.cmb_seq.currentData() or "12341")
        fs["auto_compute_slots"] = bool(self.chk_auto_slot.isChecked())
        self.ctx.press.cfg = press
        if bool(self.chk_derive.isChecked()) and not self.chk_slot_lock.isChecked():
            self.ctx.press._sync_derived_slots()
        self.refresh()

    def _on_io_slot_lock(self, on: bool) -> None:
        if self._syncing:
            return
        self.ctx.press.manual_slot_lock = bool(on)

    def _on_io_slot_spin(self, which: str = "") -> None:
        if self._syncing:
            return
        p = self.ctx.press
        if which == "place":
            p.place_slot = int(self.sp_cur_place.value())
            p.pair_from_place()
        else:
            p.pick_slot = int(self.sp_cur_pick.value())
            p.pair_from_pick()
        p.manual_slot_lock = bool(self.chk_slot_lock.isChecked())
        self._syncing = True
        self.sp_cur_place.setValue(int(p.place_slot))
        self.sp_cur_pick.setValue(int(p.pick_slot))
        self._syncing = False

    def _apply_current_slots(self) -> None:
        """手填当前放料/取料槽号；勾选锁定则不被 PLC 覆盖。"""
        self.ctx.press.set_current_slots(
            pick=int(self.sp_cur_pick.value()),
            place=int(self.sp_cur_place.value()),
            lock=bool(self.chk_slot_lock.isChecked()),
            derive_place=False,
        )
        self.refresh()

    def _apply_pick_derive_place(self) -> None:
        """只改取料槽，放料按顺时针推算。"""
        self.ctx.press.set_current_slots(
            pick=int(self.sp_cur_pick.value()),
            place=None,
            lock=bool(self.chk_slot_lock.isChecked()),
            derive_place=True,
        )
        self.sp_cur_place.setValue(int(self.ctx.press.place_slot))
        self.refresh()

    def _man_slot(self) -> int:
        return int(self.cmb_man_slot.currentText())

    def _rod(self, direction: str, on: bool) -> None:
        self.ctx.press.set_rod_move(self._man_slot(), direction, on)
        self.refresh()

    def _base(self, up: bool) -> None:
        self.ctx.press.set_base(self._man_slot(), up=up, on=True)
        self.refresh()

    def _start_place(self) -> None:
        self.ctx.press.refresh_inputs()
        self.ctx.press.begin_place_press()
        self.refresh()

    def _reconnect(self) -> None:
        ok = self.ctx.press.connect()
        QMessageBox.information(self, "压机", f"重连{'成功' if ok else '失败'}")
        self.refresh()

    def _save(self) -> None:
        press = self.ctx.cfg.setdefault("press", {})
        op = press.setdefault("opening", {})
        op["place"] = str(self.cmb_place_open.currentText())
        op["pick"] = str(self.cmb_pick_open.currentText())

        fs = press.setdefault("four_slot", {})
        fs["enabled"] = bool(self.chk_4.isChecked())
        fs["slot_sequence"] = str(self.cmb_seq.currentData() or "12341")
        fs["auto_compute_slots"] = bool(self.chk_auto_slot.isChecked())
        fs["derive_place_from_pick"] = bool(self.chk_derive.isChecked())
        fs["addr_pick_slot"] = int(self.sp_addr_pick.value())
        fs["addr_place_slot"] = int(self.sp_addr_place.value())
        fs["mock_pick_slot"] = int(self.sp_cur_pick.value())
        fs["mock_place_slot"] = int(self.sp_cur_place.value())
        fs.pop("place_side", None)
        fs.pop("pick_side", None)

        press["addr_host_control"] = int(self.sp_host.value())
        press["addr_cmd_rotate"] = int(self.sp_cmd_rot.value())
        press["addr_rotate_done"] = int(self.sp_rot_done.value())
        press["addr_cmd_start_press"] = int(self.sp_cmd_press.value())
        press["addr_press_done"] = int(self.sp_press_done.value())
        press["addr_power_ok"] = int(self.sp_power.value())

        slots = press.setdefault("slots", {})
        for i, spins in self.slot_spins.items():
            sc = slots.setdefault(i, {})
            for key, sp in spins.items():
                sc[key] = int(sp.value())
        press.pop("sides", None)

        self.ctx.press.cfg = press
        save_config(self.ctx.cfg)
        QMessageBox.information(self, "已保存", "公共口 + 槽1~4 地址已写入 config/default.yaml")
        self.refresh()

    def _set_lamp(self, key: str, on: bool) -> None:
        lb = self.lamp.get(key)
        if not lb:
            return
        if on:
            lb.setStyleSheet(
                "background:#2ecc71;color:#111;padding:8px;border-radius:4px;font-weight:bold;"
            )
        else:
            lb.setStyleSheet(
                "background:#555;color:#ccc;padding:8px;border-radius:4px;"
            )

    def refresh(self) -> None:
        if not self.isVisible():
            return
        p = self.ctx.press
        try:
            p.refresh_inputs()
        except Exception:
            pass
        snap = p.snapshot()
        lock = bool(snap.get("manual_slot_lock"))
        lock_txt = "　[手动锁定]" if lock else ""
        seq = snap.get("slot_sequence", "12341")
        auto = "自算" if snap.get("auto_compute_slots") else "读PLC"
        self.lbl_slots.setText(
            f"顺序 {seq}（{auto}）　当前放料槽 #{snap['place_slot']}（左口）　｜　"
            f"当前取料槽 #{snap['pick_slot']}（右口）{lock_txt}"
        )
        self._set_lamp("power_ok", bool(snap["power_ok"]))
        self._set_lamp("rotate_done", bool(snap["rotate_done"]))
        self._set_lamp("press_done", bool(snap["press_done"]))
        self._set_lamp("pick_ready", bool(snap["pick_ready"]))
        self._set_lamp("host_control", bool(snap.get("host_control")))

        def slot_line(slot: int, role: str) -> str:
            st = (snap.get("slots") or {}).get(slot) or (snap.get("slots") or {}).get(str(slot)) or {}
            ws = int(st.get("work_status", 0))
            return (
                f"{role} 槽#{slot} 状态={WORK_STATUS_NAMES.get(ws, ws)} "
                f"电机完成={st.get('motor_done')} 大座下={st.get('base_down')} "
                f"压杆到位={st.get('rod_in_pos')} 回正={st.get('rod_aligned')} "
                f"原点={st.get('rod_home')} 急停={st.get('estop')}"
            )

        self.lbl_place.setText(slot_line(int(snap["place_slot"]), "左口放料→压杆/底座发令"))
        self.lbl_pick.setText(slot_line(int(snap["pick_slot"]), "右口取料→完成判断"))
        for i in range(1, 5):
            mark = ""
            if i == int(snap["place_slot"]):
                mark = " [左口放料]"
            if i == int(snap["pick_slot"]):
                mark += " [右口取料]"
            self.lbl_slot_live[i].setText(slot_line(i, f"槽{i}{mark}"))

        tx = snap.get("last_tx") or {}
        if tx:
            self.lbl_tx.setText(" | ".join(f"{k}={v}" for k, v in list(tx.items())[-12:]))
        else:
            self.lbl_tx.setText("(尚无发送)")
        if not self._syncing:
            self._syncing = True
            if not self.sp_cur_place.hasFocus():
                self.sp_cur_place.setValue(int(snap["place_slot"]))
            if not self.sp_cur_pick.hasFocus():
                self.sp_cur_pick.setValue(int(snap["pick_slot"]))
            self.chk_slot_lock.blockSignals(True)
            self.chk_slot_lock.setChecked(lock)
            self.chk_slot_lock.blockSignals(False)
            # 手动默认对准当前放料槽
            if not self.cmb_man_slot.hasFocus():
                self.cmb_man_slot.setCurrentText(str(int(snap["place_slot"])))
            self._syncing = False
