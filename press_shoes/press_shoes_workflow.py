import threading
import time
from datetime import date
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

import yaml

from .robot_arm_state import PutInArmState, TakeOutArmState, PutInArmStatus, TakeOutArmStatus
from .press_machine_state import PressMachineState, PressMachineStatus
from .manager.press_machine_manager import PressMachineManager
from .manager.put_workflow_manager import PutWorkFlowManager, PutArmPose
from .manager.take_workflow_manager import TakeWorkFlowManager
from .process_context import PressProcessContext
from .utils import put_arm_type, take_arm_type, get_robot_arms, create_grippers
from .utils import FairinoRobotArm
from shoe_seg.shoe_pose_computer import ShoePoseComputer, _load_sam_predictor_if_needed

CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent
SLOT_CONFIG_CANDIDATES = (
    WORKSPACE_ROOT / "press_shoes" / "config" / "slot.yaml",
    WORKSPACE_ROOT / "press_shoes" / "config" / "slot.json",
)
SLOT_CONFIG_PATH = next((path for path in SLOT_CONFIG_CANDIDATES if path.exists()), SLOT_CONFIG_CANDIDATES[0])
DEFAULT_CONFIG_DIR = CURRENT_DIR / "config"
PRESS_SHOES_ROBOT_INFO_FILENAME = "press_shoes_robot_info.yaml"
DEFAULT_PRESS_SHOES_ROBOT_INFO_PATH = DEFAULT_CONFIG_DIR / PRESS_SHOES_ROBOT_INFO_FILENAME
TAKE_ARM_CONFIG_PATH = DEFAULT_CONFIG_DIR / "take_arm_config.yaml"


def _resolve_press_shoes_robot_info_path(
    take_arm_config_path: Path = TAKE_ARM_CONFIG_PATH,
) -> Path:
    """从 take_arm_config.yaml 解析统计文件路径，未配置时使用 press_shoes/config/ 下默认文件。"""
    configured_path: Optional[str] = None
    if take_arm_config_path.exists():
        try:
            with take_arm_config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            configured_path = data.get("press_shoes_robot_info_path")
        except yaml.YAMLError:
            pass

    if not configured_path:
        return DEFAULT_PRESS_SHOES_ROBOT_INFO_PATH

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = (take_arm_config_path.parent / path).resolve()
    if path.suffix in {".yaml", ".yml"}:
        return path
    return path / PRESS_SHOES_ROBOT_INFO_FILENAME

class PressShoesWorkflow:
    def __init__(
        self,
        machine,
        grep_vision=None,
        press_vision=None,
        log: Any = None,
    ) -> None:
        if log is None:
            raise ValueError("PressShoesWorkflow 需要传入 log 实例")
        self.log = log

        self.put_signal = threading.Event()
        self.take_signal = threading.Event()
        self.press_signal = threading.Event()
        self.state_signal_any = threading.Event()
        put_arm, take_arm = get_robot_arms(machine)
        left_gripper, right_gripper = create_grippers()
        put_arm_gripper = None
        take_arm_gripper = None
        if put_arm_type == "left":
            put_arm_gripper = left_gripper
            take_arm_gripper = right_gripper
        else:
            put_arm_gripper = right_gripper
            take_arm_gripper = left_gripper
        self.put_arm: FairinoRobotArm = put_arm
        self.take_arm: FairinoRobotArm = take_arm
        self.put_arm.log = log
        self.take_arm.log = log
        self.grab_vision = grep_vision
        self.press_vision = press_vision
        self.machine = machine

        self.put_arm_state = PutInArmState(
            PutInArmStatus.UNKNOWN,
            None,
            notifier=self._notify_put_arm,
        )
        self.take_arm_state = TakeOutArmState(
            TakeOutArmStatus.UNKNOWN,
            None,
            notifier=self._notify_take_arm,
        )
        self.press_machine_state = PressMachineState(
            notifier=self._notify_press_machine,
            log=self.log,
        )

        if not self._init_arms():
            raise Exception("机械臂初始化失败")

        self._init_visions()
        self.process_context = PressProcessContext()
        self.put_manager = PutWorkFlowManager(
            self.put_arm,
            put_arm_gripper,
            press_vision=self.press_vision,
            machine=self.machine,
            process_context=self.process_context,
            log=self.log,
        )
        self.take_manager = TakeWorkFlowManager(
            self.take_arm,
            take_arm_gripper,
            press_vision=self.press_vision,
            process_context=self.process_context,
            log=self.log,
        )
        self.press_machine_manager = PressMachineManager(log=self.log)
        # 取出任务队列（阻塞）
        self.fetch_task_queue: Queue[str] = Queue()
        self.fetch_task_waiting = threading.Event()

        self.target_shoe = None
        self.expected_shoe = put_arm_type

        self._shoe_data_prefetch_lock = threading.Lock()
        self._shoe_data_prefetch_thread: Optional[threading.Thread] = None
        self._shoe_data_prefetch_result: Optional[dict[str, Any]] = None
        self._shoe_data_prefetch_error: Optional[BaseException] = None

        # 取鞋臂本次运行期间放左/右鞋计数
        self.put_shoes_count = 0
        self.take_arm_place_left_count = 0
        self.take_arm_place_right_count = 0
        self.press_shoes_robot_info_path = _resolve_press_shoes_robot_info_path(
            Path(self.take_manager.config_path)
        )
        self._stop_event = threading.Event()
        self._stats_saved = False
        self._stats_save_lock = threading.Lock()

    def _init_arms(self):
        ret = self.put_arm.ConnectRobotArm()
        if not ret:
            self.log.error("连接放鞋机械臂失败")
            return False
        ret = self.take_arm.ConnectRobotArm()
        if not ret:
            self.log.error("连接取鞋机械臂失败")
            return False
        return True
    
    def _init_visions(self):
        self._shoe_pose_computer: Optional[ShoePoseComputer] = None
        self._shoe_pose_predictor = None
        self._shoe_pose_extract_outer_contour = None
        if self.grab_vision is None:
            return

    def _ensure_shoe_pose_computer(self) -> ShoePoseComputer:
        if self.grab_vision is None:
            raise RuntimeError("抓鞋视觉未初始化")
        if getattr(self.grab_vision, "camera", None) is None:
            raise RuntimeError("抓鞋视觉未找到相机，请检查配置和相机连接")
        if self._shoe_pose_computer is not None:
            return self._shoe_pose_computer
        self._shoe_pose_predictor, self._shoe_pose_extract_outer_contour = _load_sam_predictor_if_needed(True)
        self._shoe_pose_computer = ShoePoseComputer(
            self.put_arm,
            self.grab_vision,
            predictor=self._shoe_pose_predictor,
            extract_outer_contour=self._shoe_pose_extract_outer_contour,
        )
        self.log.info(f"[投放] 抓鞋视觉已就绪")
        return self._shoe_pose_computer

    def _notify_put_arm(self) -> None:
        # 放臂状态更新信号
        self.put_signal.set()
        self.state_signal_any.set()
        
    def _notify_take_arm(self) -> None:
        # 取臂状态更新信号
        self.take_signal.set()
        self.state_signal_any.set()

    def _notify_press_machine(self) -> None:
        # 压鞋机状态更新信号
        self.press_signal.set()
        self.state_signal_any.set()

    def _stopping(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        """请求停止工作流，唤醒阻塞中的线程并在 run() 退出时保存统计。"""
        if self._stop_event.is_set():
            return
        self.log.info("收到停止请求，工作流将在安全点退出...")
        self._stop_event.set()
        self.state_signal_any.set()

    def _wait_any_signal(self, timeout: float = 0.05) -> bool:
        """等待任一状态信号后再清除，避免忙等。返回 False 表示收到停止请求。"""
        while not self._stopping():
            if self.state_signal_any.wait(timeout):
                self.put_signal.clear()
                self.take_signal.clear()
                self.press_signal.clear()
                self.state_signal_any.clear()
                return True
        return False

    def run(self):
        self.log.info("启动压鞋工作流")
        self.put_arm_state.set_current(PutInArmStatus.GRAB_PENDING_SHOES)
        self.take_arm_state.set_current(TakeOutArmStatus.FETCH_TASK)
        self.press_machine_state.set_state(left=PressMachineStatus.LEFT_IDLE, right=PressMachineStatus.RIGHT_IDLE)
        put_thread = threading.Thread(target=self._task_put_into_press, name="PutIntoPress")
        take_thread = threading.Thread(target=self._task_take_out_press, name="TakeOutPress")
        try:
            put_thread.start()
            take_thread.start()
            put_thread.join()
            take_thread.join()
        finally:
            self._save_take_arm_place_stats()
            if self._stopping():
                self.log.info("压鞋工作流已停止")
            else:
                self.log.info("压鞋工作流已完成")
    
    def save_place_stats(self) -> None:
        """持久化取鞋臂放鞋计数（可重复调用，仅写入一次）。"""
        self._save_take_arm_place_stats()

    def _save_take_arm_place_stats(self) -> None:
        """将本次运行取鞋臂放鞋计数累加到按日统计 YAML 文件。"""
        with self._stats_save_lock:
            if self._stats_saved:
                return
            self._stats_saved = True
            left_count = self.take_arm_place_left_count
            right_count = self.take_arm_place_right_count
        if left_count == 0 and right_count == 0:
            return

        stats_path = self.press_shoes_robot_info_path
        stats_path.parent.mkdir(parents=True, exist_ok=True)

        if stats_path.exists():
            with stats_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        today = date.today().isoformat()
        day_stats = data.get(today, {})
        day_left = int(day_stats.get("left", 0))
        day_right = int(day_stats.get("right", 0))
        day_left += left_count
        day_right += right_count
        data[today] = {
            "total": day_left + day_right,
            "left": day_left,
            "right": day_right,
        }

        with stats_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

        self.log.info(
            f"[取出] 放鞋统计已更新: 本次 left={left_count}, right={right_count}; "
            f"今日 total={day_left + day_right}, left={day_left}, right={day_right} -> {stats_path}"
        )

    def query_conveyor_status(self):
        """查询传送带状态，返回状态字符串。"""
        ret = self.put_arm.GetDI(3)
        if ret[1] != 1:
            self.log.debug(f"查询传送带状态，返回值{ret[1]}，传送带等待鞋子到达")
        while ret[1] != 1:
            if self._stopping():
                return 1
            # self.log.info(f"等待传送带有鞋子...,返回值{ret}")
            time.sleep(0.05)  # 避免过快重试
            ret = self.put_arm.GetDI(3)
            if ret[0] != 0:
                self.log.error("查询传送带状态失败")
                return 1
        return 0
    
    def _get_shoe_data(self, max_arc_retries: int = 5) -> dict[str, Any]:
        """获取鞋子信息，包括鞋楦中心点、鞋头点和鞋头弧线点列表。"""
        if self._stopping():
            raise RuntimeError("工作流已停止")
        self.log.debug("[投放] 获取鞋子信息")
        self.query_conveyor_status()
        if self._stopping():
            raise RuntimeError("工作流已停止")
        computer = self._ensure_shoe_pose_computer()
        preferred_side = self.expected_shoe
        rz_min, rz_max = self.put_manager.safe_rz[0], self.put_manager.safe_rz[1]
        def _update_target(side: str) -> None:
            self.target_shoe = side
            self.expected_shoe = "right" if side == "left" else "left"

        def _fetch(side: str) -> dict[str, Any]:
            shoe_data = computer.get_shoe_data(
                side,
                rz_min=rz_min,
                rz_max=rz_max,
                max_tries=max(1, int(max_arc_retries)),
                retry_interval=0.05,
            )
            toe_arc_xyz_list = shoe_data.get("toe_arc_xyz_list") or []
            if not toe_arc_xyz_list:
                raise RuntimeError(f"获取{side}鞋数据失败: toe_arc_xyz_list 为空")
            return shoe_data
        self.log.debug('开始获取鞋的数据')
        shoe_data = _fetch(preferred_side)
        chosen_side = str(shoe_data.get("side", ""))
        if chosen_side != preferred_side:
            self.log.warning(f"[投放] 首选{preferred_side}鞋数据获取失败，重试{max_arc_retries}次后,最终获取{chosen_side}鞋数据")
        _update_target(chosen_side)

        shoe_center_xyz = shoe_data.get("shoe_center_xyz")
        toe_arc_xyz_list = shoe_data.get("toe_arc_xyz_list")
        self.log.info(f"[投放] selected_target = {chosen_side}[{shoe_data.get('index', -1)}]")
        self.log.info(f"[投放] shoe_center_xyz = {shoe_center_xyz}")
        self.log.info(f"[投放] rz = {float(shoe_data.get('rz')):.4f}°")
        self.log.info(f"[投放] toe_arc count = {len(toe_arc_xyz_list)}")
        return shoe_data

    def _start_shoe_data_prefetch(self) -> None:
        """在后台线程预取下一双鞋数据，与 EXIT_FAR_USE_NEAR 等动作并行。"""
        with self._shoe_data_prefetch_lock:
            if self._shoe_data_prefetch_thread is not None and self._shoe_data_prefetch_thread.is_alive():
                return
            self._shoe_data_prefetch_result = None
            self._shoe_data_prefetch_error = None

            def _run() -> None:
                try:
                    result = self._get_shoe_data(max_arc_retries=10)
                    with self._shoe_data_prefetch_lock:
                        self._shoe_data_prefetch_result = result
                except BaseException as exc:
                    with self._shoe_data_prefetch_lock:
                        self._shoe_data_prefetch_error = exc

            self._shoe_data_prefetch_thread = threading.Thread(
                target=_run,
                name="ShoeDataPrefetch",
                daemon=True,
            )
            self._shoe_data_prefetch_thread.start()
            self.log.debug("[投放] 已启动鞋数据预取线程")

    def _consume_prefetched_shoe_data(self) -> dict[str, Any]:
        """等待 EXIT_FAR_USE_NEAR 阶段启动的预取线程并返回结果。"""
        with self._shoe_data_prefetch_lock:
            thread = self._shoe_data_prefetch_thread
        if thread is None:
            return self._get_shoe_data()
        while thread.is_alive():
            thread.join(timeout=0.5)
            if self._stopping():
                break
        with self._shoe_data_prefetch_lock:
            self._shoe_data_prefetch_thread = None
            error = self._shoe_data_prefetch_error
            result = self._shoe_data_prefetch_result
            self._shoe_data_prefetch_error = None
            self._shoe_data_prefetch_result = None
        if self._stopping():
            raise RuntimeError("工作流已停止")
        if error is not None:
            raise error
        if result is None:
            return self._get_shoe_data()
        self.log.debug("[投放] 使用预取的鞋数据")
        return result

    def _arm_error_check(self) -> bool:
        """检查手臂状态是否进入错误状态。"""
        left_state, _ = self.put_arm_state.get_state()
        right_state, _ = self.take_arm_state.get_state()
        if left_state == PutInArmStatus.ERROR:
            self.log.error(f"{put_arm_type}[投放手臂]异常")
            return True
        if right_state == TakeOutArmStatus.ERROR:
            self.log.error(f"{take_arm_type}[取出手臂]异常")
            return True
        return False
    
    def _put_fetch_task(self, shoe_type: str) -> None:
        """将鞋子类型写入取出任务队列。"""
        self.log.debug(f"[压机] {shoe_type}鞋压制完成，添加取出任务")
        self.fetch_task_queue.put(shoe_type)

    def _get_fetch_task(self) -> Optional[str]:
        """从取出任务队列获取最前面的鞋子类型，若为空则等待。停止时返回 None。"""
        self.log.debug("[取出] 获取取出任务")
        while not self._stopping():
            try:
                task = self.fetch_task_queue.get_nowait()
                self.fetch_task_waiting.clear()
                return task
            except Empty:
                self.fetch_task_waiting.set()
                try:
                    return self.fetch_task_queue.get(block=True, timeout=0.5)
                except Empty:
                    continue
                finally:
                    self.fetch_task_waiting.clear()
        self.fetch_task_waiting.clear()
        return None

    def _clear_fetch_tasks(self) -> None:
        """清空取出任务队列。"""
        try:
            while True:
                self.fetch_task_queue.get_nowait()
        except Exception:
            pass
        self.fetch_task_waiting.clear()

    def _both_press_slots_busy(self) -> bool:
        return (
            self.press_machine_state.get_left_state() != PressMachineStatus.LEFT_IDLE
            and self.press_machine_state.get_right_state() != PressMachineStatus.RIGHT_IDLE
        )

    def _request_take_arm_early_wait(self, expected_side: Optional[str] = None) -> None:
        """取鞋臂在等待取出任务时，提前移到预计先完成一侧的近等待位。"""
        if not self.fetch_task_waiting.is_set():
            return
        def _run() -> None:
            next_finish = self.press_machine_manager.predict_next_finish_shoe()
            if next_finish not in ("left", "right"):
                self.log.debug("[投放] 暂无压制完成预测，跳过取鞋臂提前等待")
                return
            if expected_side is not None and next_finish != expected_side:
                return

            def _can_early_wait() -> bool:
                if not self.fetch_task_waiting.is_set():
                    return False
                take_cur, _ = self.take_arm_state.get_state()
                return take_cur == TakeOutArmStatus.FETCH_TASK

            if not _can_early_wait():
                return
            self.log.info(f"[投放] 取鞋臂等待任务中，请求移动到{next_finish}近等待位置")
            ret = self.take_manager.move_to_wait(next_finish, should_proceed=_can_early_wait)
            if ret != 0:
                self.log.warning(f"[投放] 取鞋臂提前移动到{next_finish}等待位失败，返回值: {ret}")

        threading.Thread(
            target=_run,
            name=f"TakeArmEarlyWait-{expected_side or 'auto'}",
            daemon=True,
        ).start()
    
    def _wait_put_arm_state(self, verify_states: list[PutInArmStatus], shoe_side: str) -> int:
        while True:
            if self._stopping():
                self.log.warning("[取出] 工作流停止")
                return 1
            put_cur, _ = self.put_arm_state.get_state()
            self.log.info(f"[取出] 等待取{shoe_side}鞋，放鞋臂状态: {put_cur.value}")
            if self._arm_error_check():
                return 1
            if put_cur not in verify_states:
                return 0
            self.log.info(f"[取出] 等待取{shoe_side}鞋，放鞋臂状态: {put_cur.value}")
            if not self._wait_any_signal():
                return 1

    def _wait_take_arm_press_machine(self, verify_states: list[TakeOutArmStatus], shoe_side: str) -> int:
        self.log.info(f"[投放] 等待取{shoe_side}鞋和压机就绪，取鞋臂状态: {self.take_arm_state.get_state()[0]}, 压机状态: {self.press_machine_state.get_states()}")
        while True:
            if self._stopping():
                self.log.warning("[投放] 工作流停止")
                return 1
            if shoe_side == "left":
                press_state = self.press_machine_state.get_left_state()
                expect_state = PressMachineStatus.LEFT_IDLE
            else:
                press_state = self.press_machine_state.get_right_state()
                expect_state = PressMachineStatus.RIGHT_IDLE
            take_cur, _ = self.take_arm_state.get_state()
            if self._arm_error_check():
                return 1
            if  press_state == expect_state and take_cur not in verify_states:
                self.log.debug(f"[投放] 等待取{shoe_side}鞋和压机就绪完成，取鞋臂状态: {take_cur.value}, 压机状态: {press_state.value}")
                return 0
            self.log.info(f"[投放] 等待放{'左' if shoe_side == 'left' else '右'}鞋，取鞋臂状态: {take_cur.value}")
            if not self._wait_any_signal():
                return 1

    def _task_put_into_press(self) -> None:
        """模拟将鞋子放入压机。"""
        self.log.debug(f"[投放] 初始化手臂: {(self.put_arm_state.get_state(), self.take_arm_state.get_state())} | 压机: {self.press_machine_state.get_states()}")
        place_left_verify = []
        if put_arm_type == "left":
            place_left_verify = [TakeOutArmStatus.PICK_LEFT_SHOE]
            place_right_verify = [TakeOutArmStatus.PICK_RIGHT_SHOE, TakeOutArmStatus.PICK_LEFT_SHOE, TakeOutArmStatus.EXIT_FAR_USE_NEAR]
        else:
            place_left_verify = [TakeOutArmStatus.PICK_LEFT_SHOE, TakeOutArmStatus.PICK_RIGHT_SHOE, TakeOutArmStatus.EXIT_FAR_USE_NEAR]
            place_right_verify = [TakeOutArmStatus.PICK_RIGHT_SHOE]
        ret = self.put_manager.move_to_initial_position(speed_multiplier = 0.15)
        if ret != 0:
            self.log.error(f"放鞋机械臂移动到初始位置失败{ret}")
            return
        while not self._stopping():
            cur_state, _ = self.put_arm_state.get_state()
            match cur_state:
                case PutInArmStatus.GRAB_PENDING_SHOES: 
                    self.log.debug(f"[投放] 进入抓取待定鞋子状态，当前状态: {cur_state.value}，退出循环")
                    start = time.time()
                    # 双鞋都在鞋槽时取鞋臂提前等待
                    if self._both_press_slots_busy():
                        self._request_take_arm_early_wait()
                        
                    ret = self.put_manager.move_to_initial_position()
                    if ret != 0:
                        self.log.error(f"放鞋机械臂移动到初始位置失败{ret}")
                        break
                    if self._stopping():
                        break
                    self.put_manager.set_cur_handle_shoe_data(None)
                    try:
                        shoe_data = self._consume_prefetched_shoe_data()
                    except RuntimeError:
                        if self._stopping():
                            break
                        raise
                    shoe_center_xyz = shoe_data.get("shoe_center_xyz") or []
                    rz = shoe_data.get("rz")
                    if len(shoe_center_xyz) < 3 or rz is None:
                        raise RuntimeError(f"[投放] 倾斜鞋抓取点数据不完整: {shoe_data}")
                    shoe_point_4d = [shoe_center_xyz[0], shoe_center_xyz[1], shoe_center_xyz[2], rz,]
                    shoe_point = self.put_manager.calculate_shoes_point(shoe_point_4d) #抓取点计算
                    if not shoe_point:
                        raise RuntimeError(f"[投放] 倾斜鞋抓取点计算失败: {shoe_point_4d}")
                    shoe_data["grab_pose"] = shoe_point
                    self.put_manager.set_cur_handle_shoe_data(shoe_data)
                    if self.target_shoe == 'left' and self.press_machine_state.get_left_state() == PressMachineStatus.LEFT_WORKING:
                        self.log.info("[投放] 抓取到左鞋但左鞋槽有鞋")
                        self._request_take_arm_early_wait("left")

                    if self.target_shoe == 'right' and self.press_machine_state.get_right_state() == PressMachineStatus.RIGHT_WORKING:
                        self.log.info("[投放] 抓取到右鞋但右鞋槽有鞋")
                        self._request_take_arm_early_wait("right")
                    ret = self.put_manager.handle_grab_pending_shoes(shoe_point)
                    self.log.debug(f"[投放] 抓取鞋子完成，返回值: {ret},鞋子{self.target_shoe}")
                    self.log.debug(f"抓取耗时{time.time() - start:.2f}秒")
                    if ret != 0:
                        next_state = PutInArmStatus.ERROR
                        self.put_arm_state.advance(next_state)
                        continue
                    if self.target_shoe == 'left':
                        next_state = PutInArmStatus.PLACE_LEFT_SHOE
                        if self._wait_take_arm_press_machine(place_left_verify, "left") != 0:
                            break
                        # 放鞋臂为右臂时：取鞋臂状态为取左鞋，且放鞋臂状态为右等待位，则将放鞋臂移动到左等待位
                        take_cur, _ = self.take_arm_state.get_state()
                        if (put_arm_type == "right"
                                and take_cur == TakeOutArmStatus.PICK_LEFT_SHOE
                                and self.put_manager.put_arm_pose == PutArmPose.RIGHT_WAIT_POSE):
                            ret = self.put_manager.right_to_left_wait_pose()
                            if ret != 0:
                                self.log.error(f"[{self.put_manager.robot_arm.name}]机械臂移动到左等待位失败，返回值: {ret}")
                                break
                    else:
                        next_state = PutInArmStatus.PLACE_RIGHT_SHOE
                        self.log.debug(f"[投放] 等待放{self.target_shoe}鞋，取鞋臂状态: {self.take_arm_state.get_state()[0].value}, 压机状态: {self.press_machine_state.get_states()}")
                        # 放鞋臂为左臂时：取鞋臂状态为取右鞋，且放鞋臂状态为左等待位，则将放鞋臂移动到右等待位
                        take_cur, _ = self.take_arm_state.get_state()
                        if put_arm_type == "left" and take_cur == TakeOutArmStatus.PICK_RIGHT_SHOE:
                            ret = self.put_manager.left_to_right_wait_pose()
                            if ret != 0:
                                self.log.error(f"[{self.put_manager.robot_arm.name}]机械臂移动到右等待位失败，返回值: {ret}")
                                break
                        if self._wait_take_arm_press_machine(place_right_verify, "right") != 0:
                            self.log.debug(f"[投放] 等待放{self.target_shoe}鞋完成，退出循环")
                            break
                    self.put_arm_state.advance(next_state)
                    self.log.debug(f"[投放] 状态更新为: {next_state.value}")
                    cur_state, _ = self.put_arm_state.get_state()
                    self.log.debug(f"[投放] 当前状态: {cur_state.value}")
                    self.put_shoes_count += 1
                case PutInArmStatus.PLACE_LEFT_SHOE:
                    start = time.time()
                    self.press_machine_state.set_state(left=PressMachineStatus.LEFT_WORKING)
                    ret_left = self.put_manager.handle_place_left_shoe(self.press_vision)
                    next_state = PutInArmStatus.SET_PRESS_MACHINE_LEFT if ret_left == 0 else PutInArmStatus.ERROR
                    self.put_arm_state.advance(next_state)
                    self.log.debug(f"[投放] 状态更新为: {next_state.value}")
                    self.log.debug(f"[投放] [耗时统计]放左鞋完成，耗时: {time.time() - start:.2f}秒, 返回值: {ret_left}")

                case PutInArmStatus.PLACE_RIGHT_SHOE:
                    start = time.time()
                    self.press_machine_state.set_state(right=PressMachineStatus.RIGHT_WORKING)
                    ret_right = self.put_manager.handle_place_right_shoe(self.press_vision)
                    next_state = PutInArmStatus.SET_PRESS_MACHINE_RIGHT if ret_right == 0 else PutInArmStatus.ERROR
                    self.put_arm_state.advance(next_state)
                    self.log.debug(f"[投放] [耗时统计]放右鞋完成，耗时: {time.time() - start:.2f}秒, 返回值: {ret_right}")

                case PutInArmStatus.SET_PRESS_MACHINE_LEFT:
                    vision = self.press_vision
                    cb = lambda : self._put_fetch_task("left")
                    self.log.debug(f"[投放] 设置压机回调，等待取{self.target_shoe}鞋完成，压机状态: {self.press_machine_state.get_states()}")    
                    self.put_manager.handle_set_press_machine(self.target_shoe, self.press_machine_manager, vision=vision, on_press_finished=cb)
                    next_state = PutInArmStatus.EXIT_FAR_USE_NEAR
                    self.put_arm_state.advance(next_state)
                case PutInArmStatus.SET_PRESS_MACHINE_RIGHT:
                    vision = self.press_vision
                    cb = lambda : self._put_fetch_task("right")
                    self.log.debug(f"[投放] 设置压机回调，等待取{self.target_shoe}鞋完成，压机状态: {self.press_machine_state.get_states()}")
                    self.put_manager.handle_set_press_machine(self.target_shoe, self.press_machine_manager, vision=vision, on_press_finished=cb)
                    next_state = PutInArmStatus.EXIT_FAR_USE_NEAR
                    self.put_arm_state.advance(next_state)
                case PutInArmStatus.EXIT_FAR_USE_NEAR:
                    self._start_shoe_data_prefetch()
                    ret_exit = 0
                    start = time.time()
                    if self.target_shoe != put_arm_type:
                        ret_exit = self.put_manager.handle_exit_far_use_near()
                    next_state = PutInArmStatus.GRAB_PENDING_SHOES if ret_exit == 0 else PutInArmStatus.ERROR
                    self.put_arm_state.advance(next_state)
                    self.log.debug(f"[投放] [耗时统计]退出远使用近完成，耗时: {time.time() - start:.2f}秒")
                case PutInArmStatus.ERROR:
                    self.log.error("[投放] 机械臂进入错误状态，终止循环")
                    break
                case _:
                    self.log.debug(f"[投放] 未知状态 {cur_state}，退出循环")
                    break
            
    def _task_take_out_press(self) -> None:
        """模拟将鞋子从压机取出。"""
        shoe_type :str = None
        pick_left_verify = []
        pick_right_verify = []
        if take_arm_type == "right":
            pick_left_verify = [PutInArmStatus.PLACE_LEFT_SHOE, PutInArmStatus.PLACE_RIGHT_SHOE, PutInArmStatus.EXIT_FAR_USE_NEAR]
            pick_right_verify = [PutInArmStatus.PLACE_RIGHT_SHOE]
        else:
            pick_left_verify = [PutInArmStatus.PLACE_LEFT_SHOE]
            pick_right_verify = [PutInArmStatus.PLACE_RIGHT_SHOE, PutInArmStatus.PLACE_LEFT_SHOE, PutInArmStatus.EXIT_FAR_USE_NEAR]
        ret = self.take_manager.move_to_initial_position()
        if ret != 0:
            return
        while not self._stopping():
            cur_state, _ = self.take_arm_state.get_state()
            match cur_state:
                case TakeOutArmStatus.FETCH_TASK:
                    shoe_type = self._get_fetch_task()
                    if shoe_type is None:
                        break

                    self.log.info(f"[取出] 获取取出任务: {shoe_type}")
                    if shoe_type == 'left':
                        next_state = TakeOutArmStatus.PICK_LEFT_SHOE
                        if self._wait_put_arm_state(pick_left_verify, "左") != 0:
                            break
                        self.take_arm_state.advance(next_state)
                    elif shoe_type == 'right':
                        next_state = TakeOutArmStatus.PICK_RIGHT_SHOE
                        if self._wait_put_arm_state(pick_right_verify, "右") != 0:
                            break
                        self.take_arm_state.advance(next_state)
                    else:
                        self.log.error(f"[取出] 获取到未知鞋子类型: {shoe_type},退出循环")
                        break
                    self.log.debug(f"[取出] 状态更新为: {next_state.value}")
                case TakeOutArmStatus.PICK_RIGHT_SHOE:
                    start = time.time()
                    ret_pick_r = self.take_manager.handle_pick_right()
                    self.press_machine_state.set_state(right=PressMachineStatus.RIGHT_IDLE)
                    next_state = TakeOutArmStatus.ERROR if ret_pick_r != 0 else TakeOutArmStatus.EXIT_FAR_USE_NEAR
                    self.take_arm_state.advance(next_state)
                    self.log.debug(f"[取出] [耗时统计]取右鞋完成，耗时: {time.time() - start:.2f}秒, 返回值: {ret_pick_r}")
                case TakeOutArmStatus.PICK_LEFT_SHOE:
                    start = time.time()
                    ret_pick_l = self.take_manager.handle_pick_left()
                    self.press_machine_state.set_state(left=PressMachineStatus.LEFT_IDLE)
                    next_state = TakeOutArmStatus.ERROR if ret_pick_l != 0 else TakeOutArmStatus.EXIT_FAR_USE_NEAR
                    self.take_arm_state.advance(next_state)
                    self.log.debug(f"[取出] [耗时统计]取左鞋完成，耗时: {time.time() - start:.2f}秒, 返回值: {ret_pick_l}")
                case TakeOutArmStatus.PLACE_LEFT_SHOE:
                    ret_place_l = self.take_manager.handle_place_left()
                    if ret_place_l == 0:
                        self.take_arm_place_left_count += 1
                    next_state = TakeOutArmStatus.ERROR if ret_place_l != 0 else TakeOutArmStatus.FETCH_TASK
                    self.take_arm_state.advance(next_state)
                    self.log.info(f"[取出] 放左鞋完成，当前完成数量：左鞋{self.take_arm_place_left_count}，右鞋{self.take_arm_place_right_count}")
                case TakeOutArmStatus.PLACE_RIGHT_SHOE:
                    ret_place_r = self.take_manager.handle_place_right()
                    if ret_place_r == 0:
                        self.take_arm_place_right_count += 1
                    next_state = TakeOutArmStatus.ERROR if ret_place_r != 0 else TakeOutArmStatus.FETCH_TASK
                    self.take_arm_state.advance(next_state)
                    self.log.info(f"[取出] 放右鞋完成，当前完成数量：左鞋{self.take_arm_place_left_count}，右鞋{self.take_arm_place_right_count}")
                case TakeOutArmStatus.EXIT_FAR_USE_NEAR:
                    start = time.time()
                    ret_exit_take = 0
                    if shoe_type != take_arm_type:
                        ret_exit_take = self.take_manager.handle_exit_far_use_near()
                    if ret_exit_take != 0:
                        next_state = TakeOutArmStatus.ERROR
                    elif shoe_type == 'left':
                        next_state = TakeOutArmStatus.PLACE_LEFT_SHOE
                    elif shoe_type == 'right':
                        next_state = TakeOutArmStatus.PLACE_RIGHT_SHOE
                    else:
                        next_state = TakeOutArmStatus.ERROR
                    self.take_arm_state.advance(next_state)
                    self.log.debug(f"[取出] [耗时统计]退出远使用近完成，耗时: {time.time() - start:.2f}秒, 返回值: {ret_exit_take}")
                case TakeOutArmStatus.ERROR:
                    self.log.error("[取出] 机械臂进入错误状态，终止循环")
                    break
                case TakeOutArmStatus.EXIT_WORKFLOW:
                    self.log.info("[取出] 取出流程退出自动循环")
                    break
                case _:
                    self.log.debug(f"[取出] 未知状态 {cur_state}，退出循环")
                    break

