"""
压鞋机Modbus TCP控制程序
基于台达 PLC
"""

"""功能说明： 
电机运动：绝对位置移动，16位整型。单位毫米，两个电机使用一个速度参数，范围（0-100）
急停功能：只有在压槽上升时设置急停才会读到急停状态，其他时候按下急停状态也一直为0。
PLC扫描周期：1.4ms
手动控制压杆移动会导致压杆一直移动，停止需发送停止命令

压杆移动还没有软限位，到现场后根据实际设置
"""
import socket
import time
import struct
import threading
from enum import Enum
from typing import Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

# ==================== Modbus功能码 ====================
class ModbusFC:
    READ_COILS = 0x01           # 读线圈(M,Y)
    READ_DISCRETE_INPUTS = 0x02 # 读离散输入(X) - 部分PLC不支持
    READ_HOLDING_REGISTERS = 0x03  # 读保持寄存器(D,R,T)
    READ_INPUT_REGISTERS = 0x04    # 读输入寄存器 - 部分PLC不支持
    WRITE_SINGLE_COIL = 0x05       # 写单个线圈
    WRITE_SINGLE_REGISTER = 0x06   # 写单个寄存器
    WRITE_MULTIPLE_COILS = 0x0F    # 写多个线圈
    WRITE_MULTIPLE_REGISTERS = 0x10 # 写多个寄存器


# ==================== 工作状态枚举 ====================
class WorkStatus(Enum):
    """工作状态（根据寄存器说明）"""
    READY = 0           # 准备就绪
    SWING_ARM_IN = 1    # 摆杆进
    FIRST_PRESS = 2     # 一次压
    CLAMP = 3           # 前后束紧
    SIDE_PRESS = 4      # 压边
    SECOND_PRESS = 5    # 二次压
    PRESS_TIMING = 6    # 压着计时
    RESET_CLAMP = 7     # 压边前后束还原
    THIRD_PRESS = 8     # 三次压
    RESET = 9           # 还原
    PRESSURE_ERROR = 10 # 压力异常
    
    def __str__(self):
        names = {
            0: "准备就绪",
            1: "摆杆进",
            2: "一次压",
            3: "前后束紧",
            4: "压边",
            5: "二次压",
            6: "压着计时",
            7: "压边前后束还原",
            8: "三次压",
            9: "还原",
            10: "压力异常"
        }
        return names.get(self.value, f"未知状态({self.value})")


# ==================== 寄存器地址映射 ====================
class PressRegister:
    """压鞋机寄存器地址定义"""
    
    # ==================== 控制寄存器 ====================
    # 上位机控制
    CONECT_CONTROL = 0xA11          # M529 - 压鞋机启动上位机控制 
    HAND_CONTROL_MODE = 0x81d          # M29 - 手动控制模式 
    
    # 放鞋状态
    LEFT_SLOT_PLACE_SHOE_STATUS = 0x878   # M120 - 左槽放鞋状态 (1=完成, 0=未完成)
    RIGHT_SLOT_PLACE_SHOE_STATUS = 0x864  # M100 - 右槽放鞋状态 (1=完成, 0=未完成)
    
    # 电机控制
    MOTOR_SPEED = 0x11C4             # D452 - 电机速度 (0-100, 16位整型)

    LEFT_MOTOR_START = 0x87a           # M122 - 左槽电机启动 (1=启动)
    LEFT_MOTOR_DONE = 0x87b         # M123 - 左槽电机完成 (1=完成)
    LEFT_MOVE_DISTANCE = 0x11C8     # D456 - 左槽移动距离 (单位mm, 16位整型)
    
    LEFT_SLOT_UP_START = 0x879      # M121 - 左槽上升 (1=上升)

    LEFT_SLOT_COUNT = 0x11b6          # D438 - 左槽计数 (16位整型)
    RESET_LEFT_SLOT_COUNT=0x844     # M68 - 重置左槽计数 (1=重置)

    RIGHT_MOTOR_START = 0x866       # M102 - 右槽电机启动 (1=启动)
    RIGHT_MOTOR_DONE = 0x867        # M103 - 右槽电机完成 (1=完成)
    RIGHT_MOVE_DISTANCE = 0x11C2    # D450 - 右槽移动距离 (单位mm, 16位整型)
    
    RIGHT_SLOT_UP_START = 0x865     # M101 - 右槽上升 (1=上升)

    RIGHT_SLOT_COUNT = 0x11b4          # D436 - 右槽计数 (16位整型)
    RESET_RIGHT_SLOT_COUNT = 0x812    # M18 - 重置右槽计数 (1=重置)
    
    # 压杆对齐状态 (X元件 - 可能需要用功能码01读取，视PLC支持情况)
    LEFT_ALIGN_PRESS_ROD = 0x406   # X6 - 左槽对齐压杆 (1=压杆回正, 0=压杆倾斜)
    RIGHT_ALIGN_PRESS_ROD = 0x401  # X1 - 右槽对齐压杆 (1=压杆回正, 0=压杆倾斜)
    
    # 设置急停
    SET_LEFT_EMERGENCY_STOP = 0x87c      # M124 - 左槽急停 (1=急停, 0=正常)
    SET_RIGHT_EMERGENCY_STOP = 0x868     # M104 - 右槽急停 (1=急停, 0=正常)
    
    # 压鞋时间
    LEFT_PRESS_TIME = 0x11A0        # D416 - 左槽压鞋时间 (单位s, 16位整型)
    RIGHT_PRESS_TIME = 0x1199       # D409 - 右槽压鞋时间 (单位s, 16位整型)

    
    # ==================== 手动控制压机 ====================
    # 左槽手动控制
    LEFT_SWING_ARM_IN = 0x847        # M71 - 左槽摆杆进
    LEFT_SWING_ARM_OUT = 0x851       # M81 - 左槽摆杆出
    LEFT_CLAMP_IN = 0x848            # M72 - 左槽前后束紧
    LEFT_CLAMP_OUT = 0x849           # M73 - 左槽前后束松
    LEFT_PRESS_UP = 0x84C            # M76 - 左槽压鞋上升
    LEFT_PRESS_DOWN = 0x84E          # M78 - 左槽压鞋下降
    LEFT_SIDE_PRESS = 0x84A          # M74 - 左槽压边缩进
    LEFT_SIDE_PRESS_RELEASE = 0x84B  # M75 - 左槽压边退
    LEFT_SECOND_PRESS = 0x84D        # M77 - 左槽二次压
    
    # 右槽手动控制
    RIGHT_SWING_ARM_IN = 0x815       # M21 - 右槽摆杆进
    RIGHT_SWING_ARM_OUT = 0x829      # M41 - 右槽摆杆出
    RIGHT_CLAMP_IN = 0x816           # M22 - 右槽前后束紧
    RIGHT_CLAMP_OUT = 0x817          # M23 - 右槽前后束松
    RIGHT_PRESS_UP = 0x81A           # M26 - 右槽压鞋上升
    RIGHT_PRESS_DOWN = 0x81C         # M28 - 右槽压鞋下降
    RIGHT_SIDE_PRESS = 0x818         # M24 - 右槽压边缩进
    RIGHT_SIDE_PRESS_RELEASE = 0x819 # M25 - 右槽压边退
    RIGHT_SECOND_PRESS = 0x81B       # M27 - 右槽二次压
    
    # ==================== 手动控制压杆 ====================
    # 左槽压杆
    LEFT_ARM_FRONT_LIMIT = 0xF80D   # X13 - 左槽摆杆前极限 (1=前极限)
    LEFT_ARM_BACK_LIMIT = 0xF80E    # X14 - 左槽摆杆后极限 (1=后极限)
    LEFT_ARM_MOVE_FORWARD = 0x854   # M84 - 左槽摆杆前移
    LEFT_ARM_MOVE_BACK = 0x856      # M86 - 左槽摆杆后移
    LEFT_ARM_GO_HOME = 0x855        # M85 - 左槽摆杆回原位
    
    # 右槽压杆
    RIGHT_ARM_FRONT_LIMIT = 0xF80F  # X15 - 右槽摆杆前极限
    RIGHT_ARM_BACK_LIMIT = 0xF810   # X16 - 右槽摆杆后极限
    RIGHT_ARM_MOVE_FORWARD = 0x851  # M81 - 右槽摆杆前移
    RIGHT_ARM_MOVE_BACK = 0x853     # M83 - 右槽摆杆后移
    RIGHT_ARM_GO_HOME = 0x852       # M82 - 右槽摆杆回原位
    
    # ==================== 反馈寄存器 ====================
    # 压鞋时间反馈（定时器）
    GET_LEFT_PRESS_TIME = 0x618    # T24 - 左槽压鞋时间 (单位ms)
    GET_RIGHT_PRESS_TIME = 0x604   # T4 - 右槽压鞋时间 (单位ms)
    
    # 压力反馈
    GET_LEFT_PRESSURE = 0x141       # D321 - 左槽压力 (单位MPa)
    GET_RIGHT_PRESSURE = 0x169      # D361 - 右槽压力 (单位MPa) 
    
    # 槽状态
    LEFT_SLOT_STATUS = 0x58         # M88 - 左槽状态 (1=停机, 0=工作中)
    RIGHT_SLOT_STATUS = 0x30        # M48 - 右槽状态 (1=停机, 0=工作中)
    
    # 工作状态(处于哪个工作流程)
    LEFT_SLOT_WORK_STATUS = 0x103d     # D61 - 左槽工作状态
    RIGHT_SLOT_WORK_STATUS = 0x103c    # D60 - 右槽工作状态

    # 急停
    LEFT_EMERGENCY_STOP_STATUS = 0x46      # M70 - 左槽急停 (1=急停, 0=正常)
    RIGHT_EMERGENCY_STOP_STATUS = 0x1E     # M30 - 右槽急停 (1=急停, 0=正常)


# ==================== Modbus TCP客户端 ====================
class ModbusTCPClient:
    """Modbus TCP客户端"""
    
    def __init__(self, host: str, port: int = 502, unit_id: int = 1, 
                 timeout: float = 3.0, retry: int = 3):
        """
        初始化Modbus TCP客户端
        
        Args:
            host: PLC的IP地址
            port: Modbus TCP端口，默认502
            unit_id: 单元标识符（从站地址）
            timeout: 超时时间（秒）
            retry: 重试次数
        """
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.retry = retry
        self._socket = None
        self._transaction_id = 0
        self._lock = threading.Lock()
        self._connected = False
        
    def connect(self) -> bool:
        """建立TCP连接"""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.timeout)
            self._socket.connect((self.host, self.port))
            self._connected = True
            print(f"✅ Modbus TCP连接成功: {self.host}:{self.port}, 单元ID: {self.unit_id}")
            return True
        except Exception as e:
            print(f"❌ Modbus TCP连接失败: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """断开TCP连接"""
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None
        self._connected = False
        print("✅ Modbus TCP连接已断开")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
    
    def _get_transaction_id(self) -> int:
        """获取下一个事务ID"""
        with self._lock:
            self._transaction_id = (self._transaction_id + 1) & 0xFFFF
            return self._transaction_id
    
    def _send_request(self, function_code: int, address: int, 
                      data: bytes = b'', quantity: int = 0) -> bytes:
        """
        发送Modbus TCP请求并接收响应（严格分离MBAP头和PDU，循环recv确保收满）
        """
        tid = self._get_transaction_id()
        # 构建PDU
        pdu = struct.pack('B', function_code) + struct.pack('>H', address)
        if quantity > 0:
            pdu += struct.pack('>H', quantity)
        else:
            pdu += data
        # MBAP头: 事务ID(2) + 协议ID(2) + 长度(2) + 单元ID(1)
        mbap = struct.pack('>HHHB', tid, 0, len(pdu) + 1, self.unit_id)
        frame = mbap + pdu
        last_error = None
        for attempt in range(self.retry):
            try:
                if not self._connected:
                    self.connect()
                self._socket.sendall(frame)
                # 先收MBAP头
                mbap_resp = b''
                while len(mbap_resp) < 7:
                    chunk = self._socket.recv(7 - len(mbap_resp))
                    if not chunk:
                        raise Exception("连接断开，未收到完整MBAP头")
                    mbap_resp += chunk
                resp_tid, resp_protocol, resp_length, resp_unit = struct.unpack('>HHHB', mbap_resp)
                if resp_tid != tid:
                    raise Exception(f"事务ID不匹配: 期望{tid}, 收到{resp_tid}")
                # resp_length 包含 单元ID+PDU长度
                pdu_len = resp_length - 1
                pdu_resp = b''
                while len(pdu_resp) < pdu_len:
                    chunk = self._socket.recv(pdu_len - len(pdu_resp))
                    if not chunk:
                        raise Exception("连接断开，未收到完整PDU")
                    pdu_resp += chunk
                if len(pdu_resp) < 1:
                    raise Exception("PDU长度不足")
                resp_fc = pdu_resp[0]
                if resp_fc & 0x80:
                    exception_code = pdu_resp[1] if len(pdu_resp) > 1 else 0
                    raise Exception(f"Modbus异常: 功能码0x{resp_fc:02X}, 异常码{exception_code}")
                return pdu_resp[1:]  # 返回PDU数据（去掉功能码）
            except Exception as e:
                last_error = e
                print(f"请求失败 (尝试 {attempt+1}/{self.retry}): {e}")
                if attempt < self.retry - 1:
                    time.sleep(0.5)
                    self._connected = False
        raise Exception(f"请求失败，已重试{self.retry}次: {last_error}")
    
    # ==================== 线圈操作 (M, Y) ====================
    def read_coil(self, address: int) -> bool:
        """读单个线圈 (功能码01)"""
        response = self._send_request(ModbusFC.READ_COILS, address, quantity=1)
        if len(response) >= 2:
            # response[0] 是byte count，response[1]才是数据
            return (response[1] & 0x01) == 0x01
        return False
    
    def read_coils(self, address: int, count: int) -> List[bool]:
        """读多个线圈"""
        if count < 1 or count > 2000:
            raise ValueError(f"读取数量{count}超出范围")
        
        response = self._send_request(ModbusFC.READ_COILS, address, quantity=count)
        byte_count = response[0] if len(response) > 0 else 0
        states = []
        for i in range(count):
            byte_idx = i // 8
            bit_idx = i % 8
            if byte_idx < byte_count and byte_idx + 1 < len(response):
                states.append((response[1 + byte_idx] >> bit_idx) & 0x01 == 0x01)
            else:
                states.append(False)
        return states
    
    def write_coil(self, address: int, state: bool) -> bool:
        """写单个线圈 (功能码05)"""
        value = 0xFF00 if state else 0x0000
        data = struct.pack('>H', value)
        self._send_request(ModbusFC.WRITE_SINGLE_COIL, address, data=data)
        return True
    
    def write_coils(self, address: int, states: List[bool]) -> bool:
        """写多个线圈 (功能码15)"""
        quantity = len(states)
        byte_count = (quantity + 7) // 8
        coil_data = bytearray(byte_count)
        for i, state in enumerate(states):
            if state:
                coil_data[i // 8] |= (1 << (i % 8))
        
        data = struct.pack('>HB', quantity, byte_count) + coil_data
        self._send_request(ModbusFC.WRITE_MULTIPLE_COILS, address, data=data)
        return True
    
    # ==================== 离散输入操作 (X) ====================
    # 注意：部分PLC可能不支持功能码02，如果报错请改用read_coil
    def read_discrete_input(self, address: int) -> bool:
        """读单个离散输入 (功能码02) - 需确认PLC支持"""
        try:
            response = self._send_request(ModbusFC.READ_DISCRETE_INPUTS, address, quantity=1)
            if len(response) >= 2:
                # response[0] 是byte count，response[1]才是数据
                return (response[1] & 0x01) == 0x01
            return False
        except Exception as e:
            # 如果不支持功能码02，回退到读线圈
            print(f"功能码02失败，尝试使用功能码01: {e}")
            return self.read_coil(address)
    
    # ==================== 寄存器操作 (D, R, T) ====================
    def read_register(self, address: int) -> int:
        """读单个寄存器 (功能码03)"""
        response = self._send_request(ModbusFC.READ_HOLDING_REGISTERS, address, quantity=1)
        if len(response) >= 3:
            return struct.unpack('>H', response[1:3])[0]
        return 0
    
    def read_registers(self, address: int, count: int) -> List[int]:
        """读多个寄存器"""
        if count < 1 or count > 125:
            raise ValueError(f"读取数量{count}超出范围")
        
        response = self._send_request(ModbusFC.READ_HOLDING_REGISTERS, address, quantity=count)
        byte_count = response[0] if len(response) > 0 else 0
        values = []
        for i in range(byte_count // 2):
            values.append(struct.unpack('>H', response[1 + i*2:3 + i*2])[0])
        return values
    
    def write_register(self, address: int, value: int) -> bool:
        """写单个寄存器 (功能码06)"""
        if value < -32768 or value > 32767:
            raise ValueError(f"值{value}超出16位范围")
        data = struct.pack('>H', value)
        self._send_request(ModbusFC.WRITE_SINGLE_REGISTER, address, data=data)
        return True
    
    def write_registers(self, address: int, values: List[int]) -> bool:
        """写多个寄存器 (功能码16)"""
        quantity = len(values)
        byte_count = quantity * 2
        data = struct.pack('>HB', quantity, byte_count)
        for value in values:
            data += struct.pack('>H', value)
        
        self._send_request(ModbusFC.WRITE_MULTIPLE_REGISTERS, address, data=data)
        return True
    
    # ==================== 32位浮点数操作 ====================
    def read_float(self, address: int) -> float:
        """读取32位浮点数 (占用两个连续寄存器)"""
        values = self.read_registers(address, 2)
        raw = (values[1] << 16) | values[0]
        return struct.unpack('>f', struct.pack('>I', raw))[0]
    
    def write_float(self, address: int, value: float) -> bool:
        """写入32位浮点数 (占用两个连续寄存器)"""
        raw = struct.unpack('>I', struct.pack('>f', value))[0]
        high = (raw >> 16) & 0xFFFF
        low = raw & 0xFFFF
        return self.write_registers(address, [low, high])


# ==================== 压鞋机控制器 ====================
class ShoePressController:
    """压鞋机控制器 (Modbus TCP版)"""
    
    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 3.0):
        """
        初始化压鞋机控制器
        
        Args:
            host: PLC的IP地址
            port: Modbus TCP端口
            unit_id: 单元标识符
            timeout: 超时时间
        """
        self.client = ModbusTCPClient(host, port, unit_id, timeout)
        self._connected = False
        self._log_enabled = True
    
    def connect(self) -> bool:
        """连接PLC"""
        self._connected = self.client.connect()
        return self._connected
    
    def disconnect(self):
        """断开连接"""
        self.client.disconnect()
        self._connected = False
    
    def log(self, msg: str, level: str = "INFO"):
        """日志输出"""
        if self._log_enabled:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}")
    
    # ==================== 上位机控制 ====================
    def set_remote_control_mode(self, enable: bool) -> bool:
        """设置上位机控制模式 (1=上位机控制, 0=PLC控制)"""
        return self.client.write_coil(PressRegister.CONECT_CONTROL, enable)
    
    def is_remote_control_mode(self) -> bool:
        """获取当前是否为上位机控制模式"""
        return self.client.read_coil(PressRegister.CONECT_CONTROL)
    
    def set_hand_control_mode(self, enable: bool) -> bool:
        """设置手动控制模式 (1=手动控制, 0=自动控制)"""
        return self.client.write_coil(PressRegister.HAND_CONTROL_MODE, enable)
    
    # ==================== 放鞋状态 ====================
    def set_left_slot_shoe_placed(self) -> bool:
        """设置左槽已放鞋"""
        return self.client.write_coil(PressRegister.LEFT_SLOT_PLACE_SHOE_STATUS, True)
    
    def set_right_slot_shoe_placed(self) -> bool:
        """设置右槽已放鞋"""
        return self.client.write_coil(PressRegister.RIGHT_SLOT_PLACE_SHOE_STATUS, True)
    
    # ==================== 电机控制 ====================
    def set_motor_speed(self, speed: int) -> bool:
        """设置电机速度 (0-100)"""
        if speed < 0 or speed > 100:
            raise ValueError("速度必须在0-100之间")
        return self.client.write_register(PressRegister.MOTOR_SPEED, speed)
        
    def start_left_motor(self) -> bool:
        """启动左槽电机"""
        return self.client.write_coil(PressRegister.LEFT_MOTOR_START, True)
    
    def stop_left_motor(self) -> bool:
        """停止左槽电机"""
        return self.client.write_coil(PressRegister.LEFT_MOTOR_START, False)
    
    def get_left_motor_done(self) -> bool:
        """获取左槽电机完成状态"""
        return self.client.read_coil(PressRegister.LEFT_MOTOR_DONE)
    
    def set_left_move_distance(self, distance_mm: float) -> bool:
        """设置左槽移动距离 (单位mm)"""
        return self.client.write_register(PressRegister.LEFT_MOVE_DISTANCE, distance_mm)
    
    def start_left_slot_up(self) -> bool:
        """左槽上升"""
        return self.client.write_coil(PressRegister.LEFT_SLOT_UP_START, True)
    
    def start_right_motor(self) -> bool:
        """启动右槽电机"""
        return self.client.write_coil(PressRegister.RIGHT_MOTOR_START, True)
    
    def stop_right_motor(self) -> bool:
        """停止右槽电机"""
        return self.client.write_coil(PressRegister.RIGHT_MOTOR_START, False)
    
    def get_right_motor_done(self) -> bool:
        """获取右槽电机完成状态"""
        return self.client.read_coil(PressRegister.RIGHT_MOTOR_DONE)
    
    def set_right_move_distance(self, distance_mm: int) -> bool:
        """设置右槽移动距离 (单位mm)"""
        return self.client.write_register(PressRegister.RIGHT_MOVE_DISTANCE, distance_mm)
    
    def get_right_move_distance(self) -> float:
        """获取右槽移动距离"""
        return self.client.read_float(PressRegister.RIGHT_MOVE_DISTANCE)
    
    def start_right_slot_up(self) -> bool:
        """右槽上升"""
        return self.client.write_coil(PressRegister.RIGHT_SLOT_UP_START, True)
    
    # ==================== 压杆对齐 ====================
    def get_left_press_rod_aligned(self) -> bool:
        """获取左槽压杆对齐 (回正)"""
        return self.client.read_discrete_input(PressRegister.LEFT_ALIGN_PRESS_ROD)
    
    def get_right_press_rod_aligned(self) -> bool:
        """获取右槽压杆对齐 (回正)"""
        return self.client.read_discrete_input(PressRegister.RIGHT_ALIGN_PRESS_ROD)
    
    # ==================== 急停控制 ====================
    def left_slot_reset(self) -> bool:
        """左槽状态复位"""
        self.client.write_coil(PressRegister.SET_LEFT_EMERGENCY_STOP, True)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.SET_LEFT_EMERGENCY_STOP, False)
        time.sleep(0.004)
        return True
       
    def is_left_emergency_stop(self) -> bool:
        """检查左槽急停状态"""
        return self.client.read_coil(PressRegister.LEFT_EMERGENCY_STOP_STATUS)
    
    def right_slot_reset(self) -> bool:
        """右槽状态复位"""
        self.client.write_coil(PressRegister.SET_RIGHT_EMERGENCY_STOP, True)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.SET_RIGHT_EMERGENCY_STOP, False)
        time.sleep(0.004)
        return True
    
    def is_right_emergency_stop(self) -> bool:
        """检查右槽急停状态"""
        return self.client.read_coil(PressRegister.RIGHT_EMERGENCY_STOP_STATUS)
    
    # ==================== 压鞋时间设置 ====================
    def set_left_press_time(self, seconds: int) -> bool:
        """设置左槽压鞋时间 (单位秒)"""
        seconds_reg=seconds*1000
        return self.client.write_register(PressRegister.LEFT_PRESS_TIME, seconds_reg)
    
    def get_left_press_time(self) -> int:
        """获取左槽压鞋时间"""
        return self.client.read_register(PressRegister.LEFT_PRESS_TIME) // 10
    
    def set_right_press_time(self, seconds: int) -> bool:
        """设置右槽压鞋时间 (单位秒)"""
        seconds_reg=seconds*1000
        return self.client.write_register(PressRegister.RIGHT_PRESS_TIME, seconds_reg)
    
    def get_right_press_time(self) -> int:
        """获取右槽压鞋时间"""
        return self.client.read_register(PressRegister.RIGHT_PRESS_TIME) // 10
    
    # ==================== 手动控制压机 ====================
    # 左槽手动控制
    def left_swing_arm_in(self) -> bool:
        """左槽摆杆进"""
        return self.client.write_coil(PressRegister.LEFT_SWING_ARM_IN, True)
    
    def left_swing_arm_out(self) -> bool:
        """左槽摆杆出"""
        return self.client.write_coil(PressRegister.LEFT_SWING_ARM_OUT, True)
    
    def left_clamp_in(self) -> bool:
        """左槽前后束紧"""
        return self.client.write_coil(PressRegister.LEFT_CLAMP_IN, True)
    
    def left_clamp_out(self) -> bool:
        """左槽前后束松"""
        return self.client.write_coil(PressRegister.LEFT_CLAMP_OUT, True)
    
    def left_press_up(self) -> bool:
        """左槽压鞋上升"""
        return self.client.write_coil(PressRegister.LEFT_PRESS_UP, True)
    
    def left_press_down(self) -> bool:
        """左槽压鞋下降"""
        return self.client.write_coil(PressRegister.LEFT_PRESS_DOWN, True)
    
    def left_side_press(self) -> bool:
        """左槽压边缩进"""
        return self.client.write_coil(PressRegister.LEFT_SIDE_PRESS, True)
    
    def left_side_press_release(self) -> bool:
        """左槽压边退"""
        return self.client.write_coil(PressRegister.LEFT_SIDE_PRESS_RELEASE, True)
    
    def left_second_press(self) -> bool:
        """左槽二次压"""
        return self.client.write_coil(PressRegister.LEFT_SECOND_PRESS, True)
    
    # 右槽手动控制
    def right_swing_arm_in(self) -> bool:
        """右槽摆杆进"""
        self.client.write_coil(PressRegister.RIGHT_SWING_ARM_IN, True)
        return True
    
    def right_swing_arm_out(self) -> bool:
        """右槽摆杆出"""
        self.client.write_coil(PressRegister.RIGHT_SWING_ARM_IN, False)
        return True
    
    def right_clamp_in(self) -> bool:
        """右槽前后束紧"""
        self.client.write_coil(PressRegister.RIGHT_CLAMP_IN, True)
        return True
    
    def right_clamp_out(self) -> bool:
        """右槽前后束松"""
        self.client.write_coil(PressRegister.RIGHT_CLAMP_IN, False)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.RIGHT_CLAMP_OUT, True)
        return True
    
    def right_press_up(self) -> bool:
        """右槽压鞋上升"""
        self.client.write_coil(PressRegister.RIGHT_PRESS_UP, True)
        return True
    
    def right_press_down(self) -> bool:
        """右槽压鞋下降"""
        self.client.write_coil(PressRegister.RIGHT_PRESS_UP, False)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.RIGHT_PRESS_DOWN, True)
        return True
    
    def right_side_press(self) -> bool:
        """右槽压边缩进"""
        self.client.write_coil(PressRegister.RIGHT_SIDE_PRESS, True)
        return True
    
    def right_side_press_release(self) -> bool:
        """右槽压边退"""
        self.client.write_coil(PressRegister.RIGHT_SIDE_PRESS, False)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.RIGHT_SIDE_PRESS_RELEASE, True)
        return True
    
    def right_second_press(self,status:bool) -> bool:
        """右槽二次压"""
        self.client.write_coil(PressRegister.RIGHT_SECOND_PRESS, status)
        return True
    
    # ==================== 压杆手动控制 ====================
    # 左槽压杆
    def left_arm_move_forward(self,status:bool) -> bool:
        """左槽摆杆前移"""
        print(status)
        return self.client.write_coil(PressRegister.LEFT_ARM_MOVE_FORWARD, status)
    
    def left_arm_move_back(self) -> bool:
        """左槽摆杆后移"""
        return self.client.write_coil(PressRegister.LEFT_ARM_MOVE_BACK, True)
    
    def left_arm_go_home(self) -> bool:
        """左槽摆杆回原位"""
        return self.client.write_coil(PressRegister.LEFT_ARM_GO_HOME, True)
    
    def is_left_arm_front_limit(self) -> bool:
        """检查左槽摆杆前极限"""
        return self.client.read_discrete_input(PressRegister.LEFT_ARM_FRONT_LIMIT)
    
    def is_left_arm_back_limit(self) -> bool:
        """检查左槽摆杆后极限"""
        return self.client.read_discrete_input(PressRegister.LEFT_ARM_BACK_LIMIT)
    
    # 右槽压杆
    def right_arm_move_forward(self,status:bool) -> bool:
        """右槽摆杆前移"""
        return self.client.write_coil(PressRegister.RIGHT_ARM_MOVE_FORWARD, status)
    
    def right_arm_move_back(self) -> bool:
        """右槽摆杆后移"""
        return self.client.write_coil(PressRegister.RIGHT_ARM_MOVE_BACK, True)
    
    def right_arm_go_home(self) -> bool:
        """右槽摆杆回原位"""
        return self.client.write_coil(PressRegister.RIGHT_ARM_GO_HOME, True)
    
    def is_right_arm_front_limit(self) -> bool:
        """检查右槽摆杆前极限"""
        return self.client.read_discrete_input(PressRegister.RIGHT_ARM_FRONT_LIMIT)
    
    def is_right_arm_back_limit(self) -> bool:
        """检查右槽摆杆后极限"""
        return self.client.read_discrete_input(PressRegister.RIGHT_ARM_BACK_LIMIT)
    
    # ==================== 反馈寄存器 ====================
    def get_left_press_time_ms(self) -> int:
        """获取左槽压鞋时间 (单位ms, 定时器)"""
        return self.client.read_register(PressRegister.GET_LEFT_PRESS_TIME)
    
    def get_right_press_time_ms(self) -> int:
        """获取右槽压鞋时间 (单位ms)"""
        return self.client.read_register(PressRegister.GET_RIGHT_PRESS_TIME)
    
    def get_left_pressure(self) -> float:
        """获取左槽压力 (单位MPa)"""
        value = self.client.read_register(PressRegister.GET_LEFT_PRESSURE)
        return value / 100.0 if value else 0.0
    
    def get_right_pressure(self) -> float:
        """获取右槽压力 (单位MPa)"""
        value = self.client.read_register(PressRegister.GET_RIGHT_PRESSURE)
        return value / 100.0 if value else 0.0
    
    def is_left_slot_stopped(self) -> bool:
        """检查左槽是否停机 (True=停机, False=工作中)"""
        return self.client.read_coil(PressRegister.LEFT_SLOT_STATUS)
    
    def is_right_slot_stopped(self) -> bool:
        """检查右槽是否停机"""
        return self.client.read_coil(PressRegister.RIGHT_SLOT_STATUS)
    
    def get_left_work_status(self) -> WorkStatus:
        """获取左槽工作状态"""
        status = self.client.read_register(PressRegister.LEFT_SLOT_WORK_STATUS)
        return status
    
    def get_right_work_status(self) -> WorkStatus:
        """获取右槽工作状态"""
        status = self.client.read_register(PressRegister.RIGHT_SLOT_WORK_STATUS)
        return status
    
    def reset_left_slot_count(self) -> bool:
        """重置左槽计数"""
        self.client.write_coil(PressRegister.RESET_LEFT_SLOT_COUNT, True)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.RESET_LEFT_SLOT_COUNT, False)
        return True

    
    def reset_right_slot_count(self) -> bool:
        """重置右槽计数"""
        self.client.write_coil(PressRegister.RESET_RIGHT_SLOT_COUNT, True)
        time.sleep(0.004)
        self.client.write_coil(PressRegister.RESET_RIGHT_SLOT_COUNT, False)
        return True
    
    def get_left_slot_count(self) -> int:
        """获取左槽计数"""
        return self.client.read_register(PressRegister.LEFT_SLOT_COUNT)
    
    def get_right_slot_count(self) -> int:
        """获取右槽计数"""
        return self.client.read_register(PressRegister.RIGHT_SLOT_COUNT)
    
    # ==================== 自动压鞋流程测试 ====================
    def auto_press_cycle(self, side: str = "left") -> bool:
        """
        自动压鞋循环
        side: "left" 或 "right"
        """
        if side == "left":
            self.log("=== 开始左槽自动压鞋循环 ===")
            
            self.set_left_slot_shoe_placed()  # 设置已放鞋
            self.log("左槽已放鞋")
            time.sleep(0.006)

            while not self.get_left_press_rod_aligned():  # 设置压杆对齐
                time.sleep(0.006)
            self.log("左槽压杆已对齐")
            self.set_left_move_distance(20)  # 示例：移动50mm

            self.start_left_motor()  # 启动电机
            time.sleep(0.2)
            self.log("左槽电机启动")
            motor_move_done=self.get_left_motor_done()  # 获取电机完成状态
            if not motor_move_done:
                while not self.get_left_motor_done():
                    time.sleep(0.005)
            self.log("左槽电机完成")

            self.start_left_slot_up()  # 左槽上升
            
            self.log("左槽上升")
            time.sleep(0.006)
            ret=self.get_left_work_status()
            while ret :
                ret=self.get_left_work_status()
                print(f"当前工作状态: {ret}")
                time.sleep(0.006)
                if ret==0:
                    break
            print(f"压鞋完成{ret}")
            return True
            
        elif side == "right":
            self.log("=== 开始右槽自动压鞋循环 ===")           
            self.set_right_slot_shoe_placed()  # 设置已放鞋
            self.log("右槽已放鞋")
            time.sleep(1)

            while not self.get_right_press_rod_aligned():  # 设置压杆对齐 
                time.sleep(0.005)
            self.log("右槽压杆已对齐")
            time.sleep(0.006)
            self.set_right_move_distance(20)  # 示例：移动50mm
            time.sleep(0.006)
            self.start_right_motor()  # 启动电机
            self.log("右槽电机启动")
            motor_move_done=self.get_right_motor_done()  # 获取电机完成状态
            if not motor_move_done:
                while not self.get_right_motor_done():
                    time.sleep(0.005)
            self.log("右槽电机完成")
            
            self.start_right_slot_up()  # 右槽上升
            self.log("右槽上升")

            ret=self.get_right_work_status()
            last_ret = ret
            ret_start_time = time.time()
            while ret :
                ret=self.get_right_work_status()
                now = time.time()
                if ret != last_ret:
                    print(f"工作状态 {last_ret} 持续: {now - ret_start_time:.3f}s")
                    last_ret = ret
                    ret_start_time = now
                print(f"当前工作状态: {ret}")
                time.sleep(0.005)
                if ret==0:
                    print(f"工作状态 {last_ret} 持续: {time.time() - ret_start_time:.3f}s")
                    break
            print(f"压鞋完成{ret}")

            return True
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
    
    # ==================== 手动控制流程 ====================
    def manual_swing_arm_cycle(self, side: str = "left", direction: str = "in") -> bool:
        """手动摆杆控制"""
        if side == "left":
            if direction == "in":
                self.left_swing_arm_in()
                self.log("左槽摆杆进")
            else:
                self.left_swing_arm_out()
                self.log("左槽摆杆出")
        elif side == "right":
            if direction == "in":
                self.right_swing_arm_in()
                self.log("右槽摆杆进")
            else:
                self.right_swing_arm_out()
                self.log("右槽摆杆出")
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
        return True
    
    def manual_clamp_cycle(self, side: str = "left", action: str = "in") -> bool:
        """手动束紧控制"""
        if side == "left":
            if action == "in":
                self.left_clamp_in()
                self.log("左槽前后束紧")
            else:
                self.left_clamp_out()
                self.log("左槽前后束松")
        elif side == "right":
            if action == "in":
                self.right_clamp_in()
                self.log("右槽前后束紧")
            else:
                self.right_clamp_out()
                self.log("右槽前后束松")
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
        return True
    
    def manual_press_cycle(self, side: str = "left", action: str = "up") -> bool:
        """手动压鞋控制"""
        if side == "left":
            if action == "up":
                self.left_press_up()
                self.log("左槽压鞋上升")
            else:
                self.left_press_down()
                self.log("左槽压鞋下降")
        elif side == "right":
            if action == "up":
                self.right_press_up()
                self.log("右槽压鞋上升")
            else:
                self.right_press_down()
                self.log("右槽压鞋下降")
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
        return True
    
    def manual_side_press_cycle(self, side: str = "left", action: str = "press") -> bool:
        """手动压边控制"""
        if side == "left":
            if action == "press":
                self.left_side_press()
                self.log("左槽压边缩进")
            else:
                self.left_side_press_release()
                self.log("左槽压边退")
        elif side == "right":
            if action == "press":
                self.right_side_press()
                self.log("右槽压边缩进")
            else:
                self.right_side_press_release()
                self.log("右槽压边退")
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
        return True
    
    def manual_arm_move_cycle(self, side: str = "left", direction: str = "forward", status:bool="False") -> bool:
        """手动压杆移动控制"""
        if side == "left":
            if direction == "forward":
                self.left_arm_move_forward(status)
                self.log("左槽摆杆前移")
            elif direction == "back":
                self.left_arm_move_back()
                self.log("左槽摆杆后移")
            elif direction == "home":
                self.left_arm_go_home()
                self.log("左槽摆杆回原位")
            else:
                self.log(f"无效的方向: {direction}", "ERROR")
                return False
        elif side == "right":
            if direction == "forward":
                self.right_arm_move_forward(status)
                self.log("右槽摆杆前移")
            elif direction == "back":
                self.right_arm_move_back()
                self.log("右槽摆杆后移")
            elif direction == "home":
                self.right_arm_go_home()
                self.log("右槽摆杆回原位")
            else:
                self.log(f"无效的方向: {direction}", "ERROR")
                return False
        else:
            self.log(f"无效的槽位: {side}", "ERROR")
            return False
        return True
    
    def 手动流程(self, side: str = "left"):
            # 摆杆进
        self.manual_swing_arm_cycle("left", "in")
        time.sleep(1)
        # 压杆移动(也是自己设置吗)
        # press.set_left_move_distance(10.0)  # 示例：移动10mm
        # self.manual_arm_move_cycle("left", "forward",True)
        # time.sleep(1)
        # self.manual_arm_move_cycle("left", "forward",False)
        # 鞋槽上升
        self.manual_press_cycle("left","up")
        time.sleep(2)
        # 束紧
        self.manual_clamp_cycle("left", "in")
        time.sleep(2)
        # 压边
        self.manual_side_press_cycle("left", "press")
        time.sleep(2)
        #二次压
        self.left_second_press()
        time.sleep(2)
        #压边退
        self.manual_side_press_cycle("left", "release")
        time.sleep(2)
        #束紧退
        self.manual_clamp_cycle("left", "out")
        time.sleep(2)
        #下降
        self.manual_press_cycle("left","down")
        time.sleep(2)
        # 压杆出？
        #摆杆出
        self.manual_swing_arm_cycle("left", "out")
        time.sleep(2)



# ==================== 主程序 ====================
def main():
    """主程序示例"""
    
    # 1. 连接PLC (Modbus TCP)
    try:
        press = ShoePressController(
            host='192.168.1.100',  # PLC的IP地址，根据实际修改
            port=502,              # Modbus TCP端口
            unit_id=1,             # 单元标识符
            timeout=3.0
        )
    except Exception as e:
        print(f"创建控制器失败: {e}")
        return
    
    # 2. 连接PLC
    if not press.connect():
        print("连接PLC失败")
        return
    
    # 3. 测试通信
    try:
        # M线圈测试
        press.set_remote_control_mode(True)  # 设置上位机控制模式
        mode = press.is_remote_control_mode()        
        print(f"上位机控制模式: {0}")

        
        # press.set_motor_speed(30)  # 设置电机速度为50%
    
        # press.set_left_press_time(100)  # 设置左槽压鞋时间为60秒
        # time.sleep(0.003)
        # print(f"左槽压鞋时间: {press.get_left_press_time()}秒")
        # time.sleep(0.003)
        # press.set_right_press_time(60)  # 设置右槽压鞋时间为30秒
        # time.sleep(0.003)
        # print(f"右槽压鞋时间: {press.get_right_press_time()}秒")   
        time.sleep(0.003)

        # print(f"左槽计数: {press.get_left_slot_count()}")
        # time.sleep(0.003)
        # print(f"右槽计数: {press.get_right_slot_count()}")
        # time.sleep(0.003)
        # press.reset_left_slot_count()
        # time.sleep(0.003)
        # press.reset_right_slot_count()
        # time.sleep(0.003)
        # print(f"左槽计数: {press.get_left_slot_count()}")
        # time.sleep(0.003)
        # print(f"右槽计数: {press.get_right_slot_count()}")
        # time.sleep(0.003)

        # press.set_left_slot_shoe_placed()  # 设置左槽已放鞋
        # time.sleep(3)
        # press.left_slot_reset()
        
        # press.set_right_slot_shoe_placed()  # 设置右槽已放鞋
        # time.sleep(3)
        # press.right_slot_reset()

        press.auto_press_cycle("right")  # 测试左槽自动压鞋流程
        # press.auto_press_cycle("right")  # 测试左槽自动压鞋流程
        #D寄存器测试
        # int型
        # press.client.write_register(0, 100)
        # value = press.client.read_register(0)
        # print(f"寄存器0的值: {value}")
        # float型
        # press.client.write_float(0, 150.0)
        # Value = press.client.read_float(0)
        # print(f"寄存器0的浮点值: {Value:.2f}")
        #DINT型
        # press.client.write_registers(0, [0x5678, 0x1234])  # 写入DINT值0x12345678
        # value = press.clientmanual_side_press_cycle.read_registers(0, 2)
        # dint_value = (value[1] << 16) | value[0]
        # print(f"寄存器0-1的DINT值: 0x{dint_value:08X}")
        #R寄存器测试

        # press.set_left_move_distance(-150.0)
        # distance = press.get_left_move_distance()
        # distance=press.client.read_float(PressRegister.LEFT_MOVE_DISTANCE)
        # print(f"左槽移动距离: mm")
    except Exception as e:
        print(f"通信测试失败: {e}")

    #自动流程
    # press.left_slot_reset()
    # press.right_slot_reset()
    
    #写一个线程一直打印 status = self.press_machine.get_left_work_status()的结果
    # import threading
    # def print_status():
    #     while True:  
    #         status = press.get_left_work_status()
    #         print(f"左槽工作状态: {status}")
    #         time.sleep(0.5)
    # status_thread = threading.Thread(target=print_status, daemon=True)
    # status_thread.start()
    num = 0
    # def print_log():
    #     while True:
    #         num = 0
    #         print(f"日志消息: {num}")
    #         num += 1
    #         time.sleep(1)
    # s_thread = threading.Thread(target=print_log, daemon=True)
    # s_thread.start()
    # press.auto_press_cycle("left")
    #急停
    # press.client.write_coil(0x190,0)
    # time.sleep(0.004)
    # a=press.is_right_emergency_stop()
    # print(a)
    # # time.sleep(0.003)
    # press.client.write_coil(0x190,0)
    # a=press.is_right_emergency_stop()
    # print(a)


    #shezhishijian
    # press.set_left_press_time(60)
    # time.sleep(0.003)
    # press.set_right_press_time(30)
    # # 
    # value=press.get_left_press_time()
    # print(f"zuoyacaoshijian{value}")
    # value=press.get_right_press_time()
    # print(f"youyacaoshijian{value}")
    # #复位左压槽
    # press.client.write_coil(0x18f,0)
    # #复位右压槽
    # press.client.write_coil(0x190,0)

    #电机运动速度控制  测试没问题
    # press.client.write_register(0x2bc,20)

    #手动流程
    # 摆杆进
    # press.set_hand_control_mode(True)  # 设置手动控制模式
    # time.sleep(0.003)
    # press.manual_swing_arm_cycle("right", "in")
    # time.sleep(1)
    # press.manual_arm_move_cycle("right", "back",True)
    # time.sleep(1)
    # press.manual_arm_move_cycle("right", "forward",False)
    # time.sleep(0.004)
    # 鞋槽上升
    # press.manual_press_cycle("right","up")
    # time.sleep(2)
    # # 束紧
    # press.manual_clamp_cycle("right", "in")
    # time.sleep(2)
    # # 压边
    # press.manual_side_press_cycle("right", "press")
    # time.sleep(2)
    # #二次压
    # press.left_second_press()
    # press.right_second_press(True)
    # time.sleep(2)
    # press.right_second_press(False)
    # time.sleep(2)
    # # # 压边退
    # press.manual_side_press_cycle("right", "release")
    # # time.sleep(2)
    # #束紧退
    # press.manual_clamp_cycle("right", "out")
    # # # time.sleep(2)
    # # # #下降
    # press.manual_press_cycle("right","down")
    # time.sleep(0.004)
    # press.client.write_coil(PressRegister.RIGHT_SIDE_PRESS_RELEASE, False)
    # time.sleep(0.004)
    # press.client.write_coil(PressRegister.RIGHT_CLAMP_OUT, False)
    # time.sleep(0.004)
    # press.client.write_coil(PressRegister.RIGHT_PRESS_DOWN, False)
    # time.sleep(1)
    # # # 压杆出？
    # # #摆杆出
    # press.manual_swing_arm_cycle("right", "out")
    time.sleep(2)

    
    # 5. 断开连接
    press.disconnect()


if __name__ == "__main__":
    main()