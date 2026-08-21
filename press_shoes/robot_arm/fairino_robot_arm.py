import numpy as np
from .fairino390.linux.fairino import Robot
import time

class FairinoRobotArm:
    def __init__(self, name, robot_ip, node = None):
        self.logger = None
        self.robot_handle = None
        self.robot_ip = robot_ip
        self.name = name
        
        # if node:
        #     # self.logger = Logger(node, name)
        #     self.logger = get_logger(name)
        # else:
        #     self.logger = get_logger(name)

    def __del__(self):
        if self.robot_handle is not None:
            self.robot_handle.CloseRPC()

    def connect_robot_arm(self):
        if self.robot_ip == "fake":
            return True
        print(f"机械臂ip为：{self.robot_ip}")
        self.robot_handle = Robot.RPC(self.robot_ip)
        if self.robot_handle.is_conect == False:
            self.robot_handle.reconnect()

        while True:
            try:
                ret  = self.robot_handle.GetSDKVersion()
                if isinstance(ret, int):
                    print(f"获取机械臂版本号失败。返回值{ret}")
                    time.sleep(1)
                    self.robot_handle = Robot.RPC(self.robot_ip)
                    return False
                else:
                    print(f"机械臂版本号为：{ret[1]}")
                    break
            except Exception as e:
                print(f"获取机械臂版本号异常：{e}")
        print(f"机械臂连接成功")


        return True
    
    def set_mode(self, mode):
        if self.robot_ip == "fake":
            return True            
        else:
            return self.robot_handle.Mode(mode)
    
    def move_cart(self, desc_pos,tool, speed = 20.0, ovl = 100.0, blendT = -1.0):
        if self.robot_ip == "fake":
            return 0
        return self.robot_handle.MoveCart(desc_pos, tool, 0, speed, 0.0, 100.0, blendT, -1)
    
    def move_robot_l(self,pose6:list, speed = 100):
        if self.robot_ip == "fake":
            return True
        tool = 0 #工具坐标系编号
        user = 0 #工件坐标系编号
        ret = self.robot_handle.MoveL(desc_pos = pose6, tool = tool, user = user, vel = speed, acc = 300)   #笛卡尔空间直线运动
        if ret == 0:
            print(f"执行机器人直线运动指令，返回值：{ret}")
            return True
        else:   
            print(f"执行机器人直线运动指令失败，返回值：{ret}")
            return False

    def get_robot_pose(self, print_content = False):
        if self.robot_ip == "fake":
            return True, np.zeros((4, 4)), np.zeros(6)   

        ret = self.robot_handle.GetActualTCPPose()
        result = ret[0]  # ret is a tuple, the first element is the result
        pose = ret[1]
        robot_pose = xyzrxryrz_to_pose(np.array([pose[:3]]).T, pose [3]/180 * 3.1415,pose[4]/180 * 3.1415,pose[5]/180 * 3.1415)
        if result == 0:
            if print_content:
                print(f"获取机器人当前工具位姿成功，返回值：{result}")
            return True, robot_pose, pose
        else:
            print(f"获取机器人当前工具位姿失败，返回值：{result}")
            return False, None, None
        
    def get_robot_joint_deg(self, print_content = False):
        if self.robot_ip == "fake":
            return False, np.zeros(6)   
        ret = self.robot_handle.GetActualJointPosDegree(flag=1)
        result = ret[0]  # ret is a tuple, the first element is the result
        joint_deg = ret[1]
        if result == 0:
            if print_content:
                print(f"获取机器人当前关节位姿成功，返回值：{joint_deg}")
            return True, joint_deg
        else:
            print(f"获取机器人当前关节位姿失败，返回值：{result}")
            return False, None

    def move_joint(self, joint6:list, speed = 10, desc_pos = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]):
        """
        joint6: 标关节位置，单位 [°]
        desc_pos: 目标关节对应的位姿, 可不传，如果传了运动执行的会更快
        """
        if self.robot_ip == "fake":
            return 0
        return self.robot_handle.MoveJ(joint6, 10, 10, desc_pos, vel = speed)
    
    def get_actual_tcp_pose(self):
        if self.robot_ip == "fake":
            return 0, np.zeros(6)
        return self.robot_handle.GetActualTCPPose()

    def servo_move_start(self):
        if self.robot_ip == "fake":
            return 0
        return self.robot_handle.ServoMoveStart()
    
    def servo_move_end(self):
        if self.robot_ip == "fake":
            return 0
        return self.robot_handle.ServoMoveEnd()
    
    def servo_cart(self, mode, desc_pos, pos_gain=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0], acc = 0.0, vel = 0.0, cmdT = 0.08,
                  filterT=0.0, gain=0.0, exaxis=None):
        if self.robot_ip == "fake":
            return 0
        if exaxis is None:
            exaxis = [0.0] * 4
        try:
            return self.robot_handle.ServoCart(mode, desc_pos, exaxis, pos_gain, acc, vel, cmdT, filterT, gain)
        except TypeError as exc:
            if "exaxis" not in str(exc):
                raise
            return self.robot_handle.ServoCart(mode, desc_pos, pos_gain, acc, vel, cmdT, filterT, gain)

    def set_speed(self, speed):
        if self.robot_ip == "fake":
            return 0
        return self.robot_handle.SetSpeed(speed)
     
    def trajectory_replay(self, name, speed = 100.0, block = True):
        if self.robot_ip == "fake":
            return 0
        error, start_pose = self.robot_handle.GetTPDStartPose(name)
        if error != 0:
            print(f"获取轨迹起始点失败，返回值：{error}")
            return error
        self.robot_handle.MoveCart(start_pose, 0, 0, vel = speed)
        error = self.robot_handle.MoveTPD(name, 0, ovl = speed)
        if error != 0:
            print(f"轨迹{name}复现失败，返回值：{error}")
            return error
        start_f = False
        while block:
            ret = self.robot_handle.GetTargetTCPSpeed()
            tcp_speed = ret[1]
            time.sleep(0.01)
            if not start_f and (tcp_speed[0] > 0.1 or tcp_speed[1] > 0.1 or tcp_speed[2] > 0.1 or tcp_speed[3] > 0.1 or tcp_speed[4] > 0.1 or tcp_speed[5] > 0.1): 
                start_f = True
            if start_f and tcp_speed[0] < 0.001 and tcp_speed[1] < 0.001 and tcp_speed[2] < 0.001 and \
               tcp_speed[3] < 0.001 and tcp_speed[4] < 0.001 and tcp_speed[5] < 0.001:
                print(f"轨迹{name}复现完成")
                return 0
        return 0

    def trajectories_replay(self, names, speed = 100.0, block = True):
        
        if self.robot_ip == "fake":
            return 0
        names = list(names)
        len = len(names)
        for i in range(len):
            name = names[i]
            if i < len - 1:
                self.trajectories_replay(name, speed, block = True)
            else:
                self.trajectories_replay(name, speed, block = False)
        return 0          

    def __position_almost_equal(self, pose1, pose2, tolerance = 0.1):

        if len(pose1) != len(pose2):
            return False
        for a, b in zip(pose1, pose2):
            if abs(a - b) >= tolerance:
                return False

        return True
    
    def cart_points_to_joint_points(self, points, reverse_rpy_axes: bool = False, config: int = -1, unwrap_joint6: bool = True):
        """将笛卡尔点位列表转换为(笛卡尔点, 关节点)两套序列。

        - points: 来自文件的点位，要求每个点至少 6 个元素 [x,y,z,rx,ry,rz]
        - reverse_rpy_axes: 保留你原来的姿态 3 轴反转逻辑
        - config: 逆解 config 参数，保持与你原来一致（默认 -1）
        - unwrap_joint6: 对第 6 轴做连续化，避免跨越 -180/180 造成突跳
        """
        robot = self.robot_handle

        cart_points = []
        joint_points = []

        joint6_offset = 0.0
        last_joint6 = None

        for i, p in enumerate(points):
            try:
                p = list(p)
            except TypeError:
                print(f"点位 {i+1} 无法转换为列表: {p}")
                return None, None

            if len(p) < 6:
                print(f"点位 {i+1} 长度不足 6: {p}")
                return None, None
            if reverse_rpy_axes:
                p[3:6] = list(reversed(p[3:6]))
            error, joint_pos = robot.GetInverseKin(0, desc_pos=p, config=config)
            if error != 0:
                print(f"点 {p} 求逆失败: {error}")
                return None, None
            if unwrap_joint6 and joint_pos and len(joint_pos) >= 6:
                for axis in range(len(joint_pos)):
                    raw = float(joint_pos[axis])
                    if len(joint_points) == 0:
                        unwrapped = raw
                    else:
                        last = float(joint_points[-1][axis])
                        k = round((last - raw) / 360.0)
                        unwrapped = raw + 360.0 * k
                    joint_pos[axis] = unwrapped

            cart_points.append((float(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
            joint_points.append(joint_pos)

        return cart_points, joint_points

    def splind_move(self, points, points_j=None, spline_type: int = 0, ovl: float = 50.0, tool: int = 0, user: int = 0):
        """将 points 按新样条运动一次性执行。

        - points 会做简单预处理（保持你原先的姿态 3 轴反转逻辑）
        - spline_type: 样条模式（沿用 NewSplineStart type 参数）
        - ovl: 默认 overlap/速度百分比，可按需覆盖
        """
        start_time = time.time()
        robot = self.robot_handle
        if not robot:
            print('没有robot')
            return
        average_time = 8000
        blendR = 0.0
        if not points_j:
            spline_points, spline_points_j = self.cart_points_to_joint_points(points)
        else:
            spline_points = points
            spline_points_j = points_j
        print(f"已加载 {len(spline_points)} 个点位，开始样条运动。")
        start_index = 0
        ret = robot.NewSplineStart(type=spline_type, averageTime=int(average_time))
        if ret != 0:
            print(f"样条运动开始失败: {ret}")
            return
        for idx, pj in enumerate(spline_points_j):
            if idx < start_index:
                continue
            lastFlag = 1 if idx == (len(spline_points_j) - 1) else 0
            print(f"加载点 {idx+1}/{len(spline_points_j)}: cart={list(spline_points[idx])}")
            ret = robot.NewSplinePoint(
                desc_pos=list(spline_points[idx]),
                tool=tool,
                user=user,
                lastFlag=lastFlag,
                joint_pos=pj,
                ovl=ovl,
                blendR=blendR,
            )
            if ret != 0:
                print(f"样条运动到点 {idx+1} 失败: {ret},目标点关节{pj}")
                robot.NewSplineEnd()
                return
        robot.NewSplineEnd()
        print(f"新样条运动执行完成，总时间: {time.time() - start_time:.2f} 秒")

def test_fairino():
    robot_arm=FairinoRobotArm(name = "左机械臂",robot_ip = "192.168.57.2")
    robot_arm.connect_robot_arm()
    robot_arm.set_mode(1)
    pose = robot_arm.get_robot_pose(True)[2]
    print(f'机器臂当前位姿{pose}')
    pose0 = [369.37249755859375, -75.95606994628906, 779.5447387695312, -167.82215881347656, 2.6530873775482178, -117.50272369384766]
    pose1 = [246.4090576171875, -273.1206970214844, 711.7450561523438, 179.6012420654297, -0.3244430720806122, -153.99081420898438]
    pose2 = [426.4111633300781, -324.6279602050781, 617.8095092773438, -178.5193634033203, -0.25793197751045227, -138.25341796875]
    pose3 = [403.88165283203125, -347.9776306152344, 671.6708374023438, -164.23020935058594, -0.26764288544654846, -141.84266662597656]
    pose4 = [613.8031005859375, -38.962425231933594, 496.2564697265625, 176.1728057861328, 2.9325411319732666, -103.90249633789062]
    start_time = time.time()
    # robot_arm.move_robot(pose1)
    # robot_arm.move_robot(pose2)
    # robot_arm.move_robot(pose3)
    # robot_arm.move_robot(pose4)
    # robot_arm.move_robot(pose0)
    # end_time = time.time()
    # print(f'直线运动时间{end_time - start_time:.3f}秒')
    # time.sleep(3)
    # start_time = time.time()
    # robot_arm.robot_handle.MoveCart(pose0, 0, 0, 100, blendT = -1)
    # robot_arm.robot_handle.MoveCart(pose1, 0, 0, 100, blendT = -1)
    # robot_arm.robot_handle.MoveCart(pose2, 0, 0, 100, blendT = -1)
    # robot_arm.robot_handle.MoveCart(pose3, 0, 0, 100, blendT = -1)
    # robot_arm.robot_handle.MoveCart(pose4, 0, 0, 100, blendT = -1)
    # robot_arm.robot_handle.MoveCart(pose0, 0, 0, 100, blendT = -1)
    # end_time = time.time()
    # print(f'cart运动时间{end_time - start_time:.3f}秒')

    time.sleep(1)
    # robot_arm.servo_move_start()
    # count = 0
    # while True:
    #     if count > 100:
    #         break
    #     pose[2] -= 3
    #     time.sleep(0.003)
    #     ret = robot_arm.servo_cart(0, pose)
    #     # ret = robot_arm.move_robot(pose0)
    #     print(f'伺服运动返回结果{ret}')
    #     count += 1
    
        
    # robot_arm.servo_move_end()

if __name__ == "__main__":
    test_fairino()