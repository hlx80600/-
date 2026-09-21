#!/usr/bin/env bash
# 拉取双槽同源视觉算法仓库（与 Casbot_Press_Shoes/init.sh 同一对私有库）。
# 需要：git、GitHub SSH、对 RobotSkillsDevelopmentTeam 有读权限。
# 默认浅克隆；若要完整历史：CLONE_FULL=1 bash init.sh
# 体检：python3 tools/check_vision_algo_deps.py（未装 torch 时只确认源码在盘上）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

clone_if_missing() {
  local url="$1"
  local dest="$2"
  local depth_args=()
  if [[ "${CLONE_FULL:-}" != "1" ]]; then
    depth_args=(--depth 1)
  fi
  if [[ -d "$dest/.git" ]]; then
    echo "已存在，跳过: $dest"
    return 0
  fi
  if [[ -e "$dest" ]]; then
    echo "目录存在但不是 git 仓库: $dest" >&2
    return 1
  fi
  git clone "${depth_args[@]}" "$url" "$dest"
}

clone_if_missing \
  git@github.com:RobotSkillsDevelopmentTeam/casbot_yolo_point4d.git \
  "$ROOT/casbot_yolo_point4d"

clone_if_missing \
  git@github.com:RobotSkillsDevelopmentTeam/casbot_yolo_obb360.git \
  "$ROOT/casbot_yolo_point4d/casbot_yolo_obb360"

echo
echo "算法源码："
echo "  casbot_yolo_point4d     -> $ROOT/casbot_yolo_point4d"
echo "  casbot_yolo_obb360      -> $ROOT/casbot_yolo_point4d/casbot_yolo_obb360"
echo "  DiscreteMultiActionHead -> $ROOT/shoe_align/ImgAct"
echo
echo "体检：python3 tools/check_vision_algo_deps.py"
