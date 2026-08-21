"""
夹爪 CAN 封装 —— 与 Casbot_Press_Shoes 同一套用法。

底层：press_shoes.robot_arm.gripper_controller_can.CANGripperController
  · open_claw() / close_claw() 阻塞等反馈（张开完成 / 夹紧完成）
  · 开合命令后 4 字节为速度 float，可通过 set_speeds 调整

本模块对外：
  · open() / close() + poll_done() —— Station 扫描用（非阻塞）
  · open_claw() / close_claw() —— HMI 手动阻塞
  · open_done / close_done / busy —— 状态灯
  · open_speed / close_speed —— 开合速度
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# type=2 默认命令里速度字约为 50.0
DEFAULT_GRIP_SPEED = 50.0


class GripperCAN:
    def __init__(
        self,
        name: str,
        interface: str,
        can_id: int,
        gripper_type: int = 2,
        use_mock: bool = True,
        open_speed: float = DEFAULT_GRIP_SPEED,
        close_speed: float = DEFAULT_GRIP_SPEED,
    ):
        self.name = name
        self.interface = str(interface)
        self.can_id = int(can_id)
        self.gripper_type = int(gripper_type)
        self.use_mock = bool(use_mock)
        self.open_speed = float(open_speed)
        self.close_speed = float(close_speed)
        self.closed = True
        self.connected = False
        self.last_ok = True
        self.last_error = ""
        self._last_action: Optional[str] = None  # "open" | "close"
        self._ctrl = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._busy = False
        self._busy_until = 0.0

    # ------------------------------------------------------------------ status
    @property
    def busy(self) -> bool:
        if self.use_mock:
            return time.monotonic() < self._busy_until
        with self._lock:
            return bool(self._busy)

    @property
    def open_done(self) -> bool:
        """张开完成灯：非忙且当前为张开态。"""
        return (not self.busy) and (not self.closed) and bool(self.last_ok)

    @property
    def close_done(self) -> bool:
        """夹紧完成灯：非忙且当前为夹紧态。"""
        return (not self.busy) and bool(self.closed) and bool(self.last_ok)

    # ------------------------------------------------------------------ link
    def connect(self) -> bool:
        with self._lock:
            return self._connect_unlocked()

    def _connect_unlocked(self) -> bool:
        self._stop_ctrl_unlocked()
        if self.use_mock:
            self.connected = True
            self._ctrl = None
            self.last_error = ""
            log.info(
                "[%s] Mock 夹爪 interface=%s can_id=0x%X type=%s open_spd=%.1f close_spd=%.1f",
                self.name,
                self.interface,
                self.can_id,
                self.gripper_type,
                self.open_speed,
                self.close_speed,
            )
            return True
        try:
            from press_shoes.robot_arm.gripper_controller_can import CANGripperController

            iface = self.interface.strip() or "can0"
            if iface.lower() == "fake":
                self.use_mock = True
                self.connected = True
                self._ctrl = None
                return True
            self._ctrl = CANGripperController(
                self.name,
                interface=iface,
                can_id=self.can_id,
                gripper_type=self.gripper_type,
            )
            self._ctrl.set_motion_speeds(self.open_speed, self.close_speed)
            self.connected = bool(self._ctrl.is_connected())
            if self.connected:
                log.info(
                    "[%s] CAN 夹爪已连接 interface=%s can_id=0x%X type=%s spd=%.1f/%.1f",
                    self.name,
                    iface,
                    self.can_id,
                    self.gripper_type,
                    self.open_speed,
                    self.close_speed,
                )
            else:
                self.last_error = "CAN 夹爪使能/反馈失败"
                log.error("[%s] %s", self.name, self.last_error)
            return self.connected
        except Exception as e:
            log.error("[%s] CAN 夹爪初始化失败: %s", self.name, e)
            self.connected = False
            self._ctrl = None
            self.last_ok = False
            self.last_error = str(e)
            return False

    def _stop_ctrl_unlocked(self) -> None:
        ctrl = self._ctrl
        self._ctrl = None
        if ctrl is not None:
            try:
                ctrl.stop()
            except Exception as e:
                log.warning("[%s] 关闭夹爪控制器: %s", self.name, e)

    def refresh_link(self) -> bool:
        if self.use_mock:
            self.connected = True
            return True
        with self._lock:
            if self._ctrl is None:
                self.connected = False
                return False
            try:
                self.connected = bool(self._ctrl.is_connected())
            except Exception:
                self.connected = False
            return self.connected

    def reconnect(self) -> bool:
        return self.connect()

    def disconnect(self) -> None:
        with self._lock:
            self._wait_thread_unlocked(timeout=3.0)
            self._stop_ctrl_unlocked()
            self.connected = False

    def set_speeds(self, open_speed: float | None = None, close_speed: float | None = None) -> None:
        """设置张开/夹紧速度（写入命令帧速度字；真机立刻下发到控制器）。"""
        if open_speed is not None:
            self.open_speed = max(1.0, float(open_speed))
        if close_speed is not None:
            self.close_speed = max(1.0, float(close_speed))
        with self._lock:
            if self._ctrl is not None:
                try:
                    self._ctrl.set_motion_speeds(self.open_speed, self.close_speed)
                except Exception as e:
                    log.warning("[%s] 更新开合速度失败: %s", self.name, e)

    # ----------------------------------------------------------- Station API
    def open(self) -> None:
        """非阻塞张开；完成由 poll_done / open_done 判定。"""
        self._last_action = "open"
        if self.use_mock:
            self.closed = False
            self.connected = True
            self.last_ok = True
            self.last_error = ""
            # Mock：速度越大完成越快（50→约0.3s）
            dt = max(0.08, 15.0 / max(self.open_speed, 1.0))
            self._busy_until = time.monotonic() + dt
            self._busy = False
            log.info("[%s] Mock 张开 spd=%.1f", self.name, self.open_speed)
            return
        self._start_bg("open")

    def close(self) -> None:
        """非阻塞夹紧；完成由 poll_done / close_done 判定。"""
        self._last_action = "close"
        if self.use_mock:
            self.closed = True
            self.connected = True
            self.last_ok = True
            self.last_error = ""
            dt = max(0.08, 15.0 / max(self.close_speed, 1.0))
            self._busy_until = time.monotonic() + dt
            self._busy = False
            log.info("[%s] Mock 夹紧 spd=%.1f", self.name, self.close_speed)
            return
        self._start_bg("close")

    def poll_done(self) -> bool:
        """张开/夹紧动作完成（成功或失败都算本步结束，避免卡死）。"""
        if self.use_mock:
            return time.monotonic() >= self._busy_until
        with self._lock:
            return not bool(self._busy)

    # ---------------------------------------------------- Casbot-compatible
    def open_claw(self) -> bool:
        if self.use_mock:
            self.open()
            while not self.poll_done():
                time.sleep(0.02)
            return True
        self._wait_idle()
        return self._run_sync("open")

    def close_claw(self) -> bool:
        if self.use_mock:
            self.close()
            while not self.poll_done():
                time.sleep(0.02)
            return True
        self._wait_idle()
        return self._run_sync("close")

    # -------------------------------------------------------------- internal
    def _wait_idle(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._busy:
                    return
            time.sleep(0.02)

    def _wait_thread_unlocked(self, timeout: float = 3.0) -> None:
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=timeout)

    def _start_bg(self, action: str) -> None:
        with self._lock:
            if self._busy:
                log.warning("[%s] 上一次开合未完成，忽略新的 %s", self.name, action)
                return
            self._busy = True
            self.last_ok = True
            self.last_error = ""
            self._last_action = action
            self._thread = threading.Thread(
                target=self._bg_worker,
                args=(action,),
                name=f"{self.name}-{action}",
                daemon=True,
            )
            self._thread.start()

    def _bg_worker(self, action: str) -> None:
        try:
            ok = self._run_sync(action)
            if not ok:
                log.error("[%s] %s 失败", self.name, action)
        finally:
            with self._lock:
                self._busy = False

    def _run_sync(self, action: str) -> bool:
        with self._lock:
            if self._ctrl is None and not self.use_mock:
                if not self._connect_unlocked():
                    self.last_ok = False
                    self.last_error = "夹爪未连接"
                    return False
            ctrl = self._ctrl
            if ctrl is not None:
                try:
                    ctrl.set_motion_speeds(self.open_speed, self.close_speed)
                except Exception:
                    pass
        if self.use_mock or ctrl is None:
            if action == "open":
                self.closed = False
            else:
                self.closed = True
            self.last_ok = True
            return True
        try:
            if action == "open":
                ok = bool(ctrl.open_claw())
                if ok:
                    self.closed = False
            else:
                ok = bool(ctrl.close_claw())
                if ok:
                    self.closed = True
            self.connected = bool(ctrl.is_connected())
            self.last_ok = ok
            self.last_error = "" if ok else f"{action} 反馈超时/失败"
            try:
                if ctrl.is_claw_opened():
                    self.closed = False
                elif ctrl.is_claw_closed():
                    self.closed = True
            except Exception:
                pass
            return ok
        except Exception as e:
            self.last_ok = False
            self.last_error = str(e)
            log.exception("[%s] %s 异常", self.name, action)
            return False
