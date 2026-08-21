#!/usr/bin/env python3
"""取鞋臂工作流管理器（TakeWorkFlowManager）。

================================================================================
【先搞懂：这个文件跟“线程”的关系】
================================================================================

本文件 **自己不会创建任何线程**。
它只是一套“动作函数库”，由上层的 PressShoesWorkflow 在线程里调用。

真正相关的线程在 press_shoes/press_shoes_workflow.py 里：

1) TakeOutPress 线程（主调用方）
   - 循环执行取鞋状态机
   - 依次调用本文件的：
       handle_pick_left / handle_pick_right
       handle_exit_far_use_near
       handle_place_left / handle_place_right
       move_to_initial_position

2) TakeArmEarlyWait-* 临时守护线程（偶尔抢先移动等待位）
   - 当取鞋臂还在“等取出任务”时，预测哪边压机先完成
   - 调用本文件的 move_to_wait(...)，提前挪到对应近等待位，省时间
   - 因此本文件用 RLock（可重入锁）保护：同一时刻只允许一条路径在动机械臂

【取鞋一整轮在干什么（物理动作）】

    近等待位 ──样条进槽──► 取鞋点(夹紧) ──样条退出──► 等待位
         │
         │（若取的是“远侧”槽，再回到本臂近等待位）
         ▼
      放置点(松开) ──► 再回近等待位 ──► 等下一任务

位姿数字从哪里来？
    press_shoes/config/take_arm_config.yaml
    （现场示教好的 TCP 坐标 / 关节角）

返回值约定（几乎所有动作函数）：
    0  = 成功
    非0 / -1 = 失败（上层会把取鞋臂状态切到 ERROR）
"""

from pathlib import Path
from typing import Any, Callable, Optional
from enum import Enum
import yaml
from ..robot_arm.gripper_controller_can import CANGripperController
from ..utils import FairinoRobotArm
from ..process_context import PressProcessContext
import time
from threading import RLock

# True = 不连真机械臂，动作函数直接 return 0（方便无硬件调试）
# False = 走真实 MoveJ / 样条 / 夹爪
FAKE_ARM = False


class TakeArmPose(str, Enum):
    """取鞋臂“当前大概在哪个示教点”的标签。

    不是机器人实时反馈的精确坐标，而是软件自己记录的“逻辑位置”，
    用来判断：要不要先换边等待、能不能直接取鞋等。
    """

    LEFT_WAIT_POSE = "left_wait_pose"      # 左近等待位
    RIGHT_WAIT_POSE = "right_wait_pose"    # 右近等待位
    LEFT_PICK_POSE = "left_pick_pose"      # 左槽取鞋点（夹紧位置）
    RIGHT_PICK_POSE = "right_pick_pose"    # 右槽取鞋点
    LEFT_PLACE_POSE = "left_place_pose"    # 左鞋放置点（松开位置）
    RIGHT_PLACE_POSE = "right_place_pose"  # 右鞋放置点


class TakeWorkFlowManager:
    """管理“从压鞋机取出鞋子 → 放到指定位置”的整套手臂动作。"""

    def __init__(
        self,
        robot_arm,
        gripper: CANGripperController,
        press_vision=None,
        config_path: Optional[str] = None,
        process_context: Optional[PressProcessContext] = None,
        *,
        log: Any,
    ) -> None:
        """创建取鞋管理器，并完成：读配置 → 张开夹爪 → 加速度平滑 → 去近等待位。

        参数说明（都是上层 PressShoesWorkflow 传进来的）：
            robot_arm:
                取鞋用的法奥机械臂对象（FairinoRobotArm）。
                真正发运动指令的是它：MoveJ / RobotServoSpline 等。
            gripper:
                CAN 总线夹爪控制器。open_claw() 张开，close_claw() 夹紧。
            press_vision:
                压杆对位视觉（Position）。取鞋时用来微调“取鞋上方点”的 XY，
                补偿压杆/鞋楦中心相对示教点的偏移。可为 None（就不视觉修正）。
            config_path:
                示教位姿 yaml。默认 press_shoes/config/take_arm_config.yaml。
            process_context:
                与放鞋流程共享的上下文。这里主要用来取 pick_dist（取鞋高度补偿）。
            log:
                日志对象（必须传）。
        """
        self.log = log
        self.process_context = process_context
        self.robot_arm: FairinoRobotArm = robot_arm

        # ---- 运动常用参数 ----
        self.speed = 100.0   # MoveJ 速度百分比类参数（具体含义由臂 SDK 定义）
        self.tool = 0        # 工具坐标系编号（夹爪 TCP），会从 yaml 覆盖
        self.user = 0        # 用户/工件坐标系编号，一般 0=基座系

        # arm_type: 这根取鞋臂本身装在“左”还是“右”
        # （配置里常见 arm_type: right，表示取鞋臂是右侧那根）
        # “近等待位”= 本臂自己这一侧；“远等待位”= 另一侧
        self.arm_type = None

        self.press_vision = press_vision
        self.gripper = gripper

        # 软件记录的当前逻辑位姿标签；初始化后会被 move_to_initial_position 更新
        self.take_arm_pose: Optional[TakeArmPose] = None

        # 'left' / 'right' / None：是否正停在某一侧等待位
        # None 表示刚干完取/放，暂时不算“空闲等待”
        self.wait_pose = None

        # 可重入锁：保护“动取鞋臂”的临界区
        # 为什么需要？因为 TakeOutPress 线程和 EarlyWait 线程都可能调用移动函数，
        # 同时发指令会危险。RLock 允许同一线程重复加锁。
        self._take_arm_lock = RLock()

        # ---- 单点示教位姿（每个通常是 [x,y,z,rx,ry,rz]，单位 mm / 度）----
        # 带 _j 后缀的是对应的 6 轴关节角（度），MoveJ 时要用
        self.left_wait_pose = []
        self.left_wait_pose_j = []
        self.right_wait_pose = []
        self.right_wait_pose_j = []
        self.left_pick_pose = []
        self.left_pick_pose_j = []
        self.left_pick_pose_up = []      # 取鞋点正上方（先到这再下降）
        self.left_pick_pose_up_j = []
        self.left_pick_pose_up1 = []     # 进槽路径上的过渡点（示教）
        self.left_pick_pose_up_j1 = []
        self.left_pick_pose_up2 = []
        self.left_pick_pose_up_j2 = []
        self.right_pick_pose = []
        self.right_pick_pose_j = []
        self.right_pick_pose_up = []
        self.right_pick_pose_up_j = []
        self.right_pick_pose_up1 = []
        self.right_pick_pose_up_j1 = []
        self.right_pick_pose_up2 = []
        self.right_pick_pose_up_j2 = []
        self.left_place_pose = []
        self.right_place_pose = []

        # ---- 路径点列表（样条用）：进槽 / 出槽 / 左右等待位切换 ----
        # 例如 move_in_left = [点1, 点2, ..., 取鞋上方, 取鞋点]
        self.move_in_left = []
        self.move_in_left_j = []
        self.move_in_right = []
        self.move_in_right_j = []
        self.move_out_left = []
        self.move_out_left_j = []
        self.move_out_right = []
        self.move_out_right_j = []
        self.move_left_to_right_wait_pose = []
        self.move_left_to_right_wait_pose_j = []
        self.move_right_to_left_wait_pose = []
        self.move_right_to_left_wait_pose_j = []

        self.spline_speed = 100.0  # 样条笛卡尔速度 mm/s，yaml 可覆盖（现场常 700）

        # 槽底高度（mm）。有值时：取鞋 z = slot_bottom + pick_dist
        self.left_slot_bottom: Optional[float] = None
        self.right_slot_bottom: Optional[float] = None

        # ---- 加载示教配置 ----
        default_config = Path(__file__).resolve().parent.parent / "config" / "take_arm_config.yaml"
        self.config_path = str(config_path or default_config)
        if not self.load_taught_poses(self.config_path):
            self.log.warning("[取出流程] 示教位姿配置加载失败，请检查配置文件")

        self.gripper: CANGripperController = gripper

        # 启动安全动作：先张开夹爪，避免带着鞋或夹爪闭合状态乱动
        if not self.gripper.open_claw():
            raise RuntimeError(f"[{self.robot_arm.name}] 夹爪打开失败")

        # AccSmoothStart(1)：打开加速度平滑，减少启停冲击
        # 返回值约定：0 通常表示成功（法奥 SDK 风格）
        ret = self.robot_arm.AccSmoothStart(1)
        if ret != 0:
            raise RuntimeError(f"[{self.robot_arm.name}] 加速度平滑启动失败，返回值: {ret}")

        # 慢速（15%）挪到本臂近等待位，作为工作流起点
        ret = self.move_to_initial_position(speed_multiplier=0.15)
        if ret != 0:
            raise RuntimeError(f"[{self.robot_arm.name}] 移动到初始位置失败，返回值: {ret}")

    # =========================================================================
    # 配置加载
    # =========================================================================

    def load_taught_poses(self, yaml_path: str):
        """从 yaml 把示教点读进成员变量。

        yaml 里大致两类数据：
        1) 直接给坐标的键：如 left_wait_pose: [x,y,z,rx,ry,rz]
        2) 路径键：如 move_in_left: [left_pick_pose_up2, left_pick_pose_up1, ...]
           这里存的是“点的名字”，再去同文件里查这些名字对应的坐标，拼成路径列表。
        """
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            self.log.error(f"[取出流程] 读取示教位姿文件失败: {exc}")
            return False

        def _pose(key):
            """按键名取一个位姿列表；没有或非法就返回空列表。"""
            pose = data.get(key, [])
            return [] if pose in (None, False) else pose

        self.arm_type = data.get("arm_type", self.arm_type)
        self.tool = int(data.get("tool", self.tool))

        # ---- 单点 ----
        self.left_wait_pose = _pose("left_wait_pose")
        self.left_wait_pose_j = _pose("left_wait_pose_j")
        self.right_wait_pose = _pose("right_wait_pose")
        self.right_wait_pose_j = _pose("right_wait_pose_j")
        self.left_pick_pose = _pose("left_pick_pose")
        self.left_pick_pose_j = _pose("left_pick_pose_j")
        self.left_pick_pose_up = _pose("left_pick_pose_up")
        self.left_pick_pose_up_j = _pose("left_pick_pose_up_j")
        self.left_pick_pose_up1 = _pose("left_pick_pose_up1")
        self.left_pick_pose_up_j1 = _pose("left_pick_pose_up_j1")
        self.left_pick_pose_up2 = _pose("left_pick_pose_up2")
        self.left_pick_pose_up_j2 = _pose("left_pick_pose_up_j2")
        self.right_pick_pose = _pose("right_pick_pose")
        self.right_pick_pose_j = _pose("right_pick_pose_j")
        self.right_pick_pose_up = _pose("right_pick_pose_up")
        self.right_pick_pose_up_j = _pose("right_pick_pose_up_j")
        self.right_pick_pose_up1 = _pose("right_pick_pose_up1")
        self.right_pick_pose_up_j1 = _pose("right_pick_pose_up_j1")
        self.right_pick_pose_up2 = _pose("right_pick_pose_up2")
        self.right_pick_pose_up_j2 = _pose("right_pick_pose_up_j2")
        self.left_place_pose = _pose("left_place_pose")
        self.left_place_pose_j = _pose("left_place_pose_j")
        self.right_place_pose = _pose("right_place_pose")
        self.right_place_pose_j = _pose("right_place_pose_j")

        # ---- 路径：yaml 里是点名列表，这里解析成真实坐标列表 ----
        # 例：move_in_left: [a, b, c] 且 move_in_left_j: [a_j, b_j, c_j]
        #     则读出 a/b/c 的坐标，依次 append 到 self.move_in_left
        move_pose_keys = [
            ("move_in_left", "move_in_left_j"),
            ("move_in_right", "move_in_right_j"),
            ("move_out_left", "move_out_left_j"),
            ("move_out_right", "move_out_right_j"),
            ("move_left_to_right_wait_pose", "move_left_to_right_wait_pose_j"),
            ("move_right_to_left_wait_pose", "move_right_to_left_wait_pose_j"),
        ]
        for key, key_j in move_pose_keys:
            val = data.get(key, [])       # 笛卡尔点名列表
            val_j = data.get(key_j, [])   # 关节角点名列表
            for p_name, j_name in zip(val, val_j):
                p = _pose(p_name)
                j = _pose(j_name)
                if p and j:
                    getattr(self, key).append(p)
                    getattr(self, key_j).append(j)
            self.log.info(f"[取出流程] self.{key} 当前值: {getattr(self, key)}")
            self.log.info(f"[取出流程] self.{key_j} 当前值: {getattr(self, key_j)}")

        self.spline_speed = float(data.get("spline_speed", self.spline_speed) or 100.0)
        self.log.info(f"[取出流程] 样条运动速度: {self.spline_speed} mm/s")

        def _optional_float(key: str) -> Optional[float]:
            if key not in data or data[key] is None:
                return None
            return float(data[key])

        self.left_slot_bottom = _optional_float("left_slot_bottom")
        self.right_slot_bottom = _optional_float("right_slot_bottom")
        self.log.info("[取出流程] 示教位姿加载完成")
        return True

    # =========================================================================
    # 初始化 / 近等待位
    # =========================================================================

    def move_to_initial_position(self, speed_multiplier=1):
        """工作流开始时：去本臂近等待位。"""
        self.log.info(f"[{self.robot_arm.name}] 移动到近等待位置")
        return self._move_to_near_wait(speed_multiplier=speed_multiplier)

    def _move_to_near_wait(self, speed_multiplier=1):
        """根据 arm_type，MoveJ 到左或右近等待位。

        MoveJ 是什么？
            关节空间运动：按关节角插值移动。适合大范围换位，不一定走直线。
        参数粗解：
            joint_pos : 目标关节角
            desc_pos  : 目标 TCP 位姿（描述位姿，供控制器参考/校验）
            tool/user : 工具系 / 用户系编号
            vel       : 速度
            ovl       : 速度缩放百分比一类参数
            blendT=-1 : 不交融（到点停稳再下一动作）
        """
        self.log.info(f"[{self.robot_arm.name}] 移动到近等待位")
        ret = -1
        wait_pose = None
        target_wait_pose = (
            TakeArmPose.LEFT_WAIT_POSE if self.arm_type == "left" else TakeArmPose.RIGHT_WAIT_POSE
        )

        if self.arm_type == "left":
            ret = self.robot_arm.MoveJ(
                joint_pos=self.left_wait_pose_j,
                desc_pos=self.left_wait_pose,
                tool=self.tool,
                user=self.user,
                vel=self.speed * speed_multiplier,
                ovl=100.0,
                blendT=-1.0,
            )
            wait_pose = "left"
        else:
            ret = self.robot_arm.MoveJ(
                joint_pos=self.right_wait_pose_j,
                desc_pos=self.right_wait_pose,
                tool=self.tool,
                user=self.user,
                vel=self.speed * speed_multiplier,
                ovl=100.0,
                blendT=-1.0,
            )
            wait_pose = "right"

        if ret == 0:
            self.wait_pose = wait_pose
            self.take_arm_pose = target_wait_pose
        return ret

    # =========================================================================
    # 视觉修正 & 取鞋高度 & 路径改写（取鞋前的“算路径”）
    # =========================================================================

    def _modify_pick_pose_up(self, pick_pose_up=None, shoe_type=None):
        """用压杆视觉，微调“取鞋上方点”的 X/Y。

        为什么要改？
            示教点是某次标定的固定位置；实际鞋楦/压杆中心会有小偏移。
            get_rod_robot_offset 测出偏移后，把上方点平移一下，下降取鞋更准。

        注意：只改 xy，不改姿态角；z 由后面的 _get_pick_z 另算。
        """
        if pick_pose_up is None:
            pick_pose_up = []
        if self.press_vision is None:
            return pick_pose_up

        # Position 接口：1=左槽，2=右槽
        type_id = 1 if shoe_type == "left" else 2
        find_flag, robot_xyz_offset, _ = self.press_vision.get_rod_robot_offset(type_id)
        print(f"[取出流程] 视觉测量{type_id}号鞋子压杆移动距离: {robot_xyz_offset}")

        if not find_flag:
            self.log.warning(f"[取出流程] 视觉未找到{shoe_type}槽位，使用默认取鞋上方位")
            return pick_pose_up

        # 视觉返回单位是米，位姿用毫米 → ×1000
        dx_m, dy_m, dz_m = robot_xyz_offset[:3]
        dx, dy, _ = dx_m * 1000.0, dy_m * 1000.0, dz_m * 1000.0

        new_pose = pick_pose_up.copy()
        new_pose[0] += dx
        new_pose[1] += dy
        self.log.info(f"[取出流程] 视觉修正{shoe_type}取出上方位，偏移: ({dx:.1f}, {dy:.1f})mm")
        return new_pose

    def _insert_z_transition_points(self, pose_up, pose_down, num_points=3):
        """在“上方点”和“取鞋点”之间，只沿 Z 插几个过渡点。

        目的：样条下降时更平滑，不要一步扎到底。
        假设：上下两点 xy/姿态相同，主要差在 z（下标 2）。
        """
        transitions = []
        for i in range(1, num_points + 1):
            t = i / (num_points + 1)  # 0~1 之间的比例
            pt = pose_up.copy()
            pt[2] = pose_up[2] + (pose_down[2] - pose_up[2]) * t
            transitions.append(pt)
        return transitions

    def _get_pick_z(self, shoe_type: str) -> float:
        """计算真正夹紧时的高度 z（mm）。

        优先：slot_bottom（槽底） + pick_dist（放鞋时记下来的取鞋距离补偿）
        否则：直接用示教 left/right_pick_pose 的 z。
        """
        if shoe_type == "left":
            slot_bottom = self.left_slot_bottom
            taught_pick_pose = self.left_pick_pose
        elif shoe_type == "right":
            slot_bottom = self.right_slot_bottom
            taught_pick_pose = self.right_pick_pose
        else:
            self.log.error(f"[取出流程] 未知鞋类型: {shoe_type}")
            return 0.0

        default_z = float(taught_pick_pose[2])
        if slot_bottom is not None:
            pick_dist = (
                self.process_context.get_pick_dist(shoe_type)
                if self.process_context is not None
                else None
            )
            pick_dist_val = float(pick_dist) if pick_dist is not None else 0.0
            pick_z = slot_bottom + pick_dist_val
            self.log.info(
                f"[取出流程] {shoe_type}取鞋高度: "
                f"slot_bottom({slot_bottom}) + pick_dist({pick_dist_val}) = {pick_z:.4f} mm"
            )
            return pick_z

        self.log.info(f"[取出流程] {shoe_type}取鞋高度: 示教点 z={default_z:.4f} mm")
        return default_z

    def _get_modify_move(self, shoe_type=None):
        """生成“进槽路径”和“出槽路径”（出槽=进槽倒序）。

        动作实现逻辑：
        1. 视觉修正取鞋上方点 xy
        2. 复制上方点，改 z 得到真正取鞋点（且 z 不低于示教取鞋点，防撞）
        3. 改写 move_in_* 末尾：... → 修正后上方点 → Z过渡点 → 取鞋点
        4. 出槽路径 = 进槽路径反过来（夹着鞋原路退出）
        """
        if shoe_type == "left":
            left_pick_pose_up = self._modify_pick_pose_up(self.left_pick_pose_up, shoe_type="left")
            left_pick_pose = left_pick_pose_up.copy()
            # max(..., 示教z)：算出来的高度如果比示教还低，用示教，避免插太深
            left_pick_pose[2] = max(self._get_pick_z("left"), self.left_pick_pose[2])

            move_in_left_modified = self.move_in_left.copy()
            move_in_left_modified[-2] = left_pick_pose_up  # 倒数第2个换成修正后上方点
            z_transitions = self._insert_z_transition_points(left_pick_pose_up, left_pick_pose)
            # 丢掉原来的最后一个点，换成：过渡点们 + 最终取鞋点
            move_in_left_modified[-1:] = z_transitions + [left_pick_pose]

            move_out_left_modified = list(reversed(move_in_left_modified))
            return move_in_left_modified, move_out_left_modified

        elif shoe_type == "right":
            right_pick_pose_up = self._modify_pick_pose_up(self.right_pick_pose_up, shoe_type="right")
            right_pick_pose = right_pick_pose_up.copy()
            right_pick_pose[2] = max(self._get_pick_z("right"), self.right_pick_pose[2])

            move_in_right_modified = self.move_in_right.copy()
            move_in_right_modified[-2] = right_pick_pose_up
            z_transitions = self._insert_z_transition_points(right_pick_pose_up, right_pick_pose)
            move_in_right_modified[-1:] = z_transitions + [right_pick_pose]

            move_out_right_modified = list(reversed(move_in_right_modified))
            return move_in_right_modified, move_out_right_modified

        else:
            self.log.error(f"[取出流程] 未知鞋类型: {shoe_type}")
            return None, None

    # =========================================================================
    # 取鞋（从压槽夹出）—— 由 TakeOutPress 线程在 PICK_* 状态调用
    # =========================================================================

    def handle_pick_left(self):
        """取出左槽鞋子的完整动作。

        步骤：
          (可选) 若本臂是右臂且还不在左等待位 → 先换到左等待位
          1. 算进/出路径（含视觉与高度修正）
          2. RobotServoSpline 沿进槽路径走到取鞋点
             RobotServoSpline = 笛卡尔空间样条/伺服跟踪一串点，适合进狭窄槽
          3. close_claw 夹紧
          4. 再样条沿出槽路径退回左等待位附近

        加锁原因：避免 EarlyWait 线程同时 Move。
        """
        with self._take_arm_lock:
            self.log.info("[取出流程] 准备取出左鞋")
            if FAKE_ARM:
                return 0

            # 右臂取左鞋：先切到左等待位，再进左槽
            if self.arm_type == "right" and self.take_arm_pose != TakeArmPose.LEFT_WAIT_POSE:
                ret = self._move_to_left_wait_unlocked()
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到近等待位失败，返回值: {ret}")
                    return ret

            self.wait_pose = None  # 开始干活，不再是“空闲等待”
            move_in_left_modified, move_out_left_modified = self._get_modify_move(shoe_type="left")

            # 进槽
            ret = self.robot_arm.RobotServoSpline(
                points=move_in_left_modified,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=1.0,  # 姿态每步最大变化，限制太大翻腕
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 进左槽取鞋样条运动失败，返回值: {ret}")
                return ret

            self.take_arm_pose = TakeArmPose.LEFT_PICK_POSE

            # 夹紧鞋子
            if not self.gripper.close_claw():
                self.log.error(f"[{self.robot_arm.name}] 夹爪关闭失败")
                return -1

            # 原路（倒序）退出
            ret = self.robot_arm.RobotServoSpline(
                points=move_out_left_modified,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.6,
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}]机械臂从左鞋取出点移到左等待位失败，返回值: {ret}")
                return ret

            self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
            return 0

    def handle_pick_right(self):
        """取出右槽鞋子。逻辑与 handle_pick_left 对称。"""
        with self._take_arm_lock:
            self.log.info("[取出流程] 准备取出右鞋")
            if FAKE_ARM:
                return 0

            # 左臂取右鞋：先切到右等待位
            if self.arm_type == "left" and self.take_arm_pose != TakeArmPose.RIGHT_WAIT_POSE:
                ret = self._move_to_right_wait_unlocked()
                if ret != 0:
                    return ret

            self.wait_pose = None
            move_in_right_modified, move_out_right_modified = self._get_modify_move(shoe_type="right")

            ret = self.robot_arm.RobotServoSpline(
                points=move_in_right_modified,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.7,
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 进右槽取鞋样条运动失败，返回值: {ret}")
                return ret

            self.take_arm_pose = TakeArmPose.RIGHT_PICK_POSE

            if not self.gripper.close_claw():
                self.log.error(f"[{self.robot_arm.name}] 夹爪关闭失败")
                return -1

            ret = self.robot_arm.RobotServoSpline(
                points=move_out_right_modified,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.4,
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}]机械臂从右鞋取出点上方移到右等待位失败，返回值: {ret}")
                return ret

            self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
            return 0

    # =========================================================================
    # 放鞋（放到流水线/托盘等放置点）—— TakeOutPress 在 PLACE_* 状态调用
    # =========================================================================

    def handle_place_left(self):
        """把夹着的左鞋放到 left_place_pose，松开，再回近等待位。

        这里用 MoveJ（关节运动）到放置点，不是样条进槽。
        注意：此函数未加 _take_arm_lock（当前上层时序下一般独占取鞋臂）。
        """
        self.log.info("[取出流程] 放回左鞋")
        self.wait_pose = None
        if FAKE_ARM:
            return 0

        ret = self.robot_arm.MoveJ(
            joint_pos=self.left_place_pose_j,
            desc_pos=self.left_place_pose,
            tool=self.tool,
            user=self.user,
            vel=self.speed,
            ovl=100.0,
            blendT=-1.0,
        )
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}]机械臂移动到左鞋放置点失败，返回值: {ret}")
            return ret

        self.take_arm_pose = TakeArmPose.LEFT_PLACE_POSE

        # 松开，鞋留在放置位
        if not self.gripper.open_claw():
            self.log.error(f"[{self.robot_arm.name}] 夹爪打开失败")
            return -1

        # 回本臂近等待位，准备接下一个取出任务
        ret = self._move_to_near_wait()
        if ret != 0:
            return ret
        return 0

    def handle_place_right(self, vision=None):
        """放置右鞋。vision 参数目前未使用（历史遗留接口）。"""
        self.log.info("[取出流程] 放右鞋")
        self.wait_pose = None
        if FAKE_ARM:
            return 0

        ret = self.robot_arm.MoveJ(
            joint_pos=self.right_place_pose_j,
            desc_pos=self.right_place_pose,
            tool=self.tool,
            user=self.user,
            vel=self.speed,
            ovl=100.0,
            blendT=-1.0,
        )
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}]机械臂移动到右鞋放置点失败，返回值: {ret}")
            return ret

        self.take_arm_pose = TakeArmPose.RIGHT_PLACE_POSE

        if not self.gripper.open_claw():
            self.log.error(f"[{self.robot_arm.name}] 夹爪打开失败")
            return -1

        ret = self._move_to_near_wait()
        if ret != 0:
            return ret
        return 0

    # =========================================================================
    # 取完鞋后：若取的是“远侧”，先回到本臂近等待位（EXIT_FAR_USE_NEAR）
    # =========================================================================

    def handle_exit_far_use_near(self):
        """“退出远槽，占用近等待位”。

        含义（结合上层逻辑）：
            取鞋臂有自己的近侧（arm_type）。
            如果刚取的是远侧槽的鞋，夹着鞋站在远侧等待位不方便放，
            就先挪回近侧等待位，再去 PLACE。

        上层只有 shoe_type != take_arm_type 时才会调用本函数。
        """
        with self._take_arm_lock:
            self.log.info("[取出流程] 取出后退出远鞋槽占用近槽")
            if FAKE_ARM:
                return 0
            if self.arm_type == "left":
                return self._move_to_left_wait_unlocked()
            if self.arm_type == "right":
                return self._move_to_right_wait_unlocked()
            return -1

    # =========================================================================
    # 左右等待位切换（供取鞋前换边、EarlyWait 提前到位）
    # =========================================================================

    def _move_to_left_wait(self):
        """对外：加锁后移到左等待位。"""
        with self._take_arm_lock:
            return self._move_to_left_wait_unlocked()

    def _move_to_left_wait_unlocked(self):
        """不加锁版本：假设调用方已经持有 _take_arm_lock。

        逻辑：
          - 已在左等待位 → 直接成功
          - 当前在右等待位 → 走 right_to_left_wait_pose 路径切过去
          - 其它状态 → 这里只处理“右等→左等”，ret 保持 0（依赖上层保证前置姿态）
        """
        self.log.info(f"[{self.robot_arm.name}] 移动到左待位")
        if FAKE_ARM:
            self.wait_pose = "left"
            self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
            return 0

        if self.wait_pose == "left" or self.take_arm_pose == TakeArmPose.LEFT_WAIT_POSE:
            self.log.info(f"[{self.robot_arm.name}] 已经在左待位，无需移动")
            self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
            return 0

        ret = 0
        if self.take_arm_pose == TakeArmPose.RIGHT_WAIT_POSE:
            ret = self.right_to_left_wait_pose()
            self.wait_pose = "left"
            if ret == 0:
                self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
        return ret

    def _move_to_right_wait(self):
        """对外：加锁后移到右等待位。"""
        with self._take_arm_lock:
            return self._move_to_right_wait_unlocked()

    def _move_to_right_wait_unlocked(self):
        """不加锁版本：右等待位切换，与左对称。"""
        self.log.info(f"[{self.robot_arm.name}] 移动到右待位")
        if FAKE_ARM:
            self.wait_pose = "right"
            self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
            return 0

        if self.wait_pose == "right" or self.take_arm_pose == TakeArmPose.RIGHT_WAIT_POSE:
            self.log.info(f"[{self.robot_arm.name}] 已经在右待位，无需移动")
            self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
            return 0

        ret = 0
        if self.take_arm_pose == TakeArmPose.LEFT_WAIT_POSE:
            ret = self.left_to_right_wait_pose()
            self.wait_pose = "right"
            if ret == 0:
                self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
        return ret

    def move_to_wait(
        self,
        wait_pose_type: str,
        should_proceed: Optional[Callable[[], bool]] = None,
    ) -> int:
        """提前移动到指定侧等待位（给 EarlyWait 线程用）。

        参数：
            wait_pose_type: "left" 或 "right"
            should_proceed: 可选回调。加锁后若返回 False，说明情况变了
                            （例如取出任务已经来了），就取消移动，返回 0。

        前置条件：当前必须已经在某个等待位（self.wait_pose 非空）。
                  正在取/放时 wait_pose=None，会拒绝移动。
        """
        if self.wait_pose is None:
            self.log.info(f"[{self.robot_arm.name}] 当前没有在等待位，无法移动到{wait_pose_type}待位")
            return -1
        if wait_pose_type not in ("left", "right"):
            self.log.error(f"[取出流程] 未知等待位类型: {wait_pose_type}")
            return -1

        with self._take_arm_lock:
            if should_proceed is not None and not should_proceed():
                self.log.debug(
                    f"[{self.robot_arm.name}] 提前等待条件已变化，取消移动到{wait_pose_type}待位"
                )
                return 0
            if wait_pose_type == "left":
                return self._move_to_left_wait_unlocked()
            return self._move_to_right_wait_unlocked()

    def left_to_right_wait_pose(self):
        """沿示教路径：左等待位 → 右等待位。

        点数 > 2：用 RobotServoSpline 平滑走完整路径
        点数 == 2：直接 MoveJ 到终点（起点+终点）
        """
        self.log.info("[取出流程] 从左等待位移动到右等待位")
        if FAKE_ARM:
            self.wait_pose = "right"
            self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
            return 0

        if self.take_arm_pose == TakeArmPose.RIGHT_WAIT_POSE:
            self.log.info("[取出流程] 已在右等待位，无需移动")
            return 0

        points = self.move_left_to_right_wait_pose
        points_j = self.move_left_to_right_wait_pose_j

        if len(points) != len(points_j):
            self.log.error("[取出流程] 左到右等待位路径点和关节点数量不匹配")
            return -1
        if len(points) < 2:
            self.log.error("[取出流程] 左到右等待位路径未配置或点数不足")
            return -1

        if len(points) > 2:
            ret = self.robot_arm.RobotServoSpline(
                points=points,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.6,
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(
                    f"[{self.robot_arm.name}] 从左等待位移动到右等待位样条运动失败，返回值: {ret}"
                )
                return ret
        else:
            ret = self.robot_arm.MoveJ(
                joint_pos=points_j[1],
                desc_pos=points[1],
                tool=self.tool,
                user=self.user,
                vel=self.speed,
                ovl=90.0,
                blendT=-1.0,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从左等待位移动到右等待位失败，返回值: {ret}")
                return ret

        self.wait_pose = "right"
        self.take_arm_pose = TakeArmPose.RIGHT_WAIT_POSE
        return 0

    def right_to_left_wait_pose(self):
        """沿示教路径：右等待位 → 左等待位。与 left_to_right_wait_pose 对称。"""
        self.log.info("[取出流程] 从右等待位移动到左等待位")
        if FAKE_ARM:
            self.wait_pose = "left"
            self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
            return 0

        if self.take_arm_pose == TakeArmPose.LEFT_WAIT_POSE:
            self.log.info("[取出流程] 已在左等待位，无需移动")
            return 0

        points = self.move_right_to_left_wait_pose
        points_j = self.move_right_to_left_wait_pose_j

        if len(points) != len(points_j):
            self.log.error("[取出流程] 右到左等待位路径点和关节点数量不匹配")
            return -1
        if len(points) < 2:
            self.log.error("[取出流程] 右到左等待位路径未配置或点数不足")
            return -1

        if len(points) > 2:
            ret = self.robot_arm.RobotServoSpline(
                points=points,
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.6,
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(
                    f"[{self.robot_arm.name}] 从右等待位移动到左等待位样条运动失败，返回值: {ret}"
                )
                return ret
        else:
            ret = self.robot_arm.MoveJ(
                joint_pos=points_j[1],
                desc_pos=points[1],
                tool=self.tool,
                user=self.user,
                vel=self.speed,
                ovl=90.0,
                blendT=-1.0,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从右等待位移动到左等待位失败，返回值: {ret}")
                return ret

        self.wait_pose = "left"
        self.take_arm_pose = TakeArmPose.LEFT_WAIT_POSE
        return 0
