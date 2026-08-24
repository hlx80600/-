"""应用上下文：把配置、设备、记忆、状态揉在一起，给 Station/HMI 用。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from core.alarm import AlarmManager
from core.config_loader import load_config
from core.dry_run_shield import DryRunShield, apply_dry_run_from_cfg
from core.gvl import GVL
from core.lights import TowerLight
from core.machine_state import MachineController, MachineState, RunMode
from core.memory import MemoryBank
from core.production_stats import ProductionStats
from devices.gripper_bank import normalize_grippers_cfg
from devices.gripper_can import create_gripper_from_config
from devices.io_manager import IOManager
from devices.press_modbus import PressMachine
from devices.robot_fr5 import RobotFR5
from core.camera_config import resolve_camera_color_res, resolve_camera_fps
from vision.camera_orbbec import OrbbecCamera
from vision.vision_service import VisionService

log = logging.getLogger(__name__)


def device_use_mock(cfg_section: Dict[str, Any], system_default: bool) -> bool:
    """
    读某设备自己的 use_mock；没有则退回 system.use_mock。
    例：只有上料臂真机 → robots.robot1.use_mock: false，其余设备 true。
    """
    if not isinstance(cfg_section, dict):
        return bool(system_default)
    if "use_mock" in cfg_section:
        return bool(cfg_section["use_mock"])
    return bool(system_default)


class AppContext:
    def __init__(self, config_path=None):
        self.cfg = load_config(config_path)
        # 全局默认；各设备实际是否 Mock 看各自 .use_mock（见 device_use_mock）
        self.use_mock = bool(self.cfg.get("system", {}).get("use_mock", True))

        self.gvl = GVL()

        self.memory = MemoryBank()
        self.machine = MachineController()
        self.alarms = AlarmManager()
        self.lights = TowerLight()
        self.production = ProductionStats()
        from core.point_undo import PointUndoStack

        self.point_undo = PointUndoStack()

        io_cfg = self.cfg.get("io", {})
        self.io = IOManager(io_cfg, use_mock=device_use_mock(io_cfg, self.use_mock))

        # ============================================================
        # ★ 机器人 IP / Mock 从哪里来？
        #   config/default.yaml → robots.robot1/2.ip 与 use_mock
        #   不要在本文件写死 192.168.x.x；不要用全局 use_mock 一刀切
        # ============================================================
        r1 = self.cfg["robots"]["robot1"]  # 上料臂
        r2 = self.cfg["robots"]["robot2"]  # 下料臂
        r1_mock = device_use_mock(r1, self.use_mock)
        r2_mock = device_use_mock(r2, self.use_mock)
        self.robot1 = RobotFR5(
            r1["name"],
            r1["ip"],
            r1.get("tool", 1),
            r1.get("user", 0),
            r1.get("vel", 30),
            r1_mock,
        )
        self.robot2 = RobotFR5(
            r2["name"],
            r2["ip"],
            r2.get("tool", 1),
            r2.get("user", 0),
            r2.get("vel", 30),
            r2_mock,
        )
        motion_cfg = self.cfg.get("motion") if isinstance(self.cfg.get("motion"), dict) else {}
        self.robot1.apply_motion_cfg(motion_cfg)
        self.robot2.apply_motion_cfg(motion_cfg)
        self.robot1.apply_payload_cfg(r1.get("payloads"))
        self.robot2.apply_payload_cfg(r2.get("payloads"))

        def _on_robot_auto_cleared(robot_name: str, detail: str) -> None:
            self.alarms.note_event(
                "AUTO185",
                f"{robot_name} 示教器瞬态报警已自动消除（不停机）: {detail}",
                robot_name,
                0,
            )

        self.robot1.on_auto_cleared = lambda d, n=r1["name"]: _on_robot_auto_cleared(n, d)
        self.robot2.on_auto_cleared = lambda d, n=r2["name"]: _on_robot_auto_cleared(n, d)
        from core.motion_steps import ensure_motion_steps

        ensure_motion_steps(self.cfg)
        # 皮带光电：真机臂时默认仍允许 HMI 模拟（di_belt_use_mock，缺省 true）
        belt_di = int(r1.get("di_belt_sensor", 0))
        belt_di_mock = bool(r1.get("di_belt_use_mock", True))
        if (not r1_mock) and belt_di_mock:
            self.robot1.set_di_force_mock(belt_di, True)
            log.info("上料臂真机，但光电 DI[%s] 仍用 HMI 模拟（robots.robot1.di_belt_use_mock）", belt_di)

        gcfg = normalize_grippers_cfg(self.cfg)
        g1 = gcfg["gripper1"]
        g2 = gcfg["gripper2"]
        # 启用槽位实例（1..motor_count）；工位仍用 gripper1/gripper2 角色别名
        self.grippers: Dict[int, Any] = {}
        motors = gcfg.get("motors") or {}
        count = int(gcfg.get("motor_count", 2))
        for i in range(1, count + 1):
            m = motors.get(str(i)) or motors.get(i) or {}
            if not isinstance(m, dict):
                continue
            label = str(m.get("label") or f"电机{i}")
            self.grippers[i] = create_gripper_from_config(
                {**m, "use_mock": device_use_mock(m, self.use_mock)},
                name=f"电机{i}-{label}",
            )
            self.grippers[i].motor_index = i
            self.grippers[i].on_fault = self._make_gripper_fault_cb(i)
        load_i = int(gcfg.get("load_index", 1))
        unload_i = int(gcfg.get("unload_index", 2 if count >= 2 else 1))
        self.gripper1 = self.grippers.get(load_i) or create_gripper_from_config(
            {**g1, "use_mock": device_use_mock(g1, self.use_mock)},
            name="夹爪1",
        )
        self.gripper2 = self.grippers.get(unload_i) or create_gripper_from_config(
            {**g2, "use_mock": device_use_mock(g2, self.use_mock)},
            name="夹爪2",
        )
        if load_i not in self.grippers:
            self.grippers[load_i] = self.gripper1
            self.gripper1.motor_index = load_i
            self.gripper1.on_fault = self._make_gripper_fault_cb(load_i)
        if unload_i not in self.grippers:
            self.grippers[unload_i] = self.gripper2
            self.gripper2.motor_index = unload_i
            self.gripper2.on_fault = self._make_gripper_fault_cb(unload_i)
        # 保证角色别名也有回调
        self.gripper1.motor_index = load_i
        self.gripper2.motor_index = unload_i
        self.gripper1.on_fault = self._make_gripper_fault_cb(load_i)
        self.gripper2.on_fault = self._make_gripper_fault_cb(unload_i)
        self._last_grip_alarm_key: Optional[tuple] = None

        press_cfg = self.cfg.get("press", {})
        self.press = PressMachine(press_cfg, use_mock=device_use_mock(press_cfg, self.use_mock))

        self.cameras: Dict[str, OrbbecCamera] = {}
        for key, ccfg in self.cfg.get("cameras", {}).items():
            cw, ch = resolve_camera_color_res(ccfg, self.cfg)
            self.cameras[key] = OrbbecCamera(
                ccfg.get("name", key),
                index=int(ccfg.get("index", 0)),
                serial=str(ccfg.get("serial", "")),
                use_mock=device_use_mock(ccfg, self.use_mock),
                fps=resolve_camera_fps(key, ccfg, self.cfg),
                color_width=cw,
                color_height=ch,
            )
        vis_cfg = self.cfg.get("vision", {})
        self.vision = VisionService(
            self.cameras, vis_cfg, use_mock=device_use_mock(vis_cfg, self.use_mock)
        )
        # 整机配置：监控换算抓鞋前/后 TCP 时读 robots.*.payloads
        self.vision.root_cfg = self.cfg

        # 空跑屏蔽（OB1 tick；yaml system.dry_run）
        self.dry_run = DryRunShield(self)
        dry_cfg = (self.cfg.get("system") or {}).get("dry_run") or {}
        if isinstance(dry_cfg, dict):
            self.dry_run.keep_belt_on = bool(dry_cfg.get("keep_belt_on", True))
            self.dry_run.auto_place_match = bool(dry_cfg.get("auto_place_match", True))
            self.dry_run.auto_pick_slot = bool(dry_cfg.get("auto_pick_slot", True))
            if "auto_rotate_s" in dry_cfg:
                self.dry_run.auto_rotate_s = float(dry_cfg.get("auto_rotate_s"))
            if "auto_press_s" in dry_cfg:
                self.dry_run.auto_press_s = float(dry_cfg.get("auto_press_s"))

        self.runtime_pick = dict(self.cfg.get("runtime_pick", {}))
        # 启动时用 runtime_pick / 屏蔽示教点初始化 PickPose（无相机联调）
        self._init_pick_pose_from_config()
        # yaml 若已开 dry_run.enabled，启动即启用
        apply_dry_run_from_cfg(self)

        self._timers: Dict[str, float] = {}

        self.init_step = 0
        self.init_message = ""

        self.step_go_token = 0
        self._last_consumed_token = 0

        self.stations = {}

        # 真机断线后台重连（Mock 设备跳过）
        self._link_last_try = 0.0
        self._link_attempts: Dict[str, int] = {}
        self._last_link_alarm_key: Optional[tuple] = None
        self._link_last_ok_log: Dict[str, float] = {}
        # 启动连接完成前：不报 LINK，避免协调线程抢跑
        self.links_bootstrapped = False
        # 空闲时未连上只提示；点初始化/启动后才升为报警态
        self.link_alarm_armed = False

        log.info(
            "设备 Mock：R1=%s R2=%s G1=%s G2=%s Press=%s Vision兜底=%s IO=%s "
            "Cam1=%s Cam2=%s Cam3=%s Cam4=%s（true=模拟）",
            self.robot1.use_mock,
            self.robot2.use_mock,
            self.gripper1.use_mock,
            self.gripper2.use_mock,
            self.press.use_mock,
            self.vision.use_mock,
            self.io.use_mock,
            self.cameras.get("cam1").use_mock if self.cameras.get("cam1") else None,
            self.cameras.get("cam2").use_mock if self.cameras.get("cam2") else None,
            self.cameras.get("cam3").use_mock if self.cameras.get("cam3") else None,
            self.cameras.get("cam4").use_mock if self.cameras.get("cam4") else None,
        )

    def mock_status_text(self) -> str:
        """HMI 一行显示各设备真机/模拟。"""

        def tag(name: str, mock: bool) -> str:
            return f"{name}:{'模拟' if mock else '真机'}"

        parts = [
            tag("上料臂", self.robot1.use_mock),
            tag("下料臂", self.robot2.use_mock),
            tag("夹爪1", self.gripper1.use_mock),
            tag("夹爪2", self.gripper2.use_mock),
            tag("压鞋机", self.press.use_mock),
        ]
        for key in ("cam1", "cam2", "cam3", "cam4"):
            cam = self.cameras.get(key)
            if cam is not None:
                parts.append(tag(key, cam.use_mock))
        return " | ".join(parts)

    def _make_gripper_fault_cb(self, motor_index: int):
        def _cb(code: str, message: str) -> None:
            self.raise_gripper_alarm(code, message, motor_index=motor_index)

        return _cb

    def raise_gripper_alarm(
        self,
        code: str,
        message: str,
        *,
        motor_index: int = 0,
        popup: bool = True,
    ) -> None:
        """
        夹爪专用报警。
        代码约定：GRIP_LINK / GRIP_OPEN / GRIP_CLOSE / GRIP_DRV
        """
        code = str(code or "GRIP").upper()
        if not code.startswith("GRIP"):
            code = f"GRIP_{code}"
        station = f"Gripper{motor_index}" if motor_index else "Gripper"
        self.raise_alarm(code, message, station, int(motor_index), popup=popup)

    def clear_gripper_faults(self) -> list[str]:
        """
        报警复位时清夹爪侧错误：last_error、尝试重连真机。
        返回提示行。
        """
        tips: list[str] = []
        seen = set()
        for idx, g in sorted(getattr(self, "grippers", {}).items()):
            if g is None or id(g) in seen:
                continue
            seen.add(id(g))
            try:
                g.clear_fault()
            except Exception:
                pass
            if bool(getattr(g, "use_mock", True)):
                tips.append(f"电机{idx}: 模拟，已清错误")
                continue
            try:
                ok = bool(g.refresh_link()) if hasattr(g, "refresh_link") else bool(g.connected)
                if not ok:
                    ok = bool(g.reconnect())
                tips.append(
                    f"电机{idx}({g.interface} 0x{int(g.can_id):X}): "
                    + ("已连接" if ok else f"仍未连接 {g.last_error or ''}".strip())
                )
            except Exception as e:
                tips.append(f"电机{idx}: 复位异常 {e}")
        self._last_grip_alarm_key = None
        return tips

    def _link_device_entries(self) -> list[tuple[str, Any, str]]:
        """(显示名, 设备对象, 端点说明)。"""
        r1 = self.cfg.get("robots", {}).get("robot1", {})
        r2 = self.cfg.get("robots", {}).get("robot2", {})
        press = self.cfg.get("press", {})
        rows: list[tuple[str, Any, str]] = [
            ("上料臂R1", self.robot1, f"IP {r1.get('ip', self.robot1.ip)}"),
            ("下料臂R2", self.robot2, f"IP {r2.get('ip', self.robot2.ip)}"),
        ]
        # 全部启用夹爪电机
        gcfg = self.cfg.get("grippers") or {}
        motors = gcfg.get("motors") if isinstance(gcfg.get("motors"), dict) else {}
        bank = getattr(self, "grippers", {}) or {}
        if bank:
            for idx in sorted(bank.keys()):
                g = bank[idx]
                if g is None:
                    continue
                m = motors.get(str(idx)) or motors.get(idx) or {}
                iface = m.get("interface", getattr(g, "interface", "can0"))
                cid = int(m.get("can_id", getattr(g, "can_id", 0)))
                label = m.get("label") or getattr(g, "name", f"电机{idx}")
                rows.append((f"夹爪{idx}-{label}", g, f"{iface} 0x{cid:X}"))
        else:
            g1 = gcfg.get("gripper1", {})
            g2 = gcfg.get("gripper2", {})
            rows.append(
                (
                    "夹爪1",
                    self.gripper1,
                    f"{g1.get('interface', self.gripper1.interface)} 0x{int(g1.get('can_id', self.gripper1.can_id)):X}",
                )
            )
            rows.append(
                (
                    "夹爪2",
                    self.gripper2,
                    f"{g2.get('interface', self.gripper2.interface)} 0x{int(g2.get('can_id', self.gripper2.can_id)):X}",
                )
            )
        rows.append(
            (
                "压鞋机",
                self.press,
                f"{press.get('ip', '?')}:{press.get('port', 502)}",
            )
        )
        for key in ("cam1", "cam2", "cam3", "cam4"):
            cam = self.cameras.get(key)
            if cam is None:
                continue
            ccfg = (self.cfg.get("cameras") or {}).get(key) or {}
            ep = ccfg.get("serial") or f"index={ccfg.get('index', cam.index)}"
            rows.append((key, cam, str(ep)))
        return rows

    @staticmethod
    def _device_link_ok(dev: Any) -> bool:
        """只读探测（不清 RPC）；Mock 恒 True。"""
        if bool(getattr(dev, "use_mock", False)):
            return True
        if hasattr(dev, "is_link_up"):
            try:
                return bool(dev.is_link_up())
            except Exception:
                pass
        if hasattr(dev, "connected"):
            return bool(dev.connected)
        return bool(getattr(dev, "opened", False))

    def device_link_snapshot(self) -> list[dict]:
        """
        各设备连接快照（给 HMI）。
        mock=True 视为已就绪，不参与重连。
        """
        out: list[dict] = []
        for name, dev, endpoint in self._link_device_entries():
            mock = bool(getattr(dev, "use_mock", False))
            opening = bool(getattr(dev, "opening", False))
            ok = self._device_link_ok(dev) if not opening else False
            attempts = int(self._link_attempts.get(name, 0))
            err = str(getattr(dev, "last_error", "") or "").strip()
            if mock:
                status = "模拟"
            elif opening:
                status = "正在连接…"
            elif ok:
                status = "已连接"
            else:
                status = f"未连接·重连中×{attempts}" if attempts else "未连接"
            out.append(
                {
                    "name": name,
                    "mock": mock,
                    "ok": ok,
                    "opening": opening,
                    "endpoint": endpoint,
                    "status": status,
                    "attempts": attempts,
                    "error": err,
                }
            )
        return out

    def missing_real_devices(self) -> list[dict]:
        """非 Mock、未在打开中、且当前未连接的设备列表。"""
        return [
            r
            for r in self.device_link_snapshot()
            if (not r["mock"]) and (not r["ok"]) and (not r.get("opening"))
        ]

    def require_all_linked(self) -> Optional[str]:
        """全部真机已连接则返回 None，否则返回中文原因。"""
        missing = self.missing_real_devices()
        if not missing:
            return None
        names = "、".join(f"{r['name']}({r['endpoint']})" for r in missing)
        return (
            f"设备未连接：{names}。"
            "请等待自动重连完成，或在「通信配置」将该设备改为 Mock。"
        )

    def connection_status_text(self) -> tuple[str, bool]:
        """
        返回 (展示文本, 是否有未连接真机)。
        """
        rows = self.device_link_snapshot()
        missing = [r for r in rows if (not r["mock"]) and (not r["ok"])]
        chips = []
        for r in rows:
            if r["mock"]:
                chips.append(f"{r['name']}:模拟")
            elif r["ok"]:
                chips.append(f"{r['name']}:已连接")
            else:
                chips.append(f"{r['name']}:未连接")
        line = " | ".join(chips)
        if missing:
            names = "、".join(r["name"] for r in missing)
            tip = f"⚠ 未连接：{names}（非 Mock 将自动重连；失败会报警）"
            return f"{line}\n{tip}", True
        return line, False

    def raise_link_failures_if_needed(self) -> None:
        """真机连不上：记 LINK 报警（不弹窗）。

        - 启动未完成（links_bootstrapped=False）：不报，避免开机抢跑误报。
        - 空闲/停止且未武装：只写日志，不把整机切到报警红灯（开机未连上常见）。
        - 初始化/运行中或已武装：正式 raise_alarm，禁止启动。
        """
        if not getattr(self, "links_bootstrapped", False):
            return
        missing = self.missing_real_devices()
        if not missing:
            self._last_link_alarm_key = None
            return
        key = tuple(r["name"] for r in missing)
        parts = []
        for r in missing:
            err = r.get("error") or r.get("status") or "未连接"
            parts.append(f"{r['name']}({r['endpoint']}): {err}")
        msg = "设备连接失败：\n" + "\n".join(parts) + "\n请检查线缆/IP/CAN/序列号，或改回模拟。"

        st = self.machine.state
        soft = (not getattr(self, "link_alarm_armed", False)) and st in (
            MachineState.IDLE,
            MachineState.STOPPED,
        )
        if soft:
            if key != self._last_link_alarm_key:
                self._last_link_alarm_key = key
                log.warning("[启动/空闲] %s", msg.replace("\n", " | "))
            return

        if key == self._last_link_alarm_key and self.alarms.has_alarm:
            return
        self._last_link_alarm_key = key
        self.raise_alarm("LINK", msg, "System", 0, popup=False)

    def maintain_device_links(self) -> None:
        """
        周期维护：探测断线；对非 Mock 未连接设备限频重连。
        Mock 设备永不重连真机链路。
        """
        if not getattr(self, "links_bootstrapped", False):
            return
        interval = float(self.cfg.get("system", {}).get("reconnect_interval_s", 3.0))
        interval = max(1.0, interval)
        now = time.monotonic()
        if now - self._link_last_try < interval:
            return
        self._link_last_try = now

        for name, dev, endpoint in self._link_device_entries():
            if bool(getattr(dev, "use_mock", False)):
                self._link_attempts.pop(name, None)
                continue
            if bool(getattr(dev, "opening", False)):
                continue
            try:
                if hasattr(dev, "refresh_link"):
                    ok = bool(dev.refresh_link())
                else:
                    ok = bool(getattr(dev, "connected", False) or getattr(dev, "opened", False))
            except Exception as e:
                log.debug("[%s] refresh_link 异常: %s", name, e)
                ok = False
            if ok:
                if self._link_attempts.pop(name, None):
                    log.info("[%s] 已重新连接 %s", name, endpoint)
                continue
            n = int(self._link_attempts.get(name, 0)) + 1
            self._link_attempts[name] = n
            try:
                if hasattr(dev, "reconnect"):
                    ok2 = bool(dev.reconnect())
                else:
                    ok2 = bool(dev.connect()) if hasattr(dev, "connect") else False
                    if not ok2 and hasattr(dev, "open"):
                        ok2 = bool(dev.open())
            except Exception as e:
                log.warning("[%s] 重连异常: %s", name, e)
                if hasattr(dev, "last_error") and not getattr(dev, "last_error", ""):
                    dev.last_error = str(e)
                ok2 = False
            if ok2:
                self._link_attempts.pop(name, None)
                log.info("[%s] 重连成功 %s", name, endpoint)
            elif n == 1 or n % 10 == 0:
                log.warning("[%s] 重连失败×%s %s（将持续尝试）", name, n, endpoint)
        self.raise_link_failures_if_needed()

    def start_timer(self, name: str, seconds: float) -> None:
        self._timers[name] = time.monotonic() + seconds

    def timer_done(self, name: str) -> bool:
        t = self._timers.get(name)
        if t is None:
            return True
        return time.monotonic() >= t

    def request_step_go(self) -> None:
        """HMI 点「执行当前步/下一步」时调用。"""
        self.step_go_token += 1

    def consume_step_go(self) -> bool:
        """Station 在单步模式想启动下一步时调用，成功则消费一次。"""
        if self.step_go_token > self._last_consumed_token:
            self._last_consumed_token = self.step_go_token
            return True
        return False

    def connect_all(self, *, on_progress=None) -> None:
        """连接机器人/夹爪/压机/相机。on_progress(text) 可选，供启动进度条。"""

        def _p(text: str) -> None:
            if callable(on_progress):
                try:
                    on_progress(text)
                except Exception:
                    pass

        _p("连接上料臂…")
        self.robot1.connect()
        _p("连接下料臂…")
        self.robot2.connect()
        _p("连接夹爪…")
        seen = set()
        for g in getattr(self, "grippers", {}).values():
            if g is None or id(g) in seen:
                continue
            seen.add(id(g))
            g.connect()
        if self.gripper1 is not None and id(self.gripper1) not in seen:
            self.gripper1.connect()
        if self.gripper2 is not None and id(self.gripper2) not in seen:
            self.gripper2.connect()
        _p("连接压鞋机…")
        self.press.connect()
        _p("打开相机…")
        for cam in self.cameras.values():
            if cam.use_mock:
                cam.open()
            else:
                cam.open_async()
        self.links_bootstrapped = True
        # 空闲不武装：未连上只写日志/监控提示，不立刻整机报警
        self.raise_link_failures_if_needed()
        missing = self.missing_real_devices()
        if missing:
            names = "、".join(r["name"] for r in missing)
            log.warning(
                "启动后仍有设备未连接：%s（后台重连；点初始化时再检查）",
                names,
            )

    def update_lights(self) -> None:
        out = self.lights.update(self.machine.state, self.machine.mode, self.alarms.has_alarm)
        self.io.write_lights(out.red, out.yellow, out.green)

    def _init_pick_pose_from_config(self) -> None:
        """
        用 runtime_pick 或 vision.belt_pick_mock 初始化 gvl.PickPose。
        无相机联调时，屏蔽示教点即取料目标。
        """
        pose = dict(self.runtime_pick) if self.runtime_pick else {}
        mock = (self.cfg.get("vision") or {}).get("belt_pick_mock") or {}
        shoes = mock.get("shoes") if isinstance(mock, dict) else None
        if (not pose.get("x") and not pose.get("y")) and isinstance(shoes, list) and shoes:
            s0 = shoes[0] if isinstance(shoes[0], dict) else {}
            pose.update(
                {
                    "x": s0.get("x", -274),
                    "y": s0.get("y", -120),
                    "rz": s0.get("rz", 0),
                    "is_left_shoe": s0.get("is_left_shoe", True),
                    "z": mock.get("z", 488),
                    "rx": mock.get("rx", -178),
                    "ry": mock.get("ry", -2),
                }
            )
        if isinstance(mock, dict):
            pose.setdefault("z", mock.get("z", 488))
            pose.setdefault("rx", mock.get("rx", -178))
            pose.setdefault("ry", mock.get("ry", -2))
        for k in ("x", "y", "z", "rx", "ry", "rz"):
            if k in pose:
                self.gvl.PickPose[k] = float(pose[k])
        if "is_left_shoe" in pose:
            self.gvl.PickPose["is_left_shoe"] = bool(pose["is_left_shoe"])

    def raise_alarm(
        self, code: str, msg: str, station: str = "", step: int = 0, *, popup: bool = True
    ) -> None:
        self.alarms.raise_alarm(code, msg, station, step, popup=popup)
        self.gvl.Main.Alarming = True
        if self.machine.state not in (MachineState.ESTOP,):
            self.machine.set_state(MachineState.ALARM)

    def set_robot_holding_shoe(self, robot_key: str, holding: bool, *, force: bool = False) -> None:
        """
        自动运行负载切换：
          holding=False → 负载1 + 工具坐标1（仅手爪）
          holding=True  → 负载2 + 工具坐标2（手爪+鞋）
        """
        robot = self.robot1 if robot_key == "robot1" else self.robot2
        try:
            robot.set_holding_shoe(bool(holding), force=force)
        except Exception as e:
            self.raise_alarm(
                "PAYLOAD",
                f"{robot.name} 切换负载失败: {e}",
                "Robot1" if robot_key == "robot1" else "Robot2",
                0,
            )

    def pose(self, robot_key: str, point_name: str) -> Dict[str, float]:
        from devices.pose_utils import numeric_pose

        return numeric_pose(self.cfg["points"][robot_key][point_name])

    def offset(self, robot_key: str, offset_name: str) -> Dict[str, float]:
        from devices.pose_utils import numeric_pose

        return numeric_pose(self.cfg["points"][robot_key][offset_name])

    def point_label(self, robot_key: str, point_name: str) -> str:
        """点位中文备注（给 HMI）。"""
        from devices.pose_utils import point_display_name

        raw = self.cfg.get("points", {}).get(robot_key, {}).get(point_name, {})
        return point_display_name(
            point_name, raw if isinstance(raw, dict) else None, robot_key=robot_key
        )

    def named_point_tag(self, robot_key: str, point_name: str) -> str:
        """运动报警用：中文名[配置键]。"""
        return f"{self.point_label(robot_key, point_name)}[{point_name}]"

    def step_motion_kwargs(self, step_key: str | None, *, precise: bool = False) -> dict:
        """
        按程序步键读 vel/acc/blend（与锁存名一致，如 s2a10_30）。
        precise=True 时强制不平滑，仍保留本步速度/加速度。
        """
        from core.motion_steps import ensure_motion_steps, read_step_motion

        ensure_motion_steps(self.cfg)
        opts = read_step_motion(self.cfg, step_key)
        if precise:
            opts["blend"] = False
            opts.pop("blend_t_ms", None)
            opts.pop("blend_r_mm", None)
        return opts

    def point_wants_blend(self, robot_key: str, point_name: str) -> bool:
        """兼容旧调用：点位页调试仍可读点上 blend；自动流程请用 step_motion_kwargs。"""
        return bool(self.point_blend_kwargs(robot_key, point_name).get("blend"))

    def point_blend_kwargs(self, robot_key: str, point_name: str) -> dict:
        """点位调试用；自动 Station 已改用程序步 motion_steps。"""
        raw = (self.cfg.get("points") or {}).get(robot_key, {}).get(point_name)
        if not isinstance(raw, dict):
            return {"blend": False}
        out: dict = {"blend": bool(raw.get("blend", False))}
        if raw.get("blend_t_ms") is not None:
            try:
                out["blend_t_ms"] = float(raw["blend_t_ms"])
            except (TypeError, ValueError):
                pass
        if raw.get("blend_r_mm") is not None:
            try:
                out["blend_r_mm"] = float(raw["blend_r_mm"])
            except (TypeError, ValueError):
                pass
        return out

    def move_to_point(
        self,
        robot_key: str,
        point_name: str,
        *,
        linear: bool = False,
        from_label: str = "",
        step_key: str | None = None,
        blend: bool | None = None,
        precise: bool = False,
        blend_t_ms: float | None = None,
        blend_r_mm: float | None = None,
        vel: float | None = None,
        acc: float | None = None,
    ) -> None:
        """按配置点名运动。自动流程请传 step_key 使用该程序步的速度/平滑。"""
        from devices.pose_utils import extract_joints, numeric_pose

        robot = self.robot1 if robot_key == "robot1" else self.robot2
        tag = self.named_point_tag(robot_key, point_name)
        raw = self.cfg["points"][robot_key][point_name]
        pose = numeric_pose(raw if isinstance(raw, dict) else {})
        if step_key:
            opts = self.step_motion_kwargs(step_key, precise=precise)
        else:
            opts = self.point_blend_kwargs(robot_key, point_name)
            if precise:
                opts = {"blend": False, "vel": 100.0, "acc": 100.0}
        if blend is not None:
            opts["blend"] = bool(blend)
        if blend_t_ms is not None:
            opts["blend_t_ms"] = float(blend_t_ms)
        if blend_r_mm is not None:
            opts["blend_r_mm"] = float(blend_r_mm)
        if vel is not None:
            opts["vel"] = float(vel)
        if acc is not None:
            opts["acc"] = float(acc)
        if linear:
            robot.move_l(
                pose,
                label=tag,
                from_label=from_label,
                precise=precise,
                **opts,
            )
        else:
            joints = extract_joints(raw if isinstance(raw, dict) else None)
            robot.move_j(
                pose,
                joints=joints,
                label=tag,
                from_label=from_label,
                precise=precise,
                **opts,
            )
