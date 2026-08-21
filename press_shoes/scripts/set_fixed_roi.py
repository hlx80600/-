#!/usr/bin/env python3
"""在图片上用鼠标选择固定尺寸 ROI，并写入左右槽配置 YAML。

用法:
    python press_shoes/scripts/set_fixed_roi.py path/to/image.jpg
    python press_shoes/scripts/set_fixed_roi.py path/to/image.jpg --width 1000 --height 500
    python press_shoes/scripts/set_fixed_roi.py path/to/image.jpg --slot-config press_shoes/config/left_slot.yaml

操作:
    - 移动鼠标: 黄色框预览 ROI
    - 左键点击: 确认 ROI 并打印坐标（绿色框）
    - s: 进入写入确认（图像上显示 ROI 结果）
    - 空格: 在写入确认界面确认并写入 YAML
    - Esc: 取消写入确认
    - q: 退出
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shoe_seg.slot_config import SlotConfig

DEFAULT_LEFT_SLOT_CONFIG = REPO_ROOT / "press_shoes" / "config" / "left_slot.yaml"
DEFAULT_RIGHT_SLOT_CONFIG = REPO_ROOT / "press_shoes" / "config" / "right_slot.yaml"
CHINESE_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/simsun.ttc",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在图片上用鼠标选择固定尺寸 ROI 并写入槽配置")
    parser.add_argument("image", help="图片路径")
    parser.add_argument("--width", type=int, default=250, help="ROI 宽度（像素，默认 200）")
    parser.add_argument("--height", type=int, default=250, help="ROI 高度（像素，默认 200）")
    parser.add_argument(
        "--slot-config",
        dest="slot_config",
        type=str,
        default=None,
        help="槽配置路径（默认启动后交互选择 left_slot.yaml / right_slot.yaml）",
    )
    return parser.parse_args()


def choose_slot_config_path(cli_path: str | None) -> Path:
    if cli_path:
        slot_path = Path(cli_path)
        if not slot_path.is_absolute():
            slot_path = REPO_ROOT / slot_path
        print(f"使用指定槽配置: {slot_path}")
        return slot_path

    options = {
        "1": ("left_slot", DEFAULT_LEFT_SLOT_CONFIG),
        "2": ("right_slot", DEFAULT_RIGHT_SLOT_CONFIG),
    }
    print("请选择要更新的槽配置:")
    print(f"  1) left_slot  -> {DEFAULT_LEFT_SLOT_CONFIG}")
    print(f"  2) right_slot -> {DEFAULT_RIGHT_SLOT_CONFIG}")

    while True:
        choice = input("请输入 1 或 2: ").strip()
        selected = options.get(choice)
        if selected is not None:
            slot_name, slot_path = selected
            print(f"已选择 {slot_name}: {slot_path}")
            return slot_path
        print("输入无效，请重新输入 1 或 2。")


def clamp_roi_start(x: int, y: int, roi_w: int, roi_h: int, img_w: int, img_h: int) -> tuple[int, int]:
    x = max(0, min(x, img_w - roi_w))
    y = max(0, min(y, img_h - roi_h))
    return x, y


def roi_corners(x: int, y: int, roi_w: int, roi_h: int) -> tuple[int, int, int, int]:
    return x, y, x + roi_w, y + roi_h


def roi_info_lines(x: int, y: int, roi_w: int, roi_h: int) -> list[str]:
    x1, y1, x2, y2 = roi_corners(x, y, roi_w, roi_h)
    return [
        f"ROI_START = ({x}, {y})",
        f"ROI_SIZE = ({roi_w}, {roi_h})",
        f"ROI = x1={x1}, y1={y1}, x2={x2}, y2={y2}",
        f"slot_roi = [[{x1}, {y1}], [{x2}, {y2}]]",
    ]


def print_roi(x: int, y: int, roi_w: int, roi_h: int) -> None:
    for line in roi_info_lines(x, y, roi_w, roi_h):
        print(line)


@lru_cache(maxsize=8)
def get_overlay_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_path in CHINESE_FONT_CANDIDATES:
        path = Path(font_path)
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    print("警告: 未找到中文字体，图像文字可能显示异常。", file=sys.stderr)
    return ImageFont.load_default()


def bgr_to_rgb(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return color[2], color[1], color[0]


def draw_text_lines(
    image,
    lines: list[str],
    origin: tuple[int, int] = (10, 30),
    line_height: int = 28,
    font_size: int = 20,
    color: tuple[int, int, int] = (255, 255, 255),
    bg_color: tuple[int, int, int] = (0, 0, 0),
) -> None:
    pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_image)
    font = get_overlay_font(font_size)
    rgb_color = bgr_to_rgb(color)
    rgb_bg = bgr_to_rgb(bg_color)

    x0, y0 = origin
    for index, line in enumerate(lines):
        y = y0 + index * line_height
        bbox = draw.textbbox((x0, y), line, font=font)
        draw.rectangle(
            (bbox[0] - 4, bbox[1] - 4, bbox[2] + 4, bbox[3] + 4),
            fill=rgb_bg,
        )
        draw.text((x0, y), line, font=font, fill=rgb_color)

    image[:] = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def draw_confirmed_roi(
    display,
    x: int,
    y: int,
    roi_w: int,
    roi_h: int,
    color: tuple[int, int, int] = (0, 255, 0),
) -> None:
    x1, y1, x2, y2 = roi_corners(x, y, roi_w, roi_h)
    cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
    cv2.circle(display, (x1, y1), 5, color, -1)
    cv2.putText(
        display,
        f"({x1},{y1})",
        (x1 + 6, max(y1 - 8, 16)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        display,
        f"({x2},{y2})",
        (max(x2 - 120, 6), min(y2 + 22, display.shape[0] - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )


def load_existing_slot_roi(slot_config_path: Path) -> tuple[int, int, int, int] | None:
    if not slot_config_path.exists():
        return None

    with slot_config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    slot_roi = data.get("slot_roi")
    if not isinstance(slot_roi, list) or len(slot_roi) < 2:
        return None

    try:
        x1, y1 = int(slot_roi[0][0]), int(slot_roi[0][1])
        x2, y2 = int(slot_roi[1][0]), int(slot_roi[1][1])
    except (IndexError, TypeError, ValueError):
        return None

    return x1, y1, x2, y2


def save_slot_roi(
    slot_config_path: Path,
    x: int,
    y: int,
    roi_w: int,
    roi_h: int,
) -> None:
    if not slot_config_path.exists():
        raise FileNotFoundError(f"槽配置不存在: {slot_config_path}")

    x1, y1, x2, y2 = roi_corners(x, y, roi_w, roi_h)
    new_slot_roi = [[x1, y1], [x2, y2]]

    slot_config = SlotConfig.load_yaml(slot_config_path)
    old_slot_roi = slot_config.extra_fields.get("slot_roi")

    print(f"\n写入 {slot_config_path}:")
    print(f"  slot_roi: {old_slot_roi} -> {new_slot_roi}")

    slot_config.extra_fields["slot_roi"] = new_slot_roi
    slot_config.save_yaml(slot_config_path)
    print(f"[OK] 已更新: {slot_config_path}")


def main() -> None:
    args = parse_args()
    slot_config_path = choose_slot_config_path(args.slot_config)

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"图片不存在: {image_path}", file=sys.stderr)
        sys.exit(1)

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        print(f"无法读取图片: {image_path}", file=sys.stderr)
        sys.exit(1)

    img_h, img_w = image.shape[:2]
    roi_w, roi_h = args.width, args.height

    if roi_w <= 0 or roi_h <= 0:
        print("ROI 宽高必须为正整数", file=sys.stderr)
        sys.exit(1)
    if roi_w > img_w or roi_h > img_h:
        print(
            f"ROI 尺寸 ({roi_w}x{roi_h}) 大于图片尺寸 ({img_w}x{img_h})",
            file=sys.stderr,
        )
        sys.exit(1)

    existing_roi = load_existing_slot_roi(slot_config_path)
    state = {"mouse_xy": (0, 0), "confirmed": None, "write_confirm": False}

    window_name = "set_fixed_roi"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    def on_mouse(event: int, x: int, y: int, flags: int, userdata) -> None:
        state["mouse_xy"] = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            roi_x, roi_y = clamp_roi_start(x, y, roi_w, roi_h, img_w, img_h)
            state["confirmed"] = (roi_x, roi_y)
            print_roi(roi_x, roi_y, roi_w, roi_h)

    cv2.setMouseCallback(window_name, on_mouse)

    print(f"图片: {image_path} ({img_w}x{img_h})")
    print(f"固定 ROI 尺寸: {roi_w}x{roi_h}")
    print(f"目标槽配置: {slot_config_path}")
    if existing_roi is not None:
        x1, y1, x2, y2 = existing_roi
        print(f"当前 slot_roi: [[{x1}, {y1}], [{x2}, {y2}]]（蓝色框）")
    print("左键点击设置 ROI；s 进入写入确认；确认界面按空格写入 YAML；Esc 取消；q 退出")

    while True:
        display = image.copy()
        confirmed = state["confirmed"]
        write_confirm = state["write_confirm"]

        if existing_roi is not None:
            ex1, ey1, ex2, ey2 = existing_roi
            cv2.rectangle(display, (ex1, ey1), (ex2, ey2), (255, 0, 0), 2)
            cv2.putText(
                display,
                "current slot_roi",
                (ex1 + 6, max(ey1 - 8, 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 0, 0),
                2,
                cv2.LINE_AA,
            )

        if not write_confirm:
            mx, my = state["mouse_xy"]
            preview_x, preview_y = clamp_roi_start(mx, my, roi_w, roi_h, img_w, img_h)
            cv2.rectangle(
                display,
                (preview_x, preview_y),
                (preview_x + roi_w, preview_y + roi_h),
                (0, 255, 255),
                2,
            )

        if confirmed is not None:
            cx, cy = confirmed
            draw_confirmed_roi(display, cx, cy, roi_w, roi_h)

        if write_confirm and confirmed is not None:
            cx, cy = confirmed
            x1, y1, x2, y2 = roi_corners(cx, cy, roi_w, roi_h)
            new_slot_roi = f"[[{x1}, {y1}], [{x2}, {y2}]]"
            old_slot_roi_text = str(existing_roi) if existing_roi is not None else "None"
            overlay_lines = [
                "写入 YAML 确认",
                f"目标配置: {slot_config_path.name}",
                *roi_info_lines(cx, cy, roi_w, roi_h),
                f"旧 slot_roi: {old_slot_roi_text}",
                f"新 slot_roi: {new_slot_roi}",
                "按空格确认写入，Esc 取消",
            ]
            draw_text_lines(display, overlay_lines, origin=(10, 30))

        cv2.imshow(window_name, display)
        key = cv2.waitKey(20) & 0xFF
        if key == ord("q"):
            break
        if key == 27:
            if write_confirm:
                state["write_confirm"] = False
                print("已取消写入。")
            continue
        if key == ord(" "):
            if not write_confirm:
                continue
            if confirmed is None:
                print("请先左键点击确认 ROI。")
                state["write_confirm"] = False
                continue
            save_slot_roi(slot_config_path, confirmed[0], confirmed[1], roi_w, roi_h)
            existing_roi = roi_corners(confirmed[0], confirmed[1], roi_w, roi_h)
            state["write_confirm"] = False
            continue
        if key == ord("s"):
            if confirmed is None:
                print("请先左键点击确认 ROI，再按 s 进入写入确认。")
                continue
            state["write_confirm"] = True
            print("已进入写入确认界面：图像上查看 ROI 结果，按空格确认写入。")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
