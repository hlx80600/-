"""Local import shim for automation_machine.

Replace this directory with the real dependency:
    git clone git@github.com:RobotSkillsDevelopmentTeam/RSDT_Simple_Automation.git ./RSDT_Simple_Automation
"""

from __future__ import annotations

import logging
from typing import Any, Optional


class _HardwareModule:
    def __init__(self) -> None:
        self.orbbec_camera_dict: dict[str, Any] = {}

    def activate_orbbec_camera(self, alias: str, serial: str) -> None:
        raise RuntimeError(
            "RSDT_Simple_Automation stub only: install the real package"
        )

    def activate_fairino_arm(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "RSDT_Simple_Automation stub only: install the real package"
        )

    def activate_fairino_robot_arm(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "RSDT_Simple_Automation stub only: install the real package"
        )

    def get_fairino_robot_arm(self, key: str) -> Any:
        raise RuntimeError(
            "RSDT_Simple_Automation stub only: install the real package"
        )


class automationMachine:
    def __init__(self) -> None:
        self.hardwareModule = _HardwareModule()


def logModule(
    name: str = "PressShoesLogger",
    log_dir: Optional[str] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.INFO,
    rotation_when: str = "midnight",
    rotation_interval: int = 1,
    rotation_backup_count: int = 5,
    **kwargs: Any,
) -> logging.Logger:
    """Minimal logger factory compatible with call sites in this repo."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(console_level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(min(console_level, file_level))
    return logger
