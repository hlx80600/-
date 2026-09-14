#!/usr/bin/env bash
# 启用/关闭「登录后自动启动四槽压鞋机」
# 用法：
#   bash tools/install_autostart.sh          # 启用
#   bash tools/install_autostart.sh enable
#   bash tools/install_autostart.sh disable
#   bash tools/install_autostart.sh status
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python3 -m core.desktop_autostart "${1:-enable}"
