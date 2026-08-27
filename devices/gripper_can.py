"""
达妙 DM-J4310-2EC（48V）夹爪 —— 单文件驱动与对外接口。

硬件：CAN 1 Mbps，电机控制模式为**位置速度模式**（目标位置 rad + 速度 float）。

对外接口（本工程统一从这里 import）：
  GripperCAN          工位/HMI 使用（推荐）
  create_gripper()    从 yaml 片段或关键字参数创建实例
  CANGripperController  底层 CAN（高级调试；与历史 press_shoes 兼容）

工位扫描（非阻塞）：
  g.open() / g.close();  g.poll_done()

HMI / 脚本（阻塞到反馈）：
  g.open_claw() / g.close_claw()

连接与参数：
  g.connect() / g.reconnect() / g.disconnect()
  g.set_speeds(open_speed=..., close_speed=...)

单独测试：
  python3 -m devices.gripper_can --side 1
"""

from __future__ import annotations

import argparse
import atexit
import errno
import logging
import struct
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional

import can

log = logging.getLogger(__name__)

DEFAULT_GRIP_SPEED = 50.0

__all__ = [
    "DEFAULT_GRIP_SPEED",
    "CANGripperController",
    "GripperCAN",
    "GripperConfig",
    "create_gripper",
    "create_gripper_from_config",
]

class ClawState(Enum):
    """夹爪开合状态。"""
    UNKNOWN = 0
    OPENED = 1
    CLOSED = 2


class HoldState(Enum):
    """夹爪持物状态。"""
    UNKNOWN = 0
    EMPTY = 1
    HOLDING = 2
    DROPPED = 3


class DriverStatus(Enum):
    """驱动器返回的状态码定义。"""
    DISABLED = 0
    ENABLED = 1
    OVER_VOLTAGE = 8
    UNDER_VOLTAGE = 9
    OVER_CURRENT = 10
    MOS_OVER_TEMP = 11
    COIL_OVER_TEMP = 12
    COMMUNICATION_LOST = 13
    OVERLOAD = 14

class CANGripperController:
    """基于 CAN 总线的夹爪控制器，负责发命令、收反馈和解析状态。"""

    def __init__(self, name, interface='can0', can_id = 0x101, bitrate=1000000, 
                 can_filters=None, loopback=False,gripper_type = 0):
        """
        初始化CAN通信器
        :param name: 控制器名称，用于日志区分左右夹爪
        :param interface: CAN接口名 (默认can0)
        :param can_id: 发送命令使用的目标 CAN ID
        :param bitrate: 波特率 (默认1Mbps)
        :param can_filters: CAN过滤器列表
        :param loopback: 是否启用回环模式
        :param gripper_type: 夹爪型号，不同型号对应不同开合命令
        """
        self.logger = logging

        # 基础通信参数
        self.name = name
        self.interface = interface
        self.can_id = can_id
        self.bitrate = bitrate
        self.loopback = False
        self.running = False
        self.bus = None
        self.can_filters = can_filters or []

        # 在线状态与当前推断结果
        self.connected = False
        self.claw_state = ClawState.UNKNOWN
        self.hold_state = HoldState.UNKNOWN

        # 最近一次反馈帧缓存
        self.last_feedback = None
        self.last_feedback_time = None
        self.last_feedback_hex = None
        self.last_feedback_seq = 0
        self.last_controller_id = None
        self.last_raw_position = None
        self.last_raw_velocity = None
        self.last_raw_torque = None
        self.last_error_code = None
        self.last_position_rad = None
        self.last_velocity_raw_signed = None
        self.last_torque_raw_signed = None
        self.last_command = None
        self.drop_detected = False
        self.feedback_timeout = 1.0
        self.feedback_can_id = None
        self.last_status_name = None
        self.last_mos_temp = None
        self.last_rotor_temp = None
        self._cleanup_done = False
        self.send_retry_count = 3
        self.send_retry_delay = 0.05
        self.min_send_interval = 0.10
        self.command_settle_delay = 0.02
        self.last_send_time = 0.0

        # 状态判定阈值
        self.torque_hold_threshold = 1.6  #是否夹取物体的扭矩判断值
        self.torque_drop_threshold = 1  #是否掉落的扭矩判断值，通常设置为小于持物判断值
        self.empty_close_position_threshold = 30500
        self.position_min_rad = -12.5
        self.position_max_rad = 12.5
        self.position_tolerance_rad_open = 0.30   #当前位置和目标距离的差值，判断是否到达open状态
        self.position_tolerance_rad_close = 0.15  #当前位置和目标距离的差值，判断是否到达close状态
        self.velocity_min_rad_s = -30.0
        self.velocity_max_rad_s = 30.0
        self.torque_min_nm = -18
        self.torque_max_nm = 18

        # 根据夹爪型号选择开合命令（fake 也需要，便于调速度）
        if gripper_type == 0:
            self.COMMANDS = {
                'open': bytes([0x00, 0x00, 0x20, 0x40,0x00,0x00,0xF0,0x41]),
                'close': bytes([0x00, 0x00, 0xC0, 0xBF,0x00,0x00,0xF0,0x41]),
                'enable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFC]),
                'disenable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFD]),
            }
        elif gripper_type == 1:
            self.COMMANDS = {
                'open': bytes([0x00, 0x00, 0x00, 0x40,0x00,0x00,0x48,0x42]),
                'close': bytes([0xCD, 0xCC, 0x8C, 0xBF,0x00,0x00,0x48,0x42]),
                'enable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFC]),
                'disenable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFD]),
            }
        else:
            self.COMMANDS = {
                'open': bytes([0x66, 0x66, 0x06, 0x40,0x00,0x00,0x48,0x42]),
                'close': bytes([0x00, 0x00, 0xC0, 0xBF,0x00,0x00,0x48,0x42]),
                'enable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFC]),
                'disenable': bytes([0xFF, 0xFF, 0xFF, 0xFF,0xFF,0xFF,0xFF,0xFD]),
            }

        # 用开合命令中的目标位置，作为反馈状态判定参考值
        self.open_target_rad = self._decode_position_command(self.COMMANDS['open'])
        self.close_target_rad = self._decode_position_command(self.COMMANDS['close'])
        # 命令后 4 字节为速度 float（type2 默认约 50.0）
        self.open_speed = self._decode_speed_command(self.COMMANDS['open'])
        self.close_speed = self._decode_speed_command(self.COMMANDS['close'])

        if str(interface).lower() == "fake":
            self.logger.info(f"{name}使用模拟CAN接口（interface={interface}）")
            self.connected = True
            return

        atexit.register(self.stop)

        self.start()
        # 机械爪电机使能
        if self._poll_command_feedback('enable', self.is_connected, timeout=1.0):
            self.logger.info("夹抓使能命令已发送，已收到反馈确认")
        else:
            self.logger.error("夹抓CAN连接失败或使能失败")
        return

    def setup_bus(self):
        """配置CAN总线"""
        try:
            # 注意：设置波特率需要先配置系统CAN接口
            # 例如: sudo ip link set can0 type can bitrate 500000
            self.bus = can.interface.Bus(
                channel=self.interface,
                interface='socketcan',
                can_filters=self.can_filters,
                receive_own_messages=self.loopback
            )
            self.logger.info(f"CAN初始化成功，接口: {self.interface}, 波特率: {self.bitrate}bps")
            return True
        except Exception as e:
            self.logger.info(f"CAN初始化失败: {str(e)}")
            self.connected = False
            return False

    def _has_recent_feedback(self):
        """判断最近是否收到过未超时的反馈帧。"""
        if self.interface.lower() == "fake":
            return True
        if self.last_feedback_time is None:
            return False
        return (time.time() - self.last_feedback_time) <= self.feedback_timeout

    def is_connected(self):
        """判断CAN是否连接成功：近期收到过有效反馈才认为已连通。"""
        if self.interface.lower() == "fake":
            return True
        return self._has_recent_feedback()

    def is_driver_enabled(self):
        """根据最近一次反馈判断驱动器是否处于使能状态。"""
        if self.interface.lower() == "fake":
            return True
        return self.is_connected() and self.last_error_code == DriverStatus.ENABLED.value

    def _reconnect_bus(self):
        """在总线异常后尝试重新建立 CAN 连接。"""
        if self.interface.lower() == "fake":
            return True

        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception as e:
                self.logger.warning(f"重连前关闭CAN总线失败: {e}")
            finally:
                self.bus = None

        self.connected = False
        self.last_feedback = None
        self.last_feedback_time = None
        self.last_feedback_hex = None
        self.feedback_can_id = None
        self.claw_state = ClawState.UNKNOWN
        self.hold_state = HoldState.UNKNOWN
        return self.start()

    def _ensure_controller_ready(self, timeout=1.0):
        """在执行开合动作前确保总线在线且驱动已使能。"""
        if self.interface.lower() == "fake":
            return True

        if self.bus is None and not self._reconnect_bus():
            self.logger.error("CAN总线不存在，重连失败")
            return False

        if not self._has_recent_feedback():
            self.logger.warning("最近未收到夹爪反馈，尝试重新获取并恢复驱动使能")
            if not self.refresh_feedback(timeout=min(timeout, 0.3), command_key='enable'):
                self.logger.warning("刷新反馈失败，尝试重连CAN总线")
                if not self._reconnect_bus():
                    return False

        if self.is_driver_enabled():
            return True

        self.logger.warning(
            f"夹爪驱动当前非使能状态(status={self.last_status_name}, code={self.last_error_code})，尝试重新使能"
        )
        return self._poll_command_feedback('enable', self.is_driver_enabled, timeout=timeout)

    def is_claw_closed(self):
        """判断夹爪是否闭合（仅根据反馈位置解析结果）。"""
        return self.is_connected() and self.claw_state == ClawState.CLOSED

    def is_claw_opened(self):
        """判断夹爪是否张开（仅根据反馈位置解析结果）。"""
        return self.is_connected() and self.claw_state == ClawState.OPENED

    def is_drop_detected(self):
        """判断夹取物是否掉落（仅根据反馈帧推断）。"""
        return self.is_connected() and self.drop_detected
    
    def refresh_feedback(self, timeout=1.0, command_key='enable'):
        """主动发送一次查询命令，获取并解析最新反馈帧。

        默认发送 `enable` 命令来触发驱动器上报最新状态，避免为了取状态再次下发开合动作。
        返回值表示本次是否成功收到并解析到一帧新的有效反馈。
        """
        if self.interface.lower() == "fake":
            return True
        if not self.bus:
            self.logger.warning("CAN总线未初始化，无法刷新反馈帧")
            return False
        if command_key not in self.COMMANDS:
            self.logger.warning(f"未知反馈查询命令: {command_key}")
            return False
        if not self.send_message(self.can_id, self.COMMANDS[command_key]):
            self.logger.warning(f"发送{command_key}命令失败，无法获取最新反馈帧")
            return False
        return self._receive_feedback_frame(timeout=timeout)

    def detect_drop_with_latest_feedback(self, timeout=0.5, command_key='enable'):
        """发送查询命令拉取最新反馈帧，并返回当前是否检测到掉落。"""
        refreshed = self.refresh_feedback(timeout=timeout, command_key=command_key)
        if not refreshed:
            self.logger.warning("未收到最新反馈帧，返回当前缓存的掉落判断结果")
        return self.is_drop_detected()

    def _receive_feedback_frame(self, timeout=1.0):
        """在指定超时时间内接收一帧有效反馈，并更新本地状态缓存。"""
        if not self.bus:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                remaining = max(0.0, deadline - time.time())
                msg = self.bus.recv(timeout=remaining)
                if msg is None:
                    continue
                if len(msg.data) < 8:
                    continue
                # 反馈帧低 4 位为电机 ID，只处理当前夹爪对应的反馈
                feedback_motor_id = msg.data[0] & 0x0F
                expected_motor_id = self.can_id & 0x0F
                if feedback_motor_id != expected_motor_id:
                    continue

                # 保存最近一次有效反馈，供状态查询接口直接读取
                self.last_feedback = msg
                self.last_feedback_time = time.time()
                self.last_feedback_seq += 1
                self.feedback_can_id = msg.arbitration_id
                self.last_feedback_hex = ' '.join(f'{byte:02X}' for byte in msg.data)
                self._parse_feedback(msg.data)
                self.connected = True
                return True
            except Exception as e:
                self.logger.error(f"接收反馈帧异常: {e}")
                self.connected = False
                return False
        return False

    def _drain_feedback_frames(self):
        """清空当前总线缓冲区中的旧反馈，避免下一条命令误用历史帧。"""
        if not self.bus:
            return
        while True:
            try:
                msg = self.bus.recv(timeout=0.0)
                if msg is None:
                    break
            except Exception as e:
                self.logger.error(f"清空反馈缓冲区异常: {e}")
                break

    def _parse_feedback(self, data):
        """解析反馈帧，更新夹爪开合状态、持物状态和掉落标记。"""
        try:
            if len(data) < 8:
                return

            # 保存解析前状态，用于判断“从持物变为空”的掉落场景
            prev_hold_state = self.hold_state

            # 反馈帧编码格式：状态+位置+速度+力矩+温度
            id_and_status = data[0]
            controller_id = id_and_status & 0x0F
            err = (id_and_status >> 4) & 0x0F
            pos = (data[1] << 8) | data[2]
            vel = (data[3] << 4) | (data[4] >> 4)
            torque = ((data[4] & 0x0F) << 8) | data[5]

            self.last_controller_id = controller_id
            self.last_raw_position = pos
            self.last_raw_velocity = vel
            self.last_raw_torque = torque
            self.last_error_code = err
            self.last_status_name = self._status_name(err)
            self.last_mos_temp = data[7]
            self.last_rotor_temp = data[7]
            self.last_position_rad = self._uint_to_float(pos, self.position_min_rad, self.position_max_rad, 16)
            self.last_velocity_raw_signed = self._uint_to_float(vel, self.velocity_min_rad_s, self.velocity_max_rad_s, 12)
            self.last_torque_raw_signed = abs(self._uint_to_float(torque, self.torque_min_nm, self.torque_max_nm, 12))
            self.connected = err in (DriverStatus.DISABLED.value, DriverStatus.ENABLED.value)

            if err not in (DriverStatus.DISABLED.value, DriverStatus.ENABLED.value):
                self.claw_state = ClawState.UNKNOWN
                self.hold_state = HoldState.UNKNOWN
                self.drop_detected = False
                return

            # 通过当前位置与开/关目标位置的接近程度判断夹爪状态。
            # 如果关闭过程中夹到物体，位置可能停在中间，但扭矩会上升，此时也视为闭合。
            open_diff = abs(self.last_position_rad - self.open_target_rad)
            close_diff = abs(self.last_position_rad - self.close_target_rad)
            torque_closed_detected = (
                self.last_command == "close"
                and self.last_torque_raw_signed >= self.torque_hold_threshold
            )

            if open_diff <= self.position_tolerance_rad_open and open_diff < close_diff:
                self.claw_state = ClawState.OPENED
                self.drop_detected = True
            elif (
                close_diff <= self.position_tolerance_rad_close and close_diff < open_diff
            ) or torque_closed_detected:
                self.claw_state = ClawState.CLOSED
                # 闭合后若扭矩明显升高，说明夹到了物体；否则认为空夹闭合。
                if self.last_raw_position>self.empty_close_position_threshold:
                    self.hold_state = HoldState.HOLDING
                    self.drop_detected = False
                else: 
                    self.hold_state = HoldState.DROPPED
                    self.drop_detected = True

            else:
                self.claw_state = ClawState.UNKNOWN

            if self.claw_state == ClawState.OPENED:
                self.hold_state = HoldState.EMPTY
                self.drop_detected = False
                return

                # 未明确判定为开/关状态时，继续用力矩和位置推断是否持物或掉落。
            if self.claw_state == ClawState.UNKNOWN:
                holding_detected = (
                    self.last_torque_raw_signed >= self.torque_hold_threshold
                    or pos > self.empty_close_position_threshold
                )
                empty_closed_detected = (
                    pos <= self.empty_close_position_threshold
                )

                if holding_detected:
                    self.hold_state = HoldState.HOLDING
                    self.drop_detected = False
                elif empty_closed_detected:
                    if self.last_command == "close":
                        self.hold_state = HoldState.DROPPED
                        self.drop_detected = True
                    else:
                        self.hold_state = HoldState.EMPTY
                        self.drop_detected = False
                else:
                    self.hold_state = HoldState.UNKNOWN
                    self.drop_detected = False
        except Exception as e:
            self.logger.error(f"反馈帧解析异常: {e}")

    def _decode_position_command(self, payload):
        """解析位置速度模式命令中的目标位置 float。"""
        if payload is None or len(payload) < 4:
            return 0.0
        return struct.unpack('<f', payload[:4])[0]

    def _decode_speed_command(self, payload):
        """解析位置速度模式命令中的目标速度 float（字节4~7）。"""
        if payload is None or len(payload) < 8:
            return 50.0
        return struct.unpack('<f', payload[4:8])[0]

    def set_motion_speeds(self, open_speed: float | None = None, close_speed: float | None = None):
        """按当前位置目标重建 open/close 命令帧中的速度字。"""
        if open_speed is not None:
            self.open_speed = float(open_speed)
        if close_speed is not None:
            self.close_speed = float(close_speed)
        pos_open = struct.pack('<f', float(self.open_target_rad))
        pos_close = struct.pack('<f', float(self.close_target_rad))
        vel_open = struct.pack('<f', float(self.open_speed))
        vel_close = struct.pack('<f', float(self.close_speed))
        self.COMMANDS['open'] = pos_open + vel_open
        self.COMMANDS['close'] = pos_close + vel_close

    def _uint_to_float(self, x_int, x_min, x_max, bits):
        """将无符号整数按位宽映射到实际物理量范围。"""
        span = float(x_max) - float(x_min)
        return float(x_int) * span / ((1 << bits) - 1) + float(x_min)

    def _decode_signed_12bit(self, value):
        """将 12 位补码数转换为 Python 有符号整数。"""
        value &= 0x0FFF
        if value & 0x0800:
            return value - 0x1000
        return value

    def _status_name(self, code):
        """将驱动器状态码转换为可读名称。"""
        try:
            return DriverStatus(code).name
        except ValueError:
            return f"UNKNOWN_{code}"

    def _is_no_buffer_space_error(self, exc):
        """判断异常是否为 CAN 发送缓冲区已满。"""
        err_no = getattr(exc, "errno", None)
        if err_no == errno.ENOBUFS:
            return True

        nested_error = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        nested_errno = getattr(nested_error, "errno", None) if nested_error is not None else None
        if nested_errno == errno.ENOBUFS:
            return True

        return "No buffer space available" in str(exc)

    def _wait_for_send_slot(self):
        """限制连续发包速度，避免短时间内把 CAN 发送缓冲区打满。"""
        now = time.time()
        elapsed = now - self.last_send_time
        if elapsed < self.min_send_interval:
            time.sleep(self.min_send_interval - elapsed)

    def _poll_command_feedback(self, command_key, predicate, timeout=1.0, interval=0.1):
        """在超时窗口内重复发送命令，直到收到满足条件的最新反馈。"""
        self._drain_feedback_frames()
        start_time = time.time()
        previous_feedback_seq = self.last_feedback_seq
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if not self.send_message(self.can_id, self.COMMANDS[command_key]):
                return False
            received_new_feedback = self._receive_feedback_frame(timeout=min(interval, remaining))
            if not received_new_feedback or self.last_feedback_seq <= previous_feedback_seq:
                continue
            if predicate():
                end_time = time.time() - start_time
                print(f"夹爪打开关闭时间===={end_time}")
                return True
            previous_feedback_seq = self.last_feedback_seq
        return False

    def send_message(self, can_id, data, extended=False):
        """
        发送一帧CAN命令。
        :param can_id: CAN ID
        :param data: 数据 (bytes或bytearray)
        :param extended: 是否是扩展帧
        """
        if not self.bus:
            self.logger.info("CAN总线未初始化")
            return False

        for attempt in range(1, self.send_retry_count + 1):
            try:
                self._wait_for_send_slot()
                msg = can.Message(
                    arbitration_id=can_id,
                    data=data,
                    is_extended_id=extended
                )
                self.bus.send(msg)
                self.last_send_time = time.time()
                time.sleep(self.command_settle_delay)
                self.logger.info(f"发送成功: ID=0x{can_id:X}, 数据={data.hex()}, attempt={attempt}")
                return True
            except Exception as e:
                if self._is_no_buffer_space_error(e):
                    self.logger.warning(
                        f"CAN发送缓冲区已满，准备重试: attempt={attempt}/{self.send_retry_count}, error={e}"
                    )
                    self.connected = False
                    self._drain_feedback_frames()
                    time.sleep(self.send_retry_delay)
                    if attempt == self.send_retry_count:
                        self.logger.warning("连续发送失败，尝试重连CAN总线后再发送一次")
                        if self._reconnect_bus():
                            try:
                                self._wait_for_send_slot()
                                msg = can.Message(
                                    arbitration_id=can_id,
                                    data=data,
                                    is_extended_id=extended
                                )
                                self.bus.send(msg)
                                self.last_send_time = time.time()
                                time.sleep(self.command_settle_delay)
                                self.logger.info(
                                    f"重连后发送成功: ID=0x{can_id:X}, 数据={data.hex()}"
                                )
                                return True
                            except Exception as reconnect_exc:
                                self.logger.error(f"重连后发送仍失败: {reconnect_exc}")
                        return False
                    continue

                self.logger.info(f"发送失败: {str(e)}")
                self.connected = False
                return False

        return False


    def start(self):
        """启动 CAN 通信并初始化总线。"""
        if not self.setup_bus():
            self.logger.error("CAN通信启动失败")
            return False

        self._cleanup_done = False
        self.running = True
        return True

    def stop(self):
        """停止 CAN 通信并关闭总线。"""
        if self._cleanup_done:
            return True

        self.running = False

        if str(self.interface).lower() == "fake":
            self.connected = False
            self._cleanup_done = True
            self.logger.info("模拟CAN通信已停止")
            return True

        try:
            # if self.bus and hasattr(self, "COMMANDS") and 'disenable' in self.COMMANDS:
            self.last_command = "disenable"
            #     self.send_message(self.can_id, self.COMMANDS['disenable'])
            #     self._receive_feedback_frame(timeout=0.2)
        except Exception as e:
            self.logger.warning(f"发送夹爪失能命令失败: {e}")
        finally:
            if self.bus:
                try:
                    self.bus.shutdown()
                except Exception as e:
                    self.logger.warning(f"关闭CAN总线失败: {e}")
                finally:
                    self.bus = None

            self.connected = False
            self.claw_state = ClawState.UNKNOWN
            self.hold_state = HoldState.UNKNOWN
            self._cleanup_done = True
            self.logger.info("CAN通信已停止")
        return True


    def open_claw(self):
        """发送张开命令，并等待反馈确认夹爪已打开。"""
        if self.interface.lower() == "fake":
            self.logger.info("模拟接口：机械爪已打开")
            return True
        # if not self._ensure_controller_ready(timeout=1.0):
        #     self.logger.error("夹爪未就绪，无法执行打开")
        #     return False
        self.last_command = "open"
        self.logger.info("打开命令已发送，等待反馈确认最新状态")
        if self._poll_command_feedback('open', self.is_claw_opened, timeout=1.0):
            return True
        self.logger.info("机械爪打开失败")
        return False


    def close_claw(self):
        """发送闭合命令，并等待反馈确认夹爪已闭合。"""
        if self.interface.lower() == "fake":
            self.logger.info("模拟接口：机械爪已闭合")
            return True
        # if not self._ensure_controller_ready(timeout=1.0):
        #     self.logger.error("夹爪未就绪，无法执行闭合")
        #     return False
        self.last_command = "close"
        self.claw_state = ClawState.UNKNOWN
        self.logger.info("闭合命令已发送，等待反馈确认最新状态")
        if self._poll_command_feedback('close', self.is_claw_closed, timeout=2.0):
            return True
        self.logger.info("机械爪闭合失败")
        return False



class GripperCAN:
    """
    达妙夹爪对外接口（工位 / HMI / 脚本统一使用本类）。

    内部持有 CANGripperController 处理 CAN；use_mock=True 时不访问总线。
    """

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
        # 故障回调：app_context 可挂 raise_gripper_alarm
        self.on_fault: Optional[Callable[[str, str], None]] = None
        self.motor_index: int = 0  # 1-based 槽位号（调试页/报警用）

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
                self._emit_fault("GRIP_LINK", self.last_error)
            return self.connected
        except Exception as e:
            log.error("[%s] CAN 夹爪初始化失败: %s", self.name, e)
            self.connected = False
            self._ctrl = None
            self.last_ok = False
            self.last_error = str(e)
            self._emit_fault("GRIP_LINK", str(e))
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
            if not ok:
                self._emit_fault(
                    "GRIP_OPEN" if action == "open" else "GRIP_CLOSE",
                    self.last_error,
                )
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
            self._emit_fault(
                "GRIP_OPEN" if action == "open" else "GRIP_CLOSE",
                str(e),
            )
            log.exception("[%s] %s 异常", self.name, action)
            return False

    def _emit_fault(self, code: str, message: str) -> None:
        cb = self.on_fault
        if cb is None:
            return
        try:
            cb(code, f"[{self.name}] {message}")
        except Exception as e:
            log.warning("[%s] on_fault 回调异常: %s", self.name, e)

    def clear_fault(self) -> None:
        """报警复位时清除本爪错误状态。"""
        self.last_ok = True
        self.last_error = ""

    def poll_feedback(self, timeout: float = 0.0, *, query: bool = False) -> None:
        """更新编码器反馈。query=True 时主动发查询；否则只收已有帧（不阻塞 UI）。"""
        if self.use_mock or self._busy or self._ctrl is None:
            return
        try:
            if query:
                self._ctrl.refresh_feedback(timeout=max(float(timeout), 0.05))
            else:
                self._ctrl._receive_feedback_frame(timeout=float(timeout))
        except Exception:
            pass

    def position_display(self) -> str:
        """HMI 用：当前位置文案。"""
        if self.use_mock:
            return "模拟"
        ctrl = self._ctrl
        if ctrl is None:
            return "—"
        rad = getattr(ctrl, "last_position_rad", None)
        if rad is None:
            return "—"
        raw = getattr(ctrl, "last_raw_position", None)
        if raw is None:
            return f"{float(rad):.3f} rad"
        return f"{float(rad):.3f} rad  (raw {int(raw)})"

    def status_snapshot(self, *, poll: bool = False) -> Dict[str, Any]:
        """调试页状态快照。poll=True 时先向驱动要一帧反馈。"""
        if poll:
            self.poll_feedback()
        snap: Dict[str, Any] = {
            "name": self.name,
            "motor_index": int(self.motor_index),
            "interface": self.interface,
            "can_id": int(self.can_id),
            "use_mock": bool(self.use_mock),
            "connected": bool(self.connected),
            "busy": bool(self.busy),
            "closed": bool(self.closed),
            "open_done": bool(self.open_done),
            "close_done": bool(self.close_done),
            "open_speed": float(self.open_speed),
            "close_speed": float(self.close_speed),
            "last_ok": bool(self.last_ok),
            "last_error": str(self.last_error or ""),
            "last_action": self._last_action or "",
            "claw_state": "-",
            "hold_state": "-",
            "position_rad": None,
            "position_raw": None,
            "open_target_rad": None,
            "close_target_rad": None,
            "position_text": self.position_display(),
            "torque_nm": None,
            "driver_status": "-",
            "drop_detected": False,
        }
        ctrl = self._ctrl
        if ctrl is not None and not self.use_mock:
            try:
                snap["claw_state"] = getattr(ctrl.claw_state, "name", "-")
                snap["hold_state"] = getattr(ctrl.hold_state, "name", "-")
                snap["position_rad"] = ctrl.last_position_rad
                snap["position_raw"] = ctrl.last_raw_position
                snap["open_target_rad"] = getattr(ctrl, "open_target_rad", None)
                snap["close_target_rad"] = getattr(ctrl, "close_target_rad", None)
                snap["position_text"] = self.position_display()
                snap["torque_nm"] = ctrl.last_torque_raw_signed
                snap["driver_status"] = ctrl.last_status_name or "-"
                snap["drop_detected"] = bool(ctrl.is_drop_detected())
                snap["connected"] = bool(ctrl.is_connected())
            except Exception as e:
                snap["last_error"] = snap["last_error"] or str(e)
        return snap


@dataclass
class GripperConfig:
    """夹爪连接参数（与 config/default.yaml → grippers.* 字段一致）。"""

    name: str
    interface: str = "can0"
    can_id: int = 0x103
    gripper_type: int = 2
    use_mock: bool = True
    open_speed: float = DEFAULT_GRIP_SPEED
    close_speed: float = DEFAULT_GRIP_SPEED

    @classmethod
    def from_mapping(cls, name: str, cfg: Mapping[str, Any]) -> "GripperConfig":
        return cls(
            name=name,
            interface=str(cfg.get("interface", "can0")),
            can_id=int(cfg.get("can_id", 0x103)),
            gripper_type=int(cfg.get("gripper_type", 2)),
            use_mock=bool(cfg.get("use_mock", True)),
            open_speed=float(cfg.get("open_speed", DEFAULT_GRIP_SPEED)),
            close_speed=float(cfg.get("close_speed", DEFAULT_GRIP_SPEED)),
        )


def create_gripper(
    name: str,
    *,
    interface: str = "can0",
    can_id: int = 0x103,
    gripper_type: int = 2,
    use_mock: bool = True,
    open_speed: float = DEFAULT_GRIP_SPEED,
    close_speed: float = DEFAULT_GRIP_SPEED,
    connect: bool = False,
) -> GripperCAN:
    """创建夹爪实例；connect=True 时立即 connect()。"""
    g = GripperCAN(
        name,
        interface=interface,
        can_id=can_id,
        gripper_type=gripper_type,
        use_mock=use_mock,
        open_speed=open_speed,
        close_speed=close_speed,
    )
    if connect:
        g.connect()
    return g


def create_gripper_from_config(cfg: Mapping[str, Any], *, name: str, connect: bool = False) -> GripperCAN:
    """从 yaml 中 grippers.gripper1 / gripper2 字典创建。"""
    gc = GripperConfig.from_mapping(name, cfg)
    return create_gripper(
        gc.name,
        interface=gc.interface,
        can_id=gc.can_id,
        gripper_type=gc.gripper_type,
        use_mock=gc.use_mock,
        open_speed=gc.open_speed,
        close_speed=gc.close_speed,
        connect=connect,
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="达妙 DM-J4310-2EC 夹爪 CAN 测试")
    parser.add_argument(
        "--side",
        choices=["1", "2"],
        help="1=上料 can0/0x103, 2=下料 can1/0x101",
    )
    args = parser.parse_args()

    presets = {
        "1": dict(name="gripper1", interface="can0", can_id=0x103),
        "2": dict(name="gripper2", interface="can1", can_id=0x101),
    }
    selected = args.side
    while selected not in presets:
        selected = input("请选择夹爪(1=上料, 2=下料): ").strip()
        if selected not in presets:
            print("输入无效，请输入 1 或 2。")

    p = presets[selected]
    gripper: Optional[GripperCAN] = None
    ctrl: Optional[CANGripperController] = None
    try:
        gripper = create_gripper(
            p["name"],
            interface=p["interface"],
            can_id=p["can_id"],
            gripper_type=2,
            use_mock=False,
            connect=True,
        )
        ctrl = gripper._ctrl
        if ctrl is None:
            raise RuntimeError("真机模式下未建立 CAN 控制器，请检查 can 接口与供电")
        print(f"已连接 {p['name']} interface={p['interface']} can_id=0x{p['can_id']:X}")

        while True:
            print("\n操作: o=张开, c=夹紧, s=状态, d=掉落检测, q=退出")
            cmd = input("指令(o/c/s/d/q): ").strip().lower()
            if cmd == "o":
                ok = gripper.open_claw()
                print(f"张开: {'成功' if ok else '失败'} — {gripper.last_error}")
            elif cmd == "c":
                ok = gripper.close_claw()
                print(f"夹紧: {'成功' if ok else '失败'} — {gripper.last_error}")
            elif cmd == "s":
                print(f"连接: {gripper.connected}  busy: {gripper.busy}  closed: {gripper.closed}")
                if ctrl:
                    print(f"夹爪状态: {ctrl.claw_state.name}")
                    print(f"持物状态: {ctrl.hold_state.name}")
                    print(f"位置(rad): {ctrl.last_position_rad}")
                    print(f"扭矩(Nm): {ctrl.last_torque_raw_signed}")
            elif cmd == "d":
                dropped = ctrl.detect_drop_with_latest_feedback(timeout=1.0) if ctrl else False
                print(f"掉落: {'是' if dropped else '否'}")
            elif cmd == "q":
                break
            else:
                print("无效指令")
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        if gripper is not None:
            gripper.disconnect()
        print("测试结束")
