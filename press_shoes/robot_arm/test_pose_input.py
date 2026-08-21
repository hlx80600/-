#!/usr/bin/env python3
"""
测试 FairinoRobotArm 连接与按键获取位姿
"""
import sys
import time
from fairino_robot_arm_inherit import FairinoRobotArm

if __name__ == "__main__":
    # 创建机械臂实例（可改为实际IP）
    arm = FairinoRobotArm("测试机械臂", robot_ip="fake")
    print("正在连接机械臂...")
    if arm.connect_robot_arm():
        print("机械臂连接成功！")
    else:
        print("机械臂连接失败！")
        sys.exit(1)

    print("请按下回车键获取一次位姿（Ctrl+C退出）...")
    try:
        while True:
            input()
            pose = arm.get_pose()
            print(f"当前位姿: {pose}")
            print("再次按回车获取下一次位姿，Ctrl+C退出...")
    except KeyboardInterrupt:
        print("退出测试。")
