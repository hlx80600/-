#!/usr/bin/env bash
# 生成/刷新桌面快捷方式：项目内一份 + 复制到「桌面」
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="四槽压鞋机"
DESKTOP_FILE_NAME="${NAME}.desktop"
ICON_SRC="$ROOT/hmi/assets/rsdt_badge.png"
LAUNCHER="$ROOT/启动四槽压鞋机.sh"
PROJECT_DESKTOP="$ROOT/$DESKTOP_FILE_NAME"

if [[ ! -f "$LAUNCHER" ]]; then
  echo "找不到启动脚本: $LAUNCHER" >&2
  exit 1
fi
if [[ ! -f "$ICON_SRC" ]]; then
  echo "找不到图标: $ICON_SRC" >&2
  exit 1
fi

chmod +x "$LAUNCHER"

USER_DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -z "${USER_DESKTOP}" || ! -d "${USER_DESKTOP}" ]]; then
  if [[ -d "$HOME/桌面" ]]; then
    USER_DESKTOP="$HOME/桌面"
  else
    USER_DESKTOP="$HOME/Desktop"
  fi
fi
mkdir -p "$USER_DESKTOP"

write_desktop() {
  local out="$1"
  cat >"$out" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=${NAME}
Name[zh_CN]=${NAME}
GenericName=四槽压鞋机控制程序
Comment=莆田鞋厂四槽机器控制程序（Casbot_FourSlot_Press_Shoes）
Exec="${LAUNCHER}"
Icon=${ICON_SRC}
Path=${ROOT}
Terminal=false
StartupNotify=true
Categories=Utility;Industrial;
Keywords=RSDT;压鞋;四槽;机器人;
EOF
  chmod +x "$out"
}

write_desktop "$PROJECT_DESKTOP"
write_desktop "$USER_DESKTOP/$DESKTOP_FILE_NAME"

# GNOME/Ubuntu：标记为可信，才能双击直接运行
if command -v gio >/dev/null 2>&1; then
  gio set "$USER_DESKTOP/$DESKTOP_FILE_NAME" metadata::trusted true 2>/dev/null || true
  gio set "$PROJECT_DESKTOP" metadata::trusted true 2>/dev/null || true
fi

echo "已生成："
echo "  项目内: $PROJECT_DESKTOP"
echo "  桌面:   $USER_DESKTOP/$DESKTOP_FILE_NAME"
echo "若桌面图标仍提示「未信任」，请右键 → 允许启动。"
