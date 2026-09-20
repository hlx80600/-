"""
空跑联调屏蔽 —— 无实物光电/槽位时跑通 Station1～6 握手。

设备是否 Mock 以设置页 / yaml 为准，空跑不再改写。
启用后每扫描周期：
  · 皮带光电保持有料（可触发 S1）
  · 放料槽：空槽 + 左右跟手中鞋（避免 Mem10 卡死）
  · 取料槽：未拍时有料供 S4/S5；拍完且取完后无料，不提前清 Mem6
  · 仅当压机本身是 Mock 时：空闲维持「取料槽工作完成=1」，启动后先忙再空闲
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from core.app_context import AppContext


DEFAULT_AUTO_ROTATE_S = 1.5
DEFAULT_AUTO_PRESS_S = 2.0


class DryRunShield:
    def __init__(self, ctx: "AppContext"):
        self.ctx = ctx
        dry = (ctx.cfg.get("system") or {}).get("dry_run") or {}
        self.enabled = bool(dry.get("enabled", False))
        self.keep_belt_on = True
        self.auto_place_match = True
        self.auto_pick_slot = True
        self.auto_rotate_s = float(dry.get("auto_rotate_s", DEFAULT_AUTO_ROTATE_S))
        self.auto_press_s = float(dry.get("auto_press_s", DEFAULT_AUTO_PRESS_S))
        self._saved_press_auto: Optional[float] = None
        self._saved_press_done_s: Optional[float] = None

    def status_lines(self) -> list[str]:
        v = self.ctx.vision
        p = self.ctx.press
        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        belt = bool(self.ctx.robot1.get_di(belt_di))
        press_cfg = self.ctx.cfg.get("press") or {}
        return [
            f"空跑屏蔽: {'开' if self.enabled else '关'}（不改设置里的设备 Mock）",
            f"R1={'模' if self.ctx.robot1.use_mock else '真'} "
            f"R2={'模' if self.ctx.robot2.use_mock else '真'} "
            f"压机={'模' if p.use_mock else '真'} "
            f"爪1={'模' if self.ctx.gripper1.use_mock else '真'} "
            f"爪2={'模' if self.ctx.gripper2.use_mock else '真'}",
            f"光电DI[{belt_di}]={'有料' if belt else '无料'} | 保持有料={self.keep_belt_on}",
            f"放料槽Mock: 有料={v.mock_place_has_material} 左槽={v.mock_place_is_left} | 自动跟手={self.auto_place_match}",
            f"取料槽Mock有料={v.mock_pick_has_material} | 自动={self.auto_pick_slot}",
            f"压机: 空闲={int(p.rotate_done)} 取料槽工作完成={int(p.is_pick_work_done())} "
            f"放鞋完成={p.cas_word('shoe_done')} 启动={p.cas_word('start')}",
            f"auto_press_s={press_cfg.get('mock_auto_press_done_s', 0)} "
            f"auto_rotate_s={press_cfg.get('mock_auto_rotate_done_s', 0)}",
            f"相机: "
            + " ".join(
                f"{k}={'模' if self.ctx.vision.cam_is_mock(k) else '真'}"
                for k in ("cam1", "cam2", "cam3", "cam4")
            ),
        ]

    def enable(self) -> None:
        """一键空跑：维持光电/槽位时序；设备是否 Mock 以设置页为准。"""
        self.enabled = True
        cfg_sys = self.ctx.cfg.setdefault("system", {})
        dry = cfg_sys.setdefault("dry_run", {})
        dry["enabled"] = True
        dry["auto_rotate_s"] = float(self.auto_rotate_s)
        dry["auto_press_s"] = float(self.auto_press_s)

        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        self.ctx.robot1.set_di_force_mock(belt_di, True)
        self.ctx.cfg["robots"]["robot1"]["di_belt_use_mock"] = True
        if self.keep_belt_on:
            self.ctx.robot1.set_di_mock(belt_di, True)

        press = self.ctx.cfg.setdefault("press", {})
        if self._saved_press_auto is None:
            self._saved_press_auto = float(press.get("mock_auto_rotate_done_s", 0) or 0)
        if self._saved_press_done_s is None:
            self._saved_press_done_s = float(press.get("mock_auto_press_done_s", 0) or 0)
        press["mock_auto_press_done_s"] = float(self.auto_press_s)
        press["mock_auto_rotate_done_s"] = float(self.auto_rotate_s)

        self.ctx.vision.mock_place_has_material = False
        self.ctx.vision.mock_place_is_left = True
        self.ctx.vision.mock_pick_has_material = True

        if self.ctx.press.use_mock:
            self.ctx.press.set_rotate_done_mock(True)
            self.ctx.press.set_press_done_mock(True)
            self.ctx.press.set_pick_work_done_mock(True)

        log.info(
            "[空跑] 已启用（压机Mock=%s）：压合=%.1fs 转盘=%.1fs",
            self.ctx.press.use_mock,
            self.auto_press_s,
            self.auto_rotate_s,
        )
        self.tick()

    def disable(self) -> None:
        self.enabled = False
        dry = self.ctx.cfg.setdefault("system", {}).setdefault("dry_run", {})
        dry["enabled"] = False
        press = self.ctx.cfg.setdefault("press", {})
        if self._saved_press_auto is not None:
            press["mock_auto_rotate_done_s"] = float(self._saved_press_auto)
            self._saved_press_auto = None
        if self._saved_press_done_s is not None:
            press["mock_auto_press_done_s"] = float(self._saved_press_done_s)
            self._saved_press_done_s = None
        log.info("[空跑] 已关闭")

    def tick(self) -> None:
        """OB1 每周期调用：维持空跑所需信号。"""
        if not self.enabled:
            return

        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        if self.keep_belt_on:
            self.ctx.robot1.set_di_force_mock(belt_di, True)
            self.ctx.robot1.set_di_mock(belt_di, True)

        press = self.ctx.cfg.setdefault("press", {})
        want_p = float(self.auto_press_s)
        want_r = float(self.auto_rotate_s)
        if float(press.get("mock_auto_press_done_s", 0) or 0) != want_p:
            press["mock_auto_press_done_s"] = want_p
        if float(press.get("mock_auto_rotate_done_s", 0) or 0) != want_r:
            press["mock_auto_rotate_done_s"] = want_r
        if self.ctx.press.use_mock and self.ctx.press.is_press_idle():
            self.ctx.press.set_pick_work_done_mock(True)

        M = self.ctx.gvl.Memory_BOOL
        v = self.ctx.vision

        if self.auto_place_match and bool(M.get(2)):
            m8, m9 = bool(M.get(8)), bool(M.get(9))
            if m8 and not m9:
                v.mock_place_is_left = True
            elif m9 and not m8:
                v.mock_place_is_left = False
            else:
                snap = getattr(self.ctx.gvl, "BeltPickSnapshot", None) or self.ctx.gvl.PickPose
                from devices.pose_utils import is_left_shoe_flag

                v.mock_place_is_left = is_left_shoe_flag(
                    (snap or {}).get("is_left_shoe", True)
                )
            v.mock_place_has_material = False

        if self.auto_pick_slot:
            s5_picking = int(self.ctx.gvl.Station[5].Auto_A.get(10, 0) or 0) != 0
            if s5_picking:
                pass
            elif not bool(M.get(7)):
                v.mock_pick_has_material = True
            elif bool(M.get(6)):
                v.mock_pick_has_material = True
            else:
                v.mock_pick_has_material = False


def apply_dry_run_from_cfg(ctx: "AppContext") -> None:
    """启动时若 yaml 已开 dry_run.enabled，则自动启用。"""
    dry = (ctx.cfg.get("system") or {}).get("dry_run") or {}
    if bool(dry.get("enabled", False)):
        ctx.dry_run.auto_rotate_s = float(dry.get("auto_rotate_s", DEFAULT_AUTO_ROTATE_S))
        ctx.dry_run.auto_press_s = float(dry.get("auto_press_s", DEFAULT_AUTO_PRESS_S))
        ctx.dry_run.enable()
