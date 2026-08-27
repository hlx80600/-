"""
法奥 FR5 封装。

★ 机器人 IP 不要写在本文件！
  改地址：config/default.yaml → robots.robot1.ip / robots.robot2.ip

接真机 / Docker 仿真：
  1. robots.robotX.use_mock: false
  2. 能 import fairino（本程序会自动把同级目录 ../fairino 加入路径）
  3. 能 ping 通 IP，控制器开远程（端口约 20003 XML-RPC）
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from devices.pose_utils import extract_joints, pose_to_list

log = logging.getLogger(__name__)

# 法奥 SDK 常见返回码（见官方 errcode 文档）；未知码仍原样显示数字
_FAIRINO_ERR_HINT = {
    4: "指令执行失败/参数异常(换工具后笛卡尔与关节不一致时常见)",
    14: "逆解失败或目标不可达",
    22: "奇异点附近",
    29: "关节超限",
    38: "奇异位姿",
    112: "给定位姿不可达(直线中间无解。Rx≈±180时易按360°插补；近距离还可能是平滑半径大于剩余行程)",
    154: "关节指令点错误(工具号/TCP刚切换后用旧笛卡尔+关节最常见；过渡点请用示教关节MoveJ且先别切鞋头工具)",
    74: "直线指令点错误(换鞋头工具后仍走工具1示教绝对点最常见；压跟只许改姿态/相对下压)",
    185: "故障信号触发(常见：刚按停止/急停后未消警，或碰撞/外部故障；请点「报警复位」后再启动)",
    186: "急停信号触发，运动已停止",
}

# MoveL：已在目标附近则不再发令（单位 mm / °）
_MOVEL_ALREADY_XYZ_MM = 0.8
_MOVEL_ALREADY_RPY_DEG = 0.5
# 示教关节与当前关节足够近才直接用，否则以邻近逆解为准
_MOVEL_TAUGHT_JOINT_NEAR_DEG = 25.0
# MoveL 仍 112 时：近距离改关节补一段（±180 插补被控制器折回时）
_MOVEL_SHORT_HOP_XYZ_MM = 80.0
_MOVEL_SHORT_HOP_RPY_DEG = 30.0


def _format_move_err(err: int) -> str:
    hint = _FAIRINO_ERR_HINT.get(int(err))
    if hint:
        return f"err={err}({hint})"
    return f"err={err}"


def _ensure_fairino_on_path() -> None:
    """把「试验/fairino」的上一级加入 sys.path，使 from fairino import Robot 可用。"""
    # 本文件: .../莆田鞋厂四槽机器控制程序/devices/robot_fr5.py
    # 期望:   .../试验/fairino/
    here = Path(__file__).resolve()
    trial_dir = here.parents[2]  # .../试验
    if str(trial_dir) not in sys.path:
        sys.path.insert(0, str(trial_dir))
    # 也兼容 fairino 就在项目旁的其它布局
    sibling = here.parents[1].parent / "fairino"
    if sibling.is_dir() and str(sibling.parent) not in sys.path:
        sys.path.insert(0, str(sibling.parent))


def _import_fairino_rpc():
    """返回 RPC 类；失败抛出带说明的异常。"""
    _ensure_fairino_on_path()
    try:
        from fairino import Robot  # type: ignore

        return Robot.RPC
    except Exception as e1:
        try:
            # 兼容直接把 Robot.py 当模块
            import Robot as robot_mod  # type: ignore

            return robot_mod.RPC
        except Exception as e2:
            raise ImportError(
                "无法导入法奥 SDK。请确认目录存在：试验/fairino/Robot.py\n"
                f"from fairino import Robot → {e1}\n"
                f"import Robot → {e2}"
            ) from e1


class RobotFR5:
    def __init__(
        self,
        name: str,
        ip: str,
        tool: int = 0,
        user: int = 0,
        vel: float = 30.0,
        use_mock: bool = True,
    ):
        self.name = name
        self.ip = ip
        self.tool = tool
        self.user = user
        self.vel = float(vel)
        self.use_mock = use_mock
        self._robot = None
        self.connected = False
        self.last_error = ""
        self._moving = False
        self._move_cmd_sent = False  # 是否已成功发出过运动（防“没动也判到位”）
        self._move_done_at = 0.0
        # 路径平滑（HMI/motion 配置）
        self.blend_enable = False
        self.blend_t_ms = 100.0
        self.blend_r_mm = 30.0
        self.blend_queue_delay_s = 0.08
        self._wait_arrival = True  # 本段是否必须 GetRobotMotionDone
        self._blend_ready_at = 0.0
        # 平滑段已「提前放行」但控制器上一段仍在跑；下一段若「到位」必须先等停稳
        self._pending_blend_on_controller: bool = False
        self.current_pose: Dict[str, float] = {
            "x": 0,
            "y": 0,
            "z": 0,
            "rx": 0,
            "ry": 0,
            "rz": 0,
        }
        self.current_joints: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.estop_active = False
        self._di_cache: Dict[int, bool] = {}
        self._di_force_mock: set[int] = set()
        self._mock_fault_msg: Optional[str] = None  # HMI「模拟机器人报警」
        self._last_fault_key: Optional[str] = None  # 去抖：同一故障不重复上报
        # 运动路径上下文：报警时提示「从哪点 → 到哪点」
        self._move_from_label: str = "当前位置"
        self._move_to_label: str = ""
        self._last_arrived_label: str = "未知位置（上电后）"
        self._jogging = False
        self._jog_ref = 0
        # 负载/工具：empty=手爪(负载1+工具1)；with_shoe=抓鞋(负载2+工具2)
        self._payload_profiles: Dict[str, dict] = {}
        self._payload_mode: str = "empty"
        # 自动消警前快照：示教器一闪而过时，HMI/日志可查到是什么
        self.last_auto_cleared: str = ""
        self.last_auto_cleared_at: float = 0.0
        self.on_auto_cleared = None  # Optional[Callable[[str], None]]
        # 停止/换负载后：下一拍 Move 前先安静消警，避免 Move 返回185→示教器红→急停感
        self._need_premove_clear: bool = False

    def apply_payload_cfg(self, payloads: Optional[Dict] = None) -> None:
        """从 yaml robots.*.payloads 载入两套负载+TCP（不立刻下发控制器）。"""
        raw = payloads if isinstance(payloads, dict) else {}
        out: Dict[str, dict] = {}
        for key, defaults in (
            (
                "empty",
                {
                    "name": "手爪",
                    "load_num": 1,
                    "tool": 1,
                    "mass_kg": 2.0,
                    "cog_mm": [0.0, 0.0, 50.0],
                    "tcp": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                },
            ),
            (
                "with_shoe",
                {
                    "name": "手爪+鞋",
                    "load_num": 2,
                    "tool": 2,
                    "mass_kg": 2.0,
                    "cog_mm": [0.0, 0.0, 80.0],
                    "tcp": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                },
            ),
        ):
            src = raw.get(key) if isinstance(raw.get(key), dict) else {}
            cog = src.get("cog_mm", defaults["cog_mm"])
            if not isinstance(cog, (list, tuple)) or len(cog) < 3:
                cog = defaults["cog_mm"]
            tcp = src.get("tcp", defaults["tcp"])
            if not isinstance(tcp, (list, tuple)) or len(tcp) < 6:
                tcp = defaults["tcp"]
            out[key] = {
                "name": str(src.get("name", defaults["name"])),
                "load_num": int(src.get("load_num", defaults["load_num"])),
                "tool": int(src.get("tool", defaults["tool"])),
                "mass_kg": float(src.get("mass_kg", defaults["mass_kg"])),
                "cog_mm": [float(cog[0]), float(cog[1]), float(cog[2])],
                "tcp": [float(tcp[i]) for i in range(6)],
            }
        self._payload_profiles = out
        # 默认工具号与 empty 对齐
        if "empty" in out:
            self.tool = int(out["empty"]["tool"])

    def payload_mode(self) -> str:
        return self._payload_mode

    def payload_profiles(self) -> Dict[str, dict]:
        return dict(self._payload_profiles)

    def _write_payload_to_controller(self, profile: dict) -> None:
        """下发单套质量+质心到指定 load_num。"""
        if self.use_mock or self._robot is None or not self.connected:
            return
        load_num = int(profile["load_num"])
        mass = float(profile["mass_kg"])
        cog = profile["cog_mm"]
        try:
            if hasattr(self._robot, "SetLoadWeight"):
                err = self._robot.SetLoadWeight(load_num, mass)
                log.info(
                    "[%s] SetLoadWeight(load=%s, mass=%.3f) → %s",
                    self.name,
                    load_num,
                    mass,
                    err,
                )
            if hasattr(self._robot, "SetLoadCoord"):
                err2 = self._robot.SetLoadCoord(
                    float(cog[0]), float(cog[1]), float(cog[2]), load_num
                )
                log.info(
                    "[%s] SetLoadCoord(load=%s, cog=%s) → %s",
                    self.name,
                    load_num,
                    cog,
                    err2,
                )
        except Exception as e:
            log.error("[%s] 下发负载失败: %s", self.name, e)
            raise

    def _tcp_nontrivial(self, tcp) -> bool:
        """yaml/配置里 TCP 是否有非零值（避免把示教器冲成全 0）。"""
        if not isinstance(tcp, (list, tuple)) or len(tcp) < 3:
            return False
        return any(abs(float(tcp[i])) > 1e-6 for i in range(min(6, len(tcp))))

    def _write_tool_tcp_to_controller(self, profile: dict, *, activate: bool = True) -> None:
        """
        下发工具 TCP 到控制器：
          SetToolList → 写入工具坐标系列表（示教器「工具列表」）
          SetToolCoord → 仅 activate=True 时调用（会改控制器「当前工具」，易导致后续 Move 工具不符）
        """
        if self.use_mock or self._robot is None or not self.connected:
            return
        tool_id = int(profile["tool"])
        load_num = int(profile["load_num"])
        tcp = list(profile.get("tcp") or [0, 0, 0, 0, 0, 0])
        if len(tcp) < 6:
            tcp = (list(tcp) + [0.0] * 6)[:6]
        tcp = [float(v) for v in tcp[:6]]
        try:
            wrote = False
            if hasattr(self._robot, "SetToolList"):
                err = self._robot.SetToolList(tool_id, tcp, 0, 0, load_num)
                log.info(
                    "[%s] SetToolList(tool=%s, tcp=%s, load=%s) → %s",
                    self.name,
                    tool_id,
                    tcp,
                    load_num,
                    err,
                )
                if err not in (0, None) and int(err) != 0:
                    raise RuntimeError(f"SetToolList 失败 err={err}")
                wrote = True
            # 仅当要激活为当前工具时才 SetToolCoord（否则会改控制器当前工具号，Move 易 74）
            if activate and hasattr(self._robot, "SetToolCoord"):
                err2 = self._robot.SetToolCoord(tool_id, tcp, 0, 0, tool_id, load_num)
                log.info(
                    "[%s] SetToolCoord(tool=%s, tcp=%s, load=%s) → %s",
                    self.name,
                    tool_id,
                    tcp,
                    load_num,
                    err2,
                )
                if err2 not in (0, None) and int(err2) != 0:
                    raise RuntimeError(f"SetToolCoord 失败 err={err2}")
                wrote = True
            if not wrote:
                log.warning("[%s] SDK 无 SetToolList/SetToolCoord，跳过 TCP 下发", self.name)
        except Exception as e:
            log.error("[%s] 下发工具TCP失败: %s", self.name, e)
            raise

    def read_tool_tcp_from_controller(self, tool_id: int) -> list[float]:
        """从控制器读工具坐标系 TCP；Mock/失败返回当前配置或零。"""
        tool_id = int(tool_id)
        if self.use_mock or self._robot is None or not self.connected:
            for p in self._payload_profiles.values():
                if int(p.get("tool", -1)) == tool_id:
                    return list(p.get("tcp") or [0.0] * 6)
            return [0.0] * 6
        if not hasattr(self._robot, "GetToolCoordWithID"):
            raise RuntimeError(f"{self.name} SDK 无 GetToolCoordWithID")
        ret = self._robot.GetToolCoordWithID(tool_id)
        err = ret[0] if isinstance(ret, (list, tuple)) else ret
        if int(err) != 0:
            raise RuntimeError(f"{self.name} GetToolCoordWithID({tool_id}) 失败: {ret}")
        coord = ret[1] if len(ret) > 1 else None
        if not coord or len(coord) < 6:
            raise RuntimeError(f"{self.name} 工具{tool_id} TCP 数据异常: {ret}")
        return [float(coord[i]) for i in range(6)]

    def sync_payloads_to_controller(self, *, sync_tcp: bool | None = None) -> None:
        """
        把两套负载写入控制器。
        sync_tcp: True 强制写 TCP；False 不写；None=自动（配置里 TCP 非全 0 则写）。
        """
        for key in ("empty", "with_shoe"):
            p = self._payload_profiles.get(key)
            if not p:
                continue
            self._write_payload_to_controller(p)
            do_tcp = sync_tcp
            if do_tcp is None:
                do_tcp = self._tcp_nontrivial(p.get("tcp"))
            if do_tcp:
                # 列表都写；仅当前运动工具 activate，避免把「当前工具」切到另一编号
                activate = int(p.get("tool", -1)) == int(getattr(self, "tool", -1))
                self._write_tool_tcp_to_controller(p, activate=activate)

    def set_payload_mode(self, mode: str, *, force: bool = False, sync_tcp: bool | None = None) -> None:
        """
        切换运行负载/工具：
          empty     → 负载1 + 工具坐标1（未抓鞋）
          with_shoe → 负载2 + 工具坐标2（已抓鞋）
        切换时会再写一次该套质量/质心，并改后续 Move 用的 tool。
        sync_tcp: True 强制写 TCP；False 不写；None=该套 TCP 非全 0 时写（示教器能看到当前工具值）。
        """
        key = "with_shoe" if mode in ("with_shoe", "shoe", "holding", "2") else "empty"
        if (not force) and key == self._payload_mode and self.tool == int(
            (self._payload_profiles.get(key) or {}).get("tool", self.tool)
        ):
            return
        if not self._payload_profiles:
            log.warning("[%s] 未配置 payloads，跳过 set_payload_mode(%s)", self.name, mode)
            return
        profile = self._payload_profiles.get(key)
        if not profile:
            raise RuntimeError(f"{self.name} 缺少 payloads.{key}")
        self._write_payload_to_controller(profile)
        do_tcp = sync_tcp
        if do_tcp is None:
            do_tcp = self._tcp_nontrivial(profile.get("tcp"))
        if do_tcp:
            self._write_tool_tcp_to_controller(profile, activate=True)
        self.tool = int(profile["tool"])
        self._payload_mode = key
        log.info(
            "[%s] 负载切换 → %s(%s) load_num=%s tool=%s mass=%.3fkg cog=%s tcp=%s sync_tcp=%s",
            self.name,
            key,
            profile.get("name", ""),
            profile["load_num"],
            self.tool,
            profile["mass_kg"],
            profile["cog_mm"],
            profile.get("tcp"),
            do_tcp,
        )

    def set_holding_shoe(self, holding: bool, *, force: bool = False) -> None:
        """抓鞋 True → 负载2+工具2；松开 False → 负载1+工具1。"""
        self.set_payload_mode("with_shoe" if holding else "empty", force=force)

    def set_vel(self, vel: float) -> None:
        """
        设置速度百分比 [1~100]。
        ★ 真机：调用控制器 SetSpeed（示教器「运行速度百分比」同步变化），
          并尽量 SetSpeedInstant 低延迟生效。
        ★ 本程序后续 MoveL 用 vel=100，实际速度 = 全局 SetSpeed%，避免再乘一次。
        """
        self.vel = max(1.0, min(100.0, float(vel)))
        log.info("[%s] 速度设为 %.0f%%", self.name, self.vel)
        if self.use_mock or self._robot is None or not self.connected:
            return
        pct = int(round(self.vel))
        try:
            if hasattr(self._robot, "SetSpeed"):
                err = self._robot.SetSpeed(pct)
                log.info("[%s] SetSpeed(%s) → %s", self.name, pct, err)
            if hasattr(self._robot, "SetSpeedInstant"):
                err2 = self._robot.SetSpeedInstant(pct)
                log.info("[%s] SetSpeedInstant(%s) → %s", self.name, pct, err2)
        except Exception as e:
            log.error("[%s] 下发控制器速度失败: %s", self.name, e)

    def set_blend(
        self,
        *,
        enable: Optional[bool] = None,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
        queue_delay_s: Optional[float] = None,
    ) -> None:
        """HMI/配置：路径平滑参数。"""
        if enable is not None:
            self.blend_enable = bool(enable)
        if blend_t_ms is not None:
            self.blend_t_ms = max(0.0, min(500.0, float(blend_t_ms)))
        if blend_r_mm is not None:
            self.blend_r_mm = max(0.0, min(1000.0, float(blend_r_mm)))
        if queue_delay_s is not None:
            self.blend_queue_delay_s = max(0.02, min(2.0, float(queue_delay_s)))
        log.info(
            "[%s] 平滑 enable=%s blendT=%.0fms blendR=%.1fmm queue_delay=%.3fs",
            self.name,
            self.blend_enable,
            self.blend_t_ms,
            self.blend_r_mm,
            self.blend_queue_delay_s,
        )

    def apply_motion_cfg(self, motion: Optional[Dict] = None) -> None:
        m = motion if isinstance(motion, dict) else {}
        self.set_blend(
            enable=bool(m.get("blend_enable", self.blend_enable)),
            blend_t_ms=float(m.get("blend_t_ms", self.blend_t_ms)),
            blend_r_mm=float(m.get("blend_r_mm", self.blend_r_mm)),
            queue_delay_s=float(m.get("blend_queue_delay_s", self.blend_queue_delay_s)),
        )

    def _resolve_blend(
        self,
        *,
        blend: Optional[bool],
        precise: bool,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
    ) -> tuple:
        """
        返回 (use_blend, blend_t, blend_r, wait_arrival)。
        precise=True：强制到位。
        blend=True 且总开关：平滑；blend_t_ms / blend_r_mm 有值则覆盖全局默认。
        """
        if precise:
            return False, -1.0, -1.0, True
        use = bool(blend) and bool(self.blend_enable)
        if not use:
            return False, -1.0, -1.0, True
        bt = float(self.blend_t_ms if blend_t_ms is None else blend_t_ms)
        br = float(self.blend_r_mm if blend_r_mm is None else blend_r_mm)
        bt = max(0.0, min(500.0, bt))
        br = max(0.0, min(1000.0, br))
        return True, bt, br, False

    def is_move_pending(self) -> bool:
        """本段运动是否已发出且尚未判完成。"""
        return bool(self._move_cmd_sent or self._moving)

    def set_use_mock(self, mock: bool) -> bool:
        """
        运行中切换 Mock/真机。返回是否已按期望模式就绪。
        切 Mock 立刻生效；切真机则尝试重连。
        """
        want = bool(mock)
        if bool(self.use_mock) == want and (want or self.connected):
            if want:
                self.halt_motion()
            return True
        self.halt_motion()
        self.inject_fault_mock(None)
        if want:
            self.disconnect()
            self.use_mock = True
            self.connected = True
            log.info("[%s] 运行中已切到 Mock（后续 Move 不再发真机）", self.name)
            return True
        self.use_mock = False
        ok = self.connect()
        log.info("[%s] 运行中切到真机 connect=%s ip=%s", self.name, ok, self.ip)
        return ok

    def _mock_move_duration(self, base_s: float) -> float:
        return base_s * (30.0 / max(1.0, self.vel))

    def connect(self) -> bool:
        if self.use_mock:
            self.connected = True
            self.last_error = ""
            try:
                if self._payload_profiles:
                    self.set_payload_mode("empty", force=True)
            except Exception:
                pass
            log.info("[%s] Mock 已连接 %s", self.name, self.ip)
            return True
        try:
            RPC = _import_fairino_rpc()
            self._robot = RPC(self.ip)
            # 新版 SDK：CNDE+XML-RPC 都成功才 is_connect=True
            ok = bool(getattr(self._robot, "is_connect", True))
            if not ok:
                self.last_error = (
                    f"Robot.RPC({self.ip}) is_connect=False。"
                    f"请检查：能 ping {self.ip}；Docker/控制器已开；端口 20003/20005"
                )
                log.error("[%s] 连接失败: %s", self.name, self.last_error)
                self.connected = False
                self._robot = None
                return False
            # 切自动模式：法奥 Mode(0)=自动，Mode(1)=手动。远程 Move/消警必须在自动模式。
            try:
                if hasattr(self._robot, "Mode"):
                    err = self._robot.Mode(0)
                    log.info("[%s] Mode(0)自动 → %s", self.name, err)
            except Exception as e:
                log.warning("[%s] Mode(0) 失败（可稍后在示教器切自动）: %s", self.name, e)
            # 上使能，避免消警后仍无法运动
            try:
                if hasattr(self._robot, "RobotEnable"):
                    err = self._robot.RobotEnable(1)
                    log.info("[%s] RobotEnable(1) → %s", self.name, err)
            except Exception as e:
                log.warning("[%s] RobotEnable(1) 失败: %s", self.name, e)
            # 连接后把 yaml/HMI 速度同步到控制器（示教器全局速度）
            try:
                self.set_vel(self.vel)
            except Exception as e:
                log.warning("[%s] 连接后 SetSpeed 失败: %s", self.name, e)
            self.connected = True
            self.last_error = ""
            # 两套负载写入控制器，并默认切到「未抓鞋」
            try:
                if self._payload_profiles:
                    self.sync_payloads_to_controller()
                    self.set_payload_mode("empty", force=True)
            except Exception as e:
                log.warning("[%s] 连接后同步负载失败: %s", self.name, e)
            log.info("[%s] 已连接 %s", self.name, self.ip)
            return True
        except Exception as e:
            log.error("[%s] 连接失败: %s", self.name, e)
            self.last_error = str(e)
            self.connected = False
            self._robot = None
            return False

    def disconnect(self) -> None:
        self.connected = False
        self._robot = None

    def is_link_up(self) -> bool:
        """只读：是否与控制器通（不清对象）。"""
        if self.use_mock:
            return True
        if not self.connected or self._robot is None:
            return False
        try:
            return bool(getattr(self._robot, "is_connect", True))
        except Exception:
            return False

    def refresh_link(self) -> bool:
        """探测与控制器链路；断线则清 RPC，供后台重连。"""
        if self.use_mock:
            self.connected = True
            return True
        if not self.is_link_up():
            if self._robot is not None or self.connected:
                log.warning("[%s] 链路断开，标记未连接并释放 RPC", self.name)
            self.connected = False
            self._robot = None
            return False
        self.connected = True
        return True

    def reconnect(self) -> bool:
        """Mock 直接就绪；真机先断开再 connect。"""
        if self.use_mock:
            return self.connect()
        self.disconnect()
        return self.connect()

    def set_di_force_mock(self, di_id: int, enabled: bool = True) -> None:
        di_id = int(di_id)
        if enabled:
            self._di_force_mock.add(di_id)
        else:
            self._di_force_mock.discard(di_id)

    def set_di_mock(self, di_id: int, value: bool) -> None:
        di_id = int(di_id)
        self._di_cache[di_id] = bool(value)
        self._di_force_mock.add(di_id)

    def get_di(self, di_id: int) -> bool:
        di_id = int(di_id)
        if self.use_mock or di_id in self._di_force_mock:
            return bool(self._di_cache.get(di_id, False))
        if not self._robot:
            return False
        try:
            ret = self._robot.GetDI(di_id, 0)
            if isinstance(ret, (list, tuple)) and len(ret) >= 2:
                return bool(ret[1])
            return bool(ret)
        except Exception as e:
            log.error("[%s] GetDI 失败: %s", self.name, e)
            return False

    def _start_mock_move(
        self, pose: Dict, duration: float = 0.8, joints: Optional[list] = None
    ) -> None:
        from devices.pose_utils import extract_joints, numeric_pose

        self._moving = True
        self._move_cmd_sent = True
        self._move_done_at = time.monotonic() + duration
        self.current_pose = numeric_pose(pose)
        j = joints if joints is not None else extract_joints(pose)
        if j and len(j) == 6:
            self.current_joints = [float(v) for v in j]

    def _set_move_context(self, to_label: str = "", from_label: str = "") -> None:
        self._move_from_label = (from_label or self._last_arrived_label or "当前位置").strip()
        self._move_to_label = (to_label or "未命名目标点").strip()

    def path_hint(self) -> str:
        """报警用：当前这段运动的起止点说明。"""
        if not self._move_to_label:
            return ""
        return (
            f"路径：从「{self._move_from_label}」→「{self._move_to_label}」。"
            f"当前工具号={getattr(self, 'tool', '?')} "
            f"负载模式={getattr(self, '_payload_mode', '?')}。"
            "若为奇异点/点位错误，请到 HMI「点位偏移」检查这两点坐标，"
            "或在中间增加过渡点后用「单点调试」试跑。"
        )

    def _with_path(self, msg: str) -> str:
        hint = self.path_hint()
        return f"{msg}\n{hint}" if hint else msg

    def get_actual_tcp_pose(self) -> Dict[str, float]:
        """读当前 TCP 位姿（Mock 返回内存 current_pose）。"""
        from devices.pose_utils import list_to_pose, numeric_pose

        if self.use_mock:
            return numeric_pose(self.current_pose)
        self._require_real()
        if not hasattr(self._robot, "GetActualTCPPose"):
            return numeric_pose(self.current_pose)
        ret = self._robot.GetActualTCPPose(1)
        if isinstance(ret, (list, tuple)) and len(ret) >= 2 and int(ret[0]) == 0:
            pose = list_to_pose(list(ret[1]))
            self.current_pose = pose
            return pose
        raise RuntimeError(f"{self.name} GetActualTCPPose 失败: {ret}")

    def get_actual_joint_pos(self) -> list[float]:
        """读当前关节角 [°]（示教 MoveJ 用）。"""
        if self.use_mock:
            return [float(v) for v in self.current_joints]
        self._require_real()
        if not hasattr(self._robot, "GetActualJointPosDegree"):
            raise RuntimeError(f"{self.name} SDK 无 GetActualJointPosDegree")
        ret = self._robot.GetActualJointPosDegree(1)
        if isinstance(ret, (list, tuple)) and len(ret) >= 2 and int(ret[0]) == 0:
            joints = [float(v) for v in ret[1]]
            if len(joints) != 6:
                raise RuntimeError(f"{self.name} 关节角长度异常: {joints}")
            self.current_joints = joints
            return joints
        raise RuntimeError(f"{self.name} GetActualJointPosDegree 失败: {ret}")

    def _require_real(self) -> None:
        if not self.connected or self._robot is None:
            raise RuntimeError(
                f"{self.name} 未连接真机/Docker（IP={self.ip}）。"
                "请看启动日志「连接失败」原因，修好后重启程序再初始化。"
            )

    def _mark_move_started(self, pose: Dict, *, wait_arrival: bool) -> None:
        self._moving = True
        self._move_cmd_sent = True
        self._wait_arrival = bool(wait_arrival)
        now = time.monotonic()
        self._move_done_at = now + 60.0
        self._blend_ready_at = now + (
            float(self.blend_queue_delay_s) if not wait_arrival else 0.0
        )
        # 平滑：程序可提前发下一段；控制器仍在跑 → 记下，供下一段「到位」先同步
        self._pending_blend_on_controller = not bool(wait_arrival)
        self.current_pose = dict(pose)

    def _sync_before_precise_move(self) -> None:
        """
        到位模式发令前：若上一段是平滑提前放行，必须等控制器真正停稳。

        否则常见于「进入点平滑 → 取料上方到位」：手臂还在去 pick_entry 的半路上，
        就对 pick_above（无示教关节）发 MoveCart/逆解，路径会拧腕/下扎 → 撞机。
        平滑接平滑由控制器交融，不必等；平滑接到位必须等。
        """
        if self.use_mock or self._robot is None or not self.connected:
            self._pending_blend_on_controller = False
            return
        if not self._pending_blend_on_controller:
            return
        log.info(
            "[%s] 上一段为平滑衔接，本段要求到位：发令前等待控制器停稳…（路径=%s）",
            self.name,
            self.path_hint() or "-",
        )
        t0 = time.monotonic()
        stable = 0
        while time.monotonic() - t0 < 60.0:
            fault = self._read_fault_message()
            if fault:
                self._pending_blend_on_controller = False
                self.halt_motion()
                raise RuntimeError(self._with_path(fault))
            if hasattr(self._robot, "GetRobotMotionDone"):
                try:
                    ret = self._robot.GetRobotMotionDone()
                    done = False
                    if isinstance(ret, (list, tuple)) and len(ret) >= 2:
                        done = bool(ret[1])
                    else:
                        done = bool(ret)
                    if done:
                        stable += 1
                        # 连续确认停稳，避免平滑段间歇性 done=True 就发下一段
                        if stable >= 3:
                            self._pending_blend_on_controller = False
                            log.info(
                                "[%s] 控制器已停稳，开始到位运动 (等待%.2fs)",
                                self.name,
                                time.monotonic() - t0,
                            )
                            return
                    else:
                        stable = 0
                except Exception as e:
                    log.debug("[%s] GetRobotMotionDone: %s", self.name, e)
                    stable = 0
            time.sleep(0.02)
        self._pending_blend_on_controller = False
        raise RuntimeError(
            self._with_path(
                f"{self.name} 等待上一段平滑运动结束超时，取消到位发令（请检查示教器是否仍在动）"
            )
        )

    def _parse_ik_joints(self, ret) -> Optional[list]:
        """解析法奥 GetInverseKin / GetInverseKinRef 返回值 → 6 关节角。"""
        if ret is None:
            return None
        # SDK 封装常见：(err, [j1..j6]) 或 扁平 [err, j1..j6]
        if isinstance(ret, (list, tuple)) and len(ret) >= 2:
            if isinstance(ret[1], (list, tuple)) and len(ret[1]) >= 6:
                if int(ret[0]) != 0:
                    return None
                return [float(ret[1][i]) for i in range(6)]
            if len(ret) >= 7:
                if int(ret[0]) != 0:
                    return None
                return [float(ret[i]) for i in range(1, 7)]
        return None

    def resync_after_tool_change(self, *, label: str = "换工具后关节同步") -> None:
        """
        更换工具号/TCP 后必须先发一拍「当前关节」MoveJ（desc 全 0 正解），
        把控制器当前点登记到新工具下，否则紧接 MoveL 常报 err=74 工具不符/直线点错误。
        """
        from devices.pose_utils import numeric_pose

        joints = self.get_actual_joint_pos()
        # 用新工具读 TCP，仅作标签；真正运动靠关节 + desc_pos=0
        try:
            pose = numeric_pose(self.get_actual_tcp_pose())
        except Exception:
            pose = numeric_pose(self.current_pose)
        self.move_j(
            pose,
            joints=joints,
            label=label,
            from_label="换工具前位置",
            blend=False,
            precise=True,
            vel=10.0,
            acc=100.0,
        )
        log.info(
            "[%s] 换工具同步 MoveJ tool=%s joints=%s",
            self.name,
            self.tool,
            [round(v, 3) for v in joints],
        )

    def _call_movel_once(
        self,
        desc: list,
        *,
        joint_pos: Optional[list],
        move_vel: float,
        move_acc: float,
        blend_r: float,
    ) -> int:
        """发一次 MoveL，返回错误码。"""
        try:
            if joint_pos is not None:
                err = self._robot.MoveL(
                    desc,
                    self.tool,
                    self.user,
                    joint_pos=joint_pos,
                    vel=move_vel,
                    blendR=float(blend_r),
                    oacc=move_acc,
                )
            else:
                err = self._robot.MoveL(
                    desc,
                    self.tool,
                    self.user,
                    vel=move_vel,
                    blendR=float(blend_r),
                    oacc=move_acc,
                )
        except TypeError:
            err = self._robot.MoveL(
                desc, self.tool, self.user, vel=move_vel, blendR=float(blend_r)
            )
        if isinstance(err, (list, tuple)):
            err = err[0]
        return int(err)

    def _current_joints_ref(self) -> Optional[list[float]]:
        """读当前关节；失败则退回缓存。"""
        try:
            aj = self.get_actual_joint_pos()
            if aj and len(aj) == 6:
                return [float(v) for v in aj]
        except Exception:
            pass
        cached = getattr(self, "current_joints", None)
        if cached and len(cached) == 6:
            return [float(v) for v in cached]
        return None

    def _ik_joints_near(
        self, desc: list[float], ref: Optional[list[float]]
    ) -> Optional[list[float]]:
        """用当前关节作参考做邻近逆解，再把结果展开到当前关节附近。"""
        from devices.pose_utils import unwrap_joints_near

        if ref is None or not hasattr(self._robot, "GetInverseKinRef"):
            return None
        try:
            ret = self._robot.GetInverseKinRef(0, desc, ref)
            joint_pos = self._parse_ik_joints(ret)
            if joint_pos is None:
                log.warning(
                    "[%s] GetInverseKinRef 无解/解析失败 ret=%s desc=%s",
                    self.name,
                    ret,
                    desc,
                )
                return None
            return unwrap_joints_near(joint_pos, ref)
        except Exception as e:
            log.warning("[%s] GetInverseKinRef 异常: %s", self.name, e)
            return None

    def _pick_movel_joints(
        self,
        desc: list[float],
        *,
        taught: Optional[list[float]],
        ref: Optional[list[float]],
    ) -> tuple[Optional[list[float]], str]:
        """选 MoveL 目标关节：近距离优先示教关节，否则邻近逆解。"""
        from devices.pose_utils import joints_max_abs_diff_deg, unwrap_joints_near

        if taught is not None and len(taught) == 6:
            taught6 = [float(v) for v in taught]
            if ref is not None:
                taught6 = unwrap_joints_near(taught6, ref)
                if joints_max_abs_diff_deg(taught6, ref) <= _MOVEL_TAUGHT_JOINT_NEAR_DEG:
                    return taught6, "taught"
            else:
                return taught6, "taught"
        ik = self._ik_joints_near(desc, ref)
        if ik is not None:
            return ik, "ref"
        if taught is not None and len(taught) == 6:
            taught6 = [float(v) for v in taught]
            if ref is not None:
                taught6 = unwrap_joints_near(taught6, ref)
            return taught6, "taught-far"
        return None, "sdk"

    def _send_movel(
        self,
        pose: Dict,
        *,
        blend: Optional[bool] = None,
        precise: bool = False,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
        vel: Optional[float] = None,
        acc: Optional[float] = None,
        async_rpc: bool = False,
        joints: Optional[list] = None,
    ) -> None:
        from devices.pose_utils import (
            extract_joints,
            numeric_pose,
            pose_near,
            pose_rpy_max_abs_diff_deg,
            pose_xyz_distance_mm,
            unwrap_pose_rpy_near,
        )

        self._require_real()
        pose_raw = numeric_pose(pose)
        taught = joints if joints is not None else extract_joints(pose)
        move_vel = max(1.0, min(100.0, float(vel if vel is not None else 100.0)))
        move_acc = max(0.0, min(100.0, float(acc if acc is not None else 100.0)))
        if async_rpc:
            # 法奥 blendR=-1 会阻塞 XML-RPC 直到到位；示教器必须立刻返回才能处理松开。
            # 用 0 而不是 1mm：剩余行程常小于 1mm，平滑半径大于行程会直接 112。
            blend_r = 0.0
            wait_arr = True
        else:
            _use, _bt, blend_r, wait_arr = self._resolve_blend(
                blend=blend,
                precise=precise,
                blend_t_ms=blend_t_ms,
                blend_r_mm=blend_r_mm,
            )
            if wait_arr:
                self._sync_before_precise_move()

        cur_tcp = None
        try:
            cur_tcp = numeric_pose(self.get_actual_tcp_pose())
        except Exception as e:
            log.debug("[%s] MoveL 读当前 TCP 失败: %s", self.name, e)
        pose_cmd = pose_raw
        if cur_tcp is not None:
            pose_cmd = unwrap_pose_rpy_near(pose_raw, cur_tcp)
            remain_xyz = pose_xyz_distance_mm(cur_tcp, pose_raw)
            remain_rpy = pose_rpy_max_abs_diff_deg(cur_tcp, pose_raw)
            if pose_near(
                cur_tcp,
                pose_raw,
                xyz_mm=_MOVEL_ALREADY_XYZ_MM,
                rpy_deg=_MOVEL_ALREADY_RPY_DEG,
            ):
                log.info(
                    "[%s] MoveL 已在目标附近，跳过发令 剩余XYZ=%.2fmm 姿态=%.2f° %s → %s",
                    self.name,
                    remain_xyz,
                    remain_rpy,
                    self._move_from_label,
                    self._move_to_label,
                )
                self._mark_move_started(pose_raw, wait_arrival=wait_arr)
                return
            # 平滑半径不能大于剩余直线行程，否则近距离 MoveL 常 112
            if float(blend_r) >= 0.0 and remain_xyz <= float(blend_r) + 0.5:
                log.info(
                    "[%s] MoveL 剩余XYZ=%.2fmm ≤ blendR=%.1f，改为 blendR=0",
                    self.name,
                    remain_xyz,
                    blend_r,
                )
                blend_r = 0.0
            if any(
                abs(pose_cmd[k] - pose_raw[k]) > 0.05 for k in ("rx", "ry", "rz")
            ):
                log.info(
                    "[%s] MoveL RPY就近展开 raw=%s cmd=%s（避免±180绕圈）",
                    self.name,
                    [round(pose_raw[k], 3) for k in ("rx", "ry", "rz")],
                    [round(pose_cmd[k], 3) for k in ("rx", "ry", "rz")],
                )
        else:
            remain_xyz = -1.0
            remain_rpy = -1.0

        desc = pose_to_list(pose_cmd)
        ref = self._current_joints_ref()
        joint_pos, ik_mode = self._pick_movel_joints(desc, taught=taught, ref=ref)

        err = self._call_movel_once(
            desc, joint_pos=joint_pos, move_vel=move_vel, move_acc=move_acc, blend_r=blend_r
        )
        if int(err) == 185 and not async_rpc:
            path = self.path_hint()
            ok, tip = self.clear_motion_fault_after_stop(
                reason=f"MoveL返回185 路径={path or '-'}",
                stop_first=False,
            )
            log.warning("[%s] MoveL 遇 185，消警后重试: %s", self.name, tip)
            if ok:
                err = self._call_movel_once(
                    desc,
                    joint_pos=joint_pos,
                    move_vel=move_vel,
                    move_acc=move_acc,
                    blend_r=blend_r,
                )
        if int(err) == 112 and float(blend_r) > 0.0:
            log.warning(
                "[%s] MoveL 112，blendR=%.1f→0 再试 剩余XYZ=%.2fmm",
                self.name,
                blend_r,
                remain_xyz,
            )
            blend_r = 0.0
            err = self._call_movel_once(
                desc, joint_pos=joint_pos, move_vel=move_vel, move_acc=move_acc, blend_r=0.0
            )
        if int(err) == 112 and ik_mode != "ref":
            ik_retry = self._ik_joints_near(desc, ref)
            if ik_retry is not None and ik_retry != joint_pos:
                log.warning("[%s] MoveL 112，改邻近逆解再试", self.name)
                joint_pos = ik_retry
                ik_mode = "ref-retry"
                err = self._call_movel_once(
                    desc,
                    joint_pos=joint_pos,
                    move_vel=move_vel,
                    move_acc=move_acc,
                    blend_r=blend_r,
                )
        # 近距离直线仍 112：多半是 Rx≈±180 被控制器折回后绕远路。关节补一段几何上等价。
        if (
            int(err) == 112
            and joint_pos is not None
            and remain_xyz >= 0.0
            and remain_xyz <= _MOVEL_SHORT_HOP_XYZ_MM
            and remain_rpy <= _MOVEL_SHORT_HOP_RPY_DEG
        ):
            blend_t = 1.0 if async_rpc else -1.0
            log.warning(
                "[%s] MoveL 112 近距离改 MoveJ 补一段 剩余XYZ=%.2fmm 姿态=%.2f°",
                self.name,
                remain_xyz,
                remain_rpy,
            )
            try:
                err_j = self._robot.MoveJ(
                    list(joint_pos),
                    self.tool,
                    self.user,
                    desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    vel=move_vel,
                    acc=move_acc,
                    blendT=float(blend_t),
                )
                if isinstance(err_j, (list, tuple)):
                    err_j = err_j[0]
                err = int(err_j)
                ik_mode = f"{ik_mode}+movej"
            except Exception as e:
                log.warning("[%s] MoveL112 后 MoveJ 补段异常: %s", self.name, e)
        if int(err) != 0:
            extra = ""
            if int(err) == 112:
                extra = (
                    f" 剩余XYZ={remain_xyz:.1f}mm 姿态差={remain_rpy:.1f}°"
                    f" ik={ik_mode} blendR={blend_r}"
                )
            raise RuntimeError(
                self._with_path(
                    f"{self.name} MoveL 失败 {_format_move_err(err)} pose={desc}{extra}"
                )
            )
        self._mark_move_started(pose_raw, wait_arrival=wait_arr)
        if joint_pos is not None:
            self.current_joints = [float(v) for v in joint_pos]
        log.info(
            "[%s] MoveL vel=%.0f oacc=%.0f blendR=%s wait=%s ik=%s 全局SetSpeed=%.0f%% %s → %s",
            self.name,
            move_vel,
            move_acc,
            blend_r,
            wait_arr,
            ik_mode,
            self.vel,
            self._move_from_label,
            self._move_to_label,
        )

    def _send_movej(
        self,
        pose: Dict,
        joints: Optional[list] = None,
        *,
        blend: Optional[bool] = None,
        precise: bool = False,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
        vel: Optional[float] = None,
        acc: Optional[float] = None,
        async_rpc: bool = False,
    ) -> None:
        """
        MoveJ：有示教关节角时走真正的 MoveJ(关节)；
        否则回退 MoveCart / 逆解（路径可能与示教不一致）。
        """
        from devices.pose_utils import extract_joints, numeric_pose

        self._require_real()
        pose = numeric_pose(pose)
        desc = pose_to_list(pose)
        move_vel = max(1.0, min(100.0, float(vel if vel is not None else 100.0)))
        move_acc = max(0.0, min(100.0, float(acc if acc is not None else 100.0)))
        if async_rpc:
            # 法奥 blendT=-1 会阻塞 XML-RPC 直到到位；示教器/点位按住必须立刻返回。
            blend_t = 1.0
            wait_arr = True
        else:
            _use, blend_t, _br, wait_arr = self._resolve_blend(
                blend=blend,
                precise=precise,
                blend_t_ms=blend_t_ms,
                blend_r_mm=blend_r_mm,
            )
            if wait_arr:
                self._sync_before_precise_move()
        taught = joints if joints is not None else extract_joints(pose)
        joint_pos = None
        if taught is not None and len(taught) == 6:
            joint_pos = [float(v) for v in taught]
            # ★ 有示教关节角时不要传旧工具下的笛卡尔 desc_pos。
            #   换工具号/TCP 后，关节角与旧 desc 在新工具下不一致 → 法奥「关节指令点错误」。
            #   传全 0 让 SDK 按当前 tool 做正解得到 desc_pos（见 fairino MoveJ 文档）。
            err = self._robot.MoveJ(
                joint_pos,
                self.tool,
                self.user,
                desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                vel=move_vel,
                acc=move_acc,
                blendT=float(blend_t),
            )
            mode = "示教关节"
        else:
            # 无示教关节：优先当前关节邻近逆解 + MoveJ，禁止一上来 MoveCart
            # （MoveCart 在「平滑→到位」时极易拧腕/异构型，取料上方尤甚）
            ref = None
            try:
                aj = self.get_actual_joint_pos()
                if aj and len(aj) == 6:
                    ref = [float(v) for v in aj]
            except Exception:
                if getattr(self, "current_joints", None) and len(self.current_joints) == 6:
                    ref = [float(v) for v in self.current_joints]
            joint_pos = None
            if ref is not None and hasattr(self._robot, "GetInverseKinRef"):
                try:
                    joint_pos = self._parse_ik_joints(
                        self._robot.GetInverseKinRef(0, desc, ref)
                    )
                except Exception as e:
                    log.warning("[%s] GetInverseKinRef 异常: %s", self.name, e)
            if joint_pos is None and hasattr(self._robot, "GetInverseKin"):
                try:
                    ik = self._robot.GetInverseKin(0, desc, -1)
                    joint_pos = self._parse_ik_joints(ik)
                    if joint_pos is None and isinstance(ik, (list, tuple)) and len(ik) >= 2:
                        if int(ik[0]) == 0 and isinstance(ik[1], (list, tuple)):
                            joint_pos = [float(v) for v in ik[1][:6]]
                except Exception as e:
                    log.warning("[%s] GetInverseKin 异常: %s", self.name, e)
            if joint_pos is not None and len(joint_pos) == 6:
                err = self._robot.MoveJ(
                    list(joint_pos),
                    self.tool,
                    self.user,
                    desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    vel=move_vel,
                    acc=move_acc,
                    blendT=float(blend_t),
                )
                mode = "邻近逆解关节"
            elif hasattr(self._robot, "MoveCart"):
                log.warning(
                    "[%s] MoveJ 无示教关节且逆解失败，最后回退 MoveCart → %s",
                    self.name,
                    self._move_to_label,
                )
                try:
                    err = self._robot.MoveCart(
                        desc,
                        self.tool,
                        self.user,
                        vel=move_vel,
                        acc=move_acc,
                        blendT=float(blend_t),
                    )
                except TypeError:
                    err = self._robot.MoveCart(
                        desc, self.tool, self.user, vel=move_vel, blendT=float(blend_t)
                    )
                mode = "MoveCart回退"
            else:
                raise RuntimeError(
                    self._with_path(
                        f"{self.name} MoveJ 目标无示教关节且逆解失败 pose={desc}"
                    )
                )
        if isinstance(err, (list, tuple)):
            err = err[0]
        err = int(err)
        if err == 185 and not async_rpc:
            path = self.path_hint()
            ok, tip = self.clear_motion_fault_after_stop(
                reason=f"MoveJ返回185 路径={path or '-'}",
                stop_first=False,
            )
            log.warning("[%s] MoveJ 遇 185，消警后重试: %s", self.name, tip)
            if ok:
                if taught is not None and len(taught) == 6:
                    err = self._robot.MoveJ(
                        joint_pos,
                        self.tool,
                        self.user,
                        desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        vel=move_vel,
                        acc=move_acc,
                        blendT=float(blend_t),
                    )
                elif mode == "MoveCart回退" and hasattr(self._robot, "MoveCart"):
                    try:
                        err = self._robot.MoveCart(
                            desc,
                            self.tool,
                            self.user,
                            vel=move_vel,
                            acc=move_acc,
                            blendT=float(blend_t),
                        )
                    except TypeError:
                        err = self._robot.MoveCart(
                            desc,
                            self.tool,
                            self.user,
                            vel=move_vel,
                            blendT=float(blend_t),
                        )
                elif joint_pos is not None:
                    err = self._robot.MoveJ(
                        list(joint_pos),
                        self.tool,
                        self.user,
                        desc_pos=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        vel=move_vel,
                        acc=move_acc,
                        blendT=float(blend_t),
                    )
                if isinstance(err, (list, tuple)):
                    err = err[0]
                err = int(err)
        if err != 0:
            raise RuntimeError(
                self._with_path(
                    f"{self.name} MoveJ 失败 {_format_move_err(err)} pose={desc}"
                )
            )
        self._mark_move_started(pose, wait_arrival=wait_arr)
        if taught is not None and len(taught) == 6:
            self.current_joints = [float(v) for v in taught]
        elif joint_pos is not None:
            self.current_joints = [float(v) for v in joint_pos]
        log.info(
            "[%s] MoveJ(%s) vel=%.0f acc=%.0f blendT=%s wait=%s 全局SetSpeed=%.0f%% %s → %s",
            self.name,
            mode,
            move_vel,
            move_acc,
            blend_t,
            wait_arr,
            self.vel,
            self._move_from_label,
            self._move_to_label,
        )

    def move_j(
        self,
        pose: Dict,
        *,
        joints: Optional[list] = None,
        label: str = "",
        from_label: str = "",
        blend: Optional[bool] = None,
        precise: bool = False,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
        vel: Optional[float] = None,
        acc: Optional[float] = None,
        async_rpc: bool = False,
    ) -> None:
        if self.estop_active:
            raise RuntimeError(f"{self.name} 急停中，禁止运动")
        self._set_move_context(label, from_label)
        taught = joints if joints is not None else extract_joints(pose)
        _use, _bt, _br, wait_arr = self._resolve_blend(
            blend=blend,
            precise=precise,
            blend_t_ms=blend_t_ms,
            blend_r_mm=blend_r_mm,
        )
        if async_rpc:
            wait_arr = True
        if self.use_mock:
            dur = 0.25 if not wait_arr else self._mock_move_duration(0.6)
            # mock 时长按本段 vel 粗略缩放
            if vel is not None:
                dur = dur * (100.0 / max(1.0, float(vel)))
            self._start_mock_move(pose, dur, joints=taught)
            self._wait_arrival = wait_arr
            self._blend_ready_at = time.monotonic() + (
                self.blend_queue_delay_s if not wait_arr else 0.0
            )
            log.info(
                "[%s] Mock MoveJ%s vel=%s blend=%s %s → %s",
                self.name,
                "(示教关节)" if taught else "",
                vel if vel is not None else 100,
                not wait_arr,
                self._move_from_label,
                self._move_to_label,
            )
            return
        self._send_movej(
            pose,
            joints=taught,
            blend=blend,
            precise=precise,
            blend_t_ms=blend_t_ms,
            blend_r_mm=blend_r_mm,
            vel=vel,
            acc=acc,
            async_rpc=async_rpc,
        )

    def move_l(
        self,
        pose: Dict,
        *,
        label: str = "",
        from_label: str = "",
        blend: Optional[bool] = None,
        precise: bool = False,
        blend_t_ms: Optional[float] = None,
        blend_r_mm: Optional[float] = None,
        vel: Optional[float] = None,
        acc: Optional[float] = None,
        async_rpc: bool = False,
        joints: Optional[list] = None,
    ) -> None:
        """笛卡尔直线。有示教关节时传入 joints，近距离/±180 姿态由底层就近展开。"""
        if self.estop_active:
            raise RuntimeError(f"{self.name} 急停中，禁止运动")
        self._set_move_context(label, from_label)
        _use, _bt, _br, wait_arr = self._resolve_blend(
            blend=blend,
            precise=precise,
            blend_t_ms=blend_t_ms,
            blend_r_mm=blend_r_mm,
        )
        if async_rpc:
            wait_arr = True
        if self.use_mock:
            dur = 0.2 if not wait_arr else self._mock_move_duration(0.5)
            if vel is not None:
                dur = dur * (100.0 / max(1.0, float(vel)))
            self._start_mock_move(pose, dur)
            self._wait_arrival = wait_arr
            self._blend_ready_at = time.monotonic() + (
                self.blend_queue_delay_s if not wait_arr else 0.0
            )
            log.info(
                "[%s] Mock MoveL vel=%s blend=%s %s → %s",
                self.name,
                vel if vel is not None else 100,
                not wait_arr,
                self._move_from_label,
                self._move_to_label,
            )
            return
        self._send_movel(
            pose,
            blend=blend,
            precise=precise,
            blend_t_ms=blend_t_ms,
            blend_r_mm=blend_r_mm,
            vel=vel,
            acc=acc,
            async_rpc=async_rpc,
            joints=joints,
        )

    def poll_move_done(self) -> bool:
        """
        返回 True 表示本段可进入下一步。
        - 到位到位(wait_arrival)：等 GetRobotMotionDone
        - 平滑衔接：发令后经 blend_queue_delay 即可发下一段（与下一段交融）
        """
        if not self._move_cmd_sent:
            return False

        # 等待到位期间：任何已注入/本体故障都立刻抛出（含奇异点）
        fault = self._read_fault_message()
        if fault:
            self.halt_motion()
            raise RuntimeError(self._with_path(fault))

        # 平滑模式：短延时后允许下一步发令，实现段间交融
        if not self._wait_arrival:
            if time.monotonic() >= self._blend_ready_at:
                self._moving = False
                self._move_cmd_sent = False
                if self._move_to_label:
                    self._last_arrived_label = self._move_to_label
                return True
            return False

        if self.use_mock:
            if self._moving and time.monotonic() >= self._move_done_at:
                self._moving = False
                self._move_cmd_sent = False
                if self._move_to_label:
                    self._last_arrived_label = self._move_to_label
                return True
            return False

        if self._robot is not None and hasattr(self._robot, "GetRobotMotionDone"):
            try:
                ret = self._robot.GetRobotMotionDone()
                done = False
                if isinstance(ret, (list, tuple)) and len(ret) >= 2:
                    done = bool(ret[1])
                else:
                    done = bool(ret)
                if done:
                    self._moving = False
                    self._move_cmd_sent = False
                    self._pending_blend_on_controller = False
                    if self._move_to_label:
                        self._last_arrived_label = self._move_to_label
                    return True
                if self._moving and time.monotonic() >= self._move_done_at:
                    fault2 = self._read_fault_message()
                    self.halt_motion()
                    raise RuntimeError(
                        self._with_path(
                            fault2
                            or f"{self.name} 运动超时未到位（可能奇异点/点位超限/被暂停），请检查示教器"
                        )
                    )
                return False
            except RuntimeError:
                raise
            except Exception as e:
                log.debug("[%s] GetRobotMotionDone 异常，改用超时: %s", self.name, e)

        if self._moving and time.monotonic() >= self._move_done_at:
            fault2 = self._read_fault_message()
            self.halt_motion()
            raise RuntimeError(
                self._with_path(fault2 or f"{self.name} 运动超时未到位，请检查示教器")
            )
        return False

    def inject_fault_mock(self, message: Optional[str]) -> None:
        """HMI 调试：注入/清除模拟故障。message=None 清除。"""
        self._mock_fault_msg = message
        if message is None:
            self._last_fault_key = None
        log.warning("[%s] 模拟故障=%s", self.name, message)

    # 法奥 StartJOG ref → StopJOG ref（文档：停用 1/3/5/9）
    _JOG_STOP_REF = {0: 1, 2: 3, 4: 5, 8: 9}

    def start_jog(
        self,
        *,
        ref: int,
        axis: int,
        positive: bool,
        max_dis: float,
        vel_pct: float = 8.0,
        acc_pct: float = 40.0,
    ) -> None:
        """点动：法奥 StartJOG。

        Args:
            ref: 0 关节，2 基座，4 工具。
            axis: 1～6（关节号或 XYZRxRyRz）。
            positive: True 正方向。
            max_dis: 单次最大行程（关节 ° / 笛卡尔 mm）。
            vel_pct: 速度百分比 0～100。
            acc_pct: 加速度百分比 0～100。
        """
        if self.estop_active:
            raise RuntimeError(f"{self.name} 急停中，禁止点动")
        axis_i = int(axis)
        if axis_i < 1 or axis_i > 6:
            raise ValueError(f"点动轴非法: {axis}")
        ref_i = int(ref)
        if ref_i not in (0, 2, 4, 8):
            raise ValueError(f"点动坐标系非法: {ref}")
        dis = abs(float(max_dis))
        if dis <= 0:
            raise ValueError("点动行程必须 > 0")
        vel = max(1.0, min(30.0, float(vel_pct)))
        acc = max(10.0, min(100.0, float(acc_pct)))
        direction = 1 if positive else 0

        if self.use_mock:
            self._mock_jog_once(ref_i, axis_i, positive, dis)
            self._jogging = True
            self._jog_ref = ref_i
            return

        self._require_real()
        if self._need_premove_clear and not self._jogging:
            self.ensure_ready_before_move()
        if self._robot is None or not hasattr(self._robot, "StartJOG"):
            # 无连续点动时只能单步 Move；禁止把「按住」的大行程一次发出去
            inch_cap = 20.0 if ref_i == 0 else 50.0
            if dis > inch_cap:
                raise RuntimeError(
                    f"{self.name} 无 StartJOG，不能连续点动；请改用「点按一步」"
                    f"（关节≤20° / 直线≤50mm）"
                )
            self._jog_by_incremental_move(ref_i, axis_i, positive, dis, vel)
            return
        if self._jogging and self._jog_ref != ref_i:
            self.stop_jog(immediate=True)
        # 法奥：StartJOG(ref, nb, dir, max_dis, vel, acc)
        err = self._robot.StartJOG(ref_i, axis_i, direction, dis, vel, acc)
        if err not in (0, None) and int(err) == 185:
            self.clear_motion_fault_after_stop(
                reason="StartJOG 返回185（多为松开急停残留）",
                stop_first=False,
            )
            err = self._robot.StartJOG(ref_i, axis_i, direction, dis, vel, acc)
        if err not in (0, None):
            raise RuntimeError(f"{self.name} StartJOG 失败: {err}")
        self._jogging = True
        self._jog_ref = ref_i
        log.info(
            "[%s] StartJOG ref=%s axis=%s dir=%s dis=%.2f vel=%.1f",
            self.name,
            ref_i,
            axis_i,
            direction,
            dis,
            vel,
        )

    def stop_jog(self, *, immediate: bool = False) -> None:
        """点动停止。未在点动则直接返回，避免空 ImmStop 触发 185。

        immediate=True：先 ImmStopJOG（松开立刻停），再 StopJOG。
        immediate=False：仅 StopJOG 减速停。
        """
        was = self._jogging
        self._jogging = False
        if not was or self.use_mock or self._robot is None:
            return
        if immediate:
            try:
                if hasattr(self._robot, "ImmStopJOG"):
                    err = self._robot.ImmStopJOG()
                    log.info("[%s] ImmStopJOG（松开即停）→ %s", self.name, err)
                    self._need_premove_clear = True
            except Exception as e:
                log.error("[%s] ImmStopJOG 失败: %s", self.name, e)
        stop_ref = int(self._JOG_STOP_REF.get(self._jog_ref, 1))
        try:
            if hasattr(self._robot, "StopJOG"):
                err = self._robot.StopJOG(stop_ref)
                log.info("[%s] StopJOG ref=%s → %s", self.name, stop_ref, err)
        except Exception as e:
            log.error("[%s] StopJOG 失败: %s", self.name, e)
            if not immediate:
                try:
                    if hasattr(self._robot, "ImmStopJOG"):
                        self._robot.ImmStopJOG()
                        self._need_premove_clear = True
                except Exception:
                    pass
        try:
            self.get_actual_tcp_pose()
            self.get_actual_joint_pos()
        except Exception:
            pass

    def _mock_jog_once(self, ref: int, axis: int, positive: bool, max_dis: float) -> None:
        """Mock：把行程一次性加到当前关节或 TCP。"""
        delta = float(max_dis) if positive else -float(max_dis)
        idx = axis - 1
        if ref == 0:
            joints = [float(v) for v in self.current_joints]
            if len(joints) != 6:
                joints = [0.0] * 6
            joints[idx] = joints[idx] + delta
            self.current_joints = joints
            return
        from devices.pose_utils import POSE_AXES, numeric_pose

        pose = numeric_pose(self.current_pose)
        key = POSE_AXES[idx]
        pose[key] = float(pose.get(key, 0.0)) + delta
        self.current_pose = pose

    def _jog_by_incremental_move(
        self,
        ref: int,
        axis: int,
        positive: bool,
        max_dis: float,
        vel_pct: float,
    ) -> None:
        """无 StartJOG 时退化为单步 MoveJ/MoveL。"""
        delta = float(max_dis) if positive else -float(max_dis)
        idx = axis - 1
        if ref == 0:
            joints = list(self.get_actual_joint_pos())
            joints[idx] = float(joints[idx]) + delta
            pose = self.get_actual_tcp_pose()
            self.move_j(
                pose,
                joints=joints,
                label="点动关节",
                precise=True,
                vel=vel_pct,
            )
            return
        from devices.pose_utils import POSE_AXES, numeric_pose

        pose = numeric_pose(self.get_actual_tcp_pose())
        key = POSE_AXES[idx]
        pose[key] = float(pose.get(key, 0.0)) + delta
        self.move_l(pose, label="点动笛卡尔", precise=True, vel=vel_pct)

    def halt_motion(self, *, hard: bool = False, rounds: int | None = None) -> None:
        """
        立刻停运动（不清急停标志）。
        hard=False（普通停止）：只 StopMotion，避免 ImmStop 留下故障需消警才能再动。
        hard=True（急停）：ImmStopJOG + StopMotion。
        rounds：RPC 轮数；示教器松开停用 1 轮，避免界面卡住。
        停完后标记需「发Move前安静消警」，禁止用再次 StopMotion 去消警（会再触发185）。
        """
        self._jogging = False
        self._moving = False
        self._move_cmd_sent = False
        self._wait_arrival = False
        self._blend_ready_at = 0.0
        self._pending_blend_on_controller = False
        self._need_premove_clear = True
        if self.use_mock or self._robot is None:
            return
        n = int(rounds) if rounds is not None else (3 if hard else 1)
        n = max(1, min(3, n))
        for i in range(n):
            if hard:
                try:
                    if hasattr(self._robot, "ImmStopJOG"):
                        err = self._robot.ImmStopJOG()
                        log.warning("[%s] ImmStopJOG[%s] → %s", self.name, i, err)
                except Exception as e:
                    log.error("[%s] ImmStopJOG 失败: %s", self.name, e)
            try:
                if hasattr(self._robot, "StopMotion"):
                    err = self._robot.StopMotion()
                    log.warning("[%s] StopMotion[%s] → %s", self.name, i, err)
            except Exception as e:
                log.error("[%s] StopMotion 失败: %s", self.name, e)

    def ensure_ready_before_move(self) -> None:
        """
        仅在控制器已能读到故障时才消警。
        ★ 禁止「无故障也 ResetAllError」：示教器会瞬时闪红，并白白吃掉 CT（约 0.3s+）。
        换负载/正常连拍不要走这里；停止后的消警由 cmd_stop / 报警复位负责。
        """
        if self.use_mock or self._robot is None or not self.connected:
            self._need_premove_clear = False
            return
        before = self.snapshot_controller_fault()
        if not before:
            # 仅有「刚停过」标记、但读不到故障码：也不预消警，避免闪红拖 CT
            self._need_premove_clear = False
            return
        ok, tip = self.clear_motion_fault_after_stop(
            reason=f"发Move前检测到残留: {before}",
            stop_first=False,
            remember=True,
        )
        self._need_premove_clear = False
        if not ok:
            log.warning("[%s] 发Move前消警未净: %s", self.name, tip)
        else:
            log.info("[%s] 发Move前已消警: %s", self.name, tip)

    def snapshot_controller_fault(self) -> str:
        """
        读取示教器/控制器当前故障快照（主子码、alarm、奇异/指令点等）。
        用于自动消警前留痕；无异常时返回空串。
        """
        if self.use_mock:
            return self._mock_fault_msg or ""
        if self._robot is None or not self.connected:
            return "未连接"
        parts: list[str] = []
        try:
            if hasattr(self._robot, "GetRobotErrorCode"):
                ret = self._robot.GetRobotErrorCode()
                if isinstance(ret, (list, tuple)) and len(ret) >= 2:
                    codes = ret[1]
                    if isinstance(codes, (list, tuple)) and len(codes) >= 2:
                        mc, sc = int(codes[0]), int(codes[1])
                        if mc or sc:
                            parts.append(f"GetRobotErrorCode主={mc}子={sc}")
                    else:
                        parts.append(f"GetRobotErrorCode={ret}")
                elif ret not in (0, None, (0,), [0]):
                    parts.append(f"GetRobotErrorCode={ret}")
            pkg = getattr(self._robot, "robot_state_pkg", None)
            if pkg is not None:
                fields = (
                    ("main_code", "主码"),
                    ("sub_code", "子码"),
                    ("alarm", "alarm"),
                    ("EmergencyStop", "急停"),
                    ("emergency_stop", "急停"),
                    ("strangePosFlag", "奇异"),
                    ("cmdPointError", "指令点错"),
                    ("motonAlarm", "运动告警"),
                    ("outSoftLimitError", "软限位"),
                    ("robot_mode", "模式"),
                    ("robotMode", "模式"),
                )
                seen = set()
                for attr, label in fields:
                    if label in seen:
                        continue
                    try:
                        v = int(getattr(pkg, attr, 0) or 0)
                    except Exception:
                        continue
                    if v:
                        parts.append(f"{label}={v}")
                        seen.add(label)
            if hasattr(self._robot, "GetSafetyCode"):
                try:
                    scode = int(self._robot.GetSafetyCode() or 0)
                    if scode:
                        parts.append(f"安全码={scode}")
                except Exception:
                    pass
            msg = self._read_fault_message()
            if msg and msg not in parts:
                parts.append(msg)
        except Exception as e:
            parts.append(f"读故障异常:{e}")
        return " | ".join(parts)

    def _remember_auto_cleared(self, reason: str, before: str, tip: str) -> None:
        detail = (
            f"{reason}；消警前={before or '无读到故障码(可能仅Move返回185)'}；结果={tip}"
        )
        self.last_auto_cleared = detail
        self.last_auto_cleared_at = time.time()
        log.warning("[%s] ★自动消警留痕: %s", self.name, detail)
        cb = getattr(self, "on_auto_cleared", None)
        if callable(cb):
            try:
                cb(detail)
            except Exception as e:
                log.debug("[%s] on_auto_cleared 回调失败: %s", self.name, e)

    def clear_motion_fault_after_stop(
        self,
        *,
        reason: str = "",
        stop_first: bool = False,
        remember: bool | None = None,
    ) -> tuple[bool, str]:
        """
        清掉「故障信号」类残留，便于再次 Move。
        ★ 默认 stop_first=False：不再二次 StopMotion（空闲 Stop 常会再触发 185/示教器闪红）。
        仅 Mode(0)+ResetAllError+Enable。消警应在静止时做，避免运动中断造成顿挫。
        """
        self._last_fault_key = None
        before = ""
        if not (self.use_mock or self._robot is None or not self.connected):
            before = self.snapshot_controller_fault()
        if self.use_mock or self._robot is None or not self.connected:
            tip = f"{self.name}: 跳过消警"
            do_remember = (
                bool(remember)
                if remember is not None
                else (
                    bool(before)
                    or ("185" in (reason or ""))
                    or reason.startswith("Move")
                )
            )
            if do_remember:
                self._remember_auto_cleared(reason or "消警", before, tip)
            self._need_premove_clear = False
            return True, tip
        steps: list[str] = []
        try:
            # 仅在明确要求时 Stop（例如急停后）；普通消警禁止 Stop
            if stop_first and hasattr(self._robot, "StopMotion"):
                try:
                    steps.append(f"StopMotion={self._robot.StopMotion()}")
                except Exception as e:
                    steps.append(f"StopMotion:{e}")
            if hasattr(self._robot, "Mode"):
                try:
                    steps.append(f"Mode(0)={self._robot.Mode(0)}")
                    time.sleep(0.05)
                except Exception as e:
                    steps.append(f"Mode:{e}")
            if hasattr(self._robot, "ResetAllError"):
                raw = self._robot.ResetAllError()
                err = raw[0] if isinstance(raw, (list, tuple)) else raw
                steps.append(f"ResetAllError={err}")
                time.sleep(0.05)
            if hasattr(self._robot, "RobotEnable"):
                try:
                    steps.append(f"Enable={self._robot.RobotEnable(1)}")
                except Exception as e:
                    steps.append(f"Enable:{e}")
            left = self._read_fault_message()
            ok = left is None
            detail = "; ".join(steps)
            if left:
                tip = f"{self.name}: 停止后消警未净 残留={left} [{detail}]"
            else:
                tip = f"{self.name}: 已安静消警 [{detail}]"
            do_remember = (
                bool(remember)
                if remember is not None
                else (
                    bool(before)
                    or ("185" in (reason or ""))
                    or reason.startswith("Move")
                )
            )
            if do_remember:
                self._remember_auto_cleared(reason or "自动消警", before, tip)
            self._need_premove_clear = False
            return ok, tip
        except Exception as e:
            tip = f"{self.name}: 消警异常 {e}"
            do_remember = (
                bool(remember)
                if remember is not None
                else (
                    bool(before)
                    or ("185" in (reason or ""))
                    or reason.startswith("Move")
                )
            )
            if do_remember:
                self._remember_auto_cleared(reason or "自动消警异常", before, tip)
            return False, tip

    def _read_fault_message(self) -> Optional[str]:
        """读取当前故障说明（不去抖）。无故障返回 None。"""
        if self._mock_fault_msg:
            return self._mock_fault_msg
        if self.use_mock or not self.connected or self._robot is None:
            return None

        if hasattr(self._robot, "is_connect") and not bool(self._robot.is_connect):
            return f"{self.name} 与控制器通信断开（is_connect=False）"

        try:
            pkg = getattr(self._robot, "robot_state_pkg", None)
            main_c, sub_c = 0, 0
            if hasattr(self._robot, "GetRobotErrorCode"):
                ret = self._robot.GetRobotErrorCode()
                if isinstance(ret, (list, tuple)) and len(ret) >= 2:
                    codes = ret[1]
                    if isinstance(codes, (list, tuple)) and len(codes) >= 2:
                        main_c, sub_c = int(codes[0]), int(codes[1])
            if pkg is not None and not main_c and not sub_c:
                main_c = int(getattr(pkg, "main_code", 0) or 0)
                sub_c = int(getattr(pkg, "sub_code", 0) or 0)

            alarm_flag = emerg = safety = 0
            strange = cmd_pt = motion_al = soft_lim = 0
            if pkg is not None:
                alarm_flag = int(getattr(pkg, "alarm", 0) or 0)
                emerg = int(
                    getattr(pkg, "EmergencyStop", 0)
                    or getattr(pkg, "emergency_stop", 0)
                    or 0
                )
                # ★ 奇异点 / 指令点错误（点位报警常见，main_code 可能仍为 0）
                strange = int(getattr(pkg, "strangePosFlag", 0) or 0)
                cmd_pt = int(getattr(pkg, "cmdPointError", 0) or 0)
                motion_al = int(getattr(pkg, "motonAlarm", 0) or 0)
                soft_lim = int(getattr(pkg, "outSoftLimitError", 0) or 0)

            if hasattr(self._robot, "GetSafetyCode"):
                try:
                    safety = int(self._robot.GetSafetyCode() or 0)
                except Exception:
                    safety = 0

            if not (
                main_c
                or sub_c
                or alarm_flag
                or emerg
                or safety
                or strange
                or cmd_pt
                or motion_al
                or soft_lim
            ):
                return None

            parts = [f"{self.name} 机器人报警"]
            if strange:
                parts.append("奇异点(strangePosFlag)")
            if cmd_pt:
                parts.append("指令点错误(cmdPointError)")
            if soft_lim:
                parts.append("超出软限位")
            if motion_al:
                parts.append("运动警告")
            if main_c or sub_c:
                parts.append(f"主码={main_c} 子码={sub_c}")
            if alarm_flag:
                parts.append(f"alarm={alarm_flag}")
            if emerg:
                parts.append("急停触点触发")
            if safety:
                parts.append(f"安全停止码={safety}")
            return "，".join(parts)
        except Exception as e:
            log.debug("[%s] _read_fault_message 异常: %s", self.name, e)
            return None

    def poll_fault(self) -> Optional[str]:
        """
        扫描机器人故障（带去抖：同一故障只上报一次）。
        供 OB1 每周期调用；运动等待中的检测见 poll_move_done。
        """
        msg = self._read_fault_message()
        if not msg:
            self._last_fault_key = None
            return None
        # 附带「从哪点→到哪点」，方便改点位
        if self.path_hint() and "路径：" not in msg:
            msg = self._with_path(msg)
        key = msg
        if key == self._last_fault_key:
            return None
        self._last_fault_key = key
        return msg

    def reset_controller_errors(self) -> tuple[bool, str]:
        """
        清除控制器/示教器上可复位故障（对齐法奥文档流程）:
          StopMotion → Mode(0)自动 → ResetAllError（可重试）→ RobotEnable(1)
        返回 (成功?, 说明)。急停未松开、外部故障信号持续等不可复位类仍须现场处理。
        """
        self.inject_fault_mock(None)
        self._last_fault_key = None
        self.halt_motion()

        if self.use_mock:
            return True, f"{self.name}: Mock，仅清程序侧报警"

        if self._robot is None or not self.connected:
            if not self.connect():
                return False, f"{self.name}: 未连接 {self.ip}，无法复位示教器"

        if not hasattr(self._robot, "ResetAllError"):
            return False, f"{self.name}: SDK 无 ResetAllError 接口"

        steps: list[str] = []
        try:
            # 1) 停运动，避免消警过程中仍在报运动故障
            if hasattr(self._robot, "StopMotion"):
                try:
                    sm = self._robot.StopMotion()
                    steps.append(f"StopMotion={sm}")
                except Exception as e:
                    steps.append(f"StopMotion异常:{e}")

            # 2) 必须自动模式，否则远程消警/运动常无效（示教器仍显示报警）
            if hasattr(self._robot, "Mode"):
                try:
                    m = self._robot.Mode(0)
                    steps.append(f"Mode(0)={m}")
                    time.sleep(0.3)
                except Exception as e:
                    steps.append(f"Mode异常:{e}")

            # 3) 官方消警，必要时重试（控制器偶发第一次未刷掉）
            err = -1
            for i in range(3):
                raw = self._robot.ResetAllError()
                err = raw[0] if isinstance(raw, (list, tuple)) else raw
                err = int(err)
                time.sleep(0.5)
                left = self._read_fault_message()
                steps.append(f"ResetAllError#{i + 1}={err}")
                if err == 0 and not left:
                    break
                log.warning(
                    "[%s] 消警第%d次 err=%s 残留=%s",
                    self.name,
                    i + 1,
                    err,
                    left,
                )

            # 4) 重新上使能（文档示例：ResetAllError 后 RobotEnable(1)）
            if hasattr(self._robot, "RobotEnable"):
                try:
                    en = self._robot.RobotEnable(1)
                    steps.append(f"RobotEnable(1)={en}")
                    time.sleep(0.3)
                except Exception as e:
                    steps.append(f"Enable异常:{e}")

            left = self._read_fault_message()
            detail = "; ".join(steps)
            if left:
                return (
                    False,
                    f"{self.name}: 已下发消警但仍有故障「{left}」。"
                    f"若为急停/外部故障信号，请先松开示教器急停并排除外部输入。"
                    f" [{detail}]",
                )
            if err != 0:
                return (
                    False,
                    f"{self.name}: ResetAllError 返回 {err}（可能属不可复位故障，请看示教器）。[{detail}]",
                )

            log.info("[%s] 示教器/控制器消警成功: %s", self.name, detail)
            return True, f"{self.name}: 示教器/控制器消警成功 [{detail}]"
        except Exception as e:
            log.exception("[%s] 消警异常", self.name)
            return False, f"{self.name}: 消警异常 {e}"

    def soft_estop(self) -> None:
        """软件急停：立刻停运动，禁止后续发令。"""
        self.estop_active = True
        self.halt_motion(hard=True)
        if self.use_mock:
            log.warning("[%s] Mock 软急停", self.name)

    def clear_estop(self) -> None:
        self.estop_active = False
