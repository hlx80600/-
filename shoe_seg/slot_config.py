from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, degrees
from pathlib import Path

import numpy as np
import yaml


class _FlowSequence(list):
    """Marker list type for YAML inline sequences."""


class _SlotConfigDumper(yaml.SafeDumper):
    pass


def _represent_flow_sequence(dumper: yaml.SafeDumper, data: _FlowSequence) -> yaml.nodes.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(data), flow_style=True)


_SlotConfigDumper.add_representer(_FlowSequence, _represent_flow_sequence)


def _as_float_list(values: object, field_name: str, expected_len: int | None = None) -> list[float]:
    if not isinstance(values, (list, tuple)):
        raise RuntimeError(f"{field_name} must be a list")
    result = [float(v) for v in values]
    if expected_len is not None and len(result) != expected_len:
        raise RuntimeError(f"{field_name} must contain {expected_len} values")
    return result


def _as_float_matrix(values: object, field_name: str, expected_cols: int) -> list[list[float]]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise RuntimeError(f"{field_name} must be a list of lists")
    return [
        _as_float_list(row, f"{field_name}[{idx}]", expected_len=expected_cols)
        for idx, row in enumerate(values)
    ]


def _as_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if float(value) in {0.0, 1.0}:
            return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise RuntimeError(f"{field_name} must be a boolean value")


def _normalize_vector(vector: np.ndarray, field_name: str) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        raise RuntimeError(f"{field_name} is too small to normalize")
    return vector / norm


def _project_xy_onto_line_xy(
    p0: np.ndarray,
    p1: np.ndarray,
    x: float,
    y: float,
) -> tuple[float, float, float, float]:
    pt = np.array([float(x), float(y)], dtype=float)
    line_vec = p1 - p0
    line_len = float(np.linalg.norm(line_vec))
    if line_len < 1e-6:
        raise RuntimeError("line endpoints are too close to define a line")

    line_dir = line_vec / line_len
    rel = pt - p0
    t = float(np.dot(rel, line_dir) / line_len)
    proj = p0 + t * line_len * line_dir
    perp = np.array([-line_dir[1], line_dir[0]], dtype=float)
    signed_dist = float(np.dot(rel, perp))
    return float(proj[0]), float(proj[1]), t, signed_dist


def project_xy_onto_line(
    x: float,
    y: float,
    slot_config: SlotConfig,
    line_p0: np.ndarray | None = None,
    line_p1: np.ndarray | None = None,
) -> tuple[float, float, float, float]:
    """将 (x, y) 投影到示教直线所在无限直线上。

    line_p0 / line_p1 默认取 slot_config.gripper_refer_pose 的前两个示教点。

    Returns:
        (proj_x, proj_y, t, signed_dist)
        t: 线段参数，p0 处为 0、p1 处为 1（不 clamp，可超出 [0, 1]）
        signed_dist: 点到直线的带符号垂直距离 (mm)
    """
    if line_p0 is None or line_p1 is None:
        poses = slot_config.gripper_refer_pose
        if len(poses) < 2:
            raise RuntimeError("slot config missing gripper_refer_pose with at least 2 poses")
        if line_p0 is None:
            line_p0 = np.array([float(poses[0][0]), float(poses[0][1])], dtype=float)
        if line_p1 is None:
            line_p1 = np.array([float(poses[1][0]), float(poses[1][1])], dtype=float)

    p0 = np.asarray(line_p0, dtype=float)[:2]
    p1 = np.asarray(line_p1, dtype=float)[:2]
    return _project_xy_onto_line_xy(p0, p1, x, y)


def _wrap_inline_sequences(value: object) -> object:
    if isinstance(value, dict):
        return {key: _wrap_inline_sequences(item) for key, item in value.items()}
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        wrapped_items = [_wrap_inline_sequences(item) for item in value]
        if all(not isinstance(item, (list, dict)) for item in wrapped_items):
            return _FlowSequence(wrapped_items)
        return wrapped_items
    return value


@dataclass(slots=True)
class SlotLine:
    point1: list[float]
    point2: list[float]
    direction: list[float] | None = None
    normal: list[float] | None = None
    c: float | None = None
    angle_deg: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SlotLine":
        return cls(
            point1=_as_float_list(data.get("point1"), "slot_line.point1", expected_len=2),
            point2=_as_float_list(data.get("point2"), "slot_line.point2", expected_len=2),
            direction=(
                _as_float_list(data["direction"], "slot_line.direction", expected_len=2)
                if data.get("direction") is not None
                else None
            ),
            normal=(
                _as_float_list(data["normal"], "slot_line.normal", expected_len=2)
                if data.get("normal") is not None
                else None
            ),
            c=float(data["c"]) if data.get("c") is not None else None,
            angle_deg=float(data["angle_deg"]) if data.get("angle_deg") is not None else None,
        )

    @classmethod
    def fit_from_points(cls, points_xy: np.ndarray) -> "SlotLine":
        points = np.asarray(points_xy, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
            raise RuntimeError("slot_xy_points must contain at least 2 XY points")

        centroid = np.mean(points, axis=0)
        centered = points - centroid
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        direction = _normalize_vector(vh[0], "fitted slot line direction")
        normal = np.array([-direction[1], direction[0]], dtype=float)

        return cls(
            point1=[float(centroid[0]), float(centroid[1])],
            point2=[float(centroid[0] + direction[0]), float(centroid[1] + direction[1])],
            direction=[float(direction[0]), float(direction[1])],
            normal=[float(normal[0]), float(normal[1])],
            c=float(-np.dot(normal, centroid)),
            angle_deg=float(degrees(atan2(direction[1], direction[0]))),
        )

    def point1_xy(self) -> np.ndarray:
        return np.asarray(self.point1, dtype=float)

    def point2_xy(self) -> np.ndarray:
        return np.asarray(self.point2, dtype=float)

    def resolved_direction(self) -> np.ndarray:
        if self.direction is not None:
            return _normalize_vector(np.asarray(self.direction, dtype=float), "slot_line.direction")
        return _normalize_vector(self.point2_xy() - self.point1_xy(), "slot_line point delta")

    def resolved_normal(self) -> np.ndarray:
        if self.normal is not None:
            return _normalize_vector(np.asarray(self.normal, dtype=float), "slot_line.normal")
        direction = self.resolved_direction()
        return np.array([-direction[1], direction[0]], dtype=float)

    def aligned_direction(self, reference_direction: np.ndarray | None = None) -> np.ndarray:
        direction = self.resolved_direction()
        if reference_direction is not None:
            ref = _normalize_vector(np.asarray(reference_direction, dtype=float), "reference direction")
            # SVD 拟合出的直线方向没有固定正负；用参考方向把它翻到槽内前进方向这一侧。
            if float(np.dot(direction, ref)) < 0.0:
                direction = -direction
        return direction

    def to_dict(self) -> dict[str, object]:
        direction = self.resolved_direction()
        normal = self.resolved_normal()
        c_value = self.c
        if c_value is None:
            c_value = float(-np.dot(normal, self.point1_xy()))
        angle_value = self.angle_deg
        if angle_value is None:
            angle_value = float(degrees(atan2(direction[1], direction[0])))

        return {
            "point1": [float(v) for v in self.point1],
            "point2": [float(v) for v in self.point2],
            "direction": [float(v) for v in direction],
            "normal": [float(v) for v in normal],
            "c": float(c_value),
            "angle_deg": float(angle_value),
        }


@dataclass(slots=True)
class SlotCamera:
    name: str
    sn: str
    rgb_resolution: list[int] = field(default_factory=lambda: [640, 480])
    depth_resolution: list[int] = field(default_factory=lambda: [640, 480])
    fps: int = 15

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SlotCamera":
        name = data.get("name")
        sn = data.get("sn")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("camera.name must be a non-empty string")
        if not isinstance(sn, str) or not sn.strip():
            raise RuntimeError("camera.sn must be a non-empty string")
        rgb_resolution = _as_float_list(
            data.get("rgb_resolution", [640, 480]),
            "camera.rgb_resolution",
            expected_len=2,
        )
        depth_resolution = _as_float_list(
            data.get("depth_resolution", [640, 480]),
            "camera.depth_resolution",
            expected_len=2,
        )
        fps_raw = data.get("fps", 15)
        return cls(
            name=name.strip(),
            sn=sn.strip(),
            rgb_resolution=[int(v) for v in rgb_resolution],
            depth_resolution=[int(v) for v in depth_resolution],
            fps=int(fps_raw),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "sn": self.sn,
            "rgb_resolution": [int(v) for v in self.rgb_resolution],
            "depth_resolution": [int(v) for v in self.depth_resolution],
            "fps": int(self.fps),
        }


@dataclass(slots=True)
class SlotConfig:
    """左/右鞋槽放鞋几何配置，对应 left_slot.yaml / right_slot.yaml。"""

    # 鞋槽弧形边缘 XY 采样点 [[x, y], ...] (mm)；可选，有 ≥2 点时用于 SVD 拟合槽中心线
    slot_xy_points: list[list[float]] = field(default_factory=list)
    # 鞋槽垂直范围 [z_min, z_max] (mm)；主流程主要使用 z_min 参与 TCP 高度约束
    z_heights: list[float] = field(default_factory=list)
    # 夹爪在槽附近的参考位姿 [[x,y,z,rx,ry,rz], ...] (mm/deg)；前 2 点定义 XY 引导线与 RZ 范围
    gripper_refer_pose: list[list[float]] = field(default_factory=list)
    # 鞋头弧线目标点 [x, y] (mm)，机器人基坐标；调插入深浅时优先改此字段
    target_point: list[float] | None = None
    # 抓取工位鞋底最低点 z (mm)，用于构造 toe_lower_in_tcp
    shoe_origin_z_min: float | None = None
    # 当前抓取 TCP z 与传送带 z 的高度差 (mm)，运行时回写
    cur_tcp_to_sole_dist: float | None = None
    # 鞋头最低点相对槽底 z_min 的目标偏移 (mm)，参与 C4 高度约束
    toe_z_offset: float = 20.0
    # 对位阶段沿槽内前进方向的额外移动距离 (mm)
    align_forward_move_distance: float = 0.0
    # 绕鞋头旋转 pivot 相对 place_z 的高度偏移 (mm)
    rotate_dh: float = 20.0
    # 鞋头对位分类模型路径（相对工作区或绝对路径）
    model_path: str | None = None
    # 鞋头对位相机 ROI，[[x1, y1], [x2, y2]] 像素坐标
    slot_roi: list[list[int]] | None = None
    # 鞋头弧线提取是否启用 SAM 分割
    use_sam: bool = False
    # 鞋头弧线点列 [[x,y,z], ...] (mm)，TCP 坐标系；由 compute_shoe_target_position 运行时回写
    toe_arc_in_tcp: list[list[float]] = field(default_factory=list)
    # 鞋头下边界点列 [[x,y,z], ...] (mm)，TCP 坐标系；依赖 shoe_origin_z_min 运行时回写
    toe_lower_in_tcp: list[list[float]] | None = None
    # 法兰在 TCP 下的方向向量 [x,y,z]；历史兼容字段，当前 YAML 通常不写
    flange_vec_in_tcp: list[float] | None = None
    # 槽位对位相机 name/sn/分辨率/fps
    camera: SlotCamera | None = None
    # 未列入正式字段的 YAML 键（如 arm_ip），原样保留供示教/测试脚本读取
    extra_fields: dict[str, object] = field(default_factory=dict)

    @classmethod
    def load_yaml(cls, path: str | Path) -> "SlotConfig":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Slot config must be a mapping: {config_path}")
        return cls.from_dict(data)

    @classmethod
    def load_yaml_or_default(cls, path: str | Path) -> "SlotConfig":
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        return cls.load_yaml(config_path)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SlotConfig":
        target_point = (
            _as_float_list(data["target_point"], "target_point", expected_len=2)
            if data.get("target_point") is not None
            else None
        )
        known_keys = {
            "slot_xy_points",
            "target_point",
            "slot_line",
            "toe_arc_in_tcp",
            "flange_vec_in_tcp",
            "z_heights",
            "shoe_origin_z_min",
            "cur_tcp_to_sole_dist",
            "toe_z_offset",
            "align_forward_move_distance",
            "rotate_dh",
            "model_path",
            "slot_roi",
            "use_sam",
            "toe_lower_in_tcp",
            "gripper_refer_pose",
            "camera",
        }

        toe_lower_data = data.get("toe_lower_in_tcp")
        toe_lower = None
        if toe_lower_data is not None:
            toe_lower = _as_float_matrix(toe_lower_data, "toe_lower_in_tcp", expected_cols=3)

        flange_vec = None
        if data.get("flange_vec_in_tcp") is not None:
            flange_vec = _as_float_list(data["flange_vec_in_tcp"], "flange_vec_in_tcp", expected_len=3)

        z_heights_raw = data.get("z_heights")
        z_heights = _as_float_list(z_heights_raw, "z_heights", expected_len=2) if z_heights_raw is not None else []

        shoe_origin_z_min = data.get("shoe_origin_z_min")
        cur_tcp_to_sole_dist = data.get("cur_tcp_to_sole_dist")
        toe_z_offset_raw = data.get("toe_z_offset", 20.0)
        align_forward_move_distance_raw = data.get("align_forward_move_distance", 0.0)
        rotate_dh_raw = data.get("rotate_dh", 20.0)
        model_path_raw = data.get("model_path")
        model_path = None
        if model_path_raw is not None:
            if not isinstance(model_path_raw, str) or not model_path_raw.strip():
                raise RuntimeError("model_path must be a non-empty string")
            model_path = model_path_raw.strip()
        slot_roi_raw = data.get("slot_roi")
        slot_roi = None
        if slot_roi_raw is not None:
            rows = _as_float_matrix(slot_roi_raw, "slot_roi", expected_cols=2)
            if len(rows) != 2:
                raise RuntimeError("slot_roi must contain exactly 2 points")
            slot_roi = [[int(v) for v in row] for row in rows]
        use_sam_raw = data.get("use_sam", False)
        camera_raw = data.get("camera")
        camera = None
        if camera_raw is not None:
            if not isinstance(camera_raw, dict):
                raise RuntimeError("camera must be a mapping with name and sn")
            camera = SlotCamera.from_dict(camera_raw)
        return cls(
            slot_xy_points=_as_float_matrix(data.get("slot_xy_points"), "slot_xy_points", expected_cols=2),
            z_heights=z_heights,
            gripper_refer_pose=_as_float_matrix(
                data.get("gripper_refer_pose"),
                "gripper_refer_pose",
                expected_cols=6,
            ),
            target_point=target_point,
            shoe_origin_z_min=float(shoe_origin_z_min) if shoe_origin_z_min is not None else None,
            cur_tcp_to_sole_dist=float(cur_tcp_to_sole_dist) if cur_tcp_to_sole_dist is not None else None,
            toe_z_offset=float(toe_z_offset_raw),
            align_forward_move_distance=float(align_forward_move_distance_raw),
            rotate_dh=float(rotate_dh_raw),
            model_path=model_path,
            slot_roi=slot_roi,
            use_sam=_as_bool(use_sam_raw, "use_sam"),
            toe_arc_in_tcp=_as_float_matrix(data.get("toe_arc_in_tcp"), "toe_arc_in_tcp", expected_cols=3),
            toe_lower_in_tcp=toe_lower,
            flange_vec_in_tcp=flange_vec,
            camera=camera,
            extra_fields={key: value for key, value in data.items() if key not in known_keys},
        )

    def slot_xy_array(self) -> np.ndarray:
        return np.asarray(self.slot_xy_points, dtype=float)

    def fit_slot_center_line(self) -> SlotLine:
        return SlotLine.fit_from_points(self.slot_xy_array())

    def resolved_slot_line(self) -> SlotLine:
        return self.fit_slot_center_line()

    def target_point_xy(self) -> np.ndarray:
        if self.target_point is not None:
            return np.asarray(self.target_point, dtype=float)
        raise RuntimeError("slot config missing target_point")

    def center_line_direction(self, reference_direction: np.ndarray | None = None) -> np.ndarray:
        if reference_direction is not None:
            return _normalize_vector(np.asarray(reference_direction, dtype=float), "reference direction")
        raise RuntimeError("slot config missing reference_direction for center line direction")

    def gripper_xy_rz_limits(self) -> dict[str, object]:
        poses = self.gripper_refer_pose
        if len(poses) < 2:
            raise RuntimeError("slot config missing gripper_refer_pose with at least 2 poses")

        p0, p1 = poses[0], poses[1]
        p0_xy = np.array([float(p0[0]), float(p0[1])], dtype=float)
        p1_xy = np.array([float(p1[0]), float(p1[1])], dtype=float)
        line_vec = p1_xy - p0_xy
        line_len = float(np.linalg.norm(line_vec))
        if line_len < 1e-6:
            raise RuntimeError("gripper_refer_pose xy points are too close to define a line")

        line_dir = line_vec / line_len
        line_tol_mm = 2.0

        def _normalize_deg(angle_deg: float) -> float:
            return ((float(angle_deg) + 180.0) % 360.0) - 180.0

        rz_start = _normalize_deg(float(p0[5]))
        rz_end = _normalize_deg(float(p1[5]))
        rz_shortest_delta = _normalize_deg(rz_end - rz_start)
        rz_delta_min = min(0.0, rz_shortest_delta)
        rz_delta_max = max(0.0, rz_shortest_delta)

        return {
            "line_p0": (float(p0_xy[0]), float(p0_xy[1])),
            "line_p1": (float(p1_xy[0]), float(p1_xy[1])),
            "line_point": (float(p0_xy[0]), float(p0_xy[1])),
            "line_dir": (float(line_dir[0]), float(line_dir[1])),
            "line_len": line_len,
            "line_tol_mm": line_tol_mm,
            "rz_anchor": rz_start,
            "rz_delta": (rz_delta_min, rz_delta_max),
            "rz_display": (rz_start, rz_end),
        }

    def project_xy_onto_gripper_line(self, x: float, y: float) -> tuple[float, float, float, float]:
        """将 (x, y) 投影到 gripper_refer_pose 前两点定义的无限直线上。"""
        return project_xy_onto_line(x, y, self)

    def set_rigid_relationship(
        self,
        toe_arc_in_tcp: list[list[float]],
        toe_lower_in_tcp: list[list[float]] | None = None,
    ) -> None:
        self.toe_arc_in_tcp = _as_float_matrix(toe_arc_in_tcp, "toe_arc_in_tcp", expected_cols=3)
        self.toe_lower_in_tcp = (
            _as_float_matrix(toe_lower_in_tcp, "toe_lower_in_tcp", expected_cols=3)
            if toe_lower_in_tcp is not None
            else None
        )
        self.flange_vec_in_tcp = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {}
        if self.slot_xy_points:
            data["slot_xy_points"] = [[float(v) for v in row] for row in self.slot_xy_points]
        if self.target_point is not None:
            data["target_point"] = [float(v) for v in self.target_point]
        data["toe_z_offset"] = float(self.toe_z_offset)
        data["align_forward_move_distance"] = float(self.align_forward_move_distance)
        data["rotate_dh"] = float(self.rotate_dh)
        if self.model_path is not None:
            data["model_path"] = self.model_path
        if self.slot_roi is not None:
            data["slot_roi"] = [[int(v) for v in row] for row in self.slot_roi]
        data["use_sam"] = bool(self.use_sam)
        if self.toe_arc_in_tcp:
            data["toe_arc_in_tcp"] = [[float(v) for v in row] for row in self.toe_arc_in_tcp]
        if self.flange_vec_in_tcp is not None:
            data["flange_vec_in_tcp"] = [float(v) for v in self.flange_vec_in_tcp]
        if self.z_heights:
            data["z_heights"] = [float(v) for v in self.z_heights]
        if self.shoe_origin_z_min is not None:
            data["shoe_origin_z_min"] = float(self.shoe_origin_z_min)
        if self.cur_tcp_to_sole_dist is not None:
            data["cur_tcp_to_sole_dist"] = float(self.cur_tcp_to_sole_dist)
        if self.toe_lower_in_tcp is not None:
            data["toe_lower_in_tcp"] = [[float(v) for v in row] for row in self.toe_lower_in_tcp]
        if self.gripper_refer_pose:
            data["gripper_refer_pose"] = [[float(v) for v in row] for row in self.gripper_refer_pose]
        if self.camera is not None:
            data["camera"] = self.camera.to_dict()
        data.update(self.extra_fields)
        return data

    def save_yaml(self, path: str | Path) -> None:
        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as file:
            yaml.dump(
                _wrap_inline_sequences(self.to_dict()),
                file,
                Dumper=_SlotConfigDumper,
                allow_unicode=True,
                sort_keys=False,
                width=200,
            )