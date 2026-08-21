"""鞋头对位测试：槽位 YAML + models/toe_align/best.pt，main 中预览 current_frame。

Usage:
    python shoe_align/shoe_allign_controller_test.py [--slot-config press_shoes/config/left_slot.yaml]
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from automation_machine import automationMachine
from rotate_tcp_about_point import TcpRotateAboutPivot
CMDT = 0.8  # ServoCart command period in seconds (e.g. 80ms for ~12.5Hz)
STEP_SIZE = 0.3  # ServoCart movement step size in mm
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHOE_ALIGN_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "press_shoes" / "config"
DEFAULT_SLOT_CONFIG = CONFIG_DIR / "left_slot.yaml"
DEFAULT_TOE_ALIGN_MODEL = PROJECT_ROOT / "models/toe_align/0626_s256_xylast.pt"
DISPLAY_WINDOW = "toe_align"
SNAPSHOT_INTERVAL_S = 1
DEFAULT_SNAPSHOT_DIR = Path("/home/casbot/robot/Casbot_Press_Shoes/shoe_align_test_img/")
# from Casbot_Press_Shoes.shoe_align.ImgAct.models.yolo.model import YOLO
DEFAULT_ROBOT_IP = "fake"
DEFAULT_ARM_KEY = "arm"
ROTATE_TCP = False
for import_root in (PROJECT_ROOT, SHOE_ALIGN_ROOT):
    if import_root.exists() and str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

DEFAULT_PIVOT = [-747.38, -825.194, 15]
DEFAULT_STEPS = 20
DEFAULT_TARGET_RX = 0.5
DEFAULT_TARGET_RY = 1
from rotate_tcp_about_point import (
    MOVE_CART_ACC,
    MOVE_CART_BLEND_T,
    MOVE_CART_OVL,
    MOVE_CART_TOOL,
    MOVE_CART_USER,
    MOVE_CART_VEL,
)
@dataclass(slots=True)
class ArmConfig:
    key_name: str = DEFAULT_ARM_KEY
    name: str = DEFAULT_ARM_KEY
    robot_ip: str = DEFAULT_ROBOT_IP


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live YOLO Classification with ServoCart Robot Control.")
    parser.add_argument(
        "--model",
        type=Path,
        default=DEFAULT_TOE_ALIGN_MODEL,
        help=f"鞋头对位分类模型 (default: {DEFAULT_TOE_ALIGN_MODEL})",
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default=None)
    parser.add_argument("--robot-ip", default=DEFAULT_ROBOT_IP)
    parser.add_argument("--arm-key", default=DEFAULT_ARM_KEY)
    parser.add_argument(
        "--slot-config",
        type=Path,
        default=DEFAULT_SLOT_CONFIG,
        help="Path to left/right slot yaml (default: press_shoes/config/left_slot.yaml)",
    )
    return parser.parse_args()


def fast_classify_preprocess(frame_bgr: np.ndarray, imgsz: int) -> torch.Tensor:
    """OpenCV 版 classify Resize+CenterCrop+ToTensor，与 PIL 路径等价且快约 8 倍。"""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    if h < w:
        new_h, new_w = imgsz, int(round(w * imgsz / h))
    else:
        new_w, new_h = imgsz, int(round(h * imgsz / w))
    rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    h, w = rgb.shape[:2]
    y0, x0 = max(0, (h - imgsz) // 2), max(0, (w - imgsz) // 2)
    rgb = rgb[y0 : y0 + imgsz, x0 : x0 + imgsz]
    return torch.from_numpy(rgb).permute(2, 0, 1).contiguous().float().div_(255.0).unsqueeze(0)


def crop_frame_to_roi(frame: np.ndarray, roi: list[list[int]]) -> np.ndarray:
    """按 [[x1, y1], [x2, y2]] 像素坐标裁剪图像。"""
    if len(roi) < 2:
        raise ValueError("roi must contain at least 2 points")
    x1, y1 = int(roi[0][0]), int(roi[0][1])
    x2, y2 = int(roi[1][0]), int(roi[1][1])
    x_min, x_max = min(x1, x2), max(x1, x2)
    y_min, y_max = min(y1, y2), max(y1, y2)
    height, width = frame.shape[:2]
    if x_min < 0 or y_min < 0 or x_max > width or y_max > height:
        raise ValueError(
            f"ROI 超出图像范围: image_size=({width}, {height}), "
            f"roi=[[{x1}, {y1}], [{x2}, {y2}]]"
        )
    if x_max <= x_min or y_max <= y_min:
        raise ValueError(f"ROI 尺寸无效: [[{x1}, {y1}], [{x2}, {y2}]]")
    return frame[y_min:y_max, x_min:x_max]


def forward_direction_from_slot_config(slot_config) -> tuple[float, float]:
    """与 put_workflow_manager._run_shoe_toe_align 相同：从槽配置的 gripper_refer_pose 求伺服方向。"""
    poses = slot_config.gripper_refer_pose
    if len(poses) < 2:
        raise ValueError("gripper_refer_pose 至少需要 2 个位姿")

    ref_dir = [float(poses[1][0]) - float(poses[0][0]), float(poses[1][1]) - float(poses[0][1])]
    if len(slot_config.slot_xy_points) >= 2:
        direction = slot_config.fit_slot_center_line().aligned_direction(ref_dir)
    else:
        length = math.hypot(ref_dir[0], ref_dir[1])
        if length < 1e-6:
            raise ValueError("gripper_refer_pose 两点 XY 过近，无法确定方向")
        direction = (ref_dir[0] / length, ref_dir[1] / length)
    return float(direction[0]), float(direction[1])


def connect_arm(config: ArmConfig, log: Any | None = None):
    machine = automationMachine()
    machine.hardwareModule.activate_fairino_arm(config.key_name, config.name, config.robot_ip)
    arm = machine.hardwareModule.get_fairino_robot_arm(config.key_name)
    if arm is None:
        raise RuntimeError(f"Failed to activate Fairino arm: {config.key_name}")
    connected = arm.ConnectRobotArm()
    if connected is not True:
        raise RuntimeError(f"Failed to connect Fairino arm at {config.robot_ip}")
    ShoeAlignController.emit_log(log, f"Connected to arm at {config.robot_ip}")
    return arm


class ShoeAlignController:
    """封装鞋头实时对位与伺服控制逻辑的类。

    供其他模块使用时传入 ``log``（需有 ``.info(msg)``），或事后 ``set_logger``。
    """

    @staticmethod
    def emit_log(log: Any | None, msg: str) -> None:
        """优先 ``log.info``，无 log 或失败时 ``print``。"""
        if log is None:
            print(msg)
            return
        try:
            log.info(msg)
        except Exception:
            print(msg)

    def __init__(self, arm, camera,
            model_path: str | Path, dir_x: float, dir_y: float, imgsz: int = 640, device: str | None = None,
            cmd_t: float = CMDT, step_size: float = STEP_SIZE, log: Any | None = None, fps: int = 15,
            roi: list[list[int]] | None = None):
        self.arm = arm
        self.camera = camera
        self.model_path = str(model_path)
        self.dir_x = dir_x
        self.dir_y = dir_y
        self.imgsz = imgsz
        self.device = device
        self.cmd_t = cmd_t
        self.step_size = step_size
        self.log = log
        self.fps = fps
        self.roi = roi
        self.vision_t = math.ceil(1000 / fps) / 1000

        self.running = False
        self._servo_active = False
        self.speed_scale_factor = 1
        self.current_label = "-1"
        self.current_lateral_label = "-1"
        self.left_dir_x = -dir_y
        self.left_dir_y = dir_x
        self.current_frame = None
        self.lock = threading.Lock()
        self._robot_thread = None
        self._vision_thread = None
        self.arm_error = 0
        self.start_time = None

        try:
            from imgact_compat import register_casbotxyz_aliases
            from ImgAct.models.casbot.action.model import DiscreteMultiActionModel

            register_casbotxyz_aliases()
            self.model = DiscreteMultiActionModel(self.model_path, task="classify")
            self._setup_inference()
        except ImportError as exc:
            raise RuntimeError("ImgAct/casbotXYZ is not installed in the selected environment") from exc

    def _setup_inference(self) -> None:
        """预热模型并缓存 GPU/CPU 推理句柄，跳过每帧 model.predict 流水线。"""
        from ImgAct.nn.modules.head import DiscreteMultiActionHead

        if self.device is None:
            self.device = "0" if torch.cuda.is_available() else "cpu"

        use_cuda = str(self.device) != "cpu" and torch.cuda.is_available()
        if use_cuda:
            cuda_idx = int(self.device) if str(self.device).isdigit() else 0
            self._infer_device = torch.device(f"cuda:{cuda_idx}")
            self._non_blocking = True
        else:
            self._infer_device = torch.device("cpu")
            self._non_blocking = False

        self._net = self.model.model.to(self._infer_device).eval()

        model_module = self._net.model if hasattr(self._net, "model") else self._net
        last_layer = model_module[-1] if hasattr(model_module, "__getitem__") else list(model_module.children())[-1]
        if isinstance(last_layer, DiscreteMultiActionHead):
            self._num_heads = last_layer.num_heads
            self._classes_per_head = last_layer.classes_per_head
        else:
            self._num_heads = getattr(self.model, "num_heads", 3)
            self._classes_per_head = getattr(self.model, "classes_per_head", 3)

        warmup = fast_classify_preprocess(
            np.zeros((480, 640, 3), dtype=np.uint8), self.imgsz
        ).to(self._infer_device, non_blocking=self._non_blocking)
        with torch.inference_mode():
            self._net(warmup)
        if use_cuda:
            torch.cuda.synchronize()
        self._log(f"Inference device: {self._infer_device}")

    def set_logger(self, log: Any | None) -> None:
        self.log = log

    def _log(self, msg: str) -> None:
        self.emit_log(self.log, msg)

    def set_label(self, label: str) -> None:
        with self.lock:
            self.current_label = label

    def set_lateral_label(self, label: str) -> None:
        with self.lock:
            self.current_lateral_label = label

    def _update_forward_label(self, label: str, confidence: float, last_label: str) -> None:
        """多级置信度阈值更新前进 label 与 speed_scale_factor。"""
        if confidence > 0.92:
            self.set_label(label)
        if label == "1" and confidence < 0.7:
            self._log(
                f"label: {label}, confidence: {confidence:.2f}, 向前移动置信度过低 开始降速"
            )
            self.speed_scale_factor = max(0.0, confidence)
        if label == "0" and confidence > 0.8:
            self.set_label(label)
        else:
            self.speed_scale_factor = max(0.0, confidence)
        if label == "0" and confidence > 0.4 and last_label == "0":
            self.set_label(label)

    def _update_lateral_label(self, label: str, confidence: float, last_label: str) -> None:
        """多级置信度阈值更新侧向 label，参与 XY 伺服。"""
        if label == "-1":
            return
        if confidence > 0.92:
            self.set_lateral_label(label)
        if label in ("1", "2") and confidence < 0.7:
            self.speed_scale_factor = min(self.speed_scale_factor, max(0.0, confidence))
        if label == "0" and confidence > 0.8:
            self.set_lateral_label(label)
        if label == "0" and confidence > 0.4 and last_label == "0":
            self.set_lateral_label(label)

    def get_label(self) -> list[str]:
        with self.lock:
            return [self.current_label, self.current_lateral_label]

    def get_lateral_label(self) -> str:
        with self.lock:
            return self.current_lateral_label
    def get_current_frame(self):
        with self.lock:
            if self.current_frame is None:
                return None
            return self.current_frame.copy()

    def predict_frame(self, frame) -> tuple[str, float, str, float]:
        """返回 (前进 label, 前进置信度, 侧向 label, 侧向置信度)。单头模型侧向为 ``"-1"``。"""
        infer_frame = frame
        if self.roi is not None:
            infer_frame = crop_frame_to_roi(frame, self.roi)
        tensor = fast_classify_preprocess(infer_frame, self.imgsz).to(
            self._infer_device, non_blocking=self._non_blocking
        )
        with torch.inference_mode():
            logits = self._net(tensor)[0].view(self._num_heads, self._classes_per_head)
            head_probs = logits.softmax(dim=1)
            fwd_label = str(int(head_probs[0].argmax().item()))
            fwd_conf = float(head_probs[0].max().item())
            if self._num_heads >= 2:
                lat_label = str(int(head_probs[1].argmax().item()))
                lat_conf = float(head_probs[1].max().item())
            else:
                lat_label, lat_conf = "-1", 0.0
        return fwd_label, fwd_conf, lat_label, lat_conf

    def _robot_control_loop(self):
        """
        Sub-thread to control the robot arm via ServoCart.
        Frequency is typically 100Hz (0.01s) or according to fairino defaults (0.008s).
        """
        self._log("Robot control thread started.")

        # 1. 必须先开启伺服模式才能下发 ServoCart 命令
        res = self.arm.ServoMoveStart()
        self._log(f"ServoMoveStart status: {res}")
        last_print = 0
        while self.running and self._servo_active:
            # break
            start_time = time.perf_counter()
            
            labels = self.get_label()
            scale = self.speed_scale_factor
            inc_x = 0.0
            inc_y = 0.0
            if labels[0] == "1":
                inc_x += self.step_size * self.dir_x * scale
                inc_y += self.step_size * self.dir_y * scale
            if labels[1] == "1":
                inc_x += self.step_size * self.left_dir_x * scale*0.3
                inc_y += self.step_size * self.left_dir_y * scale*0.3
            elif labels[1] == "2":
                inc_x -= self.step_size * self.left_dir_x * scale*0.3
                inc_y -= self.step_size * self.left_dir_y * scale*0.3
            if inc_x != 0.0 or inc_y != 0.0:
                inc_pose = [inc_x, inc_y, 0.0, 0.0, 0.0, 0.0]
                self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=inc_pose, cmdT=self.cmd_t)
                if start_time - last_print > 1.0:
                    last_print = start_time
            elif labels[0] == "0" and labels[1] == "0":
                self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], cmdT=self.cmd_t)
                self.arm_error = self.arm.ServoMoveEnd()
                align_time = time.perf_counter() - self.start_time
                print(f"鞋槽对位时间: {align_time}秒")
                # rotate_time = time.perf_counter()
                # self.tcp_planner = TcpRotateAboutPivot(
                #     pivot_point=DEFAULT_PIVOT,
                #     target_rx=DEFAULT_TARGET_RX,
                #     target_ry=DEFAULT_TARGET_RY,
                #     target_rz=None,
                #     steps=DEFAULT_STEPS,
                # )
                # target_pose, segmented_poses = plan_tcp_rotate_about_pivot(self.arm, self.tcp_planner)
                # tcp_segment_index = 0
                # total = len(segmented_poses)
                # print(
                #     f"[TCP绕点旋转] 规划完成，共 {total} 步；"
                #     "按空格逐步执行 MoveCart（每按一次走一步）"
                # )
                # last_pose = segmented_poses[-1].copy()
                # last_pose[2] = -46.27 + 35
                # segmented_poses.append(last_pose)
                # ret = self.arm.RobotServoSpline(points=segmented_poses,speed_mm_s=30,max_ori_step_deg=0.2,tool=MOVE_CART_TOOL,user=MOVE_CART_USER)
                # print(f"鞋槽对位时间: {align_time}秒")
                # print(f"[TCP绕点旋转] RobotServoSpline 成功: {ret}，旋转时间: {time.perf_counter() - rotate_time}秒，总时间: {time.perf_counter() - self.start_time}秒")
                # if ret != 0:
                #     print(f"[TCP绕点旋转] RobotServoSpline 失败: {ret}")
                
                break
                # self.running = False
            else:
                # 2. 伺服模式通常要求持续下发指令以防止底层看门狗超时断联。在停止时下发全 0 增量。
                self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], cmdT=self.cmd_t)
            if self.arm_error != 0:
                self._log(f"[鞋槽对位] 伺服运动失败: {self.arm_error}")
                # self.running = False
                break
            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, self.cmd_t - elapsed)
            time.sleep(sleep_time)
        self._log("[鞋槽对位] 伺服运动线程退出")

    def start_align(self):
        """启动机器人与视觉循环，阻塞直到对位结束。"""
        if self.running:
            return
        self.running = True
        self._servo_active = True
        self._robot_thread = threading.Thread(target=self._robot_control_loop, daemon=True)
        self._robot_thread.start()
        self._run_vision_loop()

    def start_align_async(self) -> None:
        """启动机器人与视觉线程，不阻塞；供 main 中刷新显示用。"""
        if self.running:
            return
        self.running = True
        self._servo_active = True
        self._robot_thread = threading.Thread(target=self._robot_control_loop, daemon=True)
        self._robot_thread.start()
        self._vision_thread = threading.Thread(target=self._run_vision_loop, daemon=True)
        self._vision_thread.start()

    def stop_align(self):
        self.running = False
        self._servo_active = False
        current = threading.current_thread()
        if self._robot_thread is not None and self._robot_thread is not current:
            self._robot_thread.join(timeout=2.0)
            self._robot_thread = None
        vision_thread = getattr(self, "_vision_thread", None)
        if vision_thread is not None and vision_thread is not current:
            vision_thread.join(timeout=2.0)
            self._vision_thread = None

    def pause_servo_for_cart_move(self) -> None:
        """暂停 ServoCart 线程并结束伺服，便于穿插 MoveCart；不退出视觉预览循环。"""
        self._servo_active = False
        robot_thread = self._robot_thread
        if robot_thread is not None and robot_thread is not threading.current_thread():
            robot_thread.join(timeout=2.0)
            self._robot_thread = None
        ret = self.arm.StopMotion()
        try:
            self.arm.ServoMoveEnd()
        except Exception:
            pass
        self._log(f"Servo paused for MoveCart, StopMotion response: {ret}")

    def stop_arm_movement(self):
        # 先停掉 ServoCart 控制线程，防止多线程同时下发指令导致 xmlrpc CannotSendRequest。
        if self._robot_thread is not None:
            self.running = False

        ret = self.arm.StopMotion()
        self.stop_align()
        self._log(f"StopMotion command sent, response: {ret}")

    def _run_vision_loop(self):
        self._log("Vision loop running without GUI. Call stop_align() externally to terminate.")
        self.start_time = time.perf_counter()
        try:
            empty_time = 0
            last_label = "1"
            last_lat_label = "0"
            while self.running:
                start_time = time.perf_counter()
                color_image, depth_image, _ = self.camera.get_one_frame(draw_depth_colormap=False)
                if color_image is None:
                    self._log(f"[鞋槽对位]图像为空")
                    time.sleep(0.005)
                    empty_time += 1
                    if empty_time > 3:
                        self._log(f"[鞋槽对位] 连续3帧图像为空，停止运动")
                        self.set_label("-1")
                        break
                    if empty_time > 9:
                        self._log(f"[鞋槽对位] 连续3帧图像为空，停止对位")
                        self.running = False
                        break
                    continue
                empty_time = 0
                label, confidence, lat_label, lat_conf = self.predict_frame(color_image)
                self._log(f"前后: {label}, confidence: {confidence:.2f}, 侧向: {lat_label}, lat_conf: {lat_conf:.2f}")
                # self._log(f"[鞋槽对位] 推理时间: {time.perf_counter() - start_time:.2f}s")
                self._update_forward_label(label, confidence, last_label)
                last_label = label
                self._update_lateral_label(lat_label, lat_conf, last_lat_label)
                if lat_label != "-1":
                    last_lat_label = lat_label
                with self.lock:
                    self.current_frame = color_image.copy()
                elapsed = time.perf_counter() - start_time
                sleep_time = max(0.0, self.vision_t - elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            robot_thread = self._robot_thread
            if robot_thread is not None and robot_thread is not threading.current_thread():
                robot_thread.join(timeout=2.0)


def plan_tcp_rotate_about_pivot(
    arm,
    planner: TcpRotateAboutPivot,
) -> tuple[np.ndarray, list[list[float]]]:
    """读取当前 TCP，用 TcpRotateAboutPivot 计算 target_pose 与 segmented_poses。"""
    from rotate_tcp_about_point import get_tcp_pose

    current_pose = get_tcp_pose(arm)
    target_pose, segmented_poses = planner.plan(current_pose)

    print("\n[TCP绕点旋转] 当前 TCP 位姿:")
    print(planner.format_pose(current_pose))
    print(f"[TCP绕点旋转] 旋转中心: {[round(float(v), 3) for v in planner.pivot.tolist()]}")
    print("[TCP绕点旋转] 目标 TCP 位姿:")
    print(planner.format_pose(target_pose))
    print(f"[TCP绕点旋转] 分段数: {planner.steps}")
    for idx, pose in enumerate(segmented_poses, start=1):
        print(
            f"  step {idx:03d}/{planner.steps}: "
            f"{planner.format_pose(np.asarray(pose, dtype=float))}"
        )

    return target_pose, segmented_poses


def execute_tcp_rotate_segment(arm, pose: list[float], step_idx: int, total: int) -> None:
    """执行 segmented_poses 中的单步 MoveCart。"""

    ret = arm.MoveCart(
        desc_pos=pose,
        tool=MOVE_CART_TOOL,
        user=MOVE_CART_USER,
        vel=float(MOVE_CART_VEL),
        acc=MOVE_CART_ACC,
        ovl=float(MOVE_CART_OVL),
        blendT=MOVE_CART_BLEND_T,
        config=-1,
    )
    if ret != 0:
        raise RuntimeError(f"MoveCart 第 {step_idx}/{total} 步失败, ret={ret}")
    print(f"[TCP绕点旋转] MoveCart OK step {step_idx:03d}/{total}")


def main() -> None:
    # 屏蔽相机内部日志
    try:
        from pyorbbecsdk import Context, OBLogLevel
        Context.set_logger_to_console(OBLogLevel.NONE)
    except Exception:
        pass

    args = parse_args()

    from shoe_seg.slot_config import SlotConfig

    slot_config_path = args.slot_config
    if not slot_config_path.exists():
        print(f"槽配置文件不存在: {slot_config_path}")
        return
    slot_config = SlotConfig.load_yaml(slot_config_path)
    dir_x, dir_y = forward_direction_from_slot_config(slot_config)
    print(f"从 {slot_config_path} 加载 XY 伺服方向: dx={dir_x:.4f}, dy={dir_y:.4f}")
    left_dir_x, left_dir_y = -dir_y, dir_x
    print(f"左/右方向(参与XY伺服): dx={left_dir_x:.4f}, dy={left_dir_y:.4f}")

    robot_ip = str(slot_config.extra_fields.get("arm_ip") or args.robot_ip)
    arm_config = ArmConfig(key_name=args.arm_key, robot_ip=robot_ip)
    try:
        arm = connect_arm(arm_config)
    except Exception as e:
        print(f"Failed to connect arm: {e}")
        return

    if slot_config.camera is None:
        print(f"{slot_config_path} 缺少 camera 字段，无法连接槽位相机")
        return

    cam_name = slot_config.camera.name
    cam_sn = slot_config.camera.sn
    machine = automationMachine()
    camera = machine.hardwareModule.orbbec_camera_dict.get(cam_name)
    if camera is None:
        machine.hardwareModule.activate_orbbec_camera(cam_name, cam_sn)
        camera = machine.hardwareModule.orbbec_camera_dict.get(cam_name)
    if camera is None:
        print(f"Failed to activate Orbbec camera: name={cam_name}, sn={cam_sn}")
        return

    connected = camera.connect_camera(
        slot_config.camera.rgb_resolution,
        slot_config.camera.depth_resolution,
        slot_config.camera.fps,
    )
    if not connected:
        print(f"Failed to connect Orbbec camera: name={cam_name}, sn={cam_sn}")
        return

    model_path = args.model
    if not model_path.exists():
        print(f"鞋头对位模型不存在: {model_path}")
        return
    print(f"使用鞋头对位模型: {model_path}")

    controller = ShoeAlignController(
        arm=arm,
        camera=camera,
        model_path=model_path,
        dir_x=dir_x,
        dir_y=dir_y,
        imgsz=640,
        cmd_t=CMDT,
        step_size=STEP_SIZE,
        roi=slot_config.slot_roi,
    )

    tcp_planner = TcpRotateAboutPivot(
        pivot_point=DEFAULT_PIVOT,
        target_rx=DEFAULT_TARGET_RX,
        target_ry=DEFAULT_TARGET_RY,
        target_rz=None,
        steps=DEFAULT_STEPS,
    )

    controller.start_align_async()
    snapshot_dir = DEFAULT_SNAPSHOT_DIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    last_snapshot = 0.0
    print(f"按 q 退出预览窗口 ({DISPLAY_WINDOW})")
    print("按 c 规划 TCP 绕点旋转（pivot/目标角见 rotate_tcp_about_point.py 常量）")
    print("规划完成后按空格，每按一次空格执行一步 MoveCart（segmented_poses）")
    print(f"预览帧每 {SNAPSHOT_INTERVAL_S}s 保存至 {snapshot_dir}")
    try:
        target_pose, segmented_poses = None, None
        tcp_segment_index = 0
        while controller.running:
            frame = controller.get_current_frame()
            if frame is not None:
                display = frame.copy()
                labels = controller.get_label()
                lat_label = controller.get_lateral_label()
                lat_text = {"0": "0", "1": "1", "2": "2"}.get(lat_label, lat_label)
                cv2.putText(
                    display,
                    f"fwd={labels[0]} lat={lat_text}({lat_label})",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                cv2.imshow(DISPLAY_WINDOW, display)
                now = time.perf_counter()
                if now - last_snapshot >= SNAPSHOT_INTERVAL_S:
                    path = snapshot_dir / f"{int(time.time() * 1000)}_fwd{labels[0]}_lat{lat_label}.jpg"
                    if cv2.imwrite(str(path), frame):
                        last_snapshot = now
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                controller.stop_align()
                break


    except KeyboardInterrupt:
        controller.stop_align()
    finally:
        cv2.destroyAllWindows()
        camera_stop = getattr(camera, "stop", None)
        if callable(camera_stop):
            try:
                camera_stop()
            except Exception:
                pass


if __name__ == "__main__":
    main()
