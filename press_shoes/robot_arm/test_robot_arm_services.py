#!/usr/bin/env python3
"""Interactive CLI client for the press-shoes robot arm services."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import rclpy
from rclpy.node import Node

from press_shoes_interface.srv import RobotArmCommand


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def _normalize_kwargs(raw: str) -> str:
    if not raw:
        return "{}"
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - user feedback path
        raise ValueError(f"kwargs JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("kwargs 必须是 JSON 对象，例如: '{\"speed\": 10}'")
    return json.dumps(payload)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Test press-shoes robot arm services")
    parser.add_argument("arm", choices=["left", "right"], nargs="?", default="left",
                        help="选择要调用的机械臂服务，默认 left")
    parser.add_argument("--service", dest="service_name",
                        help="覆盖默认的 ROS Service 名称")
    parser.add_argument("--method", help="直接传入方法名，跳过输入提示")
    parser.add_argument("--kwargs", help="直接传入 kwargs 的 JSON 字符串")
    parser.add_argument("--function-name", dest="function_name",
                        help="覆盖 request.function_name 字段，默认与 method 相同")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="等待服务可用的秒数，默认 3s")
    args = parser.parse_args(argv)

    rclpy.init()
    node = Node("robot_arm_service_tester")

    default_service = "left_arm_control" if args.arm == "left" else "right_arm_control"
    service_name = args.service_name or default_service
    client = node.create_client(RobotArmCommand, service_name)

    node.get_logger().info(f"等待服务 {service_name} ...")
    if not client.wait_for_service(timeout_sec=args.timeout):
        node.get_logger().error(f"服务 {service_name} 在 {args.timeout}s 内不可用")
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    method = args.method or _prompt("输入要调用的函数名")
    if not method:
        node.get_logger().error("函数名不能为空")
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    raw_kwargs = args.kwargs or _prompt("输入 kwargs JSON (可留空表示 {} )", "")
    try:
        kwargs_json = _normalize_kwargs(raw_kwargs)
    except ValueError as exc:
        node.get_logger().error(str(exc))
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    request = RobotArmCommand.Request()
    request.method = method
    request.function_name = args.function_name or method
    request.kwargs_json = kwargs_json

    node.get_logger().info(
        f"调用 {service_name}.method={request.method}, kwargs={request.kwargs_json}")
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future)

    if future.result() is None:
        node.get_logger().error(f"调用失败: {future.exception()}")
    else:
        response = future.result()
        print("\n=== 服务返回 ===")
        print(json.dumps({
            "success": response.success,
            "ret_code": response.ret_code,
            "message": response.message,
            "result_json": response.result_json,
        }, ensure_ascii=False, indent=2))

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
