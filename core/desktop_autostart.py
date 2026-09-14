"""登录后自动启动四槽压鞋机（XDG Autostart，当前用户）。

工控机需同时开启桌面「自动登录」，否则只停在登录界面，程序不会起来。
"""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "四槽压鞋机"
DESKTOP_NAME = f"{APP_NAME}.desktop"
LAUNCHER = ROOT / "启动四槽压鞋机.sh"
ICON = ROOT / "hmi/assets/rsdt_badge.png"

# 等图形会话与网卡就绪，避免一登录就因 DISPLAY/Qt 插件失败
AUTOSTART_DELAY_S = 8


def autostart_path() -> Path:
    return Path.home() / ".config" / "autostart" / DESKTOP_NAME


def _desktop_body() -> str:
    return f"""[Desktop Entry]
Version=1.0
Type=Application
Name={APP_NAME}
Name[zh_CN]={APP_NAME}
GenericName=四槽压鞋机控制程序
Comment=登录后自动启动四槽压鞋机
Exec="{LAUNCHER}"
Icon={ICON}
Path={ROOT}
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay={AUTOSTART_DELAY_S}
Hidden=false
"""


def is_enabled() -> bool:
    path = autostart_path()
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if "Hidden=true" in text:
        return False
    if "X-GNOME-Autostart-enabled=false" in text:
        return False
    return True


def enable() -> Path:
    if not LAUNCHER.is_file():
        raise FileNotFoundError(f"找不到启动脚本: {LAUNCHER}")
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_desktop_body(), encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def disable() -> None:
    path = autostart_path()
    if path.is_file():
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="四槽压鞋机开机/登录自启动")
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("enable", "disable", "status"),
    )
    args = parser.parse_args(argv)
    if args.action == "enable":
        path = enable()
        print(f"已启用登录自启动: {path}")
        print("工控机请在系统设置中打开该用户的自动登录。")
        return 0
    if args.action == "disable":
        disable()
        print("已关闭登录自启动")
        return 0
    on = is_enabled()
    print(f"{'已启用' if on else '未启用'}  ({autostart_path()})")
    return 0 if on else 1


if __name__ == "__main__":
    sys.exit(main())
