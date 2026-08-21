import threading
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from enum import Enum
import yaml
import time
from ..robot_arm.gripper_controller_can import CANGripperController
from ..utils import FairinoRobotArm
from ..process_context import PressProcessContext
import cv2
from .press_machine_manager import PressMachineManager
from ..robot_arm.tcp_redirect import pose_to_matrix
import numpy as np
FAKE_ARM  = False  # True表示使用假机械臂进行测试，False表示使用实际机械臂
put_modify = True
first_align = True
second_align = False
perception_lead = True
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
SLOT_CONFIG_BY_SIDE = {
    "left": CONFIG_DIR / "left_slot.yaml",
    "right": CONFIG_DIR / "right_slot.yaml",
}


class PutArmPose(str, Enum):
    INITIAL_POSE = "initial_pose"
    GRAB_SHOE_POINT_UP = "grab_shoe_point_up"
    GRAB_SHOE_POINT = "grab_shoe_point"
    LEFT_WAIT_POSE = "left_wait_pose"
    RIGHT_WAIT_POSE = "right_wait_pose"
    LEFT_SLOT_POSE = "left_slot_pose"
    RIGHT_SLOT_POSE = "right_slot_pose"


#假设左鞋抓取
class PutWorkFlowManager:
    """Manages put shoes into press-machine workflow."""

    def __init__(
        self,
        robot_arm,
        gripper: CANGripperController,
        press_vision=None,
        machine=None,
        config_path: Optional[str] = None,
        process_context: Optional[PressProcessContext] = None,
        *,
        log: Any,
    ) -> None:
        self.log = log
        self.process_context = process_context
        self.robot_arm: FairinoRobotArm = robot_arm
        self.speed = 100.0
        self.tool = 1
        self.user = 0
        self.put_arm_pose: Optional[PutArmPose] = None

        self.cur_handle_shoe_data = None
        self._shoe_data_lock = threading.RLock()
        self._put_arm_lock = threading.RLock()
        self.toe_align_step = 0.1

        self.arm_type = None
        self.grab_z_min = 0.0
        self.gripper_down_offset = 0.0
        self.grab_ground_z = 0.0
        self.initial_pose = []
        self.initial_pose_j = []
        self.left_wait_pose = []
        self.left_wait_pose_j = []
        self.right_wait_pose = []
        self.right_wait_pose_j = []

        self.left_slot_up = []
        self.left_slot_up_j = []
        self.left_slot_up1 = []
        self.left_slot_up_j1 = []
        self.left_slot_up2 = []
        self.left_slot_up_j2 = []
        self.right_slot_up = []
        self.right_slot_up_j = []
        self.right_slot_up1 = []
        self.right_slot_up_j1 = []
        self.right_slot_up2 = []
        self.right_slot_up_j2 = []
        self.left_place_pose_up = []
        self.right_place_pose_up = []
        self.left_place_pose = []
        self.right_place_pose = []

        self.move_to_left_slot_up = []
        self.move_to_left_slot_up_j = []
        self.move_to_right_slot_up = []
        self.move_to_right_slot_up_j = []
        self.move_out_left = []
        self.move_out_left_j = []
        self.move_out_right = []
        self.move_out_right_j = []
        self.move_left_to_right_wait_pose = []
        self.move_left_to_right_wait_pose_j = []
        self.move_right_to_left_wait_pose = []
        self.move_right_to_left_wait_pose_j = []
        self.move_grab_trans = []
        self.move_grab_trans_j = []

        self.place_dh = 20.0
        self.place_z_left = 0.0
        self.place_z_right = 0.0
        self.left_place_rz = None
        self.right_place_rz = None
        self.grab_offset_deg = 0.0
        self.spline_speed = 100.0
        self.dx = 0.0
        self.dy = 0.0
        self.safe_rz = [-180.0, 180.0]
        self.press_vision = press_vision
        self.machine = machine
        self.shoe_align_model_path = None
        self.shoe_align_imgsz = 640
        self.slot_camera_by_side: dict[str, Any] = {}

        default_config = Path(__file__).resolve().parent.parent / "config" / "put_arm_config.yaml"
        self.config_path = str(config_path or default_config)
        if not self.load_taught_poses(self.config_path):
            self.log.warning("[投放流程] 示教位姿配置加载失败，请检查配置文件")

        from slot_check_dect import SlotChecker, get_slot_check_model

        self.slot_checker = SlotChecker()
        get_slot_check_model(self.slot_checker.model_path)
        self.log.info(f"[投放流程] 鞋槽检测模型已加载: {self.slot_checker.model_path}")

        self._load_slot_cameras()

        # if self.arm_type == "left":
        #     self.gripper = left_gripper
        # elif self.arm_type == "right":
        #     self.gripper = right_gripper
        ret = self.robot_arm.AccSmoothStart(1)
        if ret != 0:
            raise RuntimeError(f"[{self.robot_arm.name}] 加速度平滑启动失败，返回值: {ret}")
        self.gripper: CANGripperController = gripper
        self.gripper.open_claw()
        ret = self.move_to_initial_position(speed_multiplier = 0.15)
        if ret != 0:
            raise RuntimeError(f"[{self.robot_arm.name}] 移动到初始位置失败，返回值: {ret}")

    @staticmethod
    def _resolve_workspace_path(path_value: object) -> Path:
        """Resolve config paths relative to the project root."""
        path_text = str(path_value).strip()
        path_obj = Path(path_text).expanduser()
        if not path_obj.is_absolute():
            path_obj = (WORKSPACE_ROOT / path_obj).resolve()
        return path_obj

    def _load_slot_cameras(self) -> None:
        """启动时加载左右鞋槽相机 name / sn 及连接参数。"""
        from shoe_seg.slot_config import SlotConfig

        for side, config_path in SLOT_CONFIG_BY_SIDE.items():
            side_label = "左" if side == "left" else "右"
            if not config_path.exists():
                self.log.warning(f"[投放流程] {side_label}槽配置文件不存在: {config_path}")
                self.slot_camera_by_side[side] = None
                continue

            try:
                slot_config = SlotConfig.load_yaml(config_path)
            except Exception as exc:
                self.log.error(f"[投放流程] {side_label}槽配置加载失败: {exc}")
                self.slot_camera_by_side[side] = None
                continue

            camera = slot_config.camera
            if camera is None:
                self.log.warning(f"[投放流程] {side_label}槽配置缺少 camera 字段")
                self.slot_camera_by_side[side] = None
                continue

            self.slot_camera_by_side[side] = camera
            self.log.info(
                f"[投放流程] {side_label}槽相机已加载: name={camera.name}, sn={camera.sn}"
            )

    def move_to_initial_position(self, speed_multiplier = 1):
        with self._put_arm_lock:
            self.log.info(f"[{self.robot_arm.name}] 移动到初始位置")
            ret = self.robot_arm.MoveJ(joint_pos=self.initial_pose_j, desc_pos=self.initial_pose, tool=self.tool, user=self.user, vel=self.speed*speed_multiplier, ovl=100.0, blendT=-1.0)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 机械臂移动到初始位置失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.INITIAL_POSE
            return 0
    
    def load_taught_poses(self, yaml_path: str):
        """从yaml文件加载示教位姿到成员变量。"""
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            self.log.error(f"[投放流程] 读取示教位姿文件失败: {exc}")
            return False

        def _pose(key):
            pose = data.get(key, [])
            return [] if pose in (None, False) else pose

        self.initial_pose = _pose("initial_pose")
        self.initial_pose_j = _pose("initial_pose_j")
        self.left_wait_pose = _pose("left_wait_pose")
        self.left_wait_pose_j = _pose("left_wait_pose_j")
        self.right_wait_pose = _pose("right_wait_pose")
        self.right_wait_pose_j = _pose("right_wait_pose_j")
        self.left_slot_up = _pose("left_slot_up")
        self.left_slot_up_j = _pose("left_slot_up_j")
        self.left_slot_up1 = _pose("left_slot_up1")
        self.left_slot_up_j1 = _pose("left_slot_up_j1")
        self.left_slot_up2 = _pose("left_slot_up2")
        self.left_slot_up_j2 = _pose("left_slot_up_j2")
        self.right_slot_up = _pose("right_slot_up")
        self.right_slot_up_j = _pose("right_slot_up_j")
        self.right_slot_up1 = _pose("right_slot_up1")
        self.right_slot_up_j1 = _pose("right_slot_up_j1")
        self.right_slot_up2 = _pose("right_slot_up2")
        self.right_slot_up_j2 = _pose("right_slot_up_j2")
        self.left_place_pose_up = _pose("left_place_pose_up")
        self.right_place_pose_up = _pose("right_place_pose_up")
        self.left_place_pose = _pose("left_place_pose")
        self.right_place_pose = _pose("right_place_pose")
        self.grab_transition = _pose("grab_transition")
        self.grab_transition_j = _pose("grab_transition_j")

        # 新增：从yaml获取move_to_left_slot_up等字段，依次调用_pose获取位姿
        move_pose_keys = [
            ("move_to_left_slot_up", "move_to_left_slot_up_j"),
            ("move_to_right_slot_up", "move_to_right_slot_up_j"),
            ("move_out_left", "move_out_left_j"),
            ("move_out_right", "move_out_right_j"),
            ("move_left_to_right_wait_pose", "move_left_to_right_wait_pose_j"),
            ("move_right_to_left_wait_pose", "move_right_to_left_wait_pose_j"),
            ("move_grab_trans", "move_grab_trans_j"),
        ]
        for key, key_j in move_pose_keys:
            if hasattr(self, key) and hasattr(self, key_j):
                val = data.get(key, [])
                val_j = data.get(key_j, [])
                self.log.info(f"[投放流程] 从yaml读取 {key}: {val}")
                self.log.info(f"[投放流程] 从yaml读取 {key_j}: {val_j}")
                # 追加到成员变量list中，而不是覆盖
                for p_name, j_name in zip(val, val_j):
                    p = _pose(p_name)
                    j = _pose(j_name)
                    if p and j:
                        getattr(self, key).append(p)
                        self.log.info(f"[投放流程] 追加: self.{key}.append({p})")
                        getattr(self, key_j).append(j)
                        self.log.info(f"[投放流程] 追加: self.{key_j}.append({j})")
                self.log.info(f"[投放流程] self.{key} 当前值: {getattr(self, key)}")
                self.log.info(f"[投放流程] self.{key_j} 当前值: {getattr(self, key_j)}")



        self.arm_type = data.get("arm_type")
        # 读取 tool
        self.tool = int(data.get("tool", self.tool))
        # 读取抓取时允许的最小 Z 高度
        self.grab_z_min = float(data.get("grab_z_min", self.grab_z_min) or 0.0)
        self.gripper_down_offset = float(data.get("gripper_down_offset", self.gripper_down_offset) or 0.0)
        # 读取抓取时地面高度
        self.grab_ground_z = float(data.get("grab_ground_z", self.grab_ground_z) or 0.0)
        # 放置时左右鞋的目标 Z
        self.place_z_left = float(data.get("place_z_left", self.place_z_left) or 0.0)
        self.place_z_right = float(data.get("place_z_right", self.place_z_right) or 0.0)
        self.left_place_rz = data.get("left_place_rz", self.left_place_rz)
        self.right_place_rz = data.get("right_place_rz", self.right_place_rz)
        self.place_dh = float(data.get("place_dh", self.place_dh) or 0.0)
        self.grab_offset_deg = float(data.get("grab_offset_deg", 0.0) or 0.0)
        self.dx = float(data.get("dx", self.dx) or 0.0)
        self.dy = float(data.get("dy", self.dy) or 0.0)
        safe_rz_val = data.get("safe_rz", self.safe_rz)
        self.safe_rz = [float(v) for v in safe_rz_val] if safe_rz_val else self.safe_rz
        # 读取样条运动速度
        self.spline_speed = float(data.get("spline_speed", self.spline_speed) or 100.0)
        self.toe_align_step = data.get("toe_align_step", 0.1)
        self.shoe_align_model_path = self._resolve_workspace_path(data.get("shoe_align_model_path"))
        self.shoe_align_imgsz = int(data.get("shoe_align_imgsz", 640) or 640)
        self.log.info(f"[投放流程] 鞋头对位模型路径: {self.shoe_align_model_path}")
        self.log.info(f"[投放流程] 鞋头对位推理尺寸: {self.shoe_align_imgsz}")
        self.log.info(f"[投放流程] 样条运动速度: {self.spline_speed} mm/s")
        self.log.info("[投放流程] 示教位姿加载完成")
        return True

    def _update_slot_grab_height(self, shoe_data: dict) -> None:
        """更新共享上下文中对应侧的 pick_dist（抓取 TCP z 与传送带 z 的高度差）。"""
        if self.process_context is None:
            return

        shoe_side = str(shoe_data.get("side", "")).strip().lower()
        if shoe_side not in ("left", "right"):
            self.log.warning(f"[投放流程] 未知鞋侧，跳过 pick_dist 更新: {shoe_side}")
            return

        grab_pose = shoe_data.get("grab_pose")
        if not grab_pose or len(grab_pose) < 3:
            self.log.warning("[投放流程] 无 grab_pose，跳过 pick_dist 更新")
            return

        grab_z = float(grab_pose[2])
        pick_dist = round(grab_z - self.grab_ground_z, 4)
        self.process_context.set_pick_dist(shoe_side, pick_dist)
        self.log.info(f"[投放流程] 已更新 {shoe_side} pick_dist: {pick_dist}")

    def _get_cur_handle_shoe_data(self) -> dict | None:
        with self._shoe_data_lock:
            if self.cur_handle_shoe_data is None:
                return None
            return self.cur_handle_shoe_data.copy()

    def _get_shoe_data_value(self, key: str, default: Any = None) -> Any:
        with self._shoe_data_lock:
            if self.cur_handle_shoe_data is None:
                return default
            return self.cur_handle_shoe_data.get(key, default)

    def _set_shoe_data_fields(self, **fields: Any) -> None:
        with self._shoe_data_lock:
            if self.cur_handle_shoe_data is None:
                return
            self.cur_handle_shoe_data.update(fields)

    def _wait_for_shoe_data_field(
        self,
        key: str,
        *,
        attempts: int = 10,
        interval: float = 0.1,
        log_label: str = "",
    ) -> Any:
        for attempt in range(attempts):
            value = self._get_shoe_data_value(key)
            if value:
                return value
            if log_label:
                self.log.debug(
                    f"[投放流程] {log_label}未就绪，等待{interval}秒 ({attempt + 1}/{attempts})"
                )
            time.sleep(interval)
        return None

    def set_cur_handle_shoe_data(self, shoe_data: dict | None) -> None:
        """设置当前待处理鞋子数据（含 grab_pose、toe_arc_xyz_list 等）。"""
        with self._shoe_data_lock:
            self.cur_handle_shoe_data = shoe_data
        if shoe_data:
            shoe_side = shoe_data.get("side")
            threading.Thread(
                target=self.get_slot_align_start_pose,
                args=(shoe_side,),
                name=f"SlotAlign-{shoe_side}",
                daemon=True,
            ).start()

    def get_slot_align_start_pose(self, shoe_side: str) -> list[float]:
        """根据当前鞋子数据与左右槽配置，计算鞋槽对位前的 TCP 位姿 [x, y, z, rx, ry, rz]。"""
        side = str(shoe_side).strip().lower()
        if side not in SLOT_CONFIG_BY_SIDE:
            self.log.error(f"[投放流程] 未知鞋侧: {shoe_side}")
            return []

        slot_config_path = SLOT_CONFIG_BY_SIDE[side]
        if not slot_config_path.exists():
            self.log.error(f"[投放流程] 槽配置文件不存在: {slot_config_path}")
            return []

        shoe_data = self._get_cur_handle_shoe_data()
        if not shoe_data:
            self.log.error("[投放流程] 当前无鞋子数据，无法计算过渡 TCP")
            return []

        toe_arc_xyz_list = shoe_data.get("toe_arc_xyz_list") or []
        if not toe_arc_xyz_list:
            self.log.error("[投放流程] toe_arc_xyz_list 为空，无法计算过渡 TCP")
            return []
        grab_pose = shoe_data.get("grab_pose")
        try:
            from shoe_seg.slot_config import SlotConfig
            from shoe_seg.compute_shoe_target_position import compute_transition_tcp

            slot_config = SlotConfig.load_yaml(slot_config_path)
            slot_z_min = self.place_z_left if side == "left" else self.place_z_right
            slot_up_pose = self.left_slot_up if side == "left" else self.right_slot_up
            rz_anchor = self.left_place_rz if side == "left" else self.right_place_rz
            gripper_limits = slot_config.gripper_xy_rz_limits()
            rz_delta_min, rz_delta_max = gripper_limits["rz_delta"]
            xy_rz_limits = {
                **gripper_limits,
                "target_point_xy": slot_config.target_point_xy().tolist(),
                "rz_anchor": rz_anchor,
                "rz_delta": (float(rz_delta_min) * 0.5, float(rz_delta_max) * 0.5),
            }
            # xy_rz_limits = {**slot_config.gripper_xy_rz_limits(), "target_point_xy": slot_config.target_point_xy().tolist()}
            transition_result = compute_transition_tcp(
                grab_pose=grab_pose,
                toe_arc_xyz_list=toe_arc_xyz_list,
                slot_z_min=slot_z_min,
                shoe_origin_z_min=self.grab_ground_z,
                rx=float(slot_up_pose[3]),
                ry=float(slot_up_pose[4]),
                toe_z_offset=float(slot_config.toe_z_offset),
                xy_rz_limits=xy_rz_limits,
                toe_point=shoe_data.get("toe_xyz"),
            )
            slot_align_start_pose = [
                round(float(v), 4) for v in transition_result.transition_tcp.tolist()
            ]
            toe_point_in_tcp = None
            if transition_result.toe_point_in_tcp is not None:
                toe_point_in_tcp = [
                    round(float(v), 4) for v in transition_result.toe_point_in_tcp.tolist()
                ]
            self.log.info(f"[投放流程] {side}鞋过渡 TCP: {slot_align_start_pose}")
            self._set_shoe_data_fields(
                slot_align_start_pose=slot_align_start_pose,
                toe_point_in_tcp=toe_point_in_tcp,
            )
            self.get_slot_align_end_pose(side)

        except Exception as exc:
            self.log.error(f"[投放流程] 计算{side}鞋过渡 TCP 失败: {exc}")
            self._set_shoe_data_fields(
                slot_align_start_pose=[],
                toe_point_in_tcp=None,
            )

        vis_frame = shoe_data.get("vis_frame")
        if vis_frame is not None:
            try:
                save_dir = WORKSPACE_ROOT / "shoe_align_imgs"
                save_dir.mkdir(parents=True, exist_ok=True)
                now = datetime.now()
                fname = f"seg_{now.strftime('%m%d%H%M%S')}{now.microsecond // 1000:03d}.jpg"
                save_path = save_dir / fname
                if cv2.imwrite(str(save_path), vis_frame):
                    self.log.info(f"[投放流程] 分割可视化图已保存: {save_path}")
                else:
                    self.log.warning(f"[投放流程] 分割可视化图保存失败: {save_path}")
            except Exception as exc:
                self.log.warning(f"[投放流程] 保存分割可视化图异常: {exc}")

        return self._get_shoe_data_value("slot_align_start_pose") or []

    def get_slot_align_end_pose(self, side: str) -> list[float]:
        """将 slot_align_start_pose 沿槽内前进方向移动 align_forward_move_distance，计算对位结束 TCP 位姿。"""
        side = str(side).strip().lower()
        if side not in SLOT_CONFIG_BY_SIDE:
            self.log.error(f"[投放流程] 未知鞋侧: {side}")
            return []

        slot_config_path = SLOT_CONFIG_BY_SIDE[side]
        if not slot_config_path.exists():
            self.log.error(f"[投放流程] 槽配置文件不存在: {slot_config_path}")
            return []

        shoe_data = None
        for attempt in range(20):
            shoe_data = self._get_cur_handle_shoe_data()
            if shoe_data:
                break
            self.log.debug(f"[投放流程] 等待鞋子数据，0.1秒 ({attempt + 1}/20)")
            time.sleep(0.1)
        else:
            self.log.error("[投放流程] 等待鞋子数据超时，无法计算对位结束 TCP")
            return []

        slot_align_start_pose = shoe_data.get("slot_align_start_pose")
        if not slot_align_start_pose or len(slot_align_start_pose) < 6:
            self.log.error("[投放流程] slot_align_start_pose 无效，无法计算对位结束 TCP")
            return []

        try:
            from shoe_seg.slot_config import SlotConfig
            from shoe_align.shoe_allign_controller import forward_direction_from_slot_config

            slot_config = SlotConfig.load_yaml(slot_config_path)
            dir_x, dir_y = forward_direction_from_slot_config(slot_config)
            distance = float(slot_config.align_forward_move_distance)
            slot_align_end_pose = [
                round(float(slot_align_start_pose[0]) + dir_x * distance, 4),
                round(float(slot_align_start_pose[1]) + dir_y * distance, 4),
                round(float(slot_align_start_pose[2]), 4),
                round(float(slot_align_start_pose[3]), 4),
                round(float(slot_align_start_pose[4]), 4),
                round(float(slot_align_start_pose[5]), 4),
            ]
            self.log.info(
                f"[投放流程] {side}鞋对位终点TCP: {slot_align_end_pose} "
                f"(forward={distance} mm, dir=({dir_x:.4f}, {dir_y:.4f}))"
            )
            self._set_shoe_data_fields(slot_align_end_pose=slot_align_end_pose)
            return slot_align_end_pose
        except Exception as exc:
            self.log.error(f"[投放流程] 计算{side}鞋对位结束 TCP 失败: {exc}")
            self._set_shoe_data_fields(slot_align_end_pose=[])
            return []

    def check_slot_has_shoe(self, side: str, camera: Any) -> Any | None:
        """检测指定侧鞋槽内是否有鞋。

        Args:
            side: ``"left"`` 或 ``"right"``
            camera: 对应侧槽位相机（需实现 ``get_one_frame``）

        Returns:
            ``SlotCheckResult``；``class_id`` 为 0 表示没鞋，1 表示有鞋。
            参数无效或检测失败时返回 ``None``。
        """
        side = str(side).strip().lower()
        if side not in SLOT_CONFIG_BY_SIDE:
            self.log.error(f"[投放流程] 未知鞋侧: {side}")
            return None

        side_label = "左" if side == "left" else "右"
        if camera is None:
            self.log.error(f"[投放流程] {side_label}槽鞋检测相机为空")
            return None

        from slot_check_dect import CLASS_HAS_SHOE, grab_color_frame

        try:
            image_bgr = grab_color_frame(
                camera,
                max_retries=self.slot_checker.max_retries,
                draw_depth_colormap=self.slot_checker.draw_depth_colormap,
            )
            result = self.slot_checker.classify(image_bgr)
            has_shoe = result.class_id == CLASS_HAS_SHOE
            self.log.info(
                f"[投放流程] {side_label}槽鞋检测: has_shoe={has_shoe}, "
                f"class_id={result.class_id}, confidence={result.confidence:.3f}"
            )
            return result
        except Exception as exc:
            self.log.error(f"[投放流程] {side_label}槽鞋检测失败: {exc}")
            return None

    def _run_shoe_toe_align(self, side: str) -> int:
        # return -1
        """使用槽位相机与 ShoeAlignController 将鞋头伺服对位至槽底。"""
        side = str(side).strip().lower()
        if side not in SLOT_CONFIG_BY_SIDE:
            self.log.error(f"[投放流程] 未知鞋侧: {side}")
            return -1
        side_label = "左" if side == "left" else "右"

        if self.machine is None:
            self.log.error(f"[投放流程] machine 未配置，无法进行{side_label}槽鞋头对位")
            return -1

        from shoe_seg.slot_config import SlotConfig
        # from shoe_align.shoe_allign_controller import (
        from shoe_align.shoe_allign_controller_twice import (
            ShoeAlignController,
            forward_direction_from_slot_config,
        )
        try:
            from pyorbbecsdk import Context, OBLogLevel
            Context.set_logger_to_console(OBLogLevel.NONE)
        except Exception:
            pass

        slot_config_path = SLOT_CONFIG_BY_SIDE[side]
        slot_config = SlotConfig.load_yaml(slot_config_path)
        if slot_config.camera is None:
            self.log.error(f"[投放流程] {side_label}槽配置缺少 camera 字段")
            return -1

        try:
            dir_x, dir_y = forward_direction_from_slot_config(slot_config)
        except ValueError as exc:
            self.log.error(f"[投放流程] {side_label}槽方向计算失败: {exc}")
            return -1

        cam_name = slot_config.camera.name
        cam_sn = slot_config.camera.sn
        camera = self.machine.hardwareModule.orbbec_camera_dict.get(cam_name)
        if camera is None:
            self.machine.hardwareModule.activate_orbbec_camera(cam_name, cam_sn)
            camera = self.machine.hardwareModule.orbbec_camera_dict.get(cam_name)
            if camera is None:
                self.log.error(f"[投放流程] {side_label}相机初始化失败: name={cam_name}, sn={cam_sn}")
                return -1
            if not camera.connect_camera(slot_config.camera.rgb_resolution, slot_config.camera.depth_resolution, slot_config.camera.fps):
                self.log.error(f"[投放流程] {side_label}相机连接失败: name={cam_name}, sn={cam_sn}")
                return -1

        model_path = self.shoe_align_model_path
        if slot_config.model_path:
            model_path = self._resolve_workspace_path(slot_config.model_path)
        if model_path is None or not model_path.exists():
            self.log.error(f"[投放流程] 鞋头对位模型不存在: {model_path}")
            return -1

        slot_roi = slot_config.slot_roi
        if slot_roi is not None:
            self.log.info(f"[投放流程] {side_label}槽鞋头对位 ROI: {slot_roi}")

        controller = ShoeAlignController(
            arm=self.robot_arm,
            camera=camera,
            model_path=model_path,
            dir_x=dir_x,
            dir_y=dir_y,
            imgsz=self.shoe_align_imgsz,
            step_size=0.9*self.toe_align_step,
            cmd_t=0.008,
            log=self.log,
            fps=15,
            roi=slot_roi,
        )
        self.log.debug(f"[投放流程] 开始将鞋头移动至{side_label}槽底部")
        controller.start_align(save_images=True, save_dir=WORKSPACE_ROOT/"shoe_align_imgs", snapshot_interval_s=0.05)
        if controller.arm_error != 0:
            self.log.error(f"[投放流程] {side_label}槽鞋头对位失败: {controller.arm_error}")
            return -1
        return 0

    def _get_place_pose(self, type, place_up_pose: list):
        place_pose = place_up_pose.copy()
        grab_z = self.grab_z_min
        grab_pose = self._get_shoe_data_value("grab_pose")
        if grab_pose and len(grab_pose) >= 3:
            grab_z = float(grab_pose[2])
        place_height = grab_z - self.grab_ground_z + self.place_dh  # 抓取高度，正值
        if type == "left":
            place_pose[2] = self.place_z_left + place_height
        elif type == "right":
            place_pose[2] = self.place_z_right + place_height
        return place_pose

    def _get_slot_rotate_dh(self, side: str) -> float:
        """从槽位 YAML 读取 rotate_dh，用于绕鞋头旋转 pivot 高度。"""
        side = str(side).strip().lower()
        slot_config_path = SLOT_CONFIG_BY_SIDE.get(side)
        if slot_config_path is None or not slot_config_path.exists():
            return 20.0
        try:
            from shoe_seg.slot_config import SlotConfig

            slot_config = SlotConfig.load_yaml(slot_config_path)
            return float(slot_config.rotate_dh)
        except Exception as exc:
            self.log.warning(f"[投放流程] 读取{side}槽 rotate_dh 失败，使用默认值: {exc}")
        return 20.0

    def _correct_pose_xy_on_gripper_line(
        self,
        pose: list[float] | np.ndarray,
        side: str,
    ) -> list[float]:
        """将位姿 XY 修正到示教夹爪两点所在直线上的投影点，Z 与姿态保持不变。"""
        pose_arr = [float(v) for v in pose]
        if len(pose_arr) < 2:
            raise ValueError("pose 至少需要包含 x, y")

        side = str(side).strip().lower()
        slot_config_path = SLOT_CONFIG_BY_SIDE.get(side)
        if slot_config_path is None or not slot_config_path.exists():
            raise ValueError(f"槽配置不可用: side={side!r}, path={slot_config_path}")

        from shoe_seg.slot_config import SlotConfig, project_xy_onto_line

        slot_config = SlotConfig.load_yaml(slot_config_path)
        proj_x, proj_y, t, signed_dist = project_xy_onto_line(
            pose_arr[0], pose_arr[1], slot_config
        )
        corrected = pose_arr.copy()
        corrected[0] = round(proj_x, 4)
        corrected[1] = round(proj_y, 4)
        self.log.debug(
            f"[投放流程] {side}槽 XY 投影修正: "
            f"({pose_arr[0]:.4f}, {pose_arr[1]:.4f}) -> ({corrected[0]:.4f}, {corrected[1]:.4f}), "
            f"t={t:.4f}, dist={signed_dist:.4f}"
        )
        return corrected

    def _plan_tcp_rotate_about_pivot(
        self,
        pivot_point: list[float],
        target_rx: float,
        target_ry: float,
        side: str,
        *,
        current_pose: list[float] | np.ndarray | None = None,
        steps: int = 10,
        final_z: float | None = None,
        final_rz: float | None = None,
    ) -> tuple[np.ndarray | None, list[list[float]] | None]:
        """规划绕 pivot 旋转到目标 rx/ry 的路径，返回 target_pose 与 segmented_poses。

        current_pose 未传入时从机械臂读取当前 TCP 位姿。
        final_z 不为 None 时，在路径末尾追加一步 Z 下降。
        final_rz 不为 None 时，路径最后一个点的 rz 设为该固定值。
        """
        from shoe_align.rotate_tcp_about_point import TcpRotateAboutPivot

        if current_pose is None:
            ret, current_pose = self.robot_arm.GetActualTCPPose()
            if ret != 0:
                self.log.error(f"[投放流程] 读取当前 TCP 位姿失败: {ret}")
                return None, None

        planner = TcpRotateAboutPivot(
            pivot_point=np.asarray(pivot_point, dtype=float),
            target_rx=float(target_rx),
            target_ry=float(target_ry),
            target_rz=None,
            steps=int(steps),
            log=self.log,
        )
        target_pose, segmented_poses = planner.plan(np.asarray(current_pose, dtype=float))
        self.log.info(
            f"[投放流程] TCP绕点旋转: pivot={[round(v, 3) for v in pivot_point]}, "
            f"target_rx={target_rx}, target_ry={target_ry}, steps={steps}"
        )
        self.log.debug(f"[投放流程] TCP绕点旋转目标位姿: {planner.format_pose(target_pose)}")
        current_pose_arr = np.asarray(current_pose, dtype=float)
        if final_rz is not None:
            end_rx = float(segmented_poses[-1][3])
            end_ry = float(segmented_poses[-1][4])
            last_pose = planner.compute_target_pose(
                current_pose_arr,
                target_rx=end_rx,
                target_ry=end_ry,
                target_rz=float(final_rz),
            )
            if final_z is not None:
                last_pose[2] = float(final_z)
                segmented_poses.append(last_pose.tolist())
            else:
                segmented_poses[-1] = last_pose.tolist()
        elif final_z is not None:
            last_pose = segmented_poses[-1].copy()
            last_pose[2] = float(final_z)
            segmented_poses.append(last_pose)
        if final_rz is not None or final_z is not None:
            self.log.debug(
                f"[投放流程] TCP绕点旋转末点: {planner.format_pose(np.asarray(segmented_poses[-1], dtype=float))}"
            )
        segmented_end_pose = self._correct_pose_xy_on_gripper_line(segmented_poses[-1], side)
        segmented_poses[-1] = segmented_end_pose
        return target_pose, segmented_poses

    def _rotate_tcp_about_pivot(
        self,
        pivot_point: list[float],
        target_rx: float,
        target_ry: float,
        side: str,
        *,
        steps: int = 20,
        final_z: float | None = None,
        final_rz: float | None = None,
        speed_mm_s: float = 50.0,
    ) -> int:
        """TCP 绕基坐标固定点旋转到目标 rx/ry，可选追加一步 Z 下降。"""
        self.log.debug(f"[投放流程] TCP绕点旋转: pivot_point={pivot_point}, target_rx={target_rx}, target_ry={target_ry}, side={side}, steps={steps}, final_z={final_z}, final_rz={final_rz}, speed_mm_s={speed_mm_s}")
        target_pose, segmented_poses = self._plan_tcp_rotate_about_pivot(
            pivot_point, target_rx, target_ry, side, steps=steps, final_z=final_z, final_rz=final_rz
        )
        if target_pose is None:
            return -1
        ret = self.robot_arm.RobotServoSpline(
            points=segmented_poses,
            speed_mm_s=self.spline_speed,
            max_ori_step_deg=0.2,
            tool=self.tool,
            user=self.user,
        )
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}] TCP绕点旋转样条运动失败，返回值: {ret}")
        return ret

    def _get_press_lever_move_distance(self, shoe_type=None):
        """计算压杆需要移动的距离。"""
        if self.press_vision is None:
            return 0.0
        if shoe_type == "left":
            self.log.info("[投放流程] 获取左鞋压杆移动距离")
            id = 1
        elif shoe_type == "right":
            self.log.info("[投放流程] 获取右鞋压杆移动距离")
            id = 2
        find_flag, distance_m, detect_result_image = self.press_vision.get_rod_distance(id)
        if not find_flag or distance_m is None:
            self.log.warning(f"[投放流程] 视觉未找到{id}号鞋子，使用默认压杆不移动")
            return 0.0
        distance = int(abs(distance_m * 1000.0))  # 转换为毫米并取绝对值
        self.log.info(f"[投放流程] 视觉测量{id}号鞋子压杆移动距离: {distance}mm")
        return distance
    
    
    def calculate_shoes_point(self, point_4d):
        """根据输入的4D位姿计算机械臂需要移动到的抓取位姿。"""
        if not point_4d or len(point_4d) < 4:
            self.log.error("[投放流程] 计算抓取点失败，point_4d 长度不足 4")
            return []
        if not self.initial_pose or len(self.initial_pose) < 6:
            self.log.error("[投放流程] 计算抓取点失败，initial_pose 未正确配置")
            return []

        x, y, z = point_4d[:3]
        #TODO: 这里可以根据需要添加dx, dy的修正
        x += self.dx
        y += self.dy
        z = max(self.grab_z_min, z - self.gripper_down_offset) # 抓取点高度不能低于抓取最低高度
        yaw_deg = point_4d[3]

        rx = self.initial_pose[3]
        ry = self.initial_pose[4]
        rz = yaw_deg - self.grab_offset_deg
        while rz < -180.0:
            rz += 360.0
        while rz > 180.0:
            rz -= 360.0
        return [x, y, z, rx, ry, rz]

    def handle_grab_pending_shoes(self, shoe_point):
        with self._put_arm_lock:
            if FAKE_ARM:
                return 0
            ret = self.gripper.open_claw()
            if not ret:
                self.log.error(f"[{self.robot_arm.name}]夹爪打开失败")
                return -1
            self.log.debug(f"[投放流程]夹爪打开完成")
            shoe_point_up = shoe_point.copy()
            shoe_point_up[2] = shoe_point[2] + self.gripper_down_offset + 70.0  # 抓取点上方
            ret = self.robot_arm.RobotServoSpline(
                points=[self.initial_pose, shoe_point_up, shoe_point],
                speed_mm_s=self.spline_speed,
                max_ori_step_deg=0.4, #抓鞋旋转的速度
                tool=self.tool,
                user=self.user,
            )
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}]机械臂移动到抓取点失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.GRAB_SHOE_POINT
            self.log.debug(f"[投放流程]移动到抓取点完成")
            g_ret = self.gripper.close_claw()
            if g_ret:
                self.log.debug(f"[投放流程]夹爪关闭完成")
            else:
                self.log.error(f"[投放流程]夹爪关闭失败，返回值: {g_ret}")
                return -1

            shoe_point_j = self.robot_arm.GetActualJointPosDegree()[1]

            wait_pose = self.left_wait_pose if self.arm_type == "left" else self.right_wait_pose
            wait_pose_j = self.left_wait_pose_j if self.arm_type == "left" else self.right_wait_pose_j
            self.log.debug(f"[投放流程] 从抓取点平滑返回等待位")
            if self.move_grab_trans:
                points = [shoe_point, shoe_point_up]
                for p in self.move_grab_trans:
                    points.append(p)
                points.append(self.grab_transition)
                ret = self.robot_arm.RobotServoSpline(points=points, speed_mm_s=self.spline_speed, max_ori_step_deg=0.3, tool=self.tool, user=self.user)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}] 移动到等待位样条运动失败，返回值: {ret}")
                    return ret
            # else:
                # ret = self.robot_arm.MoveL(desc_pos=shoe_point_up, tool=self.tool, user=self.user, vel=self.speed)
                # if ret != 0:
                #     self.log.error(f"[{self.robot_arm.name}]机械臂移动到抓取点上方失败，返回值: {ret}")
                #     return ret
                # ret = self.robot_arm.MoveJ(joint_pos=self.grab_transition_j, desc_pos=self.grab_transition, tool=self.tool, user=self.user, vel=self.speed, ovl=100.0, blendT=-1.0)
                # if ret != 0:
                #     self.log.error(f"[{self.robot_arm.name}]机械臂移动到初始位失败，返回值: {ret}")
                #     return ret
                ret = self.robot_arm.MoveJ(joint_pos=wait_pose_j, desc_pos=wait_pose, tool=self.tool, user=self.user, vel=self.speed*0.5, ovl=80.0, blendT=-1.0)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到等待位失败，返回值: {ret}")
                    return ret
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE if self.arm_type == "left" else PutArmPose.RIGHT_WAIT_POSE
            return 0  # 0 表示成功

    def handle_place_left_shoe(self, vision=None):
        # if self.slot_checker is None:
        #     self.log.error("[投放流程] 槽检查器未配置")
        #     return -1
        # if self.slot_camera_by_side["left"] is None:
        #     self.log.error("[投放流程] 槽相机未配置")
        #     return -1
        # camera_name = self.slot_camera_by_side["left"].name
        # camera = self.machine.hardwareModule.orbbec_camera_dict.get(camera_name)
        # if camera:
        #     if self.slot_checker.is_shoe_in_slot(camera) != 0:
        #         self.log.warning("[投放流程] 左槽内有鞋子，无法放置")
        #         return -1
        with self._put_arm_lock:
            return self._handle_place_left_shoe_unlocked(vision)

    def _handle_place_left_shoe_unlocked(self, vision=None):
        self.log.debug("[投放流程] 放置左鞋")
        if FAKE_ARM:
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
            return 0
        if not self.left_wait_pose:
            self.log.error("[投放流程] 左等待位未配置")
            return -1
        if self.arm_type == "right":
            if not self.right_wait_pose:
                self.log.error("[投放流程] 右等待位未配置")
                return -1
            ret = self._right_to_left_wait_pose_unlocked()
            if ret != 0:
                return ret
            self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
        ret = self.robot_arm.MoveJ(joint_pos=self.left_wait_pose_j, desc_pos=self.left_wait_pose, tool=self.tool, user=self.user, vel=self.speed, ovl=100.0, blendT=-1.0)
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}]机械臂移动到左等待位失败，返回值: {ret}")
            return ret
        self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
        move_in_left = None
        slot_align_start_pose = None
        if put_modify:
            # 先按原路径从左等待位靠近放置点
            self.log.debug(f"[投放流程] 左等待位移动到左槽对位点")
            slot_align_start_pose = self._wait_for_shoe_data_field(
                "slot_align_start_pose",
                log_label="左槽对位点",
            )
            if not slot_align_start_pose:
                self.log.error("[投放流程] 左槽对位点计算超时")
                return -1
            self.log.debug(f"[投放流程] 左槽对位点: {slot_align_start_pose}")
            slot_align_start_pose_up = slot_align_start_pose.copy()
            slot_align_start_pose_up[2] += 10
            move_to_left_slot_up = self.move_to_left_slot_up.copy()
            move_to_left_slot_up[-1] = slot_align_start_pose_up
            move_to_left_slot_up.append(slot_align_start_pose)

            if perception_lead:
                ret = self.robot_arm.RobotServoSpline(points=move_to_left_slot_up, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到左槽上方对位点失败，返回值: {ret}")
                    return ret
                if first_align:
                    self.log.debug(f"[投放流程] 开始将鞋头移动至左槽底部")
                    start_time = time.time()
                    self.log.debug(f"[投放流程] 鞋头对位开始{start_time}")
                    ret = self._run_shoe_toe_align("left")
                    self.log.debug(f"[投放流程] 鞋头对位结束{time.time()}，左槽鞋头对位耗时: {time.time() - start_time}秒")
                    if ret != 0:
                        return ret
                # 根据当前 TCP 位姿和 tcp-鞋头刚性关系，计算当前鞋头基坐标
                toe_point_in_tcp = self._get_shoe_data_value("toe_point_in_tcp")
                if not toe_point_in_tcp:
                    self.log.error("[投放流程] toe_point_in_tcp 为空，无法绕鞋头旋转")
                    return -1

                cur_left_pose = self.robot_arm.GetActualTCPPose()[1]
                R_tcp = pose_to_matrix(cur_left_pose)[:3, :3]
                tcp_xyz = np.asarray(cur_left_pose[:3], dtype=float)
                cur_toe_point = (
                    R_tcp @ np.asarray(toe_point_in_tcp, dtype=float) + tcp_xyz
                ).tolist()
                self.log.debug(f"[投放流程] 当前鞋头位置: {cur_toe_point}")
                pivot_point = [
                    cur_toe_point[0],
                    cur_toe_point[1],
                    self.place_z_left + self._get_slot_rotate_dh("left"),
                ]
                final_z = self._get_place_pose("left", cur_left_pose)[2]
                ret = self._rotate_tcp_about_pivot(
                    pivot_point=pivot_point,
                    target_rx=float(self.initial_pose[3]),
                    target_ry=float(self.initial_pose[4]),
                    side="left",
                    final_z=final_z,
                    final_rz=self.left_place_rz,
                )
                if ret != 0:
                    self.log.error(f"[投放流程] 左鞋回正下落失败{ret}")
                    return ret
                if second_align:
                    self.log.debug(f"[投放流程] 开始第二次将鞋头移动至左槽底部")
                    self.log.debug(f"[投放流程] 鞋头对位开始")
                    ret = self._run_shoe_toe_align("left")
                    self.log.debug(f"[投放流程] 鞋头对位结束")
                    if ret != 0:
                        return ret
            else:
                slot_align_end_pose = self._wait_for_shoe_data_field(
                    "slot_align_end_pose",
                    log_label="左槽对位终点",
                )
                if not slot_align_end_pose:
                    self.log.error("[投放流程] 左槽对位终点计算超时")
                    return -1
                self.log.debug(f"[投放流程] 左槽对位终点: {slot_align_end_pose}")

                move_to_left_slot_up.append(slot_align_end_pose)
                ret = self.robot_arm.RobotServoSpline(
                    points=move_to_left_slot_up,
                    speed_mm_s=self.spline_speed,
                    max_ori_step_deg=0.6,
                    tool=self.tool,
                    user=self.user,
                )
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到左槽对位终点失败，返回值: {ret}")
                    return ret
                # 计算对位终点鞋头位置，并旋转下落至放置点
                toe_point_in_tcp = self._get_shoe_data_value("toe_point_in_tcp")
                if not toe_point_in_tcp:
                    self.log.error("[投放流程] toe_point_in_tcp 为空，无法绕鞋头旋转")
                    return -1
                slot_align_end_tcp_pose = slot_align_end_pose
                R_tcp = pose_to_matrix(slot_align_end_tcp_pose)[:3, :3]
                tcp_xyz = np.asarray(slot_align_end_tcp_pose[:3], dtype=float)
                toe_point_end = (
                    R_tcp @ np.asarray(toe_point_in_tcp, dtype=float) + tcp_xyz
                ).tolist()
                self._set_shoe_data_fields(toe_point_end=toe_point_end)
                self.log.debug(f"[投放流程] 对位终点鞋头位置: {toe_point_end}")

                pivot_point = [
                    toe_point_end[0],
                    toe_point_end[1],
                    self.place_z_left + self._get_slot_rotate_dh("left"),
                ]
                final_z = self._get_place_pose("left", slot_align_end_tcp_pose)[2]
                place_pose, segmented_poses = self._plan_tcp_rotate_about_pivot(
                    pivot_point=pivot_point,
                    target_rx=float(self.initial_pose[3]),
                    target_ry=float(self.initial_pose[4]),
                    side="left",
                    current_pose=slot_align_end_tcp_pose,
                    final_z=final_z,
                    final_rz=self.left_place_rz,
                )
                if place_pose is None:
                    return -1
                ret = self.robot_arm.RobotServoSpline(points=segmented_poses, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂旋转下落失败，返回值: {ret}")
                    return ret
        else:
            move_in_left = self.move_to_left_slot_up.copy()
            left_place_up_pose = self.left_slot_up
            left_place_pose = self._get_place_pose("left", left_place_up_pose)
            move_in_left.append(left_place_pose)
            ret = self.robot_arm.RobotServoSpline(points=move_in_left, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}]机械臂移动到左槽放置点失败，返回值: {ret}")
                return ret
        # 张开夹爪并记录左放置位的关节角
        ret = self.gripper.open_claw()
        if not ret:
            self.log.error(f"[{self.robot_arm.name}]夹爪打开失败")
            return -1
        shoe_data = self._get_cur_handle_shoe_data()
        if shoe_data:
            self._update_slot_grab_height(shoe_data)
        # time.sleep(0.5)  # 等待夹爪完全张开
        if put_modify:
            left_place_pose_j = self.robot_arm.GetActualJointPosDegree()[1]
            left_place_pose = self.robot_arm.GetActualTCPPose()[1]
            left_place_pose_up = left_place_pose.copy()
            left_place_pose_up[2] += 5

            self.log.debug(f"[投放流程] 左鞋放置点: {left_place_pose}, 关节角: {left_place_pose_j}")
            # 使用样条运动从放置点平滑返回等待位
            left_wait_pose = self.left_wait_pose
            left_wait_pose_j = self.left_wait_pose_j
            if not left_wait_pose or not left_wait_pose_j:
                self.log.error("[投放流程] 左等待位或其关节角未配置，无法执行样条回退")
                return -1
            points = [left_place_pose, left_place_pose_up, slot_align_start_pose]
            for p, j in zip(self.move_out_left, self.move_out_left_j):
                points.append(p)
            ret = self.robot_arm.RobotServoSpline(points=points, speed_mm_s=self.spline_speed, max_ori_step_deg=0.3, tool=self.tool, user=self.user)
        else:
            move_out_left = list(reversed(move_in_left))
            ret = self.robot_arm.RobotServoSpline(points=move_out_left, speed_mm_s=self.spline_speed, max_ori_step_deg=0.3, tool=self.tool, user=self.user)
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}] 退出左鞋槽样条运动失败，返回值: {ret}")
            return ret
        self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
        return 0

    def handle_set_press_machine(self, shoe_type, press_machine_manager: PressMachineManager, vision=None, on_press_finished=None):
        def _do_press():
            if shoe_type == "left":
                ret = press_machine_manager.left_lever_ready()
                if ret != 0:
                    self.log.error(f"[投放流程] 压机左压杆准备失败，返回值: {ret}")
                    return -1
                lever_move_distance = self._get_press_lever_move_distance(shoe_type)
                self.log.info(f"[投放流程] 压机{shoe_type}杠杆移动距离: {lever_move_distance}")
                press_machine_manager.move_left_lever(lever_move_distance)
            else:
                ret = press_machine_manager.right_lever_ready()
                if ret != 0:
                    self.log.error(f"[投放流程] 压机右压杆准备失败，返回值: {ret}")
                    return -1
                lever_move_distance = self._get_press_lever_move_distance(shoe_type)
                self.log.info(f"[投放流程] 压机{shoe_type}杠杆移动距离: {lever_move_distance}")
                press_machine_manager.move_right_lever(lever_move_distance)
            self.log.info(f"[投放流程] 请求压机压鞋: {shoe_type}")
            press_machine_manager.press_shoe(shoe_type, on_finished=on_press_finished)

        threading.Thread(target=_do_press, name=f"Press-{shoe_type}", daemon=True).start()
        return 0

    def handle_place_right_shoe(self, vision=None):
        # if self.slot_checker is None:
        #     self.log.error("[投放流程] 槽检查器未配置")
        #     return -1
        # if self.slot_camera_by_side["right"] is None:
        #     self.log.error("[投放流程] 槽相机未配置")
        #     return -1
        # camera_name = self.slot_camera_by_side["right"].name
        # camera = self.machine.hardwareModule.orbbec_camera_dict.get(camera_name)
        # if camera:
        #     if self.slot_checker.is_shoe_in_slot(camera) != 0:
        #         self.log.warning("[投放流程] 右槽内有鞋子，无法放置")
        #         return -1
        with self._put_arm_lock:
            return self._handle_place_right_shoe_unlocked(vision)

    def _handle_place_right_shoe_unlocked(self, vision=None):
        self.log.debug("[投放流程] 放置右鞋")
        if FAKE_ARM:
            self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
            return 0
        if not self.right_wait_pose:
            self.log.error("[投放流程] 右等待位未配置")
            return -1
        if self.arm_type == "left":
            if not self.left_wait_pose:
                self.log.error("[投放流程] 左等待位未配置")
                return -1
            ret = self._left_to_right_wait_pose_unlocked()
            if ret != 0:
                return ret
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
        ret = self.robot_arm.MoveJ(joint_pos=self.right_wait_pose_j, desc_pos=self.right_wait_pose, tool=self.tool, user=self.user, vel=self.speed, ovl=100.0, blendT=-1.0)
        if ret != 0:
            self.log.error(f"[{self.robot_arm.name}]机械臂移动到右等待位失败，返回值: {ret}")
            return ret
        self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
        self.log.debug(f"[投放流程] 右等待位移动到右槽对位点")
        move_in_right = None
        slot_align_start_pose = None
        slot_align_start_pose_j = None
        if put_modify:
            slot_align_start_pose = self._wait_for_shoe_data_field(
                "slot_align_start_pose",
                log_label="右槽对位点",
            )
            if not slot_align_start_pose:
                self.log.error("[投放流程] 右槽对位点计算超时")
                return -1
            self.log.debug(f"[投放流程] 右槽对位点: {slot_align_start_pose}")
            slot_align_start_pose_up = slot_align_start_pose.copy()
            slot_align_start_pose_up[2] += 15
            move_to_right_slot_up = self.move_to_right_slot_up.copy()
            move_to_right_slot_up[-1] = slot_align_start_pose_up
            move_to_right_slot_up.append(slot_align_start_pose)
            if perception_lead:
                ret = self.robot_arm.RobotServoSpline(points=move_to_right_slot_up, speed_mm_s=self.spline_speed, cart_vel = 60.0, max_ori_step_deg=0.25, tool=self.tool, user=self.user)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到右槽上方对位点失败，返回值: {ret}")
                    return ret
                slot_align_start_pose_j = self.robot_arm.GetActualJointPosDegree()[1]
                if first_align:
                    self.log.debug(f"[投放流程] 开始将鞋头移动至右槽底部")
                    start_time = time.time()
                    self.log.debug(f"[投放流程] 鞋头对位开始{start_time}")
                    ret = self._run_shoe_toe_align("right")
                    self.log.debug(f"[投放流程] 鞋头对位结束{time.time()}，右槽鞋头对位耗时{time.time() - start_time:.2f}秒")
                    if ret != 0:
                        return ret

                toe_point_in_tcp = self._get_shoe_data_value("toe_point_in_tcp")
                if not toe_point_in_tcp:
                    self.log.error("[投放流程] toe_point_in_tcp 为空，无法绕鞋头旋转")
                    return -1
                cur_right_pose = self.robot_arm.GetActualTCPPose()[1]
                R_tcp = pose_to_matrix(cur_right_pose)[:3, :3]
                tcp_xyz = np.asarray(cur_right_pose[:3], dtype=float)
                cur_toe_point = (
                    R_tcp @ np.asarray(toe_point_in_tcp, dtype=float) + tcp_xyz
                ).tolist()
                self.log.debug(f"[投放流程] 当前鞋头位置: {cur_toe_point}")
                pivot_point = [
                    cur_toe_point[0],
                    cur_toe_point[1],
                    self.place_z_right + self._get_slot_rotate_dh("right"),
                ]
                final_z = self._get_place_pose("right", cur_right_pose)[2]
                ret = self._rotate_tcp_about_pivot(
                    pivot_point=pivot_point,
                    target_rx=float(self.initial_pose[3]),
                    target_ry=float(self.initial_pose[4]),
                    side="right",
                    final_z=final_z,
                    final_rz=self.right_place_rz,
                )
                if ret != 0:
                    self.log.error(f"[投放流程] 右鞋回正下落失败{ret}")
                    return retleft_place_rz

                if second_align:
                    self.log.debug(f"[投放流程] 开始第二次将鞋头移动至右槽底部")
                    self.log.debug(f"[投放流程] 鞋头对位开始{time.time()}")
                    ret = self._run_shoe_toe_align("right")
                    self.log.debug(f"[投放流程] 鞋头对位结束{time.time()}")
                    if ret != 0:
                        return ret
            else:
                slot_align_end_pose = self._wait_for_shoe_data_field(
                    "slot_align_end_pose",
                    log_label="右槽对位终点",
                )
                if not slot_align_end_pose:
                    self.log.error("[投放流程] 右槽对位终点计算超时")
                    return -1
                self.log.debug(f"[投放流程] 右槽对位终点: {slot_align_end_pose}")

                move_to_right_slot_up.append(slot_align_end_pose)
                ret = self.robot_arm.RobotServoSpline(
                    points=move_to_right_slot_up,
                    speed_mm_s=self.spline_speed,
                    max_ori_step_deg=0.6,
                    tool=self.tool,
                    user=self.user,
                )
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂移动到右槽对位终点失败，返回值: {ret}")
                    return ret
                toe_point_in_tcp = self._get_shoe_data_value("toe_point_in_tcp")
                if not toe_point_in_tcp:
                    self.log.error("[投放流程] toe_point_in_tcp 为空，无法绕鞋头旋转")
                    return -1
                slot_align_end_tcp_pose = slot_align_end_pose
                R_tcp = pose_to_matrix(slot_align_end_tcp_pose)[:3, :3]
                tcp_xyz = np.asarray(slot_align_end_tcp_pose[:3], dtype=float)
                toe_point_end = (
                    R_tcp @ np.asarray(toe_point_in_tcp, dtype=float) + tcp_xyz
                ).tolist()
                self._set_shoe_data_fields(toe_point_end=toe_point_end)
                self.log.debug(f"[投放流程] 对位终点鞋头位置: {toe_point_end}")

                pivot_point = [
                    toe_point_end[0],
                    toe_point_end[1],
                    self.place_z_right + self._get_slot_rotate_dh("right"),
                ]
                final_z = self._get_place_pose("right", slot_align_end_tcp_pose)[2]
                place_pose, segmented_poses = self._plan_tcp_rotate_about_pivot(
                    pivot_point=pivot_point,
                    target_rx=float(self.initial_pose[3]),
                    target_ry=float(self.initial_pose[4]),
                    side="right",
                    current_pose=slot_align_end_tcp_pose,
                    final_z=final_z,
                    final_rz=self.right_place_rz,
                )
                if place_pose is None:
                    return -1
                ret = self.robot_arm.RobotServoSpline(points=segmented_poses, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
                if ret != 0:
                    self.log.error(f"[{self.robot_arm.name}]机械臂旋转下落失败，返回值: {ret}")
                    return ret
        else:
            right_place_up_pose = self.right_slot_up
            right_place_pose = self._get_place_pose("right", right_place_up_pose)
            move_in_right = self.move_to_right_slot_up.copy()
            move_in_right.append(right_place_pose)
            ret = self.robot_arm.RobotServoSpline(points=move_in_right, speed_mm_s=self.spline_speed, max_ori_step_deg=0.5, tool=self.tool, user=self.user)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}]机械臂移动到右槽失败，返回值: {ret}")
                return ret

        ret = self.gripper.open_claw()
        if not ret:
            self.log.error(f"[{self.robot_arm.name}]夹爪打开失败")
            return -1
        shoe_data = self._get_cur_handle_shoe_data()
        if shoe_data:
            self._update_slot_grab_height(shoe_data)
        if put_modify:
            right_place_pose_j = self.robot_arm.GetActualJointPosDegree()[1]
            right_place_pose = self.robot_arm.GetActualTCPPose()[1]
            right_place_pose_up = right_place_pose.copy()
            right_place_pose_up[2] += 5
            self.log.debug(f"[投放流程] 右鞋放置点: {right_place_pose}, 关节角: {right_place_pose_j}")
            right_wait_pose = self.right_wait_pose
            right_wait_pose_j = self.right_wait_pose_j
            if not right_wait_pose or not right_wait_pose_j:
                self.log.error("[投放流程] 右等待位或其关节角未配置，无法执行样条回退")
                return -1
            points = [right_place_pose, right_place_pose_up, slot_align_start_pose]
            points_j = [right_place_pose_j, slot_align_start_pose_j]
            for p, j in zip(self.move_out_right, self.move_out_right_j):
                points.append(p)
                points_j.append(j)
            ret = self.robot_arm.RobotServoSpline(points=points, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
        else:
            move_out_right = list(reversed(move_in_right))
            ret = self.robot_arm.RobotServoSpline(points=move_out_right, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
        if ret!= 0:
            self.log.error(f"[{self.robot_arm.name}] 退出右鞋槽样条运动失败，返回值: {ret}")
            return ret 
        self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
        return 0

    def handle_exit_far_use_near(self):
        with self._put_arm_lock:
            self.log.debug("[投放流程] 放置后退出右槽")
            if FAKE_ARM:
                return 0
            if self.arm_type == "left":
                return self._right_to_left_wait_pose_unlocked()
            if self.arm_type == "right":
                return self._left_to_right_wait_pose_unlocked()
            return -1

    def left_to_right_wait_pose(self):
        with self._put_arm_lock:
            return self._left_to_right_wait_pose_unlocked()

    def _left_to_right_wait_pose_unlocked(self):
        self.log.debug("[投放流程] 从左等待位移动到右等待位")
        if FAKE_ARM:
            self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
            return 0
        points = self.move_left_to_right_wait_pose
        points_j = self.move_left_to_right_wait_pose_j
        if self.put_arm_pose == PutArmPose.RIGHT_WAIT_POSE: 
            self.log.debug("[投放流程] 已在右等待位，无需移动")
            return 0
        if len(points) != len(points_j):
            self.log.error("[投放流程] 左到右等待位路径点和关节点数量不匹配")
            return -1
        if len(points) < 2:
            self.log.error("[投放流程] 左到右等待位路径未配置或点数不足")
            return -1
        if len(points) > 2:
            ret = self.robot_arm.RobotServoSpline(points=points, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从左等待位移动到右等待位样条运动失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
        if len(points) == 2:
            # 只有两个点，直接MoveJ
            ret = self.robot_arm.MoveJ(joint_pos=points_j[1], desc_pos=points[1], tool=self.tool, user=self.user, vel=self.speed, ovl=90.0, blendT=-1.0)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从左等待位移动到右等待位失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.RIGHT_WAIT_POSE
        return 0

    def right_to_left_wait_pose(self):
        with self._put_arm_lock:
            return self._right_to_left_wait_pose_unlocked()

    def _right_to_left_wait_pose_unlocked(self):
        self.log.debug("[投放流程] 从右等待位移动到左等待位")
        if FAKE_ARM:
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
            return 0
        if self.put_arm_pose == PutArmPose.LEFT_WAIT_POSE: 
            self.log.debug("[投放流程] 已在左等待位，无需移动")
            return 0
        points = self.move_right_to_left_wait_pose
        points_j = self.move_right_to_left_wait_pose_j
        if len(points) != len(points_j):
            self.log.error("[投放流程] 右到左等待位路径点和关节点数量不匹配")
            return -1
        if len(points) < 2:
            self.log.error("[投放流程] 右到左等待位路径未配置")
            return -1
        if len(points) > 2:
            ret = self.robot_arm.RobotServoSpline(points=points, speed_mm_s=self.spline_speed, max_ori_step_deg=0.6, tool=self.tool, user=self.user)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从右等待位移动到左等待位样条运动失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
        if len(points) == 2:
            # 只有两个点，直接MoveJ
            ret = self.robot_arm.MoveJ(joint_pos=points_j[1], desc_pos=points[1], tool=self.tool, user=self.user, vel=self.speed, ovl=90.0, blendT=-1.0)
            if ret != 0:
                self.log.error(f"[{self.robot_arm.name}] 从右等待位移动到左等待位失败，返回值: {ret}")
                return ret
            self.put_arm_pose = PutArmPose.LEFT_WAIT_POSE
        return 0