"""负载 / 工具TCP：上料、下料两臂各自独立设置两套参数。"""

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
from hmi.style import apply_page_chrome, groupbox_qss, style_button

_MODE_CAPTION = {
    "empty": "负载1 / 工具TCP1 · 手爪（未抓鞋）",
    "with_shoe": "负载2 / 工具TCP2 · 手爪+鞋（抓鞋）",
}

_ACTIVE_MODE_QSS = """
QGroupBox {
    font-weight: bold;
    border: 3px solid #1a7a37;
    border-radius: 6px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    background: #e8f8ef;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 8px;
    color: #145a32;
}
"""


def _spin_mass() -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(0.0, 50.0)
    sp.setDecimals(3)
    sp.setSingleStep(0.1)
    sp.setSuffix(" kg")
    return sp


def _spin_mm() -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(-1000.0, 1000.0)
    sp.setDecimals(2)
    sp.setSingleStep(1.0)
    sp.setSuffix(" mm")
    return sp


def _spin_deg() -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(-360.0, 360.0)
    sp.setDecimals(2)
    sp.setSingleStep(0.1)
    sp.setSuffix(" °")
    return sp


_TCP_KEYS = ("tx", "ty", "tz", "trx", "try_", "trz")


class PayloadPage(QWidget):
    """
    两臂分开：负载质量/质心 + 工具 TCP（相对法兰）。
    未抓鞋→工具1；抓鞋→工具2。
    """

    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._fields: dict[tuple[str, str], dict[str, QDoubleSpinBox]] = {}
        self._mode_boxes: dict[tuple[str, str], QGroupBox] = {}
        self._mode_btns: dict[tuple[str, str], QPushButton] = {}
        self._status: dict[str, QLabel] = {}
        self._chk_tcp: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(self._robot_box("robot1", "上料机器人 robot1（独立）"))
        row.addWidget(self._robot_box("robot2", "下料机器人 robot2（独立）"))
        root.addLayout(row)

        apply_page_chrome(self)
        self._reload_from_cfg()
        self._refresh_status()

    def _robot_box(self, robot_key: str, title: str) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        for mode, caption in _MODE_CAPTION.items():
            g = QGroupBox(caption)
            self._mode_boxes[(robot_key, mode)] = g
            form = QFormLayout(g)
            mass = _spin_mass()
            cx, cy, cz = _spin_mm(), _spin_mm(), _spin_mm()
            tx, ty, tz = _spin_mm(), _spin_mm(), _spin_mm()
            trx, try_, trz = _spin_deg(), _spin_deg(), _spin_deg()
            form.addRow("质量", mass)
            form.addRow("质心 X", cx)
            form.addRow("质心 Y", cy)
            form.addRow("质心 Z", cz)
            form.addRow("TCP X", tx)
            form.addRow("TCP Y", ty)
            form.addRow("TCP Z", tz)
            form.addRow("TCP Rx", trx)
            form.addRow("TCP Ry", try_)
            form.addRow("TCP Rz", trz)
            lay.addWidget(g)
            self._fields[(robot_key, mode)] = {
                "mass_kg": mass,
                "cx": cx,
                "cy": cy,
                "cz": cz,
                "tx": tx,
                "ty": ty,
                "tz": tz,
                "trx": trx,
                "try_": try_,
                "trz": trz,
            }

        chk = QCheckBox("保存时同时下发 TCP 到控制器（会覆盖示教器该工具坐标）")
        # yaml 已有非零 TCP 时默认勾选，避免只下发负载、示教器看不到 TCP
        has_tcp = False
        for mode in ("empty", "with_shoe"):
            tcp = (
                self.ctx.cfg.get("robots", {})
                .get(robot_key, {})
                .get("payloads", {})
                .get(mode, {})
                .get("tcp")
            )
            if isinstance(tcp, (list, tuple)) and any(abs(float(x)) > 1e-6 for x in tcp[:6]):
                has_tcp = True
                break
        chk.setChecked(has_tcp)
        self._chk_tcp[robot_key] = chk
        lay.addWidget(chk)

        status = QLabel("-")
        status.setWordWrap(True)
        status.setStyleSheet(
            "font-size:15px;font-weight:bold;padding:10px;border-radius:6px;"
            "background:#eaf2f8;color:#1a5276;"
        )
        self._status[robot_key] = status
        lay.addWidget(status)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存并下发本臂")
        style_button(btn_save, "success")
        btn_save.clicked.connect(lambda: self._save_and_apply(robot_key))
        btn_tcp = QPushButton("仅下发TCP到示教器")
        style_button(btn_tcp, "primary")
        btn_tcp.clicked.connect(lambda: self._push_tcp_only(robot_key))
        btn_read = QPushButton("读回控制器TCP")
        style_button(btn_read, "neutral")
        btn_read.clicked.connect(lambda: self._read_tcp(robot_key))
        btn_empty = QPushButton("切到 1：负载1+工具TCP1（未抓鞋）")
        btn_empty.setToolTip("同时把本臂当前负载和运动工具/TCP切到第1套（未抓鞋）。")
        btn_empty.clicked.connect(lambda: self._force_mode(robot_key, False))
        btn_shoe = QPushButton("切到 2：负载2+工具TCP2（抓鞋）")
        btn_shoe.setToolTip("同时把本臂当前负载和运动工具/TCP切到第2套（抓鞋）。")
        btn_shoe.clicked.connect(lambda: self._force_mode(robot_key, True))
        self._mode_btns[(robot_key, "empty")] = btn_empty
        self._mode_btns[(robot_key, "with_shoe")] = btn_shoe
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_tcp)
        btn_row.addWidget(btn_read)
        btn_row.addWidget(btn_empty)
        btn_row.addWidget(btn_shoe)
        lay.addLayout(btn_row)
        return box

    def _reload_from_cfg(self) -> None:
        for (rk, mode), spins in self._fields.items():
            p = (
                self.ctx.cfg.get("robots", {})
                .get(rk, {})
                .get("payloads", {})
                .get(mode, {})
            )
            if not isinstance(p, dict):
                p = {}
            spins["mass_kg"].setValue(float(p.get("mass_kg", 2.0)))
            cog = p.get("cog_mm", [0.0, 0.0, 50.0])
            if not isinstance(cog, (list, tuple)) or len(cog) < 3:
                cog = [0.0, 0.0, 50.0]
            spins["cx"].setValue(float(cog[0]))
            spins["cy"].setValue(float(cog[1]))
            spins["cz"].setValue(float(cog[2]))
            tcp = p.get("tcp", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            if not isinstance(tcp, (list, tuple)) or len(tcp) < 6:
                tcp = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            for i, k in enumerate(_TCP_KEYS):
                spins[k].setValue(float(tcp[i]))

    def _collect_robot_to_cfg(self, robot_key: str) -> None:
        robots = self.ctx.cfg.setdefault("robots", {})
        rcfg = robots.setdefault(robot_key, {})
        payloads = rcfg.setdefault("payloads", {})
        for mode in ("empty", "with_shoe"):
            spins = self._fields[(robot_key, mode)]
            slot = payloads.setdefault(mode, {})
            if mode == "empty":
                slot.setdefault("name", "手爪")
                slot["load_num"] = 1
                slot["tool"] = 1
                rcfg["tool"] = 1
            else:
                slot.setdefault("name", "手爪+鞋")
                slot["load_num"] = 2
                slot["tool"] = 2
            slot["mass_kg"] = float(spins["mass_kg"].value())
            slot["cog_mm"] = [
                float(spins["cx"].value()),
                float(spins["cy"].value()),
                float(spins["cz"].value()),
            ]
            slot["tcp"] = [float(spins[k].value()) for k in _TCP_KEYS]

    def _apply_one_robot(self, robot_key: str) -> None:
        robot = self.ctx.robot1 if robot_key == "robot1" else self.ctx.robot2
        payloads = self.ctx.cfg["robots"][robot_key].get("payloads")
        robot.apply_payload_cfg(payloads)
        mode = robot.payload_mode() or "empty"
        push_tcp = bool(self._chk_tcp.get(robot_key) and self._chk_tcp[robot_key].isChecked())
        robot.sync_payloads_to_controller(sync_tcp=push_tcp)
        # 当前激活工具也再写一次 TCP，示教器「当前工具」立即刷新
        robot.set_payload_mode(mode, force=True, sync_tcp=push_tcp)

    def _push_tcp_only(self, robot_key: str) -> None:
        """不改 yaml，只把界面/当前配置里的工具1/2 TCP 下发到控制器。"""
        name = "上料" if robot_key == "robot1" else "下料"
        robot = self.ctx.robot1 if robot_key == "robot1" else self.ctx.robot2
        try:
            self._collect_robot_to_cfg(robot_key)
            payloads = self.ctx.cfg["robots"][robot_key].get("payloads")
            robot.apply_payload_cfg(payloads)
            robot.sync_payloads_to_controller(sync_tcp=True)
            mode = robot.payload_mode() or "empty"
            robot.set_payload_mode(mode, force=True, sync_tcp=True)
        except Exception as e:
            QMessageBox.warning(self, f"{name}臂下发TCP失败", str(e))
            return
        self._refresh_status()
        QMessageBox.information(
            self,
            "已下发TCP",
            f"已把【{name}臂】工具1/2 TCP 写入控制器（SetToolList+SetToolCoord）。\n"
            "请到示教器「工具坐标」查看；当前激活工具号={robot.tool}。",
        )

    def _save_and_apply(self, robot_key: str) -> None:
        name = "上料" if robot_key == "robot1" else "下料"
        push_tcp = bool(self._chk_tcp.get(robot_key) and self._chk_tcp[robot_key].isChecked())
        try:
            self._collect_robot_to_cfg(robot_key)
            save_config(self.ctx.cfg)
            self._apply_one_robot(robot_key)
        except Exception as e:
            QMessageBox.warning(self, f"{name}臂保存失败", str(e))
            return
        self._refresh_status()
        tcp_msg = "已下发负载+TCP。" if push_tcp else "已下发负载（未下发TCP，如需请勾选后重存）。"
        QMessageBox.information(
            self,
            "已保存",
            f"已单独写入【{name}臂】yaml。{tcp_msg}\n"
            "自动运行仍按抓取状态切换工具编号 1/2。",
        )

    def _read_tcp(self, robot_key: str) -> None:
        name = "上料" if robot_key == "robot1" else "下料"
        robot = self.ctx.robot1 if robot_key == "robot1" else self.ctx.robot2
        lines = []
        try:
            for mode, tool_id in (("empty", 1), ("with_shoe", 2)):
                tcp = robot.read_tool_tcp_from_controller(tool_id)
                spins = self._fields[(robot_key, mode)]
                for i, k in enumerate(_TCP_KEYS):
                    spins[k].setValue(float(tcp[i]))
                lines.append(f"工具{tool_id}: {tcp}")
        except Exception as e:
            QMessageBox.warning(self, f"{name}臂读TCP失败", str(e))
            return
        self._log_status_hint(robot_key)
        QMessageBox.information(
            self,
            "已读回",
            f"已从控制器读入【{name}臂】工具1/2 TCP到界面（尚未写 yaml）。\n"
            + "\n".join(lines)
            + "\n请确认后点「保存并下发本臂」。",
        )

    def _log_status_hint(self, robot_key: str) -> None:
        self._refresh_status()

    def _force_mode(self, robot_key: str, holding: bool) -> None:
        name = "上料" if robot_key == "robot1" else "下料"
        try:
            self.ctx.set_robot_holding_shoe(robot_key, holding, force=True)
        except Exception as e:
            QMessageBox.warning(self, f"{name}臂切换失败", str(e))
            return
        self._refresh_status()

    def _refresh_status(self) -> None:
        idle_qss = groupbox_qss()
        for rk, robot in (("robot1", self.ctx.robot1), ("robot2", self.ctx.robot2)):
            mode = robot.payload_mode() or "empty"
            if mode not in ("empty", "with_shoe"):
                mode = "with_shoe" if mode in ("shoe", "holding", "2") else "empty"
            p = robot.payload_profiles().get(mode, {})
            tool = int(getattr(robot, "tool", 0) or 0)
            load_num = int(p.get("load_num") or (2 if mode == "with_shoe" else 1))
            expect_tool = int(p.get("tool") or load_num)
            tcp = p.get("tcp") or []
            tcp_s = (
                ", ".join(f"{float(x):.1f}" for x in list(tcp)[:6]) if tcp else "-"
            )
            load_name = (
                "负载1 · 手爪（未抓鞋）"
                if mode == "empty"
                else "负载2 · 手爪+鞋（抓鞋）"
            )
            aligned = tool == expect_tool
            lbl = self._status.get(rk)
            if lbl is not None:
                if aligned:
                    lbl.setText(
                        f"当前选中：{load_name}\n"
                        f"运动工具 / TCP：工具{tool}    "
                        f"质量 {p.get('mass_kg', '-')} kg    质心 {p.get('cog_mm', '-')}\n"
                        f"TCP  {tcp_s}"
                    )
                    lbl.setStyleSheet(
                        "font-size:15px;font-weight:bold;padding:10px;border-radius:6px;"
                        "background:#d5f5e3;color:#145a32;"
                    )
                else:
                    lbl.setText(
                        f"当前负载：{load_name}\n"
                        f"运动工具 / TCP：工具{tool}（与负载编号不同；"
                        "取料夹紧后、切鞋头 TCP 前会出现）\n"
                        f"质量 {p.get('mass_kg', '-')} kg    质心 {p.get('cog_mm', '-')}    "
                        f"TCP  {tcp_s}"
                    )
                    lbl.setStyleSheet(
                        "font-size:15px;font-weight:bold;padding:10px;border-radius:6px;"
                        "background:#fdebd0;color:#7e5103;"
                    )
            for m, caption in _MODE_CAPTION.items():
                box = self._mode_boxes.get((rk, m))
                if box is None:
                    continue
                on = m == mode
                box.setTitle(("● 当前使用 · " if on else "") + caption)
                box.setStyleSheet(_ACTIVE_MODE_QSS if on else idle_qss)
            for m, btn in (
                ("empty", self._mode_btns.get((rk, "empty"))),
                ("with_shoe", self._mode_btns.get((rk, "with_shoe"))),
            ):
                if btn is None:
                    continue
                on = m == mode
                style_button(btn, "success" if on else "neutral")
                if m == "empty":
                    btn.setText(
                        "● 正在用 1：负载1+工具TCP1"
                        if on
                        else "切到 1：负载1+工具TCP1（未抓鞋）"
                    )
                else:
                    btn.setText(
                        "● 正在用 2：负载2+工具TCP2"
                        if on
                        else "切到 2：负载2+工具TCP2（抓鞋）"
                    )

    def refresh(self) -> None:
        self._refresh_status()
