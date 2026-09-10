#!/usr/bin/env bash
# 把旧压鞋机 models 软链到本工程。只补缺失项，不覆盖已有文件（含 custom_*.pt）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OLD="${1:-/home/hlx8060/文档/program/压鞋机_旧/Casbot_Press_Shoes-main/models}"
NEW="$ROOT/models"

if [[ ! -d "$OLD" ]]; then
  echo "找不到旧模型目录: $OLD"
  echo "用法: $0 /path/to/Casbot_Press_Shoes-main/models"
  exit 1
fi

mkdir -p "$NEW/shoe_vision" "$NEW/toe_align" "$NEW/slot_check" "$NEW/position/rod" "$NEW/position/slot" "$NEW/legacy"

link_missing() {
  local src="$1"
  local dst="$2"
  if [[ "$dst" == *custom_* ]]; then
    echo "跳过自训: $dst"
    return
  fi
  if [[ -e "$dst" ]]; then
    echo "已存在，跳过: $dst"
    return
  fi
  if [[ ! -e "$src" ]]; then
    echo "源缺失: $src"
    return
  fi
  ln -sfn "$src" "$dst"
  echo "链接 $dst"
}

link_missing "$OLD/shoe_vision/7.23鞋obb.pt" "$NEW/shoe_vision/7.23鞋obb.pt"
link_missing "$OLD/shoe_vision/7.1鞋头朝上左右脚分类.pt" "$NEW/shoe_vision/7.1鞋头朝上左右脚分类.pt"
link_missing "$OLD/shoe_vision/7.24鞋楦obb.pt" "$NEW/shoe_vision/7.24鞋楦obb.pt"
link_missing "$OLD/toe_align/0722best.pt" "$NEW/toe_align/0722best.pt"
link_missing "$OLD/slot_check/7.10slot_check.pt" "$NEW/slot_check/7.10slot_check.pt"
link_missing "$OLD/position/rod/obb.pt" "$NEW/position/rod/obb.pt"
link_missing "$OLD/position/slot/slot_check.pt" "$NEW/position/slot/slot_check.pt"
if [[ ! -e "$NEW/legacy/Casbot_Press_Shoes_models" ]]; then
  ln -sfn "$OLD" "$NEW/legacy/Casbot_Press_Shoes_models"
  echo "链接 $NEW/legacy/Casbot_Press_Shoes_models"
fi
echo "已挂接旧模型到 $NEW（仅补缺失）"
ls -la "$NEW/shoe_vision" "$NEW/toe_align" "$NEW/slot_check" "$NEW/position/rod" || true
