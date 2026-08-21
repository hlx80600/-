"""通信配置页：全部设备接口参数可改，保存到 default.yaml，实机联调直接改这里。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.app_context import device_use_mock
from core.config_loader import save_config
from core.coordinator import Coordinator
from hmi.style import apply_page_chrome, style_button


def _spin_int(lo: int, hi: int, val: int, *, hex_mode: bool = False) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setValue(int(val))
    if hex_mode:
        sp.setDisplayIntegerBase(16)
        sp.setPrefix("0x")
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore
    return sp


def _spin_float(lo: float, hi: float, val: float, step: float = 1.0, dec: int = 1) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(dec)
    sp.setSingleStep(step)
    sp.setValue(float(val))
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore
    return sp


class ConfigPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        cfg = self.ctx.cfg
        sys_def = bool(cfg.get("system", {}).get("use_mock", True))

        root = QVBoxLayout(self)

        # —— 系统 ——
        box_sys = QGroupBox("系统")
        fs = QFormLayout(box_sys)
        sys = cfg.get("system") or {}
        self.sp_scan = _spin_float(0.01, 1.0, float(sys.get("scan_time_s", 0.05)), 0.01, 3)
        self.sp_recon = _spin_float(0.5, 60.0, float(sys.get("reconnect_interval_s", 3.0)), 0.5, 1)
        self.chk_bypass = QCheckBox("允许调试旁路（忽略 Station 进入互锁）")
        self.chk_bypass.setChecked(bool(sys.get("allow_debug_bypass", True)))
        self.chk_sys = QCheckBox("全局默认 system.use_mock（仅当某设备未单独配置时生效）")
        self.chk_sys.setChecked(sys_def)
        fs.addRow("扫描周期 scan_time_s", self.sp_scan)
        fs.addRow("断线重连间隔 s", self.sp_recon)
        fs.addRow(self.chk_bypass)
        fs.addRow(self.chk_sys)
        root.addWidget(box_sys)

        # —— 机器人 ——
        r1 = cfg["robots"]["robot1"]
        r2 = cfg["robots"]["robot2"]
        box_r = QGroupBox("机器人（法奥 FR5）")
        fr = QFormLayout(box_r)
        self.ed_r1 = QLineEdit(str(r1.get("ip", "")))
        self.ed_r2 = QLineEdit(str(r2.get("ip", "")))
        self.sp_r1_tool = _spin_int(0, 15, int(r1.get("tool", 1)))
        self.sp_r1_user = _spin_int(0, 15, int(r1.get("user", 0)))
        self.sp_r1_vel = _spin_float(1.0, 100.0, float(r1.get("vel", 50.0)), 1.0, 0)
        self.sp_r2_tool = _spin_int(0, 15, int(r2.get("tool", 1)))
        self.sp_r2_user = _spin_int(0, 15, int(r2.get("user", 0)))
        self.sp_r2_vel = _spin_float(1.0, 100.0, float(r2.get("vel", 50.0)), 1.0, 0)
        self.sp_belt_di = _spin_int(0, 64, int(r1.get("di_belt_sensor", 0)))
        self.chk_belt_mock = QCheckBox("皮带光电走模拟（真机臂时勾选才能用 HMI 点光电）")
        self.chk_belt_mock.setChecked(bool(r1.get("di_belt_use_mock", True)))
        self.chk_r1 = QCheckBox("上料机器人 robot1 模拟")
        self.chk_r1.setChecked(device_use_mock(r1, sys_def))
        self.chk_r2 = QCheckBox("下料机器人 robot2 模拟")
        self.chk_r2.setChecked(device_use_mock(r2, sys_def))
        fr.addRow("上料 IP robot1", self.ed_r1)
        fr.addRow("上料 tool / user", self._pair(self.sp_r1_tool, self.sp_r1_user))
        fr.addRow("上料速度 %", self.sp_r1_vel)
        fr.addRow("皮带光电 DI 号", self.sp_belt_di)
        fr.addRow(self.chk_belt_mock)
        fr.addRow(self.chk_r1)
        fr.addRow("下料 IP robot2", self.ed_r2)
        fr.addRow("下料 tool / user", self._pair(self.sp_r2_tool, self.sp_r2_user))
        fr.addRow("下料速度 %", self.sp_r2_vel)
        fr.addRow(self.chk_r2)
        root.addWidget(box_r)

        # —— 夹爪 ——
        g1 = cfg["grippers"]["gripper1"]
        g2 = cfg["grippers"]["gripper2"]
        box_g = QGroupBox("夹爪 CAN（Casbot：左 can0/0x103，右 can1/0x101，type=2）")
        fg = QFormLayout(box_g)
        self.ed_g1_if = QLineEdit(str(g1.get("interface", "can0")))
        self.ed_g2_if = QLineEdit(str(g2.get("interface", "can1")))
        self.sp_g1_id = _spin_int(0, 0x7FF, int(g1.get("can_id", 0x103)), hex_mode=True)
        self.sp_g2_id = _spin_int(0, 0x7FF, int(g2.get("can_id", 0x101)), hex_mode=True)
        self.sp_g1_type = _spin_int(0, 2, int(g1.get("gripper_type", 2)))
        self.sp_g2_type = _spin_int(0, 2, int(g2.get("gripper_type", 2)))
        self.sp_g1_open = _spin_float(1.0, 200.0, float(g1.get("open_speed", 50.0)), 5.0, 1)
        self.sp_g1_close = _spin_float(1.0, 200.0, float(g1.get("close_speed", 50.0)), 5.0, 1)
        self.sp_g2_open = _spin_float(1.0, 200.0, float(g2.get("open_speed", 50.0)), 5.0, 1)
        self.sp_g2_close = _spin_float(1.0, 200.0, float(g2.get("close_speed", 50.0)), 5.0, 1)
        self.chk_g1 = QCheckBox("夹爪1（上料）模拟")
        self.chk_g1.setChecked(device_use_mock(g1, sys_def))
        self.chk_g2 = QCheckBox("夹爪2（下料）模拟")
        self.chk_g2.setChecked(device_use_mock(g2, sys_def))
        fg.addRow("夹爪1 接口 canX", self.ed_g1_if)
        fg.addRow("夹爪1 can_id", self.sp_g1_id)
        fg.addRow("夹爪1 gripper_type", self.sp_g1_type)
        fg.addRow("夹爪1 张开/夹紧速度", self._pair(self.sp_g1_open, self.sp_g1_close))
        fg.addRow(self.chk_g1)
        fg.addRow("夹爪2 接口 canX", self.ed_g2_if)
        fg.addRow("夹爪2 can_id", self.sp_g2_id)
        fg.addRow("夹爪2 gripper_type", self.sp_g2_type)
        fg.addRow("夹爪2 张开/夹紧速度", self._pair(self.sp_g2_open, self.sp_g2_close))
        fg.addRow(self.chk_g2)
        root.addWidget(box_g)

        # —— 压鞋机 ——
        press = cfg.get("press") or {}
        box_p = QGroupBox("压鞋机 ModbusTCP（线圈地址按现场 PLC 改）")
        fp = QFormLayout(box_p)
        self.ed_press = QLineEdit(str(press.get("ip", "")))
        self.sp_port = _spin_int(1, 65535, int(press.get("port", 502)))
        self.sp_unit = _spin_int(0, 255, int(press.get("unit_id", 1)))
        self.sp_addr_power = _spin_int(0, 65535, int(press.get("addr_power_ok", 0)))
        self.sp_addr_rot_done = _spin_int(0, 65535, int(press.get("addr_rotate_done", 1)))
        self.sp_addr_press_done = _spin_int(0, 65535, int(press.get("addr_press_done", 2)))
        self.sp_addr_cmd_rot = _spin_int(0, 65535, int(press.get("addr_cmd_rotate", 10)))
        self.sp_addr_cmd_press = _spin_int(0, 65535, int(press.get("addr_cmd_start_press", 11)))
        self.sp_mock_press_s = _spin_float(0.0, 30.0, float(press.get("mock_auto_press_done_s", 2.0)), 0.5, 1)
        self.sp_mock_rot_s = _spin_float(0.0, 30.0, float(press.get("mock_auto_rotate_done_s", 1.5)), 0.5, 1)
        self.chk_press = QCheckBox("压鞋机 模拟")
        self.chk_press.setChecked(device_use_mock(press, sys_def))
        fp.addRow("压机 IP", self.ed_press)
        fp.addRow("端口 / unit_id", self._pair(self.sp_port, self.sp_unit))
        fp.addRow("addr_power_ok（上电完成）", self.sp_addr_power)
        fp.addRow("addr_rotate_done（旋转完成）", self.sp_addr_rot_done)
        fp.addRow("addr_press_done（压鞋完成）", self.sp_addr_press_done)
        fp.addRow("addr_cmd_rotate（旋转命令）", self.sp_addr_cmd_rot)
        fp.addRow("addr_cmd_start_press（压鞋命令）", self.sp_addr_cmd_press)
        fp.addRow("Mock 压鞋完成延时 s", self.sp_mock_press_s)
        fp.addRow("Mock 旋转完成延时 s", self.sp_mock_rot_s)
        fp.addRow(self.chk_press)
        root.addWidget(box_p)

        # —— IO ——
        io = cfg.get("io") or {}
        tw = io.get("tower_light") or {}
        box_io = QGroupBox("本机 IO / 急停 / 三色灯")
        fio = QFormLayout(box_io)
        self.sp_estop = _spin_int(0, 64, int(io.get("estop_di", 0)))
        self.sp_lt_r = _spin_int(0, 64, int(tw.get("red_do", 0)))
        self.sp_lt_y = _spin_int(0, 64, int(tw.get("yellow_do", 1)))
        self.sp_lt_g = _spin_int(0, 64, int(tw.get("green_do", 2)))
        self.chk_io = QCheckBox("本机 IO/三色灯 模拟")
        self.chk_io.setChecked(device_use_mock(io, sys_def))
        fio.addRow("急停 DI", self.sp_estop)
        fio.addRow("三色灯 DO 红/黄/绿", self._triple(self.sp_lt_r, self.sp_lt_y, self.sp_lt_g))
        fio.addRow(self.chk_io)
        root.addWidget(box_io)

        # —— 相机 ——
        box_c = QGroupBox("相机（Orbbec：index / serial；空 serial 则用 index）")
        fc = QFormLayout(box_c)
        cams = cfg.get("cameras") or {}
        self.cam_ed: dict[str, tuple[QLineEdit, QSpinBox, QCheckBox]] = {}
        cam_labels = {
            "cam1": "相机1 皮带上料",
            "cam2": "相机2 鞋头对位",
            "cam3": "相机3 放料鞋槽",
            "cam4": "相机4 取料鞋槽",
        }
        for key, title in cam_labels.items():
            ccfg = cams.get(key) or {}
            ed_ser = QLineEdit(str(ccfg.get("serial", "")))
            sp_idx = _spin_int(0, 16, int(ccfg.get("index", 0)))
            cb = QCheckBox("模拟")
            if key in self.ctx.cameras:
                cb.setChecked(bool(self.ctx.cameras[key].use_mock))
            else:
                cb.setChecked(device_use_mock(ccfg, sys_def))
            row = QHBoxLayout()
            row.addWidget(QLabel("serial"))
            row.addWidget(ed_ser, 1)
            row.addWidget(QLabel("index"))
            row.addWidget(sp_idx)
            row.addWidget(cb)
            w = QWidget()
            w.setLayout(row)
            fc.addRow(title, w)
            self.cam_ed[key] = (ed_ser, sp_idx, cb)
        self.chk_vision = QCheckBox("视觉算法兜底 vision.use_mock（相机未单独配置时）")
        self.chk_vision.setChecked(device_use_mock(cfg.get("vision", {}), sys_def))
        fc.addRow(self.chk_vision)
        root.addWidget(box_c)

        # —— 手机 Web ——
        mw = (cfg.get("system") or {}).get("mobile_web") or {}
        box_m = QGroupBox("手机 Web 监控（可选）")
        fm = QFormLayout(box_m)
        self.chk_mobile = QCheckBox("启用 mobile_web")
        self.chk_mobile.setChecked(bool(mw.get("enabled", False)))
        self.ed_mw_host = QLineEdit(str(mw.get("host", "0.0.0.0")))
        self.sp_mw_port = _spin_int(1, 65535, int(mw.get("port", 8765)))
        self.ed_mw_token = QLineEdit(str(mw.get("token", "")))
        fm.addRow(self.chk_mobile)
        fm.addRow("host", self.ed_mw_host)
        fm.addRow("port", self.sp_mw_port)
        fm.addRow("token", self.ed_mw_token)
        root.addWidget(box_m)

        self.lbl_status = QLabel(
            "当前运行态："
            + self.ctx.mock_status_text()
            + "\n"
            + self.ctx.connection_status_text()[0]
        )
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        btn = QPushButton("保存配置（写入 yaml 并尽量立刻生效）")
        style_button(btn, "success")
        btn.clicked.connect(self._save)
        btn_reload = QPushButton("从运行态刷新到界面")
        style_button(btn_reload, "neutral")
        btn_reload.clicked.connect(self._reload_from_runtime)
        btn_row.addWidget(btn)
        btn_row.addWidget(btn_reload)
        root.addLayout(btn_row)
        apply_page_chrome(self)

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(a)
        lay.addWidget(b)
        return w

    @staticmethod
    def _triple(a: QWidget, b: QWidget, c: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(a)
        lay.addWidget(b)
        lay.addWidget(c)
        return w

    def _reload_from_runtime(self) -> None:
        """简单：提示用户重新打开页或重启；此处重读 cfg 到主要控件。"""
        cfg = self.ctx.cfg
        g1 = cfg["grippers"]["gripper1"]
        g2 = cfg["grippers"]["gripper2"]
        self.ed_g1_if.setText(str(g1.get("interface", "can0")))
        self.ed_g2_if.setText(str(g2.get("interface", "can1")))
        self.sp_g1_id.setValue(int(g1.get("can_id", 0x103)))
        self.sp_g2_id.setValue(int(g2.get("can_id", 0x101)))
        self.sp_g1_open.setValue(float(g1.get("open_speed", 50)))
        self.sp_g1_close.setValue(float(g1.get("close_speed", 50)))
        self.sp_g2_open.setValue(float(g2.get("open_speed", 50)))
        self.sp_g2_close.setValue(float(g2.get("close_speed", 50)))
        self.ed_r1.setText(str(cfg["robots"]["robot1"].get("ip", "")))
        self.ed_r2.setText(str(cfg["robots"]["robot2"].get("ip", "")))
        self.ed_press.setText(str((cfg.get("press") or {}).get("ip", "")))
        self.lbl_status.setText(
            "当前运行态："
            + self.ctx.mock_status_text()
            + "\n"
            + self.ctx.connection_status_text()[0]
        )

    def _save(self) -> None:
        cfg = self.ctx.cfg
        # 系统
        sys = cfg.setdefault("system", {})
        sys["use_mock"] = bool(self.chk_sys.isChecked())
        sys["scan_time_s"] = float(self.sp_scan.value())
        sys["reconnect_interval_s"] = float(self.sp_recon.value())
        sys["allow_debug_bypass"] = bool(self.chk_bypass.isChecked())
        mw = sys.setdefault("mobile_web", {})
        mw["enabled"] = bool(self.chk_mobile.isChecked())
        mw["host"] = self.ed_mw_host.text().strip() or "0.0.0.0"
        mw["port"] = int(self.sp_mw_port.value())
        mw["token"] = self.ed_mw_token.text()

        # 机器人
        r1 = cfg["robots"]["robot1"]
        r2 = cfg["robots"]["robot2"]
        r1["ip"] = self.ed_r1.text().strip()
        r2["ip"] = self.ed_r2.text().strip()
        r1["tool"] = int(self.sp_r1_tool.value())
        r1["user"] = int(self.sp_r1_user.value())
        r1["vel"] = float(self.sp_r1_vel.value())
        r2["tool"] = int(self.sp_r2_tool.value())
        r2["user"] = int(self.sp_r2_user.value())
        r2["vel"] = float(self.sp_r2_vel.value())
        r1["di_belt_sensor"] = int(self.sp_belt_di.value())
        r1["di_belt_use_mock"] = bool(self.chk_belt_mock.isChecked())
        r1["use_mock"] = bool(self.chk_r1.isChecked())
        r2["use_mock"] = bool(self.chk_r2.isChecked())

        # 夹爪
        g1 = cfg["grippers"]["gripper1"]
        g2 = cfg["grippers"]["gripper2"]
        g1["interface"] = self.ed_g1_if.text().strip() or "can0"
        g2["interface"] = self.ed_g2_if.text().strip() or "can1"
        g1["can_id"] = int(self.sp_g1_id.value())
        g2["can_id"] = int(self.sp_g2_id.value())
        g1["gripper_type"] = int(self.sp_g1_type.value())
        g2["gripper_type"] = int(self.sp_g2_type.value())
        g1["open_speed"] = float(self.sp_g1_open.value())
        g1["close_speed"] = float(self.sp_g1_close.value())
        g2["open_speed"] = float(self.sp_g2_open.value())
        g2["close_speed"] = float(self.sp_g2_close.value())
        g1["use_mock"] = bool(self.chk_g1.isChecked())
        g2["use_mock"] = bool(self.chk_g2.isChecked())

        # 压机
        press = cfg.setdefault("press", {})
        press["ip"] = self.ed_press.text().strip()
        press["port"] = int(self.sp_port.value())
        press["unit_id"] = int(self.sp_unit.value())
        press["addr_power_ok"] = int(self.sp_addr_power.value())
        press["addr_rotate_done"] = int(self.sp_addr_rot_done.value())
        press["addr_press_done"] = int(self.sp_addr_press_done.value())
        press["addr_cmd_rotate"] = int(self.sp_addr_cmd_rot.value())
        press["addr_cmd_start_press"] = int(self.sp_addr_cmd_press.value())
        press["mock_auto_press_done_s"] = float(self.sp_mock_press_s.value())
        press["mock_auto_rotate_done_s"] = float(self.sp_mock_rot_s.value())
        press["use_mock"] = bool(self.chk_press.isChecked())

        # IO
        io = cfg.setdefault("io", {})
        io["estop_di"] = int(self.sp_estop.value())
        io["use_mock"] = bool(self.chk_io.isChecked())
        tw = io.setdefault("tower_light", {})
        tw["red_do"] = int(self.sp_lt_r.value())
        tw["yellow_do"] = int(self.sp_lt_y.value())
        tw["green_do"] = int(self.sp_lt_g.value())

        # 相机 / 视觉
        cfg.setdefault("vision", {})["use_mock"] = bool(self.chk_vision.isChecked())
        for key, (ed_ser, sp_idx, cb) in self.cam_ed.items():
            c = cfg.setdefault("cameras", {}).setdefault(key, {})
            c["serial"] = ed_ser.text().strip()
            c["index"] = int(sp_idx.value())
            c["use_mock"] = bool(cb.isChecked())
            cam = self.ctx.cameras.get(key)
            if cam is not None:
                cam.serial = str(c["serial"])
                cam.index = int(c["index"])
            self.ctx.vision.set_cam_mock(key, bool(cb.isChecked()))

        # —— 立刻应用到运行对象 ——
        self.ctx.robot1.ip = r1["ip"]
        self.ctx.robot2.ip = r2["ip"]
        self.ctx.robot1.tool = int(r1["tool"])
        self.ctx.robot1.user = int(r1["user"])
        self.ctx.robot2.tool = int(r2["tool"])
        self.ctx.robot2.user = int(r2["user"])
        self.ctx.robot1.set_vel(float(r1["vel"]))
        self.ctx.robot2.set_vel(float(r2["vel"]))
        self.ctx.robot1.set_use_mock(bool(r1["use_mock"]))
        self.ctx.robot2.set_use_mock(bool(r2["use_mock"]))
        self.ctx.robot1.set_di_force_mock(
            int(r1["di_belt_sensor"]), bool(r1.get("di_belt_use_mock", True))
        )

        self.ctx.gripper1.use_mock = bool(g1["use_mock"])
        self.ctx.gripper2.use_mock = bool(g2["use_mock"])
        self.ctx.gripper1.interface = str(g1["interface"])
        self.ctx.gripper2.interface = str(g2["interface"])
        self.ctx.gripper1.can_id = int(g1["can_id"])
        self.ctx.gripper2.can_id = int(g2["can_id"])
        self.ctx.gripper1.gripper_type = int(g1["gripper_type"])
        self.ctx.gripper2.gripper_type = int(g2["gripper_type"])
        self.ctx.gripper1.set_speeds(float(g1["open_speed"]), float(g1["close_speed"]))
        self.ctx.gripper2.set_speeds(float(g2["open_speed"]), float(g2["close_speed"]))
        self.ctx.gripper1.connect()
        self.ctx.gripper2.connect()

        self.ctx.press.cfg = press
        self.ctx.press.use_mock = bool(press["use_mock"])
        try:
            self.ctx.press.connect()
        except Exception:
            pass

        self.ctx.vision.use_mock = bool(self.chk_vision.isChecked())
        self.ctx.io.use_mock = bool(self.chk_io.isChecked())
        self.ctx.io.cfg = io

        self.ctx.gvl.clear_cmd_state()
        self.ctx.robot1.halt_motion()
        self.ctx.robot2.halt_motion()

        save_config(cfg)
        self.lbl_status.setText(
            "当前运行态："
            + self.ctx.mock_status_text()
            + "\n"
            + self.ctx.connection_status_text()[0]
        )
        QMessageBox.information(
            self,
            "已保存",
            "接口参数已写入 config/default.yaml，并已尽量应用到当前运行实例。\n"
            "相机改 serial 后后台连接，失败会弹「连接失败」报警，无需重启。\n"
            f"{self.ctx.mock_status_text()}",
        )
