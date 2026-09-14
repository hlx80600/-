#!/usr/bin/env bash
# 四槽压鞋机 HMI 启动脚本（桌面图标调用）
# 先检查/自动安装依赖（清华镜像 + 进度条），再进入 main.py
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

unset QT_PLUGIN_PATH || true

# Qt 6.5+ 在 X11 上需要 libxcb-cursor。系统未装时使用用户目录里的副本。
XCB_LIB="${HOME}/.local/opt/xcb-cursor/usr/lib/x86_64-linux-gnu"
if [[ -d "$XCB_LIB" ]]; then
  export LD_LIBRARY_PATH="${XCB_LIB}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LAUNCH_LOG="$LOG_DIR/desktop_launch.log"
LOCK_FILE="$LOG_DIR/hmi.singleton.lock"

# 防止桌面图标与开机自启动各拉起一份
exec 9>"$LOCK_FILE"
if command -v flock >/dev/null 2>&1; then
  if ! flock -n 9; then
    if command -v notify-send >/dev/null 2>&1; then
      notify-send "四槽压鞋机" "程序已在运行" || true
    fi
    exit 0
  fi
fi

set +e
python3 "$ROOT/tools/ensure_deps_and_run.py" "$@" >"$LAUNCH_LOG" 2>&1
code=$?
set -e

if [[ "$code" -ne 0 ]]; then
  msg="$(tail -n 25 "$LAUNCH_LOG" 2>/dev/null || true)"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="四槽压鞋机启动失败" --width=520 \
      --text="启动失败（退出码 ${code}）。\n日志：${LAUNCH_LOG}\n\n${msg}" || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send "四槽压鞋机启动失败" "见 ${LAUNCH_LOG}"
  fi
  exit "$code"
fi
