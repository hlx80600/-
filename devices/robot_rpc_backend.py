"""法奥 RPC 后端：工位仍只调 RobotFR5；底层可换本仓 SDK 或 RSDT 封装。

工位 / HMI 不要直接 import 本模块。请继续：
  ctx.robot1.move_j / move_l / poll_move_done
可选样条（仅 rsdt 后端）：
  ctx.robot1.follow_quintic_spline(points)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BACKEND_FR5 = "fr5"
BACKEND_RSDT = "rsdt"

_ROOT = Path(__file__).resolve().parents[1]
_RSDT = _ROOT / "RSDT_Simple_Automation"


def normalize_rpc_backend(raw: Any) -> str:
    """yaml robots.rpc_backend → fr5 | rsdt。"""
    key = str(raw or BACKEND_RSDT).strip().lower()
    if key in ("fr5", "native", "local", "sdk"):
        return BACKEND_FR5
    return BACKEND_RSDT


def _ensure_official_fairino_on_path() -> None:
    """与 robot_fr5 相同：把试验/fairino 的上一级加入 path。"""
    trial_dir = _ROOT.parent
    if str(trial_dir) not in sys.path:
        sys.path.insert(0, str(trial_dir))
    sibling = _ROOT.parent / "fairino"
    if sibling.is_dir() and str(sibling.parent) not in sys.path:
        sys.path.insert(0, str(sibling.parent))


def _alias_official_sdk_into_rsdt() -> None:
    """RSDT 写的是 from .fairino import Robot，现场 SDK 往往在试验/fairino。"""
    _ensure_official_fairino_on_path()
    try:
        import fairino as official  # type: ignore
    except Exception:
        return
    sys.modules.setdefault("hardware_module.fairino.fairino", official)
    pkg = "RSDT_Simple_Automation.hardware_module.fairino.fairino"
    sys.modules.setdefault(pkg, official)


def _import_fr5_rpc() -> Any:
    _ensure_official_fairino_on_path()
    from fairino import Robot  # type: ignore

    return Robot.RPC


def _import_rsdt_arm_cls() -> Any:
    if not _RSDT.is_dir():
        raise RuntimeError(f"未找到 {_RSDT}，请先 clone RSDT_Simple_Automation")
    root_s = str(_RSDT)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    _alias_official_sdk_into_rsdt()
    from hardware_module.fairino.fairino_robot_arm_inherit import (  # type: ignore
        FairinoRobotArm,
    )

    return FairinoRobotArm


def _mark_connect_flag(rpc: Any) -> None:
    """统一 is_connect / 官方拼写 is_conect。"""
    if hasattr(rpc, "is_connect"):
        return
    if hasattr(rpc, "is_conect"):
        try:
            rpc.is_connect = bool(rpc.is_conect)
        except Exception:
            pass


def create_fairino_rpc(
    ip: str,
    *,
    backend: str = BACKEND_RSDT,
    name: str = "robot",
) -> Any:
    """创建底层 RPC 对象。Station 层仍经 RobotFR5 发 Move。

    rsdt：RSDT FairinoRobotArm（继承 RPC，带 RobotServoSpline 五次样条）。
    fr5：本机 from fairino import Robot.RPC。
    rsdt 导入失败时自动退回 fr5。
    """
    kind = normalize_rpc_backend(backend)
    if kind == BACKEND_RSDT:
        try:
            cls = _import_rsdt_arm_cls()
            arm = cls(name, ip)
            _mark_connect_flag(arm)
            log.info("[%s] 机械臂 RPC 后端=rsdt（RSDT FairinoRobotArm）ip=%s", name, ip)
            return arm
        except Exception as exc:
            log.warning(
                "[%s] RSDT 机械臂后端不可用，改用本仓 fairino RPC: %s", name, exc
            )
    rpc = _import_fr5_rpc()(ip)
    _mark_connect_flag(rpc)
    log.info("[%s] 机械臂 RPC 后端=fr5（Robot.RPC）ip=%s", name, ip)
    return rpc
