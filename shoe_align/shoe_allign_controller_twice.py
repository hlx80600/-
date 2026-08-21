"""鞋头对位测试：槽位 YAML + models/toe_align/best.pt，main 中预览 current_frame。

Usage:
    python shoe_align/shoe_allign_controller_test.py [--slot-config press_shoes/config/left_slot.yaml]
"""
from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import cv2
import numpy as np
import torch
from automation_machine import automationMachine

CMDT = 0.04  # ServoCart command period in seconds (e.g. 80ms for ~12.5Hz)
STEP_SIZE = 0.1  # ServoCart movement step size in mm
LAT_SCALE = 0.55  # 侧向缩放系数初值
LAT_SCALE_MIN = 0.35  # 侧向缩放系数下限
LAT_SCALE_DECAY = 0.01  # 每次侧向修正后的衰减量
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHOE_ALIGN_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "press_shoes" / "config"
DEFAULT_SLOT_CONFIG = CONFIG_DIR / "left_slot.yaml"
DEFAULT_TOE_ALIGN_MODEL = PROJECT_ROOT / "models/toe_align/0617_s256best.pt"
DISPLAY_WINDOW = "toe_align"
SNAPSHOT_INTERVAL_S = 0.2
DEFAULT_SNAPSHOT_DIR = Path("/home/casbot/robot/Casbot_Press_Shoes/shoe_align_test_img/")
# from Casbot_Press_Shoes.shoe_align.ImgAct.models.yolo.model import YOLO
DEFAULT_ROBOT_IP = "192.168.57.2"
DEFAULT_ARM_KEY = "arm"

for import_root in (PROJECT_ROOT, SHOE_ALIGN_ROOT):
    if import_root.exists() and str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

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
    parser.add_argument("--imgsz", type=int, default=256)
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


def lateral_model_path(model_path: str | Path) -> Path:
    """由前进模型路径推导侧向模型路径：在文件名 stem 后追加 ``_later``。"""
    path = Path(model_path)
    return path.with_name(f"{path.stem}_later{path.suffix}")


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
        self.lateral_model_path = str(lateral_model_path(model_path))
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
        self._save_images = False
        self._save_dir: Path | None = None
        self._last_snapshot_time = 0.0
        self._snapshot_interval_s = SNAPSHOT_INTERVAL_S
        self._snapshot_queue: queue.Queue | None = None
        self._snapshot_worker: threading.Thread | None = None
        self._lat_call_count = 0

        try:
            from imgact_compat import register_casbotxyz_aliases
            from ImgAct.models.casbot.action.model import DiscreteMultiActionModel

            register_casbotxyz_aliases()
            self.model = DiscreteMultiActionModel(self.model_path, task="classify")
            self.lateral_model = DiscreteMultiActionModel(self.lateral_model_path, task="classify")
            self._setup_inference()
        except ImportError as exc:
            raise RuntimeError("ImgAct/casbotXYZ is not installed in the selected environment") from exc

    def _prepare_classify_net(self, model) -> tuple[Any, int, int]:
        """加载 classify 模型到推理设备并预热，返回 (net, num_heads, classes_per_head)。"""
        from ImgAct.nn.modules.head import DiscreteMultiActionHead

        net = model.model.to(self._infer_device).eval()
        model_module = net.model if hasattr(net, "model") else net
        last_layer = (
            model_module[-1]
            if hasattr(model_module, "__getitem__")
            else list(model_module.children())[-1]
        )
        if isinstance(last_layer, DiscreteMultiActionHead):
            num_heads = last_layer.num_heads
            classes_per_head = last_layer.classes_per_head
        else:
            num_heads = getattr(model, "num_heads", 3)
            classes_per_head = getattr(model, "classes_per_head", 3)

        warmup = fast_classify_preprocess(
            np.zeros((480, 640, 3), dtype=np.uint8), self.imgsz
        ).to(self._infer_device, non_blocking=self._non_blocking)
        with torch.inference_mode():
            net(warmup)
        return net, num_heads, classes_per_head

    def _setup_inference(self) -> None:
        """预热前进/侧向两个模型并缓存 GPU/CPU 推理句柄。"""
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

        self._net, self._num_heads, self._classes_per_head = self._prepare_classify_net(self.model)
        self._lat_net, self._lat_num_heads, self._lat_classes_per_head = self._prepare_classify_net(
            self.lateral_model
        )
        if use_cuda:
            torch.cuda.synchronize()
        self._log(f"Inference device: {self._infer_device}")
        self._log(f"Forward model: {self.model_path}")
        self._log(f"Lateral model: {self.lateral_model_path}")

    def set_logger(self, log: Any | None) -> None:
        self.log = log

    def _log(self, msg: str) -> None:
        self.emit_log(self.log, msg)

    def _configure_snapshot(
        self,
        save_images: bool,
        save_dir: str | Path | None,
        snapshot_interval_s: float = SNAPSHOT_INTERVAL_S,
    ) -> None:
        self._save_images = save_images
        self._snapshot_interval_s = snapshot_interval_s
        self._last_snapshot_time = 0.0
        if not save_images:
            self._save_dir = None
            self._stop_snapshot_worker()
            return
        dir_path = Path(save_dir) if save_dir is not None else DEFAULT_SNAPSHOT_DIR
        dir_path.mkdir(parents=True, exist_ok=True)
        self._save_dir = dir_path
        self._log(f"存图已开启，间隔 {self._snapshot_interval_s}s，目录: {dir_path}")
        self._start_snapshot_worker()

    def _start_snapshot_worker(self) -> None:
        if self._snapshot_worker is not None and self._snapshot_worker.is_alive():
            return
        self._snapshot_queue = queue.Queue(maxsize=2)
        self._snapshot_worker = threading.Thread(
            target=self._snapshot_worker_loop,
            name="shoe_align_snapshot",
            daemon=True,
        )
        self._snapshot_worker.start()

    def _stop_snapshot_worker(self) -> None:
        worker = self._snapshot_worker
        snapshot_queue = self._snapshot_queue
        if worker is None or snapshot_queue is None:
            return
        try:
            snapshot_queue.put_nowait(None)
        except queue.Full:
            try:
                snapshot_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                snapshot_queue.put_nowait(None)
            except queue.Full:
                pass
        if worker is not threading.current_thread() and worker.is_alive():
            worker.join(timeout=3.0)
        self._snapshot_worker = None
        self._snapshot_queue = None

    def _snapshot_worker_loop(self) -> None:
        snapshot_queue = self._snapshot_queue
        if snapshot_queue is None:
            return
        while True:
            item = snapshot_queue.get()
            try:
                if item is None:
                    break
                path, frame = item
                if not cv2.imwrite(str(path), frame):
                    self._log(f"存图失败: {path}")
            except Exception as exc:
                self._log(f"存图异常: {exc}")
            finally:
                snapshot_queue.task_done()

    def _maybe_save_snapshot(self, frame, label: str, confidence: float) -> None:
        if not self._save_images or self._save_dir is None or self._snapshot_queue is None:
            return
        now = time.perf_counter()
        if now - self._last_snapshot_time < self._snapshot_interval_s * self.speed_scale_factor:
            return
        dt = datetime.now()
        path = self._save_dir / (
            f"align_{dt.strftime('%m%d%H%M%S')}_{dt.strftime('%f')[:3]}"
            f"_l{label}_con{confidence:.2f}.jpg"
        )
        try:
            self._snapshot_queue.put_nowait((path, frame.copy()))
            self._last_snapshot_time = now
        except queue.Full:
            pass

    def set_label(self, label: str):
        with self.lock:
            self.current_label = label

    def set_lateral_label(self, label: str) -> None:
        with self.lock:
            self.current_lateral_label = label
            
    def get_label(self) -> str:
        with self.lock:
            return self.current_label

    def get_lateral_label(self) -> str:
        with self.lock:
            return self.current_lateral_label
    def get_current_frame(self):
        with self.lock:
            if self.current_frame is None:
                return None
            return self.current_frame.copy()

    def predict_frame(self, frame) -> tuple[str, float, str, float]:
        """返回 (前进 label, 前进置信度, 侧向 label, 侧向置信度)。侧向由独立 lateral 模型推理。"""
        infer_frame = frame
        if self.roi is not None:
            infer_frame = crop_frame_to_roi(frame, self.roi)
        tensor = fast_classify_preprocess(infer_frame, self.imgsz).to(
            self._infer_device, non_blocking=self._non_blocking
        )
        with torch.inference_mode():
            fwd_logits = self._net(tensor)[0].view(self._num_heads, self._classes_per_head)
            fwd_probs = fwd_logits.softmax(dim=1)
            fwd_label = str(int(fwd_probs[0].argmax().item()))
            fwd_conf = float(fwd_probs[0].max().item())

            lat_logits = self._lat_net(tensor)[0].view(
                self._lat_num_heads, self._lat_classes_per_head
            )
            lat_probs = lat_logits.softmax(dim=1)
            lat_label = str(int(lat_probs[0].argmax().item()))
            lat_conf = float(lat_probs[0].max().item())
        return fwd_label, fwd_conf, lat_label, lat_conf

    def _update_forward_label(self, label: str, confidence: float, last_label: str) -> None:
        """多级置信度阈值更新前进 label 与 speed_scale_factor。"""
        if confidence > 0.92:
            self.set_label(label)
        if label == "1" and confidence < 0.7:
            self._log(
                f"label: {label}, confidence: {confidence:.2f}, 向前移动置信度过低 开始降速"
            )
            self.speed_scale_factor = max(0.4, confidence)
        if label == "0" and confidence > 0.8:
            self.set_label(label)
        else:
            self.speed_scale_factor = max(0.0, confidence)
        if label == "0" and confidence > 0.4 and last_label == "0":
            self.set_label(label)

    def _update_lateral_label(self, label: str, confidence: float, last_label: str) -> None:
        """更新左右方向 label。"""
        if label == "-1":
            return
        if confidence > 0.92:
            self.set_lateral_label(label)
        if label in ("1", "2") and confidence > 0.5:
            self.set_lateral_label(label)
        if label == "0":
            self.set_lateral_label(label)

    def _robot_control_loop(self):
        """
        Sub-thread to control the robot arm via ServoCart.
        Frequency is typically 100Hz (0.01s) or according to fairino defaults (0.008s).
        """
        self._log("Robot control thread started.")
        self._lat_call_count = 0

        # 1. 必须先开启伺服模式才能下发 ServoCart 命令
        res = self.arm.ServoMoveStart()
        self._log(f"ServoMoveStart status: {res}")
        last_print = 0
        while self.running:
            start_time = time.perf_counter()
            
            label = self.get_label()
            lat_label = self.get_lateral_label()
            if label == "1":
                scale = self.speed_scale_factor
                inc_x = self.step_size * self.dir_x * scale
                inc_y = self.step_size * self.dir_y * scale
                lat_scale = max(
                    LAT_SCALE_MIN,
                    LAT_SCALE - self._lat_call_count * LAT_SCALE_DECAY,
                )
                if lat_label == "1":
                    inc_x += self.step_size * self.left_dir_x * scale * lat_scale
                    inc_y += self.step_size * self.left_dir_y * scale * lat_scale
                    self._lat_call_count += 1
                elif lat_label == "2":
                    inc_x -= self.step_size * self.left_dir_x * scale * lat_scale
                    inc_y -= self.step_size * self.left_dir_y * scale * lat_scale
                    self._lat_call_count += 1
                inc_pose = [inc_x, inc_y, 0.0, 0.0, 0.0, 0.0]
                self._log(f"inc_pose: {inc_pose}")
                ret = self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=inc_pose, cmdT=self.cmd_t)
                if ret != 0:
                    self._log(f"[鞋槽对位] 伺服运动失败: {ret}")
                    self.running = False
                    break
                if start_time - last_print > 1.0:
                    last_print = start_time
            elif label == "0":
                self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], cmdT=self.cmd_t)
                self.arm_error = self.arm.ServoMoveEnd()
                self.running = False
            else:
                # 2. 伺服模式通常要求持续下发指令以防止底层看门狗超时断联。在停止时下发全 0 增量。
                self.arm_error = self.arm.ServoCart(1, exaxis=[0.0] * 4, desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], cmdT=self.cmd_t)
            if self.arm_error != 0:
                self._log(f"[鞋槽对位] 伺服运动失败: {self.arm_error}")
                self.running = False
                break
            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, self.cmd_t - elapsed)
            time.sleep(sleep_time)
        self._log("[鞋槽对位] 伺服运动线程退出")

    def start_align(
        self,
        save_images: bool = False,
        save_dir: str | Path | None = None,
        snapshot_interval_s: float = SNAPSHOT_INTERVAL_S,
    ) -> None:
        """启动机器人与视觉循环，阻塞直到对位结束。

        Args:
            save_images: 是否按固定间隔保存相机帧。
            save_dir: 存图目录，不存在时自动创建；``save_images=True`` 且未指定时使用默认目录。
            snapshot_interval_s: 存图时间间隔（秒）。
        """
        if self.running:
            return
        self._configure_snapshot(save_images, save_dir, snapshot_interval_s)
        self.running = True
        self._robot_thread = threading.Thread(target=self._robot_control_loop, daemon=True)
        self._robot_thread.start()
        self._run_vision_loop()

    def start_align_async(
        self,
        save_images: bool = False,
        save_dir: str | Path | None = None,
        snapshot_interval_s: float = SNAPSHOT_INTERVAL_S,
    ) -> None:
        """启动机器人与视觉线程，不阻塞；供 main 中刷新显示用。"""
        if self.running:
            return
        self._configure_snapshot(save_images, save_dir, snapshot_interval_s)
        self.running = True
        self._robot_thread = threading.Thread(target=self._robot_control_loop, daemon=True)
        self._robot_thread.start()
        self._vision_thread = threading.Thread(target=self._run_vision_loop, daemon=True)
        self._vision_thread.start()

    def stop_align(self):
        self.running = False
        current = threading.current_thread()
        if self._robot_thread is not None and self._robot_thread is not current:
            self._robot_thread.join(timeout=2.0)
            self._robot_thread = None
        vision_thread = getattr(self, "_vision_thread", None)
        if vision_thread is not None and vision_thread is not current:
            vision_thread.join(timeout=2.0)
            self._vision_thread = None
        self._stop_snapshot_worker()

    def stop_arm_movement(self):
        # 先停掉 ServoCart 控制线程，防止多线程同时下发指令导致 xmlrpc CannotSendRequest。
        if self._robot_thread is not None:
            self.running = False

        ret = self.arm.StopMotion()
        self.stop_align()
        self._log(f"StopMotion command sent, response: {ret}")

    def _run_vision_loop(self):
        self._log("Vision loop running without GUI. Call stop_align() externally to terminate.")

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
                # self._log(f"[鞋槽对位] 推理{label}, confidence: {confidence:.2f}, lat_label: {lat_label}, lat_conf: {lat_conf:.2f}")
                self._update_forward_label(label, confidence, last_label)
                last_label = label
                self._update_lateral_label(lat_label, lat_conf, last_lat_label)
                if lat_label != "-1":
                    last_lat_label = lat_label
                with self.lock:
                    self.current_frame = color_image
                self._maybe_save_snapshot(color_image, label, confidence)
                elapsed = time.perf_counter() - start_time
                sleep_time = max(0.0, min(self.vision_t, self.vision_t - elapsed))
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            robot_thread = self._robot_thread
            if robot_thread is not None and robot_thread is not threading.current_thread():
                robot_thread.join(timeout=2.0)
            self._stop_snapshot_worker()


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
    print(f"左/右方向(横向速度为纵向1/3): dx={left_dir_x:.4f}, dy={left_dir_y:.4f}")

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
        imgsz=args.imgsz,
        device=args.device,
        cmd_t=CMDT,
        step_size=STEP_SIZE,
        roi=slot_config.slot_roi,
    )

    controller.start_align_async()
    snapshot_dir = DEFAULT_SNAPSHOT_DIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    last_snapshot = 0.0
    print(f"按 q 退出预览窗口 ({DISPLAY_WINDOW})")
    print(f"预览帧每 {SNAPSHOT_INTERVAL_S}s 保存至 {snapshot_dir}")
    try:
        while controller.running:
            frame = controller.get_current_frame()
            if frame is not None:
                display = frame.copy()
                label = controller.get_label()
                lat_label = controller.get_lateral_label()
                lat_text = {"0": "stop", "1": "left", "2": "right"}.get(lat_label, lat_label)
                cv2.putText(
                    display,
                    f"fwd={label} lat={lat_text}({lat_label})",
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
                    path = snapshot_dir / f"{int(time.time() * 1000)}_fwd{label}_lat{lat_label}.jpg"
                    if cv2.imwrite(str(path), frame):
                        last_snapshot = now
            if cv2.waitKey(1) & 0xFF == ord("q"):
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
