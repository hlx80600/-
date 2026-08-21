#!/usr/bin/env python3
"""Simple CLI client for the gripper services exposed by robot_arm_service_node."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import rclpy
from rclpy.node import Node

from press_shoes_interface.srv import RobotGripperCommand


class GripperServiceClient(Node):
    """ROS 2 client that sends Open/Close requests to a gripper service."""

    def __init__(self, service_name: str) -> None:
        super().__init__("gripper_test_client")
        self._client = self.create_client(RobotGripperCommand, service_name)
        while not self._client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(f"Waiting for service '{service_name}' ...")

    def call(self, command: str) -> Optional[RobotGripperCommand.Response]:
        request = RobotGripperCommand.Request()
        request.function_name = command
        future = self._client.call_async(request)
        rclpy.spin_until_future_complete(self, future)
        return future.result()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test client for gripper services")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["Open", "Close"],
        help="Gripper command to send; if omitted you will be prompted",
    )
    parser.add_argument(
        "--service",
        default=None,
        help="Fully qualified service name; if omitted choose 左/右 交互式",
    )
    return parser.parse_args()


def _prompt_side() -> str:
    mapping = {
        "左": "left_gripper_control",
        "右": "right_gripper_control",
        "left": "left_gripper_control",
        "right": "right_gripper_control",
    }
    while True:
        choice = input("请选择夹爪 (左/右 或 left/right): ").strip().lower()
        if choice in mapping:
            return mapping[choice]
        print("输入无效，请输入 左 / 右 / left / right。")


def _prompt_command() -> str:
    mapping = {"open": "Open", "close": "Close"}
    while True:
        choice = input("请选择操作 (Open/Close): ").strip().lower()
        if choice in mapping:
            return mapping[choice]
        print("输入无效，请输入 Open 或 Close。")


def main() -> None:
    args = parse_args()
    service_name = args.service or _prompt_side()
    command = args.command or _prompt_command()
    rclpy.init()
    node = GripperServiceClient(service_name)
    try:
        response = node.call(command)
        if response is None:
            node.get_logger().error("Service call failed (no response)")
            sys.exit(1)
        node.get_logger().info(
            f"Gripper response: success={response.success} ret_code={response.ret_code} message={response.message}"
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
