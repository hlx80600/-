from RSDT_Simple_Automation.automation_machine import automationMachine
import numpy as np
from pathlib import Path
import yaml

from position_obb import OBBOnlyDetector
from position_artifacts import save_detection_artifacts
from position_detector import detect_with_roi_filter
from position_geometry import (
    build_robot_xyz_offset,
)
from postion_slot_check import is_slot_position_good


DEFAULT_POSITION_CONFIG_PATH = Path(__file__).resolve().parent / "position_config.yaml"


class Position:
    """封装压杆对位和卡鞋检测相关的视觉逻辑。"""

    __rod_yolo4d: OBBOnlyDetector = None
    __CONFIG_ATTRS = {
        "cam_left_sn",
        "cam_right_sn",
        "cam_left_K",
        "cam_right_K",
        "cam_left_color_exposure",
        "cam_left_color_gain",
        "cam_right_color_exposure",
        "cam_right_color_gain",
        "rod_obb_model_path",
        "cam_left_robot_base_rotation_degrees",
        "cam_right_robot_base_rotation_degrees",
        "rgb_resolution_setting",
        "depth_resolution_setting",
        "fps_setting",
        "measurement_retry_count",
        "rod_obb_img_size",
        "rod_obb_detection_conf",
        "rod_default_shift",
        "cam_left_rod_preset_xyz",
        "cam_right_rod_preset_xyz",
        "cam_left_gripper_preset_xyz",
        "cam_right_gripper_preset_xyz",
        "roi_width",
        "roi_height",
        "cam_left_rod_roi",
        "cam_right_rod_roi",
        "slot_check_model_path",
        "slot_check_conf",
        "slot_check_img_size",
        "slot_check_depth_alpha",
        "slot_check_depth_beta",
        "slot_check_color_start_depth_mm",
        "slot_check_color_range_mm",
        "slot_check_min_depth_mm",
        "slot_check_max_depth_mm",
        "slot_check_roi_start",
        "slot_check_roi_size",
        "enable_detection_artifact_save",
        "detection_artifact_root",
    }

    # ===== 初始化与基础能力 =====

    def __load_config(self, config_path: str | Path) -> dict:
        config_path = Path(config_path).expanduser()
        if not config_path.is_absolute():
            config_path = Path(__file__).resolve().parent / config_path
        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                config = yaml.safe_load(config_file) or {}
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"定位配置文件不存在: {config_path}") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"定位配置文件解析失败: {config_path}: {exc}") from exc

        missing_keys = sorted(self.__CONFIG_ATTRS - set(config.keys()))
        if missing_keys:
            raise ValueError(
                f"定位配置文件缺少配置项: {', '.join(missing_keys)} ({config_path})"
            )

        artifact_root = Path(config["detection_artifact_root"]).expanduser()
        if not artifact_root.is_absolute():
            artifact_root = Path(__file__).resolve().parent / artifact_root
        config["detection_artifact_root"] = artifact_root
        return config

    def __apply_config(self, config: dict) -> None:
        for key in self.__CONFIG_ATTRS:
            setattr(self, f"_Position__{key}", config[key])

    def __get_camera_config_prefix(self, camera_id: int) -> str | None:
        if camera_id == 1:
            return "cam_left"
        if camera_id == 2:
            return "cam_right"
        return None

    def __build_slot_check_config(self) -> dict:
        return {
            "model_path": self.__slot_check_model_path,
            "img_conf": self.__slot_check_conf,
            "img_size": self.__slot_check_img_size,
            "alpha": self.__slot_check_depth_alpha,
            "beta": self.__slot_check_depth_beta,
            "color_start_depth_mm": self.__slot_check_color_start_depth_mm,
            "color_range_mm": self.__slot_check_color_range_mm,
            "min_depth_mm": self.__slot_check_min_depth_mm,
            "max_depth_mm": self.__slot_check_max_depth_mm,
            "roi_start": self.__slot_check_roi_start,
            "roi_size": self.__slot_check_roi_size,
        }

    def __init_camera(
        self,
        machine: automationMachine,
        alias: str,
        sn: str,
        side_name: str,
        color_exposure: int,
        color_gain: int,
    ):
        """初始化并连接指定侧的 Orbbec 相机。

        Args:
            machine: 自动化设备对象，内部持有硬件模块。
            alias: 相机在硬件模块中的别名。
            sn: 相机序列号。
            side_name: 侧别名称，用于错误提示。
            color_exposure: 彩色图曝光参数。
            color_gain: 彩色图增益参数。

        Returns:
            已连接成功的相机对象。

        Raises:
            RuntimeError: 相机对象未创建或连接失败。
        """
        machine.hardwareModule.activate_orbbec_camera(alias, sn)
        cam = machine.hardwareModule.orbbec_camera_dict.get(alias)
        if cam is None:
            raise RuntimeError(
                f"{side_name}相机初始化失败: alias={alias}, sn={sn}, 未获取到相机对象"
            )

        flag = cam.connect_camera(
            self.__rgb_resolution_setting,
            self.__depth_resolution_setting,
            self.__fps_setting,
            color_exposure=color_exposure,
            color_gain=color_gain,
        )
        if not flag:
            raise RuntimeError(f"{side_name}相机连接失败: alias={alias}, sn={sn}")
        return cam

    def __init__(
        self,
        machine: automationMachine,
        artifact_save: bool | None = None,
        config_path: str | Path = DEFAULT_POSITION_CONFIG_PATH,
    ):
        """初始化定位模块，完成相机、方向向量和检测模型加载。

        Args:
            machine: 提供硬件访问能力的自动化设备对象。
            artifact_save: 是否启用检测产物保存；传入 None 时使用 YAML 配置。
            config_path: 定位参数 YAML 配置路径。
        """
        self.__apply_config(self.__load_config(config_path))
        self.__slot_check_config = self.__build_slot_check_config()
        if artifact_save is not None:
            self.__enable_detection_artifact_save = artifact_save

        self.__cam_left = self.__init_camera(
            machine,
            "cam1",
            self.__cam_left_sn,
            "左侧",
            self.__cam_left_color_exposure,
            self.__cam_left_color_gain,
        )
        self.__cam_right = self.__init_camera(
            machine,
            "cam2",
            self.__cam_right_sn,
            "右侧",
            self.__cam_right_color_exposure,
            self.__cam_right_color_gain,
        )

        self.__cam_left_cov = self.__convert_to_robot_base_coords(
            self.__cam_left_robot_base_rotation_degrees, [1, 0, 0]
        )
        self.__cam_right_cov = self.__convert_to_robot_base_coords(
            self.__cam_right_robot_base_rotation_degrees, [1, 0, 0]
        )

        self.__rod_yolo4d = OBBOnlyDetector(self.__rod_obb_model_path)
        self.__rod_yolo4d.set_parameters(
            obb_img_size=self.__rod_obb_img_size,
            obb_detection_conf=self.__rod_obb_detection_conf,
        )

    # ===== ROI 配置与检测执行 =====

    def __get_detection_roi(self, camera_id: int, task_name: str):
        """获取指定相机、任务对应的 ROI 配置。"""
        if camera_id not in (1, 2):
            return None
        if task_name != "rod":
            return None
        config_prefix = self.__get_camera_config_prefix(camera_id)
        roi = getattr(self, f"_Position__{config_prefix}_{task_name}_roi", None)
        if not roi:
            return None
        if len(roi) != 1 or len(roi[0]) != 2:
            raise ValueError(
                f"相机{camera_id} {task_name} ROI 配置错误，应为 [[x1, y1]]，当前为 {roi}"
            )
        x1, y1 = roi[0]
        return [[x1, y1], [x1 + self.__roi_width, y1 + self.__roi_height]]

    def __detect_with_roi_filter(
        self,
        detector: OBBOnlyDetector,
        rgb_image,
        depth_image,
        fx: float,
        fy: float,
        cx: float,
        cy: float,
        shift,
        roi=None,
    ):
        """委托 ``position_detector`` 执行全图检测和 ROI 过滤。

        Args:
            detector: OBBOnlyDetector 检测器实例。
            rgb_image: 原始 RGB 图像。
            depth_image: 与 RGB 对齐的深度图像。
            fx: 相机 x 方向焦距。
            fy: 相机 y 方向焦距。
            cx: 主点 x 坐标。
            cy: 主点 y 坐标。
            shift: 类别偏移配置。
            roi: 可选 ROI，格式为 ``[[x1, y1], [x2, y2]]``。

        Returns:
            与检测器一致的二元组 ``(result, detect_result_image)``。
        """
        return detect_with_roi_filter(
            detector=detector,
            rgb_image=rgb_image,
            depth_image=depth_image,
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            shift=shift,
            roi=roi,
        )

    # ===== 检测结果归档 =====

    def __save_detection_artifacts(
        self,
        task_name: str,
        camera_id: int,
        rgb_image,
        detect_result_image,
        result,
        extra_info: dict | None = None,
        depth_image=None,
    ):
        """委托 ``position_artifacts`` 保存检测产物。

        Args:
            task_name: 逻辑任务名称。
            camera_id: 相机编号。
            rgb_image: 原始 RGB 图像。
            detect_result_image: 检测结果可视化图像。
            result: 原始检测结果列表。
            extra_info: 额外元数据字典。
            depth_image: 可选的深度图像，保存为 16-bit PNG。

        Returns:
            ``None``。函数通过文件写入完成保存。
        """
        save_detection_artifacts(
            enabled=self.__enable_detection_artifact_save,
            root=self.__detection_artifact_root,
            task_name=task_name,
            camera_id=camera_id,
            rgb_image=rgb_image,
            detect_result_image=detect_result_image,
            result=result,
            extra_info=extra_info,
            depth_image=depth_image,
        )

    # ===== 图像采集 =====

    def __get_image(self, id: int):
        """采集指定相机的一帧图像。

        会先丢弃几帧缓存，再返回最新的 RGB 图、深度图和伪彩深度图。

        Args:
            id: 相机编号，`1` 为左相机，`2` 为右相机。

        Returns:
            `(rgb_image, depth_image, color_depth)` 三元组。若相机未启用则返回空值。
        """
        rgb_image, depth_image, color_depth = None, None, None

        def safe_get_one_frame(cam, camera_id: int):
            try:
                return cam.get_one_frame(True)
            except Exception as exc:
                print(f"相机{camera_id}取帧异常: {exc}")
                return None, None, None

        if id == 1:
            # for _ in range(3):
            #     _, _, _ = safe_get_one_frame(self.__cam_left, 1)
            rgb_image, depth_image, color_depth = safe_get_one_frame(self.__cam_left, 1)
        elif id == 2:
            # for _ in range(3):
            #     _, _, _ = safe_get_one_frame(self.__cam_right, 2)
            rgb_image, depth_image, color_depth = safe_get_one_frame(self.__cam_right, 2)
        return rgb_image, depth_image, color_depth

    # ===== 压杆测距流程 =====

    def get_rod_distance(self, id: int):
        """计算鞋与压杆在 X 方向上的 3D 距离。

        压杆的 X 坐标使用左/右相机对应的预设值，
        仅通过 YOLO 检测鞋（class_id=0）的空间位置。

        Args:
            id: 相机编号，`1` 为左相机，`2` 为右相机。

        Returns:
            `(find_flag, distance, detect_result_image)`：
            - `find_flag`: 是否成功检测到鞋
            - `distance`: 鞋 X_3D 减去预设压杆 X 的距离，单位米
            - `detect_result_image`: 检测结果图
        """
        if id == 1:
            preset_xyz = self.__cam_left_rod_preset_xyz
        elif id == 2:
            preset_xyz = self.__cam_right_rod_preset_xyz
        else:
            print(f"不支持的相机id: {id}")
            return False, None, None
        return self.__get_distance_to_preset(id, preset_xyz, "rod_distance")

    def get_gripper_distance(self, id: int):
        """计算鞋楦与夹抓在 X 方向上的 3D 距离。

        夹抓的 X 坐标使用左/右相机对应的预设值，
        仅通过 YOLO 检测鞋（class_id=0）的空间位置。

        Args:
            id: 相机编号，`1` 为左相机，`2` 为右相机。

        Returns:
            `(find_flag, distance, detect_result_image)`：
            - `find_flag`: 是否成功检测到鞋
            - `distance`: 鞋 X_3D 减去预设夹抓 X 的距离，单位米
            - `detect_result_image`: 检测结果图
        """
        if id == 1:
            preset_xyz = self.__cam_left_gripper_preset_xyz
        elif id == 2:
            preset_xyz = self.__cam_right_gripper_preset_xyz
        else:
            print(f"不支持的相机id: {id}")
            return False, None, None
        return self.__get_distance_to_preset(id, preset_xyz, "gripper_distance")

    def __get_distance_to_preset(self, id: int, preset_xyz: list[float], task_name: str):
        """计算鞋与指定预设点在 X 方向上的 3D 距离（通用实现）。

        通过 YOLO 检测鞋（class_id=0）的空间位置，
        然后与预设坐标的 X 分量做差值。

        Args:
            id: 相机编号，`1` 为左相机，`2` 为右相机。
            preset_xyz: 预设点在相机坐标系下的 `[x, y, z]` 坐标（米）。
            task_name: 用于日志和归档的任务名称。

        Returns:
            `(find_flag, distance, detect_result_image)`：
            - `find_flag`: 是否成功检测到鞋
            - `distance`: 鞋 X_3D 减去预设 X 的距离，单位米
            - `detect_result_image`: 检测结果图
        """
        xie_class_id = 0

        cam_K = None
        if id == 1:
            cam_K = self.__cam_left_K
        elif id == 2:
            cam_K = self.__cam_right_K
        else:
            print(f"不支持的相机id: {id}")
            return False, None, None

        last_detect_result_image = None
        last_failure_snapshot = None
        roi = self.__get_detection_roi(id, "rod")
        for attempt in range(1, self.__measurement_retry_count + 1):
            print(
                f"[{task_name}] 相机{id}开始第 {attempt}/{self.__measurement_retry_count} 次测量"
            )
            rgb_image, depth_image, color_depth = self.__get_image(id)
            if rgb_image is None or depth_image is None:
                print(f"相机{id}获取图像失败，第{attempt}次测量未成功")
                continue

            result, detect_result_image = self.__detect_with_roi_filter(
                self.__rod_yolo4d,
                rgb_image,
                depth_image,
                cam_K[0],
                cam_K[1],
                cam_K[2],
                cam_K[3],
                self.__rod_default_shift,
                roi=roi,
            )
            if detect_result_image is None:
                detect_result_image = rgb_image
            last_detect_result_image = detect_result_image

            # 从检测结果中筛选鞋（class_id=0）且有深度的目标
            xie_x_3d = None
            for detection in result or []:
                has_depth = detection[0]
                class_id = detection[1]
                x_3d = detection[5]

                if not has_depth:
                    continue
                if class_id == xie_class_id:
                    xie_x_3d = x_3d
                    break

            if xie_x_3d is not None:
                distance = xie_x_3d - preset_xyz[0]
                print(
                    f"[{task_name}] 相机{id}第 {attempt}/{self.__measurement_retry_count} 次测量成功，"
                    f"shoe_x={xie_x_3d:.6f}, preset_x={preset_xyz[0]:.6f}, "
                    f"distance={distance:.6f} m"
                )
                return True, distance, detect_result_image

            last_failure_snapshot = {
                "rgb_image": rgb_image,
                "depth_image": depth_image,
                "detect_result_image": detect_result_image,
                "result": result,
                "extra_info": {
                    "attempt": attempt,
                    "retry_limit": self.__measurement_retry_count,
                    "success": False,
                    "reason": "未检测到鞋或鞋缺少深度信息",
                },
            }
            print(f"未检测到鞋（或缺少深度），第{attempt}次测量未成功")

        if last_failure_snapshot is not None:
            self.__save_detection_artifacts(
                task_name=task_name,
                camera_id=id,
                rgb_image=last_failure_snapshot["rgb_image"],
                detect_result_image=last_failure_snapshot["detect_result_image"],
                result=last_failure_snapshot["result"],
                extra_info=last_failure_snapshot["extra_info"],
                depth_image=last_failure_snapshot["depth_image"],
            )
        print(
            f"[{task_name}] 相机{id}连续 {self.__measurement_retry_count} 次测量失败"
        )
        return False, None, last_detect_result_image


    def get_rod_robot_offset(self, id: int):
        """计算鞋楦与夹抓的距离，并转换为机械臂基坐标系下的 XYZ 分量。

        内部调用 ``get_gripper_distance`` 获取相机 X 方向的标量距离，
        再通过方向向量 ``cov`` 分解到机器人基坐标系的各轴。

        Args:
            id: 相机编号，`1` 为左相机，`2` 为右相机。

        Returns:
            `(find_flag, robot_xyz_offset, detect_result_image)`：
            - `find_flag`: 是否成功检测到鞋
            - `robot_xyz_offset`: 机械臂基坐标系下的 `[dx, dy, dz]`，单位米
            - `detect_result_image`: 检测结果图
        """
        find_flag, distance, detect_result_image = self.get_gripper_distance(id)
        if not find_flag or distance is None:
            return False, None, detect_result_image
        robot_xyz_offset = self.__build_robot_xyz_offset(id, distance)
        print(
            f"[get_rod_robot_offset] 相机{id} distance={distance:.6f} m -> "
            f"robot_xyz_offset={robot_xyz_offset}"
        )
        return True, robot_xyz_offset, detect_result_image

    # ===== 坐标系转换 =====

    def __build_robot_xyz_offset(self, camera_id: int, distance: float):
        return build_robot_xyz_offset(
            camera_id, distance, self.__cam_left_cov, self.__cam_right_cov
        )

    def __convert_to_robot_base_coords(
        self, rotation_degrees: list[float], init_direction_vector: list
    ):
        """把初始方向向量旋转到机器人基坐标系下。

        Args:
            rotation_degrees: 按 `[x_degree, y_degree, z_degree]` 给出的旋转角度。
            init_direction_vector: 初始方向向量，格式为 `[x, y, z]`。

        Returns:
            旋转后的方向向量列表。

        Raises:
            ValueError: 旋转角度格式或方向向量格式不合法。
        """
        if len(rotation_degrees) != 3:
            raise ValueError(
                "机器人基坐标系旋转角度格式错误,应为[x_degree, y_degree, z_degree]"
            )

        x_degree, y_degree, z_degree = rotation_degrees

        if init_direction_vector.__len__() != 3:
            raise ValueError("初始速度方向向量格式错误,应为[x,y,z]")

        speed_direction_vector = np.array(init_direction_vector).reshape(3, 1)
        speed = np.linalg.norm(speed_direction_vector)
        if speed == 0:
            raise ValueError("初始速度方向向量不能为零向量")
        norm_direction_vector = speed_direction_vector / speed

        x_rotation_matrix = np.array(
            [
                [1, 0, 0],
                [0, np.cos(np.radians(x_degree)), -np.sin(np.radians(x_degree))],
                [0, np.sin(np.radians(x_degree)), np.cos(np.radians(x_degree))],
            ]
        )

        y_rotation_matrix = np.array(
            [
                [np.cos(np.radians(y_degree)), 0, np.sin(np.radians(y_degree))],
                [0, 1, 0],
                [-np.sin(np.radians(y_degree)), 0, np.cos(np.radians(y_degree))],
            ]
        )

        z_rotation_matrix = np.array(
            [
                [np.cos(np.radians(z_degree)), -np.sin(np.radians(z_degree)), 0],
                [np.sin(np.radians(z_degree)), np.cos(np.radians(z_degree)), 0],
                [0, 0, 1],
            ]
        )

        converted_direction_vector = (
            z_rotation_matrix
            @ y_rotation_matrix
            @ x_rotation_matrix
            @ norm_direction_vector
        )

        return (converted_direction_vector * speed).flatten().tolist()


    # 检测是否卡鞋
    def is_slot_ok(self, id: int):
        
        rgb_image, depth_image, _ = self.__get_image(id)
        if rgb_image is None or depth_image is None:
            print(f"相机{id}获取图像失败")

        return is_slot_position_good(rgb_image, depth_image, **self.__slot_check_config)
