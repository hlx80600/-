"""
空跑联调屏蔽 —— 无实物/少传感器时跑通 Station1～6 握手。

启用后每扫描周期自动：
  · 皮带光电保持有料（可触发 S1）
  · 放料槽：空槽 + 左右跟手中鞋（避免 Mem10 卡死）
  · 取料槽：待旋转时无料（S6 要 not Mem6）；转完后有料（S5）
  · 压鞋机 Mock：先压鞋完成延时，再旋转完成延时（对齐 Station6 先压后转）

相机模拟以通信配置 / yaml cameras.camN.use_mock 为准，空跑不再改写。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, Optional

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
        self._saved_press_mock: Optional[bool] = None
        self._cam_prev: Dict[str, bool] = {}

    def status_lines(self) -> list[str]:
        v = self.ctx.vision
        p = self.ctx.press
        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        belt = bool(self.ctx.robot1.get_di(belt_di))
        press_cfg = self.ctx.cfg.get("press") or {}
        return [
            f"空跑屏蔽: {'开' if self.enabled else '关'}",
            f"光电DI[{belt_di}]={'有料' if belt else '无料'} | 保持有料={self.keep_belt_on}",
            f"放料槽Mock: 有料={v.mock_place_has_material} 左槽={v.mock_place_is_left} | 自动跟手={self.auto_place_match}",
            f"取料槽Mock有料={v.mock_pick_has_material} | 自动={self.auto_pick_slot}",
            f"压机: rotate_done={p.rotate_done} press_done={p.press_done} "
            f"auto_press_s={press_cfg.get('mock_auto_press_done_s', 0)} "
            f"auto_rotate_s={press_cfg.get('mock_auto_rotate_done_s', 0)}",
            f"相机: "
            + " ".join(
                f"{k}={'模' if self.ctx.vision.cam_is_mock(k) else '真'}"
                for k in ("cam1", "cam2", "cam3", "cam4")
            ),
        ]

    def enable(self) -> None:
        """一键空跑：打开屏蔽并写入运行态。"""
        self.enabled = True
        cfg_sys = self.ctx.cfg.setdefault("system", {})
        dry = cfg_sys.setdefault("dry_run", {})
        dry["enabled"] = True
        dry["auto_rotate_s"] = float(self.auto_rotate_s)
        dry["auto_press_s"] = float(self.auto_press_s)

        # 皮带光电强制模拟 + 有料
        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        self.ctx.robot1.set_di_force_mock(belt_di, True)
        self.ctx.cfg["robots"]["robot1"]["di_belt_use_mock"] = True
        if self.keep_belt_on:
            self.ctx.robot1.set_di_mock(belt_di, True)

        # 压机：空跑强制 Mock，并写入「先压后转」延时
        press = self.ctx.cfg.setdefault("press", {})
        if self._saved_press_auto is None:
            self._saved_press_auto = float(press.get("mock_auto_rotate_done_s", 0) or 0)
        if self._saved_press_done_s is None:
            self._saved_press_done_s = float(press.get("mock_auto_press_done_s", 0) or 0)
        if self._saved_press_mock is None:
            self._saved_press_mock = bool(self.ctx.press.use_mock)
        press["use_mock"] = True
        self.ctx.press.use_mock = True
        press["mock_auto_press_done_s"] = float(self.auto_press_s)
        press["mock_auto_rotate_done_s"] = float(self.auto_rotate_s)

        # 初始放料空槽、取料先无料（避免一开机 Mem6 挡住 S6）
        self.ctx.vision.mock_place_has_material = False
        self.ctx.vision.mock_place_is_left = True
        self.ctx.vision.mock_pick_has_material = False

        # 压机到位默认 True，便于进入条件
        self.ctx.press.set_rotate_done_mock(True)
        self.ctx.press.set_press_done_mock(True)

        log.info(
            "[空跑] 已启用：光电模拟 压机auto_press=%.1fs auto_rotate=%.1fs 放料自动跟手（不改相机模拟）",
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
        if self._saved_press_mock is not None:
            press["use_mock"] = bool(self._saved_press_mock)
            self.ctx.press.use_mock = bool(self._saved_press_mock)
            self._saved_press_mock = None
        log.info("[空跑] 已关闭（相机 Mock 保持现状，可自行在通信配置改回）")

    def tick(self) -> None:
        """OB1 每周期调用：维持空跑所需信号。"""
        if not self.enabled:
            return

        belt_di = int(self.ctx.cfg["robots"]["robot1"].get("di_belt_sensor", 0))
        if self.keep_belt_on:
            self.ctx.robot1.set_di_force_mock(belt_di, True)
            self.ctx.robot1.set_di_mock(belt_di, True)

        # 压机 auto 时间保持（先压后转）
        press = self.ctx.cfg.setdefault("press", {})
        want_p = float(self.auto_press_s)
        want_r = float(self.auto_rotate_s)
        if float(press.get("mock_auto_press_done_s", 0) or 0) != want_p:
            press["mock_auto_press_done_s"] = want_p
        if float(press.get("mock_auto_rotate_done_s", 0) or 0) != want_r:
            press["mock_auto_rotate_done_s"] = want_r
        if not self.ctx.press.use_mock:
            self.ctx.press.use_mock = True
            press["use_mock"] = True

        M = self.ctx.gvl.Memory_BOOL
        v = self.ctx.vision

        # —— 放料槽：手中有料时强制空槽 + 左右跟 Mem8/9 ——
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

        # —— 取料槽：Mem3 待转时必须无料；否则（且下手无料）有料供 S4/S5 ——
        if self.auto_pick_slot:
            if bool(M.get(3)):
                v.mock_pick_has_material = False
                # 若开机已拍成 Mem6=1，会挡住 S6：空跑强制清
                if bool(M.get(6)) and not bool(M.get(5)):
                    from core.plc_util import sync_mem

                    sync_mem(self.ctx, 6, False)
            elif bool(M.get(5)):
                v.mock_pick_has_material = False
            else:
                # 转完 / 待机：有料，供取料槽拍照与下料臂
                v.mock_pick_has_material = True


def apply_dry_run_from_cfg(ctx: "AppContext") -> None:
    """启动时若 yaml 已开 dry_run.enabled，则自动启用。"""
    dry = (ctx.cfg.get("system") or {}).get("dry_run") or {}
    if bool(dry.get("enabled", False)):
        ctx.dry_run.auto_rotate_s = float(dry.get("auto_rotate_s", DEFAULT_AUTO_ROTATE_S))
        ctx.dry_run.auto_press_s = float(dry.get("auto_press_s", DEFAULT_AUTO_PRESS_S))
        ctx.dry_run.enable()
