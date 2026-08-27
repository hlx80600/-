"""夹爪调试页：按电机序号单独试夹、看状态、报警复位。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from devices.gripper_bank import motor_cfg, normalize_grippers_cfg, write_motor
from hmi.style import apply_page_chrome, style_button, style_many


class GripperDebugPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._busy_cmd = False

        root = QVBoxLayout(self)
        apply_page_chrome(self)

        tip = QLabel(
            "夹爪独立调试（达妙 DM-J4310-2EC）。自动连续运行中禁止开合；"
            "失败会报 GRIP_* 报警，可用本页或运行监控「报警复位」清除。"
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        # —— 选用电机 ——
        box_sel = QGroupBox("选用电机")
        fs = QFormLayout(box_sel)
        self.cmb_motor = QComboBox()
        self.cmb_motor.currentIndexChanged.connect(self._on_motor_changed)
        fs.addRow("电机序号", self.cmb_motor)
        self.lbl_bind = QLabel("-")
        fs.addRow("角色绑定", self.lbl_bind)
        self.lbl_ep = QLabel("-")
        fs.addRow("接口 / can_id", self.lbl_ep)
        root.addWidget(box_sel)

        # —— 手动动作 ——
        box_act = QGroupBox("手动开合")
        ga = QGridLayout(box_act)
        self.btn_open = QPushButton("张开")
        self.btn_close = QPushButton("夹紧")
        self.btn_reconnect = QPushButton("重连")
        self.btn_drop = QPushButton("掉落检测")
        self.btn_alarm_reset = QPushButton("报警复位（含夹爪）")
        style_many(
            [
                (self.btn_open, "success"),
                (self.btn_close, "warn"),
                (self.btn_reconnect, "primary"),
                (self.btn_drop, "neutral"),
                (self.btn_alarm_reset, "danger"),
            ]
        )
        self.btn_open.clicked.connect(lambda: self._cmd_open_close(True))
        self.btn_close.clicked.connect(lambda: self._cmd_open_close(False))
        self.btn_reconnect.clicked.connect(self._cmd_reconnect)
        self.btn_drop.clicked.connect(self._cmd_drop)
        self.btn_alarm_reset.clicked.connect(self._cmd_alarm_reset)
        ga.addWidget(self.btn_open, 0, 0)
        ga.addWidget(self.btn_close, 0, 1)
        ga.addWidget(self.btn_reconnect, 0, 2)
        ga.addWidget(self.btn_drop, 0, 3)
        ga.addWidget(self.btn_alarm_reset, 1, 0, 1, 4)

        ga.addWidget(QLabel("张开速度"), 2, 0)
        self.sp_open = QDoubleSpinBox()
        self.sp_open.setRange(1.0, 200.0)
        self.sp_open.setDecimals(1)
        self.sp_open.setSingleStep(5.0)
        ga.addWidget(self.sp_open, 2, 1)
        ga.addWidget(QLabel("夹紧速度"), 2, 2)
        self.sp_close = QDoubleSpinBox()
        self.sp_close.setRange(1.0, 200.0)
        self.sp_close.setDecimals(1)
        self.sp_close.setSingleStep(5.0)
        ga.addWidget(self.sp_close, 2, 3)
        self.btn_save_spd = QPushButton("保存速度到 yaml")
        style_button(self.btn_save_spd, "success")
        self.btn_save_spd.clicked.connect(self._save_speeds)
        ga.addWidget(self.btn_save_spd, 3, 0, 1, 4)
        for sp in (self.sp_open, self.sp_close):
            sp.wheelEvent = lambda e: e.ignore()  # type: ignore
        root.addWidget(box_act)

        # —— 状态 ——
        box_st = QGroupBox("当前电机状态")
        vst = QVBoxLayout(box_st)
        self.lbl_pos = QLabel("当前位置: —")
        self.lbl_pos.setAlignment(Qt.AlignCenter)
        self.lbl_pos.setMinimumHeight(48)
        self.lbl_pos.setStyleSheet(
            "background:#0e2a3a;color:#2ecc71;padding:10px;border-radius:6px;"
            "font-size:22px;font-weight:bold;"
        )
        vst.addWidget(self.lbl_pos)
        self.lbl_pos_target = QLabel("目标: 开 — / 关 —")
        self.lbl_pos_target.setAlignment(Qt.AlignCenter)
        self.lbl_pos_target.setStyleSheet("color:#85c1e9;font-size:13px;")
        vst.addWidget(self.lbl_pos_target)
        self.lbl_status = QLabel("-")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            "background:#273746;color:#ecf0f1;padding:10px;border-radius:4px;font-size:14px;"
        )
        vst.addWidget(self.lbl_status)
        self.lamp_open = QLabel("张开完成")
        self.lamp_close = QLabel("夹紧完成")
        for lb in (self.lamp_open, self.lamp_close):
            lb.setAlignment(Qt.AlignCenter)
            lb.setMinimumHeight(28)
        row_lamp = QHBoxLayout()
        row_lamp.addWidget(self.lamp_open)
        row_lamp.addWidget(self.lamp_close)
        vst.addLayout(row_lamp)
        root.addWidget(box_st)

        # —— 启用电机一览 ——
        box_list = QGroupBox("启用电机一览")
        vl = QVBoxLayout(box_list)
        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(
            ["序号", "名称", "接口", "can_id", "连接/状态", "位置(rad)", "错误"]
        )
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setMinimumHeight(160)
        self.tbl.cellClicked.connect(self._on_table_click)
        vl.addWidget(self.tbl)
        root.addWidget(box_list)

        root.addStretch(1)
        self._reload_motor_list(select_first=True)

    def _gcfg(self) -> dict:
        return normalize_grippers_cfg(self.ctx.cfg)

    def _current_index(self) -> int:
        data = self.cmb_motor.currentData()
        try:
            return int(data)
        except (TypeError, ValueError):
            return 1

    def _gripper(self):
        idx = self._current_index()
        bank = getattr(self.ctx, "grippers", {}) or {}
        g = bank.get(idx)
        if g is not None:
            return g
        gcfg = self._gcfg()
        if idx == int(gcfg.get("load_index", 1)):
            return self.ctx.gripper1
        if idx == int(gcfg.get("unload_index", 2)):
            return self.ctx.gripper2
        return None

    def _reload_motor_list(self, *, select_first: bool = False) -> None:
        gcfg = self._gcfg()
        count = int(gcfg.get("motor_count", 2))
        load_i = int(gcfg.get("load_index", 1))
        unload_i = int(gcfg.get("unload_index", 2))
        prev = self._current_index() if self.cmb_motor.count() else 1
        self.cmb_motor.blockSignals(True)
        self.cmb_motor.clear()
        for i in range(1, count + 1):
            m = motor_cfg(gcfg, i)
            role = []
            if i == load_i:
                role.append("上料")
            if i == unload_i:
                role.append("下料")
            tag = f"电机{i} {m.get('label', '')}"
            if role:
                tag += f" [{'/'.join(role)}]"
            self.cmb_motor.addItem(tag, i)
        self.cmb_motor.blockSignals(False)
        # 恢复选择
        target = 1 if select_first else prev
        for i in range(self.cmb_motor.count()):
            if int(self.cmb_motor.itemData(i)) == target:
                self.cmb_motor.setCurrentIndex(i)
                break
        self._on_motor_changed()
        self._refresh_table()

    def _on_motor_changed(self, *_a) -> None:
        g = self._gripper()
        gcfg = self._gcfg()
        idx = self._current_index()
        m = motor_cfg(gcfg, idx)
        roles = []
        if idx == int(gcfg.get("load_index", 1)):
            roles.append("上料→gripper1")
        if idx == int(gcfg.get("unload_index", 2)):
            roles.append("下料→gripper2")
        self.lbl_bind.setText("、".join(roles) if roles else "未绑定工位（仅调试）")
        self.lbl_ep.setText(f"{m.get('interface', '?')} / 0x{int(m.get('can_id', 0)):X}")
        if g is not None:
            self.sp_open.setValue(float(g.open_speed))
            self.sp_close.setValue(float(g.close_speed))
        else:
            self.sp_open.setValue(float(m.get("open_speed", 50)))
            self.sp_close.setValue(float(m.get("close_speed", 50)))
        self._refresh_status()

    def _on_table_click(self, row: int, _col: int) -> None:
        it = self.tbl.item(row, 0)
        if it is None:
            return
        try:
            idx = int(it.text())
        except ValueError:
            return
        for i in range(self.cmb_motor.count()):
            if int(self.cmb_motor.itemData(i)) == idx:
                self.cmb_motor.setCurrentIndex(i)
                break

    def _auto_locked(self) -> bool:
        from core.machine_state import MachineState, RunMode

        m = self.ctx.machine
        return (
            m.mode == RunMode.AUTO
            and m.state == MachineState.RUNNING
            and not self.ctx.gvl.Main.Paused
        )

    def _cmd_open_close(self, open_: bool) -> None:
        if self._busy_cmd:
            return
        if self._auto_locked():
            QMessageBox.information(self, "夹爪调试", "自动连续运行中请先暂停/停止。")
            return
        g = self._gripper()
        if g is None:
            QMessageBox.warning(self, "夹爪调试", "当前电机实例不存在，请重启程序或检查启用数量。")
            return
        self._busy_cmd = True
        try:
            g.set_speeds(float(self.sp_open.value()), float(self.sp_close.value()))
            ok = g.open_claw() if open_ else g.close_claw()
            act = "张开" if open_ else "夹紧"
            if not ok:
                # on_fault 已报 GRIP_*；此处再提示
                QMessageBox.warning(
                    self,
                    "夹爪调试",
                    f"{act}失败：{g.last_error or '无反馈'}\n已产生夹爪报警，可用「报警复位」清除。",
                )
            else:
                QMessageBox.information(self, "夹爪调试", f"{act}完成")
        except Exception as e:
            idx = self._current_index()
            self.ctx.raise_gripper_alarm(
                "GRIP_OPEN" if open_ else "GRIP_CLOSE",
                str(e),
                motor_index=idx,
            )
            QMessageBox.warning(self, "夹爪调试", f"异常: {e}")
        finally:
            self._busy_cmd = False
            self._refresh_status()
            self._refresh_table()

    def _cmd_reconnect(self) -> None:
        g = self._gripper()
        if g is None:
            return
        ok = g.reconnect()
        QMessageBox.information(
            self,
            "重连",
            f"{'成功' if ok else '失败'}: {g.interface} 0x{g.can_id:X}\n{g.last_error or ''}",
        )
        self._refresh_status()
        self._refresh_table()

    def _cmd_drop(self) -> None:
        g = self._gripper()
        if g is None:
            return
        ctrl = getattr(g, "_ctrl", None)
        if g.use_mock or ctrl is None:
            QMessageBox.information(self, "掉落检测", "模拟模式或未连接，无法检测。")
            return
        try:
            dropped = bool(ctrl.detect_drop_with_latest_feedback(timeout=1.0))
            if dropped:
                self.ctx.raise_gripper_alarm(
                    "GRIP_DRV",
                    "掉落检测：可能已掉料",
                    motor_index=self._current_index(),
                )
            QMessageBox.information(
                self, "掉落检测", "检测到掉落（已报警）" if dropped else "未检测到掉落"
            )
        except Exception as e:
            QMessageBox.warning(self, "掉落检测", str(e))
        self._refresh_status()

    def _cmd_alarm_reset(self) -> None:
        tips = self.coord.cmd_alarm_reset() or []
        text = "\n".join(str(t) for t in tips) if tips else "已复位"
        failed = any(
            ("失败" in str(t))
            or ("仍未连接" in str(t))
            or ("仍有故障" in str(t))
            for t in tips
        )
        if failed:
            QMessageBox.warning(self, "报警复位", text)
        else:
            QMessageBox.information(self, "报警复位", text)
        self._refresh_status()
        self._refresh_table()

    def _save_speeds(self) -> None:
        g = self._gripper()
        idx = self._current_index()
        op = float(self.sp_open.value())
        cl = float(self.sp_close.value())
        if g is not None:
            g.set_speeds(op, cl)
        gcfg = self._gcfg()
        write_motor(gcfg, idx, {"open_speed": op, "close_speed": cl})
        save_config(self.ctx.cfg)
        QMessageBox.information(self, "已保存", f"电机{idx} 开合速度已写入 yaml")

    def _set_lamp(self, label: QLabel, on: bool, color: str) -> None:
        if on:
            label.setStyleSheet(
                f"background:{color};color:#111;padding:6px;border-radius:4px;font-weight:bold;"
            )
        else:
            label.setStyleSheet(
                "background:#444;color:#aaa;padding:6px;border-radius:4px;"
            )

    def _refresh_status(self) -> None:
        g = self._gripper()
        if g is None:
            self.lbl_pos.setText("当前位置: 无实例")
            self.lbl_pos_target.setText("目标: 开 — / 关 —")
            self.lbl_status.setText("无实例")
            self._set_lamp(self.lamp_open, False, "#2ecc71")
            self._set_lamp(self.lamp_close, False, "#f39c12")
            return
        snap = (
            g.status_snapshot(poll=True)
            if hasattr(g, "status_snapshot")
            else {}
        )
        tick = getattr(self, "_ui_tick", 0)
        if tick % 8 == 0 and hasattr(g, "poll_feedback"):
            g.poll_feedback(query=True)
            snap = g.status_snapshot(poll=False) if hasattr(g, "status_snapshot") else snap
        pos_txt = snap.get("position_text") or "—"
        self.lbl_pos.setText(f"当前位置: {pos_txt}")
        open_t = snap.get("open_target_rad")
        close_t = snap.get("close_target_rad")
        if open_t is None or close_t is None:
            self.lbl_pos_target.setText("目标: 开 — / 关 —（Mock 无编码器）")
        else:
            self.lbl_pos_target.setText(
                f"目标: 开 {float(open_t):.3f} rad  /  关 {float(close_t):.3f} rad"
            )
        alarm = self.ctx.alarms.active
        alarm_txt = (
            f"\n当前报警: [{alarm.code}] {alarm.message}"
            if alarm and str(alarm.code or "").startswith("GRIP")
            else (
                f"\n其它报警: [{alarm.code}] {alarm.message}"
                if alarm
                else "\n当前报警: 无"
            )
        )
        lines = [
            f"名称: {snap.get('name', g.name)}",
            f"Mock={snap.get('use_mock')}  连接={snap.get('connected')}  忙={snap.get('busy')}",
            f"开合: {'夹紧' if snap.get('closed') else '张开'}  last_ok={snap.get('last_ok')}",
            f"速度 开={snap.get('open_speed')} 关={snap.get('close_speed')}",
            f"爪状态={snap.get('claw_state')} 持物={snap.get('hold_state')} 驱动={snap.get('driver_status')}",
            f"位置rad={snap.get('position_rad')} 扭矩Nm={snap.get('torque_nm')} 掉落={snap.get('drop_detected')}",
            f"错误: {snap.get('last_error') or '-'}",
        ]
        self.lbl_status.setText("\n".join(lines) + alarm_txt)
        self._set_lamp(self.lamp_open, bool(snap.get("open_done")), "#2ecc71")
        self._set_lamp(self.lamp_close, bool(snap.get("close_done")), "#f39c12")

    def _refresh_table(self) -> None:
        gcfg = self._gcfg()
        count = int(gcfg.get("motor_count", 2))
        bank = getattr(self.ctx, "grippers", {}) or {}
        self.tbl.setRowCount(count)
        for r in range(count):
            i = r + 1
            m = motor_cfg(gcfg, i)
            g = bank.get(i)
            self.tbl.setItem(r, 0, QTableWidgetItem(str(i)))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(m.get("label", ""))))
            self.tbl.setItem(r, 2, QTableWidgetItem(str(m.get("interface", ""))))
            self.tbl.setItem(r, 3, QTableWidgetItem(f"0x{int(m.get('can_id', 0)):X}"))
            if g is None:
                st = "无实例"
                err = ""
                pos = "—"
            elif g.use_mock:
                st = "模拟"
                err = g.last_error or ""
                pos = "模拟"
            else:
                st = "已连接" if g.connected else "未连接"
                if g.busy:
                    st += "/动作中"
                err = g.last_error or ""
                pos = g.position_display() if hasattr(g, "position_display") else "—"
            self.tbl.setItem(r, 4, QTableWidgetItem(st))
            self.tbl.setItem(r, 5, QTableWidgetItem(pos))
            self.tbl.setItem(r, 6, QTableWidgetItem(err))

    def refresh(self) -> None:
        """主窗周期刷新（仅当前页可见时）。"""
        if not self.isVisible():
            return
        # 数量变化时重建下拉
        gcfg = self._gcfg()
        count = int(gcfg.get("motor_count", 2))
        if self.cmb_motor.count() != count:
            self._reload_motor_list()
            return
        self._refresh_status()
        # 表格状态降频：约 450ms 一次，减少 setText
        tick = getattr(self, "_ui_tick", 0) + 1
        self._ui_tick = tick
        if tick % 3 != 0:
            return
        if self.tbl.rowCount() != count:
            self._refresh_table()
        else:
            bank = getattr(self.ctx, "grippers", {}) or {}
            for r in range(count):
                i = r + 1
                g = bank.get(i)
                if g is None:
                    continue
                if g.use_mock:
                    st = "模拟"
                    pos = "模拟"
                else:
                    st = "已连接" if g.connected else "未连接"
                    if g.busy:
                        st += "/动作中"
                    pos = g.position_display() if hasattr(g, "position_display") else "—"
                err = g.last_error or ""
                it = self.tbl.item(r, 4)
                if it and it.text() != st:
                    it.setText(st)
                itp = self.tbl.item(r, 5)
                if itp is None:
                    self.tbl.setItem(r, 5, QTableWidgetItem(pos))
                elif itp.text() != pos:
                    itp.setText(pos)
                ite = self.tbl.item(r, 6)
                if ite is None:
                    self.tbl.setItem(r, 6, QTableWidgetItem(err))
                elif ite.text() != err:
                    ite.setText(err)
