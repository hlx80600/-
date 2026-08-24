"""导航页内部 id（稳定键）；显示文案见 hmi.i18n.nav_label。"""

from __future__ import annotations

from hmi import i18n


class NavId:
    MONITOR = "monitor"
    PRODUCTION = "production"
    STEP_DEBUG = "step_debug"
    MOTION = "motion"
    VISION = "vision"
    POINTS = "points"
    SHIELD_PICK = "shield_pick"
    DRY_RUN = "dry_run"
    PAYLOAD = "payload"
    PRESS_IO = "press_io"
    GRIPPER = "gripper"
    SETTINGS = "settings"
    ALARM = "alarm"
    HELP = "help"
    CAM_MONITOR = "cam_monitor"
    # 非导航项：goto / 帮助别名
    CONFIG = "config"
    VISION_SETUP = "vision_setup"


class T(NavId):
    """兼容旧 import：T.MONITOR 等为 NavId，不再直接等于中文标题。"""


def nav_title(nav_id: str) -> str:
    """当前语言的导航/页标题。"""
    if nav_id == NavId.VISION_SETUP:
        return i18n.tr("alias.vision_setup")
    if nav_id == NavId.CONFIG:
        return i18n.tr("settings.tab.communication")
    return i18n.nav_label(nav_id)
