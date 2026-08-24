"""
兼容层：历史代码从本路径 import CANGripperController。

实现已合并至 devices/gripper_can.py，请新代码统一使用：

    from devices.gripper_can import GripperCAN, create_gripper, CANGripperController
"""

from devices.gripper_can import CANGripperController

__all__ = ["CANGripperController"]
