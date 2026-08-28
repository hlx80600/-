# =============================================================================
# GVL —— 全局变量表（对应 Codesys 的 GVL_Memory / GVL_Station / Main）
#
# 用法举例：
#   ctx.gvl.Memory_BOOL[1] = True          # 记忆
#   ctx.gvl.Station[2].Auto_A[10] = 10     # 步号
#   ctx.gvl.Station[2].Busy                # 忙
#   ctx.gvl.Main.Running / Paused / Stop   # 主状态
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class StationFB:
    """
    一个 Station（像 Codesys 功能块）。
    Auto_A[10]=0 表示该 Auto 没在跑；=10/20/30... 表示当前步号。
    """

    name: str
    auto_list: List[int]  # 本站有哪些 Auto，例如 [10, 20]
    Auto_A: Dict[int, int] = field(default_factory=dict)
    Busy: bool = False
    StepPulse: bool = False  # 单步：HMI 点「下一步」时置 TRUE 一拍

    def __post_init__(self) -> None:
        if not self.Auto_A:
            self.Auto_A = {k: 0 for k in self.auto_list}

    def any_auto_running(self) -> bool:
        return any(v != 0 for v in self.Auto_A.values())

    def reset_all_auto(self) -> None:
        for k in self.Auto_A:
            self.Auto_A[k] = 0

    def update_busy(self) -> None:
        """
        IF 所有 Auto_A=0 THEN Busy:=FALSE ELSE Busy:=TRUE END_IF
        """
        self.Busy = self.any_auto_running()

    def status_text(self) -> str:
        parts = [f"Auto_A[{k}]={v}" for k, v in sorted(self.Auto_A.items()) if v != 0]
        if not parts:
            return f"{self.name}: 空闲 Busy={self.Busy}"
        return f"{self.name}: {' | '.join(parts)} Busy={self.Busy}"


@dataclass
class MainFB:
    """对应 Main 程序的运行标志。"""

    Running: bool = False
    Paused: bool = False
    Stop: bool = False
    EStopped: bool = False
    Alarming: bool = False
    InitDone: bool = False
    Initializing: bool = False
    Mode: str = "AUTO"  # AUTO / SINGLE_STEP / MANUAL
    DebugBypass: bool = False  # TRUE=忽略进入互锁，由 HMI 武装
    Init_Auto: int = 0  # 初始化步号
    InitStepPulse: bool = False


class GVL:
    """全局变量集合。"""

    def __init__(self) -> None:
        # ----- 记忆 Mem[1..10] -----
        self.Memory_BOOL: Dict[int, bool] = {i: False for i in range(1, 11)}

        # ----- 六个 Station -----
        self.Station: Dict[int, StationFB] = {
            1: StationFB("Station1皮带拍照", [10]),
            2: StationFB("Station2上料机器人", [10, 20]),
            3: StationFB("Station3放料槽拍照", [10]),
            4: StationFB("Station4取料槽拍照", [10]),
            5: StationFB("Station5下料机器人", [10, 20]),
            6: StationFB("Station6旋转压鞋", [10]),
        }

        self.Main = MainFB()

        # 视觉算出的取料位（相当于 DataSave）；默认贴近 pick_entry，避免 Mock 给到不可达点
        self.PickPose = {
            "x": -274.0,
            "y": -120.0,
            "z": 488.0,
            "rx": -178.005,
            "ry": -1.999,
            "rz": 77.9,
            "is_left_shoe": True,
        }
        # Station1 确认后的本拍取料快照；Station2 取料/抬起/Mem8·9 只认它
        # 含 toe_offset_in_grasp_tcp：抓取中心→鞋头（抓取TCP系 mm）
        self.BeltPickSnapshot = None

        # 鞋头 TCP：抓取后激活，放料对位/绕点用鞋头；张爪后关闭
        self.ToeTcpActive: bool = False
        self.ActiveToeTcp: list = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        # 内部：延时、发令锁存、重试计数（流程用，不必在 HMI 改）
        self._delay_until: Dict[str, float] = {}
        self._cmd_latch: Dict[str, bool] = {}
        self._photo_retries: Dict[str, int] = {}
        self._guide_tries: int = 0
        self._guide_aligned: bool = False
        self._toe_align_tries: int = 0
        self._toe_align_done: bool = False
        self._heel_seg_poses: list = []
        self._heel_seg_i: int = 0
        self._last_place_result = None
        self._last_pick_result = None
        self._last_belt_result = None
        # 本拍视觉快照 id：上料(cam1) / 下料(cam4)，运送完成后再回写结果
        self._vision_load_snap_id = ""
        self._vision_unload_snap_id = ""
        self._last_guide = None
        self._last_toe_align = None

    def clear_cmd_state(self) -> None:
        """
        报警/停止/急停后清空发令锁存与延时。
        否则会出现：步30 已 pulse 过一次，MoveL 失败后锁存仍为 True，
        复位再启动永远不会再发运动，一直卡在该步。
        """
        self._cmd_latch.clear()
        self._delay_until.clear()
