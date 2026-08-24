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
import threading
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
    os.environ.pop("QT_PLUGIN_PATH", None)

    try:
        import PySide6

        plugins = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
        if plugins.is_dir():
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins)
    except Exception:
        pass

    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        else:
            os.environ["QT_QPA_PLATFORM"] = "xcb"


_fix_qt_env()

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class BootSplash(QWidget):
    """启动进度窗：标题 + 状态文字 + 进度条。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("启动中")
        self.setFixedSize(440, 180)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setStyleSheet(
            """
            QWidget#bootSplash {
                background: #154360;
                border: 1px solid #1a5276;
                border-radius: 10px;
            }
            QLabel#bootTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
            }
            QLabel#bootStatus {
                color: #d5dbdb;
                font-size: 13px;
            }
            QProgressBar {
                border: 1px solid #5dade2;
                border-radius: 6px;
                background: #1a5276;
                text-align: center;
                color: #ecf0f1;
                height: 22px;
            }
            QProgressBar::chunk {
                background: #2ecc71;
                border-radius: 5px;
            }
            """
        )
        self.setObjectName("bootSplash")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title = QLabel("莆田鞋厂四槽机器控制程序")
        title.setObjectName("bootTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        self._status = QLabel("正在启动…")
        self._status.setObjectName("bootStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFormat("%p%")
        root.addWidget(self._bar)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#2471a3;")
        root.addWidget(line)

    def center_on_screen(self, app: QApplication) -> None:
        screen = app.primaryScreen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.x() + (geo.width() - self.width()) // 2,
            geo.y() + (geo.height() - self.height()) // 2,
        )

    def set_progress(self, value: int, text: str, app: QApplication) -> None:
        """更新进度并立刻刷新界面。"""
        self._bar.setValue(max(0, min(100, int(value))))
        self._status.setText(text)
        app.processEvents()


def _start_mobile_web(coord, win) -> None:
    """手机 Web 不挡主界面，窗口出来后再开。"""
    from mobile.web_server import start_mobile_web_from_cfg

    try:
        mobile = start_mobile_web_from_cfg(coord)
        coord.mobile_web = mobile
        if mobile is not None:
            win.setWindowTitle(
                f"莆田鞋厂四槽机器控制程序  |  手机: {mobile.access_url()}"
            )
            print(f"\n★ 手机监控: {mobile.access_url()}\n", flush=True)
        log.info("手机 Web 已就绪")
    except Exception:
        log.exception("启动手机 Web 失败")


def main() -> None:
    setup_logging()
    _fix_qt_env()

    app = QApplication(sys.argv)

    splash = BootSplash()
    splash.center_on_screen(app)
    splash.show()
    splash.set_progress(5, "正在启动…", app)

    splash.set_progress(15, "加载配置与模块…", app)
    from core.app_context import AppContext
    from core.coordinator import Coordinator
    from hmi import i18n
    from hmi.main_window import MainWindow

    splash.set_progress(30, "创建应用上下文…", app)
    ctx = AppContext()
    i18n.init_from_cfg(ctx.cfg)
    app.setApplicationName(i18n.tr("app.title"))

    # ★ 先连设备再开协调线程，避免「未连接就报 LINK」
    def _conn_progress(text: str) -> None:
        # 连接阶段 35～70
        splash.set_progress(min(70, splash._bar.value() + 5), text, app)

    splash.set_progress(35, "连接设备…", app)
    try:
        ctx.connect_all(on_progress=_conn_progress)
    except Exception:
        log.exception("启动连接设备异常")
        splash.set_progress(70, "设备连接异常（可稍后在通信配置重连）", app)

    splash.set_progress(75, "启动协调线程…", app)
    coord = Coordinator(ctx)
    coord.start_thread()

    splash.set_progress(88, "构建主界面…", app)
    win = MainWindow(coord)

    splash.set_progress(100, "启动完成", app)
    splash.close()

    win.show()
    app.processEvents()

    threading.Thread(
        target=_start_mobile_web,
        args=(coord, win),
        daemon=True,
        name="mobile-web",
    ).start()

    code = app.exec()

    mobile = getattr(coord, "mobile_web", None)
    if mobile is not None:
        mobile.stop()
    coord.stop_thread()
    try:
        ctx.io.write_lights(False, True, False)
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
