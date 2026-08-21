"""主界面标签名称（现场 HMI 用语，短、专业、职责一眼能看懂）。"""

from __future__ import annotations


class T:
    MONITOR = "运行监控"
    PRODUCTION = "产量统计"
    STEP_DEBUG = "工位调试"
    MOTION = "运动参数"
    VISION_SETUP = "视觉采图"
    VISION = "视觉调试"
    POINTS = "示教点位"
    SHIELD_PICK = "屏蔽取料"
    DRY_RUN = "空跑联调"
    PAYLOAD = "负载工具"
    PRESS_IO = "压机信号"
    CONFIG = "通信配置"
    ALARM = "报警记录"
    HELP = "使用说明"
    CAM_MONITOR = "相机监控"
