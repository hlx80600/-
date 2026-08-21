"""CLI 入口：``python -m contour_seg``。"""

import argparse
import sys
from pathlib import Path

import os

os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")

try:
    import torch
    from segment_anything import SamPredictor
except ImportError:
    print("缺少依赖，请先运行: pip install segment-anything torch torchvision")
    sys.exit(1)

from contour_seg.model import load_sam
from contour_seg.core import process_image, obb_to_xyxy
from contour_seg.interactive import click_quad_points_interactively


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m contour_seg",
        description="目标外轮廓提取 —— 基于 SAM box prompt / 自动分割",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式逐张手画框
  python -m contour_seg --input shoes/ --output output/ --draw-box

  # 单张图片
  python -m contour_seg --input shoes/img.jpg --output output/ --draw-box

  # 固定框坐标（可多次 --box 指定多个框）
  python -m contour_seg --input shoes/ --output output/ --box 100 200 800 900

  # 自动分割（不指定 --box 也不指定 --draw-box）
  python -m contour_seg --input shoes/ --output output/
""",
    )
    parser.add_argument("--input",      default="shoes",                  help="输入目录或单张图片")
    parser.add_argument("--output",     default="output",                 help="输出目录")
    parser.add_argument("--checkpoint", default="weights/sam_vit_b_01ec64.pth", help="SAM 权重路径")
    parser.add_argument("--model-type", default="vit_b",
                        choices=["vit_h", "vit_l", "vit_b"],              help="SAM 模型规格")
    parser.add_argument("--device",     default=None,                     help="运行设备 (cuda/cpu)")
    parser.add_argument("--box",        nargs=4, type=int, action="append",
                        metavar=("X0", "Y0", "X1", "Y1"),
                        help="固定框坐标，可多次指定")
    parser.add_argument("--draw-box",   action="store_true",              help="交互式拖拽矩形框模式")
    parser.add_argument("--click-quad", action="store_true",              help="交互式点击四点 OBB 框模式")
    parser.add_argument("--preview",    action="store_true",              help="分割后弹出预览")
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"使用设备: {device}")
    print(f"加载 SAM 模型 ({args.model_type}) ...")
    sam = load_sam(args.checkpoint, args.model_type, device)
    predictor = SamPredictor(sam)

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp")
    if input_path.is_file():
        image_files = [input_path]
    else:
        image_files = sorted(p for ext in exts for p in input_path.glob(ext))

    if not image_files:
        print(f"✗ 在 {input_path} 中未找到图片文件")
        sys.exit(1)

    print(f"找到 {len(image_files)} 张图片，开始处理...\n")
    fixed_boxes = args.box
    if fixed_boxes:
        print(f"使用固定框 ({len(fixed_boxes)} 个): {fixed_boxes}")

    success = 0
    for img_path in image_files:
        # --click-quad 模式：先弹窗让用户点 4 点，转为 xyxy 再传入
        if args.click_quad:
            import cv2
            image_bgr = cv2.imread(str(img_path))
            if image_bgr is None:
                print(f"\n[处理] {img_path.name}\n  ✗ 无法读取图片，跳过")
                continue
            quads = click_quad_points_interactively(image_bgr)
            if quads is None:
                print(f"  ✗ 跳过")
                continue
            # 四点框 → 外接水平框
            boxes_for_img = [obb_to_xyxy(q) for q in quads]
            print(f"  四点框 ({len(quads)} 个) → xyxy: {boxes_for_img}")
            result = process_image(
                img_path, sam, predictor, output_dir,
                boxes=boxes_for_img,
                interactive_box=False,
                preview=args.preview,
            )
        else:
            result = process_image(
                img_path, sam, predictor, output_dir,
                boxes=fixed_boxes,
                interactive_box=args.draw_box,
                preview=args.preview,
            )
        if result is not None:
            success += 1

    print(f"\n完成: {success}/{len(image_files)} 张成功，结果保存在 {output_dir}/")


if __name__ == "__main__":
    main()
