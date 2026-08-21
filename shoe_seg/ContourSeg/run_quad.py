"""单张图片四点 OBB 框分割脚本。"""

import cv2
import json
from pathlib import Path
from segment_anything import SamPredictor
from contour_seg import load_sam, pick_mask_by_box, extract_outer_contour
from contour_seg.visualize import visualize

# ── 配置 ──────────────────────────────────────────────────────────
IMAGE_PATH  = Path("shoes/20260518170738_rgb.png")
OUTPUT_DIR  = Path("output")
CHECKPOINT  = "weights/sam_vit_b_01ec64.pth"
MODEL_TYPE  = "vit_b"

# 多个四点框，每个框为 4 个 (x, y) 点，顺序任意
ALL_QUADS = [
    [
        (543.6022338867188,  642.6063232421875),
        (475.8197021484375,  396.152099609375),
        (372.53546142578125, 424.5584716796875),
        (440.3179931640625,  671.0126953125),
    ],
    [
        (602.8197021484375,  575.6962280273438),
        (634.744384765625,   372.9144592285156),
        (549.7470703125,     359.5329895019531),
        (517.8223876953125,  562.3147583007812),
    ],
]
# ─────────────────────────────────────────────────────────────────

import torch
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"设备: {device}")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print("加载 SAM 模型...")
sam = load_sam(CHECKPOINT, MODEL_TYPE, device)
predictor = SamPredictor(sam)

image_bgr = cv2.imread(str(IMAGE_PATH))
if image_bgr is None:
    raise FileNotFoundError(f"无法读取图片: {IMAGE_PATH}")
image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
print(f"图片尺寸: {image_bgr.shape[1]}×{image_bgr.shape[0]}")

stem = IMAGE_PATH.stem
for idx, quad_points in enumerate(ALL_QUADS, start=1):
    quad_flat = [coord for pt in quad_points for coord in pt]
    print(f"\n框 {idx} 四点坐标（8 值）: {quad_flat}")

    mask = pick_mask_by_box(image_rgb, predictor, quad_flat)
    if mask is None or not mask.any():
        print(f"  ✗ 框 {idx} 分割失败，mask 为空")
        continue
    contour = extract_outer_contour(mask)
    if contour is None:
        print(f"  ✗ 框 {idx} 轮廓提取失败")
        continue

    vis_path  = OUTPUT_DIR / f"{stem}_box{idx}_result.jpg"
    json_path = OUTPUT_DIR / f"{stem}_box{idx}_contour.json"

    visualize(image_bgr, mask, contour, vis_path)
    payload = {
        "image": IMAGE_PATH.name,
        "box_index": idx,
        "quad_points": [list(pt) for pt in quad_points],
        "contour_points": contour.tolist(),
        "point_count": len(contour),
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"  ✓ 轮廓点数: {len(contour)}，Mask 面积: {mask.sum()} px")
    print(f"  可视化 → {vis_path}")
    print(f"  JSON   → {json_path}")
