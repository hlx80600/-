import os
import sys
from pathlib import Path

# 直接运行时把仓库根目录和 RSDT_Simple_Automation 注入 sys.path
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[2]
    _rsdt = _repo_root / "RSDT_Simple_Automation"
    for _p in (_rsdt, _repo_root):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    from press_shoes.press_machine_modbusTCP import ShoePressController, WorkStatus
    from press_shoes.press_machine_state import PressMachineStatus
else:
    from ..press_machine_modbusTCP import ShoePressController, WorkStatus
    from ..press_machine_state import PressMachineStatus

import time
from threading import Lock
from typing import Any, Callable, Optional

EmulatedDevice = False  # Set to False when integrating with real hardware
CMD_T = 0.004  # Minimum command interval to avoid overwhelming the press machine

class PressMachineManager:
    """Manages press machine operations."""

    def __init__(self, *, log: Any) -> None:
        self.log = log
        self.left_press_time = 4
        self.right_press_time = 4
        self._machine_lock = Lock()
        self._prediction_lock = Lock()
        self.press_machine: Optional[ShoePressController] = None
        self._press_sequence = 0
        self._active_press_predictions: dict[str, tuple[float, int]] = {}


        if not EmulatedDevice:
            host = os.getenv("PRESS_MACHINE_HOST", "192.168.1.100")
            port = int(os.getenv("PRESS_MACHINE_PORT", "502"))
            unit_id = int(os.getenv("PRESS_MACHINE_UNIT_ID", "1"))
            timeout = float(os.getenv("PRESS_MACHINE_TIMEOUT", "3.0"))
            controller = ShoePressController(host=host, port=port, unit_id=unit_id, timeout=timeout)
            if not controller.connect():
                raise RuntimeError(f"连接压鞋机失败: {host}:{port}")
            controller.set_remote_control_mode(True)
            self.press_machine = controller
            with self._machine_lock:
                self.press_machine.set_remote_control_mode(True) 
                time.sleep(CMD_T)  # Ensure the machine has time to switch modes
                self.press_machine.left_slot_reset()
                self.press_machine.right_slot_reset()

    def _mark_press_started(self, shoe_type: str, press_time: float) -> None:
        finish_at = time.monotonic() + max(float(press_time), 0.0)
        with self._prediction_lock:
            self._press_sequence += 1
            self._active_press_predictions[shoe_type] = (finish_at, self._press_sequence)

    def _mark_press_finished(self, shoe_type: str) -> None:
        with self._prediction_lock:
            self._active_press_predictions.pop(shoe_type, None)

    def _get_slot_status(self, side: str) -> Optional[str]:
        """Get the status of the left or right slot."""
        if EmulatedDevice:
            return "finished"
        if self.press_machine is None:
            return "error"

        try:
            with self._machine_lock:
                if side == "left":
                    status = self.press_machine.get_left_work_status()
                elif side == "right":
                    status = self.press_machine.get_right_work_status()
                else:
                    return "error"
        except Exception as exc:
            self.log.error(f"[压机] 获取{side}槽工作状态失败: {exc}")
            return "error"

        if status == WorkStatus.PRESSURE_ERROR.value:
            self.log.error(f"[压机] {side}槽压力异常")
            return "error"
        if status == WorkStatus.RESET.value:
            # time.sleep(1.5)
            return "finished"
        return "working"
    def _wait_slot_finished(self, side: str) -> int:
        """Poll slot status until finished or error."""
        self.log.debug(f"[压机] 检查{side}槽状态")
        if EmulatedDevice:
            self.log.info(f"[压机] 模拟{side}槽压鞋完成")
            return 0
        while True:
            status = self._get_slot_status(side)
            if status == "finished":
                self.log.info(f"[压机] {side}槽压鞋完成")
                return 0
            if status == "error":
                self.log.error(f"[压机] {side}槽发生错误")
                return -1
            time.sleep(CMD_T)

    def _wait_lever_move_done(self, side: str) -> int:
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            return -1

        while True:
            try:
                with self._machine_lock:
                    done = self.press_machine.get_left_motor_done() if side == "left" else self.press_machine.get_right_motor_done()
            except Exception as exc:
                self.log.error(f"[压机] 获取{side}杆电机完成状态失败: {exc}")
                return -1
            if done:
                return 0
            time.sleep(0.005)

    def move_left_lever(self, distance: float) -> int:
        """Move the left press lever by a given distance."""
        distance = int(distance)
        self.log.debug(f"[压机] 左杆移动距离: {distance}")
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 左杆移动失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_left_move_distance(distance)
                if not ret:
                    self.log.error(f"[压机] 设置左杆移动距离失败: {distance}")
                    return -1
                time.sleep(CMD_T)
                ret = self.press_machine.start_left_motor()
        except Exception as exc:
            self.log.error(f"[压机] 左杆移动过程异常: {exc}")
            return -1

        if not ret:
            self.log.error("[压机] 左杆启动失败")
            return -1
        if self._wait_lever_move_done("left") != 0:
            self.log.error("[压机] 等待左杆移动完成失败")
            return -1
        return 0

    def move_right_lever(self, distance: float) -> int:
        """Move the right press lever by a given distance."""
        distance = int(distance)
        self.log.debug(f"[压机] 右杆移动距离: {distance}")
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 右杆移动失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_right_move_distance(distance)
                if not ret:
                    self.log.error(f"[压机] 设置右杆移动距离失败: {distance}")
                    return -1
                time.sleep(CMD_T)
                ret = self.press_machine.start_right_motor()
        except Exception as exc:
            self.log.error(f"[压机] 右杆移动过程异常: {exc}")
            return -1

        if not ret:
            self.log.error("[压机] 右杆启动失败")
            return -1
        if self._wait_lever_move_done("right") != 0:
            self.log.error("[压机] 等待右杆移动完成失败")
            return -1
        return 0

    def set_left_press_time(self, seconds: int) -> int:
        self.log.info(f"[压机] 设置左槽压鞋时间: {seconds}")
        time.sleep(CMD_T)  # Ensure we don't send commands too rapidly
        self.left_press_time = seconds
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 设置左槽压鞋时间失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_left_press_time(seconds)
        except Exception as exc:
            self.log.error(f"[压机] 设置左槽压鞋时间异常: {exc}")
            return -1
        return 0 if ret else -1

    def set_right_press_time(self, seconds: int) -> int:
        self.log.info(f"[压机] 设置右槽压鞋时间: {seconds}")
        self.right_press_time = seconds
        time.sleep(CMD_T)  # Ensure we don't send commands too rapidly
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 设置右槽压鞋时间失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_right_press_time(seconds)
        except Exception as exc:
            self.log.error(f"[压机] 设置右槽压鞋时间异常: {exc}")
            return -1
        return 0 if ret else -1

    def left_lever_ready(self) -> int:
        self.log.info("[压机] 左压杆放下准备对位")
        time.sleep(CMD_T)  # Ensure we don't send commands too rapidly
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 左压杆放下准备对位失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_left_slot_shoe_placed()
        except Exception as exc:
            self.log.error(f"[压机] 左压杆准备对位异常: {exc}")
            return -1
        return 0 if ret else -1

    def right_lever_ready(self) -> int:
        self.log.info("[压机] 右压杆放下准备对位")
        time.sleep(CMD_T)  # Ensure we don't send commands too rapidly
        if EmulatedDevice:
            return 0
        if self.press_machine is None:
            self.log.error("[压机] 右压杆放下准备对位失败，压机控制器未初始化")
            return -1

        try:
            with self._machine_lock:
                ret = self.press_machine.set_right_slot_shoe_placed()
        except Exception as exc:
            self.log.error(f"[压机] 右压杆准备对位异常: {exc}")
            return -1
        return 0 if ret else -1

    def press_shoe(self, shoe_type: str, on_finished: Optional[Callable[[], None]] = None) -> PressMachineStatus:
        self.log.info(f"[压机] 开始压{'左' if shoe_type == 'left' else '右'}鞋")

        if EmulatedDevice:
            press_time = self.left_press_time if shoe_type == "left" else self.right_press_time
            self._mark_press_started(shoe_type, press_time)
            if on_finished is not None:
                try:
                    time.sleep(7)  # Simulate pressing time
                    on_finished()
                finally:
                    self._mark_press_finished(shoe_type)
            else:
                time.sleep(20)
                self._mark_press_finished(shoe_type)
            return 0

        if self.press_machine is None:
            self.log.error("[压机] 压鞋失败，压机控制器未初始化")
            return -1

        side_label = "左" if shoe_type == "left" else "右"
        press_time = None
        try:
            with self._machine_lock:
                if shoe_type == "left":
                    ok = self.press_machine.start_left_slot_up()
                    time.sleep(CMD_T)  # 等待压机开始动作
                    self.left_press_time = self.press_machine.get_left_press_time()
                    self.log.info(f"[压机] 左槽实际压鞋时间: {self.left_press_time}秒")
                    press_time = self.left_press_time
                elif shoe_type == "right":
                    ok = self.press_machine.start_right_slot_up()
                    time.sleep(CMD_T)  # 等待压机开始动作
                    self.right_press_time = self.press_machine.get_right_press_time()
                    self.log.info(f"[压机] 右槽实际压鞋时间: {self.right_press_time}秒")
                    press_time = self.right_press_time
                else:
                    self.log.error(f"[压机] 不支持的鞋型: {shoe_type}")
                    return -1
        except Exception as exc:
            self.log.error(f"[压机] {side_label}槽启动上升异常: {exc}")
            return -1
        if not ok:
            self.log.error(f"[压机] {side_label}槽上升失败")
            return -1
        self._mark_press_started(shoe_type, press_time)
        try:
            time.sleep(max(press_time - 4.5, 0.5))  # 等待压机开始动作
            ret = self._wait_slot_finished(shoe_type)
            if ret == 0 and on_finished is not None:
                on_finished()
                self.log.info(f"[压机] {side_label}鞋压制完成")
            return ret
        finally:
            self._mark_press_finished(shoe_type)

    def predict_next_finish_shoe(self) -> Optional[str]:
        with self._prediction_lock:
            if not self._active_press_predictions:
                return None
            shoe_type, _ = min(
                self._active_press_predictions.items(),
                key=lambda item: (item[1][0], item[1][1]),
            )
            return shoe_type
    
if __name__ == "__main__":
    from press_shoes.utils import log

    manager = PressMachineManager(log=log)
    manager.set_right_press_time(4)
    time.sleep(CMD_T)
    r_time = manager.press_machine.get_right_press_time()
    print(f"当前右槽压鞋时间: {r_time}秒")
    # manager.press_machine.right_slot_reset()
    # manager.right_lever_ready()
    # time.sleep(3)
    # manager.move_right_lever(10)
    # manager.press_shoe("right")

    manager.set_left_press_time(5)
    # time.sleep(CMD_T)
    manager.press_machine.get_left_press_time()
    print(f"当前左槽压鞋时间: {manager.left_press_time}秒")
    # manager.press_machine.left_slot_reset()
    # manager.left_lever_ready()
    # time.sleep(3)
    # manager.move_left_lever(10)
    # manager.press_shoe("left")