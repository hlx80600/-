#!/usr/bin/env bash
# 把旧压鞋机 models 软链到本工程（可反复执行）
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
ln -sfn "$OLD/shoe_vision/7.23鞋obb.pt" "$NEW/shoe_vision/7.23鞋obb.pt"
ln -sfn "$OLD/shoe_vision/7.1鞋头朝上左右脚分类.pt" "$NEW/shoe_vision/7.1鞋头朝上左右脚分类.pt"
ln -sfn "$OLD/shoe_vision/7.24鞋楦obb.pt" "$NEW/shoe_vision/7.24鞋楦obb.pt"
ln -sfn "$OLD/toe_align/0722best.pt" "$NEW/toe_align/0722best.pt"
ln -sfn "$OLD/slot_check/7.10slot_check.pt" "$NEW/slot_check/7.10slot_check.pt"
ln -sfn "$OLD/position/rod/obb.pt" "$NEW/position/rod/obb.pt"
ln -sfn "$OLD/position/slot/slot_check.pt" "$NEW/position/slot/slot_check.pt"
ln -sfn "$OLD" "$NEW/legacy/Casbot_Press_Shoes_models"
echo "已挂接旧模型到 $NEW"
ls -la "$NEW/shoe_vision" "$NEW/toe_align" "$NEW/slot_check" "$NEW/position/rod"
