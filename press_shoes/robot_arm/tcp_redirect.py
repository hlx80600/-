"""TCP 重定向工具。

输入当前时刻的工具末端位姿和法兰位姿，可以先反推出固定的工具偏移，
再根据目标工具末端位姿反算机械臂法兰应到达的位姿。

本模块默认使用 pose = [x, y, z, rx, ry, rz]：
- 平移单位：毫米（mm）
- 旋转单位：角度（degree）

示例:
    current_tip = [550.0, 80.0, 320.0, 15.0, -10.0, 35.0]
    current_flange = [490.0, 30.0, 280.0, 5.0, -3.0, 12.0]
    target_tip = [550.0, 80.0, 320.0, 15.0, -10.0, 80.0]

    redirector = TCPRedirector.from_current_state(current_tip, current_flange)
    target_flange = redirector.compute_flange_pose(target_tip)
    flange_path = redirector.interpolate_path(current_tip, target_tip, steps=50)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation as R
from scipy.spatial.transform import Slerp


def _as_pose_array(pose: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
    """将输入位姿规范成 shape=(6,) 的 numpy 数组。"""
    pose_array = np.asarray(pose, dtype=float)
    if pose_array.shape != (6,):
        raise ValueError(f"Expected pose shape (6,), got {pose_array.shape}")
    return pose_array


def pose_to_matrix(pose: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
    """将 6D 位姿转换为 4x4 齐次变换矩阵。"""
    pose_array = _as_pose_array(pose)
    transform = np.eye(4)
    transform[:3, :3] = R.from_euler("xyz", pose_array[3:], degrees=True).as_matrix()
    transform[:3, 3] = pose_array[:3]
    return transform


def matrix_to_pose(transform: np.ndarray) -> np.ndarray:
    """将 4x4 齐次变换矩阵转换回 [x, y, z, rx, ry, rz]。"""
    transform_array = _as_transform_matrix(transform)

    pose = np.empty(6, dtype=float)
    pose[:3] = transform_array[:3, 3]
    pose[3:] = R.from_matrix(transform_array[:3, :3]).as_euler("xyz", degrees=True)
    return pose


def invert_transform(transform: np.ndarray) -> np.ndarray:
    """求 4x4 刚体变换的逆矩阵。"""
    transform_array = _as_transform_matrix(transform)

    inverse = np.eye(4)
    rotation = transform_array[:3, :3]
    translation = transform_array[:3, 3]
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -rotation.T @ translation
    return inverse


def _as_transform_matrix(transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...]) -> np.ndarray:
    """将输入规范成 shape=(4, 4) 的齐次变换矩阵。"""
    transform_array = np.asarray(transform, dtype=float)
    if transform_array.shape != (4, 4):
        raise ValueError(f"Expected transform shape (4, 4), got {transform_array.shape}")
    return transform_array


@dataclass(frozen=True)
class TCPRedirector:
    """缓存工具偏移的轻量封装。

    适用于同一把工具连续执行多个目标位姿时，避免每次都重复计算 flange->tip 偏移。
    """

    tool_offset_transform: np.ndarray

    @classmethod
    def compute_tool_offset_transform(
        cls,
        tip_pose: np.ndarray | list[float] | tuple[float, ...],
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> np.ndarray:
        """根据当前 tip/base 与 flange/base 位姿，计算 flange->tip 固定偏移。"""
        base_tip = pose_to_matrix(tip_pose)
        base_flange = pose_to_matrix(flange_pose)
        return invert_transform(base_flange) @ base_tip

    @classmethod
    def compute_tool_offset_pose(
        cls,
        tip_pose: np.ndarray | list[float] | tuple[float, ...],
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> np.ndarray:
        """以 6D 位姿形式返回 flange->tip 的工具偏移。"""
        return matrix_to_pose(cls.compute_tool_offset_transform(tip_pose, flange_pose))

    @classmethod
    def redirect_tcp(
        cls,
        tip_pose: np.ndarray | list[float] | tuple[float, ...],
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
        target_tip_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> np.ndarray:
        """给定目标工具末端位姿，反算所需法兰位姿。

        这是单步静态求解：
        1. 先由当前状态求出固定工具偏移 T_flange_tip
        2. 再由目标 tip 位姿反算目标 flange 位姿
        """
        tool_offset = cls.compute_tool_offset_transform(tip_pose, flange_pose)
        target_tip_transform = pose_to_matrix(target_tip_pose)
        target_flange_transform = target_tip_transform @ invert_transform(tool_offset)
        return matrix_to_pose(target_flange_transform)

    @classmethod
    def redirect_tcp_transform(
        cls,
        tip_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
        flange_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
        target_tip_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
    ) -> np.ndarray:
        """给定目标工具末端变换矩阵，反算所需法兰变换矩阵。"""
        tool_offset = invert_transform(_as_transform_matrix(flange_transform)) @ _as_transform_matrix(tip_transform)
        return _as_transform_matrix(target_tip_transform) @ invert_transform(tool_offset)

    @classmethod
    def interpolate_flange_path(
        cls,
        tip_pose: np.ndarray | list[float] | tuple[float, ...],
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
        target_tip_pose: np.ndarray | list[float] | tuple[float, ...],
        steps: int = 50,
    ) -> np.ndarray:
        """在 tip 空间插值，并逐点反算法兰路径。

        这个函数用于“运动过程中也要保证工具末端轨迹正确”的场景。
        它不是直接在法兰空间插值，而是：
        1. 对工具末端位置做线性插值
        2. 对工具末端姿态做球面线性插值
        3. 对每个插值点重新反算法兰位姿
        """
        if steps < 2:
            raise ValueError("steps must be at least 2")

        tip_pose_array = _as_pose_array(tip_pose)
        target_tip_pose_array = _as_pose_array(target_tip_pose)
        tool_offset = cls.compute_tool_offset_transform(tip_pose_array, flange_pose)
        tool_offset_inverse = invert_transform(tool_offset)

        times = np.array([0.0, 1.0])
        sample_times = np.linspace(0.0, 1.0, steps)

        tip_rotations = R.from_euler(
            "xyz",
            np.vstack([tip_pose_array[3:], target_tip_pose_array[3:]]),
            degrees=True,
        )
        slerp = Slerp(times, tip_rotations)
        interpolated_rotations = slerp(sample_times)

        interpolated_positions = (
            tip_pose_array[:3][None, :]
            + (target_tip_pose_array[:3] - tip_pose_array[:3])[None, :] * sample_times[:, None]
        )

        flange_poses = np.empty((steps, 6), dtype=float)
        # 每个 tip 插值点都通过固定工具偏移反算出对应的 flange 位姿。
        for index, (position, rotation) in enumerate(
            zip(interpolated_positions, interpolated_rotations.as_matrix(), strict=True)
        ):
            tip_transform = np.eye(4)
            tip_transform[:3, :3] = rotation
            tip_transform[:3, 3] = position
            flange_transform = tip_transform @ tool_offset_inverse
            flange_poses[index] = matrix_to_pose(flange_transform)

        return flange_poses

    @classmethod
    def from_current_state(
        cls,
        tip_pose: np.ndarray | list[float] | tuple[float, ...],
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> "TCPRedirector":
        """由当前 tip 和 flange 状态构造一个带固定工具偏移的重定向器。"""
        return cls(tool_offset_transform=cls.compute_tool_offset_transform(tip_pose, flange_pose))

    @classmethod
    def from_flange_to_tcp_transform(
        cls,
        flange_to_tcp_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
    ) -> "TCPRedirector":
        """直接通过 flange->tcp 的 4x4 变换矩阵构造重定向器。"""
        return cls(tool_offset_transform=_as_transform_matrix(flange_to_tcp_transform))

    @property
    def tool_offset_pose(self) -> np.ndarray:
        """返回 flange->tip 偏移的 6D 位姿表示。"""
        return matrix_to_pose(self.tool_offset_transform)

    @property
    def flange_to_tcp_transform(self) -> np.ndarray:
        """返回 flange->tcp 的 4x4 变换矩阵。"""
        return self.tool_offset_transform.copy()

    def set_flange_to_tcp_transform(
        self,
        flange_to_tcp_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
    ) -> "TCPRedirector":
        """设置 flange->tcp 变换矩阵并返回新的重定向器实例。"""
        return TCPRedirector.from_flange_to_tcp_transform(flange_to_tcp_transform)

    def compute_flange_pose(
        self,
        target_tip_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> np.ndarray:
        """基于缓存的工具偏移，反算单个目标 tip 对应的 flange 位姿。"""
        target_tip_transform = pose_to_matrix(target_tip_pose)
        target_flange_transform = target_tip_transform @ invert_transform(self.tool_offset_transform)
        return matrix_to_pose(target_flange_transform)

    def compute_flange_transform(
        self,
        target_tip_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
    ) -> np.ndarray:
        """基于缓存的工具偏移，反算单个目标 tip 对应的 flange 变换矩阵。"""
        return _as_transform_matrix(target_tip_transform) @ invert_transform(self.tool_offset_transform)

    def compute_tcp_pose(
        self,
        flange_pose: np.ndarray | list[float] | tuple[float, ...],
    ) -> np.ndarray:
        """基于缓存的工具偏移，计算给定法兰位姿对应的 TCP 在基坐标系下的位姿。"""
        tcp_transform = pose_to_matrix(flange_pose) @ self.tool_offset_transform
        return matrix_to_pose(tcp_transform)

    def compute_tcp_transform(
        self,
        flange_transform: np.ndarray | list[list[float]] | tuple[tuple[float, ...], ...],
    ) -> np.ndarray:
        """基于缓存的工具偏移，计算给定法兰对应的 TCP 在基坐标系下的变换矩阵。"""
        return _as_transform_matrix(flange_transform) @ self.tool_offset_transform

    def interpolate_path(
        self,
        current_tip_pose: np.ndarray | list[float] | tuple[float, ...],
        target_tip_pose: np.ndarray | list[float] | tuple[float, ...],
        steps: int = 50,
    ) -> np.ndarray:
        """基于缓存的工具偏移，生成满足 tip 轨迹要求的 flange 路径。"""
        current_tip_pose_array = _as_pose_array(current_tip_pose)
        target_tip_pose_array = _as_pose_array(target_tip_pose)
        if steps < 2:
            raise ValueError("steps must be at least 2")

        times = np.array([0.0, 1.0])
        sample_times = np.linspace(0.0, 1.0, steps)
        tip_rotations = R.from_euler(
            "xyz",
            np.vstack([current_tip_pose_array[3:], target_tip_pose_array[3:]]),
            degrees=True,
        )
        slerp = Slerp(times, tip_rotations)
        interpolated_rotations = slerp(sample_times)
        interpolated_positions = (
            current_tip_pose_array[:3][None, :]
            + (target_tip_pose_array[:3] - current_tip_pose_array[:3])[None, :] * sample_times[:, None]
        )

        tool_offset_inverse = invert_transform(self.tool_offset_transform)
        flange_poses = np.empty((steps, 6), dtype=float)
        for index, (position, rotation) in enumerate(
            zip(interpolated_positions, interpolated_rotations.as_matrix(), strict=True)
        ):
            tip_transform = np.eye(4)
            tip_transform[:3, :3] = rotation
            tip_transform[:3, 3] = position
            flange_transform = tip_transform @ tool_offset_inverse
            flange_poses[index] = matrix_to_pose(flange_transform)

        return flange_poses


def _demo_usage() -> None:
    """运行一个最小示例，展示单步重定向和路径插值的输出格式。"""
    current_tip_pose = np.array([407.426264, 676.333666, 35.435767, 167.06, -8.93, -23.29])
    current_flange_pose = np.array([229.97, 308.22, 343.46, 167.06, -8.93, -23.29])
    target_tip_pose = np.array([07.426264, 676.333666, 35.435767, 179, -2.93, -23.29])

    redirector = TCPRedirector.from_current_state(current_tip_pose, current_flange_pose)
    target_tip_transform = pose_to_matrix(target_tip_pose)
    target_flange_pose = redirector.compute_flange_pose(target_tip_pose)
    target_flange_transform = redirector.compute_flange_transform(target_tip_transform)
    flange_path = redirector.interpolate_path(current_tip_pose, target_tip_pose, steps=200)

    print("tool_offset_pose:")
    print(np.round(redirector.tool_offset_pose, 6))
    print("flange_to_tcp_transform:")
    print(np.round(redirector.flange_to_tcp_transform, 6))
    print("target_tip_transform:")
    print(np.round(target_tip_transform, 6))
    print("target_flange_pose:")
    print(np.round(target_flange_pose, 6))
    print("target_flange_transform:")
    print(np.round(target_flange_transform, 6))
    print("flange_path shape:", flange_path.shape)
    print("flange_path:")
    print(np.round(flange_path, 6))


if __name__ == "__main__":
    _demo_usage()