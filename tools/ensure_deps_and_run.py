#!/usr/bin/env python3
"""启动前检查依赖：缺失则用国内镜像自动安装，并显示进度条。

由「启动四槽压鞋机.sh」/桌面图标调用；装完后 exec 进入 main.py。
仅依赖 Python 标准库（tkinter）即可弹出进度窗。
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
MAIN_PY = ROOT / "main.py"

# 清华 PyPI 镜像（与 README「国内镜像安装」一致）
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_TRUSTED_HOST = "pypi.tuna.tsinghua.edu.cn"

# pip 包名 → 用于检测的 import 名
_PACKAGE_IMPORTS: dict[str, str] = {
    "PySide6": "PySide6",
    "PyYAML": "yaml",
    "numpy": "numpy",
    "opencv-python-headless": "cv2",
    "scipy": "scipy",
    "pymodbus": "pymodbus",
    "python-can": "can",
}


def _parse_requirement_names(path: Path) -> list[str]:
    """从 requirements.txt 取出需安装的发行包名（忽略注释与空行）。"""
    if not path.is_file():
        return list(_PACKAGE_IMPORTS.keys())
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉 -e / 选项行
        if line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def _is_importable(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def missing_packages() -> list[str]:
    """返回尚未可导入的 pip 包名列表。"""
    missing: list[str] = []
    for pkg in _parse_requirement_names(REQUIREMENTS):
        mod = _PACKAGE_IMPORTS.get(pkg, pkg.replace("-", "_"))
        if not _is_importable(mod):
            missing.append(pkg)
    return missing


def _pip_base_cmd() -> list[str]:
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-i",
        PIP_INDEX,
        "--trusted-host",
        PIP_TRUSTED_HOST,
    ]


def _run_pip(args: list[str], on_line: Callable[[str], None] | None = None) -> int:
    """跑 pip；返回退出码。on_line 收到解码后的输出行。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # 禁止 pip 弹浏览器 / 交互
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    proc = subprocess.Popen(
        args,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if on_line is not None and text:
            on_line(text)
    return int(proc.wait())


def install_requirements(
    *,
    on_status: Callable[[str], None],
    on_progress: Callable[[int], None],
    break_system_packages: bool = False,
) -> tuple[bool, str]:
    """用国内镜像安装 requirements.txt。

    Returns:
        (成功?, 错误信息)
    """
    extra: list[str] = []
    if break_system_packages:
        extra.append("--break-system-packages")

    on_progress(8)
    on_status("正在升级 pip（清华镜像）…")

    def _line(text: str) -> None:
        # 进度条上只显示短状态，避免刷屏
        short = text if len(text) <= 72 else text[:69] + "…"
        on_status(short)

    code = _run_pip(
        _pip_base_cmd() + extra + ["-U", "pip", "setuptools", "wheel"],
        on_line=_line,
    )
    if code != 0:
        if not break_system_packages:
            on_status("系统保护 Python，改用 --break-system-packages 重试升级 pip…")
            return install_requirements(
                on_status=on_status,
                on_progress=on_progress,
                break_system_packages=True,
            )
        return False, f"升级 pip 失败（退出码 {code}）"

    on_progress(20)
    on_status("正在安装项目依赖（清华镜像）…")

    # 安装过程用假进度：每读一行略增，封顶 88
    progress = 20

    def _install_line(text: str) -> None:
        nonlocal progress
        low = text.lower()
        if "downloading" in low or "collecting" in low:
            progress = min(70, progress + 2)
        elif "installing" in low or "using cached" in low:
            progress = min(85, progress + 1)
        else:
            progress = min(88, progress + 0.3)
        on_progress(int(progress))
        _line(text)

    code = _run_pip(
        _pip_base_cmd() + extra + ["-r", str(REQUIREMENTS)],
        on_line=_install_line,
    )
    if code != 0:
        if not break_system_packages:
            on_status("检测到 externally-managed-environment，自动加参数重试…")
            return install_requirements(
                on_status=on_status,
                on_progress=on_progress,
                break_system_packages=True,
            )
        return False, (
            f"安装依赖失败（退出码 {code}）。\n"
            f"可手动执行：\n"
            f"python3 -m pip install -r requirements.txt "
            f"-i {PIP_INDEX}"
        )

    on_progress(92)
    on_status("正在校验依赖…")
    still = missing_packages()
    if still:
        return False, "安装后仍缺少：" + "、".join(still)

    on_progress(100)
    on_status("依赖已就绪")
    return True, ""


def _show_progress_and_install(missing: list[str]) -> bool:
    """弹出进度窗并安装；成功返回 True。"""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:  # noqa: BLE001
        print(f"[依赖] 无法打开进度窗（无 tkinter）: {exc}", flush=True)
        print(f"[依赖] 缺失: {', '.join(missing)}，开始命令行安装…", flush=True)

        def _print_status(text: str) -> None:
            print(f"[依赖] {text}", flush=True)

        def _print_progress(value: int) -> None:
            print(f"[依赖] 进度 {value}%", flush=True)

        ok, err = install_requirements(
            on_status=_print_status, on_progress=_print_progress
        )
        if not ok:
            print(f"[依赖] 失败: {err}", flush=True)
        return ok

    root = tk.Tk()
    root.title("四槽压鞋机 · 安装依赖")
    root.geometry("520x220")
    root.resizable(False, False)

    # 尽量置顶，像启动闪屏
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)

    title = ttk.Label(
        frame,
        text="正在检查并安装运行依赖（国内镜像）",
        font=("Sans", 12, "bold"),
    )
    title.pack(anchor=tk.W)

    tip = ttk.Label(
        frame,
        text="镜像：" + PIP_INDEX + "\n缺少：" + "、".join(missing),
        wraplength=480,
        justify=tk.LEFT,
    )
    tip.pack(anchor=tk.W, pady=(8, 12))

    status = ttk.Label(frame, text="准备中…", wraplength=480)
    status.pack(anchor=tk.W)

    bar = ttk.Progressbar(frame, mode="determinate", maximum=100, length=480)
    bar.pack(pady=(10, 0))
    bar["value"] = 2

    result: dict[str, object] = {"ok": False, "err": ""}

    def on_status(text: str) -> None:
        status.after(0, lambda t=text: status.configure(text=t))

    def on_progress(value: int) -> None:
        bar.after(0, lambda v=int(value): bar.configure(value=v))

    def worker() -> None:
        ok, err = install_requirements(on_status=on_status, on_progress=on_progress)
        result["ok"] = ok
        result["err"] = err
        root.after(0, root.quit)

    threading.Thread(target=worker, daemon=True, name="deps-install").start()
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass

    if not result["ok"]:
        try:
            messagebox.showerror("依赖安装失败", str(result["err"] or "未知错误"))
        except Exception:
            print(f"[依赖] 失败: {result['err']}", flush=True)
        return False
    return True


def exec_main() -> None:
    """进入正式程序（替换当前进程）。"""
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # 与 main.py / 启动脚本一致
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.execv(sys.executable, [sys.executable, str(MAIN_PY), *sys.argv[1:]])


def main() -> int:
    if not MAIN_PY.is_file():
        print(f"找不到入口: {MAIN_PY}", file=sys.stderr)
        return 1

    missing = missing_packages()
    if missing:
        print(f"[依赖] 缺少: {', '.join(missing)}，开始自动安装…", flush=True)
        if not _show_progress_and_install(missing):
            return 2
        # 安装后必须重启进程，否则已失败的 import 缓存/旧解释器状态不可靠
        os.execv(
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        )

    exec_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
