"""
三色灯灯语：
- 绿常亮 = 自动运行
- 黄常亮 + 绿常亮 = 初始化完成（READY，可启动）
- 黄常亮 = 停止后 / 未初始化（IDLE）
- 黄闪 = 初始化中 / 暂停 / 单步
- 红常亮 = 报警
- 红闪 = 急停
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock

from core.machine_state import MachineState, RunMode


@dataclass
class LightOutput:
    red: bool = False
    yellow: bool = False
    green: bool = False
    red_blink: bool = False
    yellow_blink: bool = False


class TowerLight:
    def __init__(self) -> None:
        self._lock = RLock()
        self.cmd = LightOutput()
        self._blink_on = True
        self._last_toggle = time.monotonic()

    def update(self, state: MachineState, mode: RunMode, has_alarm: bool) -> LightOutput:
        now = time.monotonic()
        if now - self._last_toggle >= 0.5:
            self._blink_on = not self._blink_on
            self._last_toggle = now

        out = LightOutput()

        # 停止后只亮黄；READY 黄+绿。报警队列未清时 STOPPED/READY/IDLE 不抢成红灯
        if state == MachineState.ESTOP:
            out.red_blink = True
            out.red = self._blink_on
        elif state == MachineState.ALARM or (
            has_alarm and state not in (MachineState.READY, MachineState.STOPPED, MachineState.IDLE)
        ):
            out.red = True
        elif state == MachineState.INITIALIZING or state == MachineState.PAUSED:
            out.yellow_blink = True
            out.yellow = self._blink_on
        elif state == MachineState.RUNNING and mode == RunMode.AUTO:
            out.green = True
        elif state == MachineState.READY:
            # 初始化完成，可启动
            out.yellow = True
            out.green = True
        elif state == MachineState.STOPPED:
            # 停止程序后：只亮黄
            out.yellow = True
        elif state == MachineState.RUNNING and mode == RunMode.SINGLE_STEP:
            out.yellow_blink = True
            out.yellow = self._blink_on
        else:
            # IDLE 等：黄常亮
            out.yellow = True

        with self._lock:
            self.cmd = out
        return out

    def snapshot(self) -> dict:
        with self._lock:
            c = self.cmd
            return {
                "red": c.red,
                "yellow": c.yellow,
                "green": c.green,
                "red_blink": c.red_blink,
                "yellow_blink": c.yellow_blink,
            }
