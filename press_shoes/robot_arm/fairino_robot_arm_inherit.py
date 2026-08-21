
import sys
import os
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if CUR_DIR not in sys.path:
    sys.path.insert(0, CUR_DIR)
import time
from fairino393.linux.fairino import Robot
try:
   from servo_cart_sdk import ServoCartConfig, plan_from_points, run_servo_spline
except ImportError as e:
   print(f"无法导入 servo_cart_sdk: {e}")

class FairinoRobotArm(Robot.RPC):
    """Thin wrapper around Robot.RPC that only customizes lifecycle helpers."""

    def __init__(self, name, robot_ip, log=None):
        self.logger = None
        self.robot_ip = robot_ip
        self.name = name
        self.log = log
        if robot_ip != "fake":
            super().__init__(robot_ip)
            if not getattr(self, "is_conect", False):
                self.reconnect()

    def _print(self, msg: str):
        """优先使用全局 log 记录信息，若不可用则退回到 print。"""
        try:
            logger = getattr(self, "log", None)
        except Exception:
            logger = None
        if logger is not None:
            try:
                logger.info(msg)
                return
            except Exception:
                # 如果日志对象异常，退回到标准输出
                pass
        print(msg)
 
    def __getattribute__(self, name):
        # Only intercept parent class methods if robot_ip == 'fake'
        try:
            robot_ip = object.__getattribute__(self, 'robot_ip')
        except Exception:
            # robot_ip还未初始化时，直接走默认
            return object.__getattribute__(self, name)
        if robot_ip == 'fake':
            allow = {'robot_ip', 'name', 'node', 'logger', '__class__', '__dict__', '__init__', '__del__', '__getattribute__', '__setattr__', '__str__', '__repr__'}
            if name in allow or name.startswith('__'):
                return object.__getattribute__(self, name)
            parent = super(FairinoRobotArm, self)
            if hasattr(parent, name):
                return lambda *args, **kwargs: 0
        return object.__getattribute__(self, name)

    def __del__(self):
        try:
            self.CloseRPC()
        except AttributeError:
            pass
    
    def ConnectRobotArm(self):
        if self.robot_ip == "fake":
            self._print(f"{self.name}使用模拟连接（IP={self.robot_ip}）")
            return True
        self._print(f"机械臂ip为：{self.robot_ip}")
        if not getattr(self, "is_conect", False):
            self.reconnect()
        max_retry = 5
        for attempt in range(1, max_retry + 1):
            try:
                ret = self.GetSDKVersion()
                if isinstance(ret, int):
                    self._print(f"获取机械臂版本号失败。返回值{ret}，重试({attempt}/{max_retry})")
                    time.sleep(1)
                    self.reconnect()
                else:
                    self._print(f"机械臂版本号为：{ret[1]}")
                    self._print("机械臂连接成功")
                    return True
            except Exception as exc:
                self._print(f"获取机械臂版本号异常：{exc}，重试({attempt}/{max_retry})")
                time.sleep(1)
                self.reconnect()
        self._print("机械臂连接失败，超过最大重试次数")
        return False

    def CartPointsToJoint(self, points, reverse_rpy_axes: bool = False, config: int = -1, unwrap_joint6: bool = True):
        """将笛卡尔点位列表转换为(笛卡尔点, 关节点)两套序列。
        - points: 来自文件的点位，要求每个点至少 6 个元素 [x,y,z,rx,ry,rz]
        - reverse_rpy_axes: 保留你原来的姿态 3 轴反转逻辑
        - config: 逆解 config 参数，保持与你原来一致（默认 -1）
        - unwrap_joint6: 对第 6 轴做连续化，避免跨越 -180/180 造成突跳
        """
        cart_points = []
        joint_points = []

        for i, p in enumerate(points):
            try:
                p = list(p)
            except TypeError:
                self._print(f"点位 {i+1} 无法转换为列表: {p}")
                return None, None

            if len(p) < 6:
                self._print(f"点位 {i+1} 长度不足 6: {p}")
                return None, None
            if reverse_rpy_axes:
                p[3:6] = list(reversed(p[3:6]))
            error, joint_pos = self.GetInverseKin(0, desc_pos=p, config=config)
            if error != 0:
                self._print(f"点 {p} 求逆失败: {error}")
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

    def RobotSplindMove(self, points, points_j: list = [], spline_type: int = 0, ovl: float = 50.0, tool: int = 0, user: int = 0):
        """将 points 按新样条运动一次性执行。

        - points 会做简单预处理（保持你原先的姿态 3 轴反转逻辑）
        - spline_type: 样条模式（沿用 NewSplineStart type 参数）
        - ovl: 默认 overlap/速度百分比，可按需覆盖
        """
        if self.robot_ip == "fake":
            self._print(f"模拟执行 RobotSplindMove，点数: {len(points)}")
            return 0
        start_time = time.time()
        average_time = 8000
        blendR = 0.0
        if points_j and len(points_j) == len(points):
            spline_points = points
            spline_points_j = points_j

        else:
            spline_points, spline_points_j = self.CartPointsToJoint(points)
        self._print(f"已加载 {len(spline_points)} 个点位，开始样条运动。")
        start_index = 0
        ret = self.NewSplineStart(type=spline_type, averageTime=int(average_time))
        if ret != 0:
            self._print(f"样条运动开始失败: {ret}")
            return -1
        for idx, pj in enumerate(spline_points_j):
            if idx < start_index:
                continue
            lastFlag = 1 if idx == (len(spline_points_j) - 1) else 0
            self._print(f"加载点 {idx+1}/{len(spline_points_j)}: cart={list(spline_points[idx])}")
            ret = self.NewSplinePoint(
                desc_pos=list(spline_points[idx]),
                tool=tool,
                user=user,
                lastFlag=lastFlag,
                joint_pos=pj,
                ovl=ovl,
                blendR=blendR,
            )
            if ret != 0:
                self._print(f"样条运动到点 {idx+1} 失败: {ret},目标点关节{pj}")
                self.NewSplineEnd()
                return ret
        self._print(f"样条运动指令发送完成，正在执行中...")
        # while self.GetRobotMotionDone()[1] == 0:
        #     time.sleep(0.008)
        self.NewSplineEnd()
        self._print(f"新样条运动执行完成，总时间: {time.time() - start_time:.2f} 秒")
        return 0

    def RobotServoSpline(self, points, cmdt_s: float = 0.004, speed_mm_s: float = 100.0,
                        max_ori_step_deg: float = 1.0, tool: int = 0, user: int = 0):
        """使用 ServoCart 平滑轨迹运动执行点位列表。

        Args:
            points: 位姿列表，每个点为 [x,y,z,rx,ry,rz]
            cmdt_s: 命令周期（秒），默认 0.004
            speed_mm_s: 移动速度（毫米/秒），默认 100.0
            max_ori_step_deg: 最大姿态步进角度，默认 1.0
            tool: 工具坐标系，默认 0
            user: 用户坐标系，默认 0
        """
        if self.robot_ip == "fake":
            self._print(f"模拟执行 RobotServoSpline")
            return 0
        start_time = time.time()

        # 构建配置
        cfg = ServoCartConfig(
            robot_ip=None,
            tool=int(tool),
            user=int(user),
            points=[list(map(float, p[:6])) for p in points],
            swap_rpy_order=False,
            auto_swap_rpy_order=False,
            cmdt_s=float(cmdt_s),
            speed_mm_s=float(speed_mm_s),
            max_ori_step_deg=float(max_ori_step_deg),
            verbose=True,
        )

        self._print(f"已加载 {len(points)} 个点位，开始 ServoCart 轨迹规划...")

        # 轨迹规划
        plan, targets = plan_from_points(self, cfg)
        self._print(f"轨迹规划完成，共 {len(targets)} 个目标点，cmdt={plan.used_cmdt_s}")

        # MoveCart 到样条起点
        move_to_start_err = int(
            self.MoveCart(
                desc_pos=list(plan.geometry_points[0]),
                vel=100,
                tool=int(tool),
                user=int(user),
            )
        )
        if move_to_start_err != 0:
            self._print(f"MoveCart 到起点失败: {move_to_start_err}")
            return move_to_start_err

        self._print(f"开始执行 ServoCart 平滑轨迹...")

        # 执行伺服样条运动
        err = run_servo_spline(
            self,
            targets,
            cmdt_s=float(plan.used_cmdt_s),
            queue_low_watermark=int(cfg.queue_low_watermark),
            queue_high_watermark=int(cfg.queue_high_watermark),
            queue_guard=int(cfg.queue_guard),
            queue_prefill_target=int(cfg.queue_prefill_target),
            queue_poll_period_s=float(cfg.queue_poll_period_s),
            log_queue_len=bool(cfg.log_queue_len),
        )

        total_time = time.time() - start_time
        self._print(f"RobotServoSpline运行结束: errcode={int(err)}, 总耗时: {total_time:.3f} 秒")
        return err


if __name__ == "__main__":
    # 创建机械臂实例（可改为实际IP）
    arm = FairinoRobotArm("测试机械臂", robot_ip="fake")
    print("正在连接机械臂...")
    if arm.ConnectRobotArm():
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
        sys.exit(0)