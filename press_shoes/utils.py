import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path so automation_machine is importable when running locally.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import logging
import yaml
from hardware_module.fairino.fairino_robot_arm_inherit import FairinoRobotArm
from .robot_arm.gripper_controller_can import CANGripperController
import os
from automation_machine import automationMachine

log: Any = logging.getLogger("PressShoesLogger")
_initialized = False
put_arm_type = "left"
take_arm_type = "left"

_VALID_ARM_TYPES = frozenset({"left", "right"})


def _validate_arm_types(put_type: str, take_type: str) -> None:
    """放鞋臂与取鞋臂必须分别为 left / right，且不能同侧。"""
    if put_type not in _VALID_ARM_TYPES:
        raise ValueError(f"put_arm_type 无效: {put_type!r}，必须为 left 或 right")
    if take_type not in _VALID_ARM_TYPES:
        raise ValueError(f"take_arm_type 无效: {take_type!r}，必须为 left 或 right")
    if put_type == take_type:
        raise ValueError(
            f"放鞋臂与取鞋臂必须在左右两侧，"
            f"当前 put_arm_type={put_type!r}, take_arm_type={take_type!r}"
        )


def _load_arm_ip(config_filename: str, default_ip: str = "fake") -> str:
    """Load the arm_ip field from a YAML config file.""" 
    config_dir = os.path.join(os.path.dirname(__file__), "config")
    config_path = os.path.join(config_dir, config_filename)
    try:
        with open(config_path, "r", encoding="utf-8") as cfg:
            data = yaml.safe_load(cfg) or {}
            return data.get("arm_ip", default_ip)
    except FileNotFoundError:
        log.warning(f"配置文件{config_path}不存在，使用默认IP {default_ip}")
    except yaml.YAMLError as exc:
        log.error(f"解析配置文件{config_path}失败: {exc}")
    return default_ip

# def create_robot_arms():
#     """Instantiate both robot arms using the configured IPs."""
#     put_arm_type = _load_arm_type("put_arm_config.yaml")
#     take_arm_type = _load_arm_type("take_arm_config.yaml")
#     put_arm_ip = _load_arm_ip("put_arm_config.yaml")
#     take_arm_ip = _load_arm_ip("take_arm_config.yaml")
#     return (
#         FairinoRobotArm("左机械臂", robot_ip = put_arm_ip, log=log),
#         FairinoRobotArm("右机械臂", robot_ip = take_arm_ip, log=log),
#     )

def get_robot_arms(machine: automationMachine):
    """通过 automationMachine 激活并获取放鞋/取鞋机械臂。"""
    put_type = _load_arm_type("put_arm_config.yaml")
    take_type = _load_arm_type("take_arm_config.yaml")
    _validate_arm_types(put_type, take_type)
    put_arm_ip = _load_arm_ip("put_arm_config.yaml")
    take_arm_ip = _load_arm_ip("take_arm_config.yaml")
    put_label = "左机械臂" if put_type == "left" else "右机械臂"
    take_label = "左机械臂" if take_type == "left" else "右机械臂"
    machine.hardwareModule.activate_fairino_arm("put_arm", put_label, put_arm_ip)
    machine.hardwareModule.activate_fairino_arm("take_arm", take_label, take_arm_ip)
    put_arm = machine.hardwareModule.get_fairino_robot_arm("put_arm")
    take_arm = machine.hardwareModule.get_fairino_robot_arm("take_arm")
    if put_arm is None or take_arm is None:
        raise RuntimeError("机械臂激活失败")
    return put_arm, take_arm

def create_grippers():
    """Instantiate left and right CAN grippers."""
    return (
        CANGripperController("左夹爪", interface="can0", can_id=0x103, gripper_type=2),
        CANGripperController("右夹爪", interface="can1", can_id=0x101, gripper_type=2),
    )

def _load_arm_type(config_filename: str, default_type: str = "left") -> str:
    """Load the arm_type field from a YAML config file."""
    config_dir = os.path.join(os.path.dirname(__file__), "config")
    config_path = os.path.join(config_dir, config_filename)
    try:
        with open(config_path, "r", encoding="utf-8") as cfg:
            data = yaml.safe_load(cfg) or {}
            return data.get("arm_type", default_type)
    except FileNotFoundError:
        log.warning(f"配置文件{config_path}不存在，使用默认arm_type {default_type}")
    except yaml.YAMLError as exc:
        log.error(f"解析配置文件{config_path}失败: {exc}")
    return default_type

def _init_runtime() -> None:
    global put_arm_type, take_arm_type, _initialized
    if _initialized:
        return
    put_arm_type = _load_arm_type("put_arm_config.yaml")
    take_arm_type = _load_arm_type("take_arm_config.yaml")
    _validate_arm_types(put_arm_type, take_arm_type)
    log.info(f"加载到的put_arm_type: {put_arm_type}, take_arm_type: {take_arm_type}")
    _initialized = True


_init_runtime()