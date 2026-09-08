#!/usr/bin/env bash
# 四槽压鞋机 HMI 启动脚本（桌面图标调用）
# 先检查/自动安装依赖（清华镜像 + 进度条），再进入 main.py
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

unset QT_PLUGIN_PATH || true

exec python3 "$ROOT/tools/ensure_deps_and_run.py" "$@"
