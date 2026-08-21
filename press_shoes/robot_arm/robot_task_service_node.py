#!/usr/bin/env python3
"""ROS 2 services that proxy operations to the left and right Fairino robot arms."""

from __future__ import annotations

import os
import sys
import json
from typing import Any, Dict, Optional

import rclpy
from rclpy.node import Node


# 动态添加utils.py所在目录到sys.path
UTILS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if UTILS_PATH not in sys.path:
    sys.path.insert(0, UTILS_PATH)
from robot_arm.fairino_robot_arm_inherit import FairinoRobotArm
from press_shoes_interface.srv import RobotArmCommand, RobotGripperCommand
from robot_arm.gripper_controller_can import CANGripperController

class RobotArmServiceNode(Node):
    """Expose separate ROS 2 services for the left and right robot arms."""

    def __init__(self) -> None:
        super().__init__("robot_task_server")

        self._arms = self._create_arms()
        self._connect_arms()
        params = self._declare_parameters()
        self._grippers = self._create_grippers(params)
        self._create_services(params)

    def _create_arms(self) -> Dict[str, FairinoRobotArm]:
        return {
            "left": FairinoRobotArm("左机械臂", robot_ip="fake"),
            "right": FairinoRobotArm("右机械臂", robot_ip="fake"),
        }

    def _connect_arms(self) -> None:
        for label, arm in self._arms.items():
            if arm is None:
                self.get_logger().error(f"{label} arm instance unavailable")
                continue
            if arm.ConnectRobotArm():
                self.get_logger().info(f"{label} arm connected (ip={arm.robot_ip})")
            else:
                self.get_logger().warning(f"{label} arm failed to connect (ip={arm.robot_ip})")

    def _declare_parameters(self) -> Dict[str, Any]:
        return {
            "left_service": self.declare_parameter("left_service_name", "left_arm_control").value,
            "right_service": self.declare_parameter("right_service_name", "right_arm_control").value,
            "left_gripper_service": self.declare_parameter(
                "left_gripper_service_name", "left_gripper_control"
            ).value,
            "right_gripper_service": self.declare_parameter(
                "right_gripper_service_name", "right_gripper_control"
            ).value,
            "left_gripper_can_interface": self.declare_parameter(
                "left_gripper_can_interface", "fake"
            ).value,
            "right_gripper_can_interface": self.declare_parameter(
                "right_gripper_can_interface", "fake"
            ).value,
            "left_gripper_can_id": int(self.declare_parameter("left_gripper_can_id", 0x101).value),
            "right_gripper_can_id": int(self.declare_parameter("right_gripper_can_id", 0x102).value),
        }

    def _create_grippers(self, params: Dict[str, Any]) -> Dict[str, Optional[CANGripperController]]:
        return {
            "left": self._init_gripper(
                label="left",
                name="左夹爪",
                interface=params["left_gripper_can_interface"],
                can_id=params["left_gripper_can_id"],
            ),
            "right": self._init_gripper(
                label="right",
                name="右夹爪",
                interface=params["right_gripper_can_interface"],
                can_id=params["right_gripper_can_id"],
            ),
        }

    def _create_services(self, params: Dict[str, Any]) -> None:
        self._left_srv = self.create_service(
            RobotArmCommand,
            params["left_service"],
            lambda req, res: self._handle_request("left", self._arms.get("left"), req, res),
        )
        self._right_srv = self.create_service(
            RobotArmCommand,
            params["right_service"],
            lambda req, res: self._handle_request("right", self._arms.get("right"), req, res),
        )
        self._left_gripper_srv = self.create_service(
            RobotGripperCommand,
            params["left_gripper_service"],
            lambda req, res: self._handle_gripper_request(
                "left", self._grippers.get("left"), req, res
            ),
        )
        self._right_gripper_srv = self.create_service(
            RobotGripperCommand,
            params["right_gripper_service"],
            lambda req, res: self._handle_gripper_request(
                "right", self._grippers.get("right"), req, res
            ),
        )

    def _handle_request(self, label: str, arm: Any, request: RobotArmCommand.Request,
                         response: RobotArmCommand.Response) -> RobotArmCommand.Response:
        if arm is None:
            response.success = False
            response.ret_code = -1
            response.message = f"{label} arm is not initialized"
            response.result_json = ""
            return response

        method_name = (request.method or "").strip()
        if not method_name:
            response.success = False
            response.ret_code = -1
            response.message = "method field cannot be empty"
            response.result_json = ""
            return response

        try:
            method = getattr(arm, method_name)
        except AttributeError:
            response.success = False
            response.ret_code = -1
            response.message = f"{label} arm does not implement {method_name}"
            response.result_json = ""
            return response

        try:
            kwargs = self._parse_kwargs(request.kwargs_json)
        except ValueError as exc:
            response.success = False
            response.ret_code = -1
            response.message = str(exc)
            response.result_json = ""
            return response

        try:
            result = method(**kwargs)
        except Exception as exc:  # noqa: BLE001 - need to surface SDK issues verbatim
            self.get_logger().error(f"{label} arm call {method_name} failed: {exc}")
            response.success = False
            response.ret_code = -1
            response.message = f"{label} arm execution failed: {exc}"
            response.result_json = ""
            return response

        response.success = True
        response.ret_code = self._extract_ret_code(result)
        response.message = request.function_name or f"{label} arm executed {method_name}"
        response.result_json = self._encode_result(result)
        return response

    def _handle_gripper_request(
        self,
        label: str,
        gripper: Optional[CANGripperController],
        request: RobotGripperCommand.Request,
        response: RobotGripperCommand.Response,
    ) -> RobotGripperCommand.Response:
        if gripper is None:
            response.success = False
            response.ret_code = -1
            response.message = f"{label} gripper is not initialized"
            return response

        command = (request.function_name or "").strip()
        if not command:
            response.success = False
            response.ret_code = -1
            response.message = "function_name cannot be empty"
            return response

        if command == "Open":
            handler = gripper.open_claw
        elif command == "Close":
            handler = gripper.close_claw
        else:
            response.success = False
            response.ret_code = -1
            response.message = f"unsupported gripper command: {command}"
            return response

        success = bool(handler())
        response.success = success
        response.ret_code = 0 if success else -1
        if success:
            response.message = request.function_name or f"{label} gripper executed {command}"
        else:
            response.message = f"{label} gripper failed to execute {command}"
        return response

    def _init_gripper(
        self, label: str, name: str, interface: str, can_id: int
    ) -> Optional[CANGripperController]:
        try:
            controller = CANGripperController(name, interface=interface, can_id=can_id)
            self.get_logger().info(
                f"{label} gripper ready (interface={interface}, can_id=0x{can_id:X})"
            )
            return controller
        except Exception as exc:  # noqa: BLE001 - propagate hw setup issues
            self.get_logger().error(f"{label} gripper init failed: {exc}")
            return None

    @staticmethod
    def _parse_kwargs(raw_json: str) -> Dict[str, Any]:
        if not raw_json or not raw_json.strip():
            return {}
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:  # pragma: no cover - informative path
            raise ValueError(f"kwargs_json is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("kwargs_json must describe a JSON object")
        return payload

    @staticmethod
    def _extract_ret_code(result: Any) -> int:
        if isinstance(result, int):
            return result
        if isinstance(result, (tuple, list)) and result:
            head = result[0]
            if isinstance(head, int):
                return head
        return 0

    @staticmethod
    def _encode_result(payload: Any) -> str:
        try:
            return json.dumps(payload, default=RobotArmServiceNode._fallback_serializer)
        except (TypeError, ValueError):
            return json.dumps(str(payload))

    @staticmethod
    def _fallback_serializer(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if hasattr(value, "__dict__"):
            return value.__dict__
        return str(value)


def main(args: Any = None) -> None:
    rclpy.init(args=args)
    node = RobotArmServiceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
