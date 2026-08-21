"""工控机 IO：物理急停、三色灯 DO（驱动未定，先 Mock）。"""

from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger(__name__)


class IOManager:
    def __init__(self, cfg: Dict[str, Any], use_mock: bool = True):
        self.cfg = cfg
        self.use_mock = use_mock
        self.estop_physical = False
        self.lights = {"red": False, "yellow": False, "green": False}

    def read_estop(self) -> bool:
        if self.use_mock:
            return self.estop_physical
        return self.estop_physical

    def set_estop_mock(self, value: bool) -> None:
        self.estop_physical = bool(value)

    def write_lights(self, red: bool, yellow: bool, green: bool) -> None:
        self.lights = {"red": red, "yellow": yellow, "green": green}
