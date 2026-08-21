from fairino_robot_arm_inherit import FairinoRobotArm
import time
import sys


def get_pose_once(arm: FairinoRobotArm) -> None:
    """获取机械臂一次位姿并打印。"""
    pose = arm.GetActualTCPPose()
    print(f"当前位姿: {pose[1]}")
    joint_angles = arm.GetActualJointPosDegree()
    print(f"当前关节角度: {joint_angles[1]}")

def move_j(arm: FairinoRobotArm) -> None:
    """移动机械臂到目标位姿和关节角度。"""
    pose = [-384.42919921875, 82.32695770263672, 508.88604736328125, -179.38645935058594, 0.2754369080066681, 23.555879592895508]
    j = [-27.07830238342285, -105.56390380859375, 97.0751953125, -82.16033172607422, -90.17621612548828, 39.36629867553711]
    ret = arm.MoveJ(joint_pos=j, desc_pos=pose, tool=0, user= 0, vel=50, ovl=100.0, blendT=-1.0)
    if ret != 0:
        print(f"移动失败，返回值: {ret}")
    else:
        print("移动成功！")

def move(arm: FairinoRobotArm) -> None:
    """移动机械臂到目标位姿和关节角度。"""
    left_slot_up_pose = [-345.1474609375, 477.2402648925781, 410.620361328125, 177.09654235839844, 1.2304203510284424, 1.1403778791427612]
    ret = arm.MoveCart(desc_pos=left_slot_up_pose, tool=0, user= 0, vel=50, ovl=100.0, blendT=-1.0)
    if ret != 0:
        print(f"移动失败，返回值: {ret}")
    else:
        print("移动成功！")

def get_pose_loop(arm: FairinoRobotArm) -> None:
    """循环获取机械臂位姿并打印，按 Ctrl+C 退出。"""
    print("请按下回车键获取一次位姿（Ctrl+C退出）...")
    try:
        while True:
            input()
            pose = arm.get_pose()
            print(f"当前位姿: {pose}")
            print("再次按回车获取下一次位姿，Ctrl+C退出...")
    except KeyboardInterrupt:
        print("\n已退出获取位姿循环。")

def move_left_l(arm: FairinoRobotArm) -> None:
    """使用 MoveL 接口移动机械臂到目标位姿。"""
    target_pose = [42.67698287963867, -879.8802490234375, 189.9781036376953, 179.10504150390625, -1.0565379858016968, -129.43417358398438]
    target_pose[0] += 1000*0.043093233184926
    target_pose[1] += 1000* 0.04777597401412687
    ret = arm.MoveL(desc_pos=target_pose, tool=0, user=0, vel=50, ovl=100.0)
    if ret != 0:
        print(f"MoveL 移动失败，返回值: {ret}")
    else:
        print("MoveL 移动成功！")


if __name__ == "__main__":
    # 创建机械臂实例（可改为实际IP）
    arm = FairinoRobotArm("测试机械臂", robot_ip="192.168.57.3")
    print("正在连接机械臂...")
    if arm.ConnectRobotArm():
        print("机械臂连接成功！")
    else:
        print("机械臂连接失败！")
        sys.exit(1)
    move_left_l(arm)
    # get_pose_once(arm)
    