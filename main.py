#!/usr/bin/env python3
"""
莆田鞋厂四槽机器控制程序 —— 入口

使用：
1. 先装依赖：pip install -r requirements.txt
2. 默认 use_mock=true，无需真机即可打开 HMI 走流程
3. 运行：python3 main.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 保证以项目根目录为模块搜索路径
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 同级目录法奥 SDK：.../试验/fairino → 把「试验」加入 path，才能 from fairino import Robot
_FAIRINO_PARENT = ROOT.parent
if (_FAIRINO_PARENT / "fairino").is_dir() and str(_FAIRINO_PARENT) not in sys.path:
    sys.path.insert(0, str(_FAIRINO_PARENT))


def _fix_qt_env() -> None:
    """
    修复常见闪退原因：
    1) opencv-python 自带 Qt 插件会抢 PySide6 的插件路径 → 约 1 秒后崩溃退出
    2) Wayland/X11 平台插件选错也会直接退出
    """
    # 清掉 OpenCV 等注入的插件搜索路径
    os.environ.pop("QT_PLUGIN_PATH", None)

    try:
        import PySide6

        plugins = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
        if plugins.is_dir():
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins)
    except Exception:
        pass

    # 未手动指定时：Wayland 会话用 wayland，否则用 xcb
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        else:
            os.environ["QT_QPA_PLATFORM"] = "xcb"


_fix_qt_env()

from PySide6.QtWidgets import QApplication

from core.app_context import AppContext
from core.coordinator import Coordinator
from hmi.main_window import MainWindow
from mobile.web_server import start_mobile_web_from_cfg


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    setup_logging()
    # AppContext 可能 import cv2，之后再修一次 Qt 环境
    _fix_qt_env()

    app = QApplication(sys.argv)
    app.setApplicationName("莆田鞋厂四槽机器控制程序")

    ctx = AppContext()
    ctx.connect_all()
    coord = Coordinator(ctx)
    coord.start_thread()

    mobile = start_mobile_web_from_cfg(coord)
    coord.mobile_web = mobile  # 供监视页显示地址

    win = MainWindow(coord)
    if mobile is not None:
        win.setWindowTitle(
            f"莆田鞋厂四槽机器控制程序  |  手机: {mobile.access_url()}"
        )
    win.show()
    code = app.exec()

    if mobile is not None:
        mobile.stop()
    coord.stop_thread()
    # 退出软件时三色灯只亮黄，避免物理灯停在运行绿
    try:
        ctx.io.write_lights(False, True, False)
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
