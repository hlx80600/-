"""核心分割与轮廓提取逻辑。"""

import json
from pathlib import Path

import cv2
import numpy as np

try:
    from segment_anything import SamPredictor, SamAutomaticMaskGenerator
except ImportError as e:
    raise ImportError(
        "请先安装 segment-anything: "
        "pip install git+https://github.com/facebookresearch/segment-anything.git"
    ) from e

from contour_seg.interactive import draw_boxes_interactively
from contour_seg.visualize import visualize, visualize_multi, show_preview


# ─────────────────────────── 辅助工具 ───────────────────────────

def _mask_border_touch_count(mask_u8: np.ndarray) -> int:
    """统计 mask 与图像四条边界相交的数量（0–4）。

    用于判断是否误选中了背景（背景通常会贴住多条边界）。
    """
    count = 0
    if mask_u8[0, :].any():   count += 1  # 上边
    if mask_u8[-1, :].any():  count += 1  # 下边
    if mask_u8[:, 0].any():   count += 1  # 左边
    if mask_u8[:, -1].any():  count += 1  # 右边
    return count


# ─────────────────────────── 分割 ───────────────────────────────

def obb_to_xyxy(points: list | np.ndarray) -> list[float]:
    """将 OBB 四点框转换为轴对齐外接矩形框。

    Parameters
    ----------
    points : list or ndarray
        长度为 8 的一维序列 ``[x0,y0, x1,y1, x2,y2, x3,y3]``，
        或形状 (4,2) / (8,) 的 ndarray。

    Returns
    -------
    list[float]
        ``[x_min, y_min, x_max, y_max]``
    """
    pts = np.array(points, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"四点框需要恰好 4 个点，实际收到 {pts.shape[0]} 个")
    return [float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max())]


def pick_mask_by_box(
    image_rgb: np.ndarray,
    predictor: SamPredictor,
    box: list | np.ndarray,
) -> np.ndarray | None:
    """使用矩形框提示 SAM，返回最高置信度的布尔型 mask。

    同时支持两种输入格式：

    * **xyxy 水平框**：长度为 4 的序列 ``[x0, y0, x1, y1]``
    * **xyxyxyxy 四点框**：长度为 8 的序列或形状 (4,2) 的数组
      ``[x0,y0, x1,y1, x2,y2, x3,y3]``，内部自动转为外接矩形框后传给 SAM

    Parameters
    ----------
    image_rgb : np.ndarray
        RGB 格式图像。
    predictor : SamPredictor
        已加载图像的 SAM predictor。
    box : list or ndarray
        水平框（4 值）或四点 OBB 框（8 值 / (4,2)）。

    Returns
    -------
    np.ndarray or None
        布尔型 mask，形状 H×W。
    """
    arr = np.array(box, dtype=np.float32).ravel()
    if arr.size == 8:
        arr = np.array(obb_to_xyxy(arr), dtype=np.float32)
    elif arr.size != 4:
        raise ValueError(f"box 应为长度 4（xyxy）或 8（xyxyxyxy），实际长度 {arr.size}")

    predictor.set_image(image_rgb)
    masks, scores, _ = predictor.predict(
        box=arr,
        multimask_output=True,
    )
    if masks is None or len(masks) == 0:
        return None
    return masks[int(np.argmax(scores))]


def pick_mask_auto(
    image_rgb: np.ndarray,
    sam,
) -> np.ndarray | None:
    """自动检测图像中的主体目标，返回最佳布尔型 mask。

    使用 ``SamAutomaticMaskGenerator`` 生成候选 mask，过滤掉面积过大
    或贴边过多（疑似背景）的候选，从剩余候选中取置信度最高的一个。

    Parameters
    ----------
    image_rgb : np.ndarray
        RGB 格式图像。
    sam : segment_anything.modeling.Sam
        已加载的 SAM 模型实例。

    Returns
    -------
    np.ndarray or None
        布尔型 mask，形状 H×W；若无法检测则返回 ``None``。
    """
    mask_gen = SamAutomaticMaskGenerator(
        sam,
        points_per_side=32,
        pred_iou_thresh=0.88,
        stability_score_thresh=0.92,
        min_mask_region_area=5000,
    )
    masks = mask_gen.generate(image_rgb)
    if not masks:
        return None

    h, w = image_rgb.shape[:2]
    total_px = h * w

    candidates = []
    for m in masks:
        seg = m["segmentation"].astype(np.uint8)
        area_ratio = seg.sum() / total_px
        border_touches = _mask_border_touch_count(seg)
        # 过滤背景：面积过大 或 贴住 ≥3 条边
        if area_ratio > 0.75 or border_touches >= 3:
            continue
        candidates.append((m["predicted_iou"], m["segmentation"]))

    if not candidates:
        # 降级：返回面积最大的 mask
        best = max(masks, key=lambda m: m["area"])
        return best["segmentation"]

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].astype(bool)


# ─────────────────────────── 轮廓提取 ───────────────────────────

def extract_outer_contour(
    mask_bool: np.ndarray,
    smooth_eps: float = 0.0004,
) -> np.ndarray | None:
    """从布尔 mask 中提取最大外轮廓。

    先对 mask 做形态学闭运算（消除空洞）和开运算（去除噪声），
    再用多边形近似平滑轮廓。

    Parameters
    ----------
    mask_bool : np.ndarray
        布尔型 mask，形状 H×W。
    smooth_eps : float
        多边形近似精度系数（越小保留细节越多）。

    Returns
    -------
    np.ndarray or None
        形状 (N, 2) 的 int32 轮廓坐标数组；失败返回 ``None``。
    """
    mask_u8 = mask_bool.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN,  kernel, iterations=1)
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    epsilon = smooth_eps * cv2.arcLength(largest, True)
    return cv2.approxPolyDP(largest, epsilon, True).reshape(-1, 2).astype(np.int32)


# ─────────────────────────── 主流程 ─────────────────────────────

def process_image(
    img_path: Path,
    sam,
    predictor: SamPredictor,
    output_dir: Path,
    boxes: list[list[int]] | None = None,
    interactive_box: bool = False,
    preview: bool = False,
) -> list[np.ndarray] | None:
    """对单张图片执行完整分割流程并保存结果。

    优先级：``interactive_box`` > ``boxes`` > 自动分割（``pick_mask_auto``）。

    Parameters
    ----------
    img_path : Path
        输入图片路径。
    sam : segment_anything.modeling.Sam
        SAM 模型实例（自动分割时使用）。
    predictor : SamPredictor
        SAM predictor（box prompt 时使用）。
    output_dir : Path
        结果输出目录（必须已存在）。
    boxes : list of [x0, y0, x1, y1], optional
        固定框列表；为 ``None`` 时使用自动分割。
    interactive_box : bool
        若为 ``True``，优先弹出交互窗口让用户手绘框。
    preview : bool
        分割完成后是否弹出预览窗口。

    Returns
    -------
    list[np.ndarray] or None
        成功时返回各框的轮廓数组列表；跳过/失败返回 ``None``。
    """
    print(f"\n[处理] {img_path.name}")
    image_bgr = cv2.imread(str(img_path))
    if image_bgr is None:
        print("  ✗ 无法读取图片，跳过")
        return None
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    # ── 确定框列表 ────────────────────────────────────
    if interactive_box:
        boxes = draw_boxes_interactively(image_bgr)
        if boxes is None:
            print("  ✗ 跳过")
            return None
        print(f"  框坐标 ({len(boxes)} 个): {boxes}")

    stem = img_path.stem
    output_dir = Path(output_dir)
    results: list[np.ndarray] = []

    # ── 有框：box prompt 分割 ─────────────────────────
    if boxes:
        collected: list[tuple[np.ndarray, np.ndarray, list[int], int]] = []
        for index, box_xyxy in enumerate(boxes, start=1):
            mask = pick_mask_by_box(image_rgb, predictor, box_xyxy)
            if mask is None or not mask.any():
                print(f"  ✗ 框 {index} 分割失败，跳过")
                continue
            contour = extract_outer_contour(mask)
            if contour is None:
                print(f"  ✗ 框 {index} 轮廓提取失败，跳过")
                continue
            print(f"  ✓ 框 {index}: 轮廓点数 {len(contour)}，Mask 面积 {mask.sum()} px")
            collected.append((mask, contour, list(map(int, box_xyxy)), index))
            results.append(contour)
            # 每框单独保存 JSON
            payload: dict = {
                "image": img_path.name,
                "box_index": index,
                "box_xyxy": list(map(int, box_xyxy)),
                "contour_points": contour.tolist(),
                "point_count": len(contour),
            }
            with open(output_dir / f"{stem}_box{index}_contour.json", "w") as f:
                json.dump(payload, f, indent=2)
            print(f"  \u2192 框 {index} JSON 已保存")

        if not collected:
            return None

        # 所有框合并到一张图
        items  = [(m, c) for m, c, _, _ in collected]
        labels = [f"Box {idx}" for _, _, _, idx in collected]
        combined_path = output_dir / f"{stem}_result.jpg"
        visualize_multi(image_bgr, items, combined_path, labels)
        if preview:
            show_preview(image_bgr, items=items)
        return results if results else None

    # ── 无框：自动分割 ────────────────────────────────
    mask = pick_mask_auto(image_rgb, sam)
    if mask is None or not mask.any():
        print("  ✗ 自动分割失败，跳过")
        return None
    contour = extract_outer_contour(mask)
    if contour is None:
        print("  ✗ 轮廓提取失败，跳过")
        return None
    print(f"  ✓ 自动分割: 轮廓点数 {len(contour)}，Mask 面积 {mask.sum()} px")
    _save_result(image_bgr, mask, contour, output_dir, stem, img_path, 0, None, preview)
    return [contour]


def _save_result(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    contour: np.ndarray,
    output_dir: Path,
    tag: str,
    img_path: Path,
    box_index: int,
    box_xyxy: list[int] | None,
    preview: bool,
) -> None:
    """保存可视化图和 JSON 轮廓文件（单目标，内部辅助函数）。"""
    visualize(image_bgr, mask, contour, output_dir / f"{tag}_result.jpg")
    if preview:
        show_preview(image_bgr, mask_bool=mask, contour=contour)
    payload: dict = {
        "image": img_path.name,
        "contour_points": contour.tolist(),
        "point_count": len(contour),
    }
    if box_xyxy is not None:
        payload["box_index"] = box_index
        payload["box_xyxy"] = list(map(int, box_xyxy))
    with open(output_dir / f"{tag}_contour.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(f"  → 轮廓坐标已保存: {output_dir / f'{tag}_contour.json'}")
