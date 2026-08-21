"""分类采图：左右脚需要「鞋头朝上」的抠图，与推理时 crop_toe_up 对齐。"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from vision.numpy_compat import np

try:
    import numpy as _np  # type: ignore

    np = _np
except ImportError:
    pass


def grab_slot_image(ctx, cam_id: str):
    cam = getattr(ctx, "cameras", {}).get(cam_id) if ctx is not None else None
    img = None
    if hasattr(ctx, "vision"):
        try:
            img = ctx.vision.grab_raw(cam_id, wait_s=0.6)
        except Exception:
            img = None
    if img is None and cam is not None and hasattr(cam, "grab"):
        img = cam.grab(wait_s=0.6)
    if img is None and cam is not None:
        img = getattr(cam, "last_color", None)
    if img is None:
        raise RuntimeError(f"{cam_id} 当前无图（请先出图：取消 Mock 或打开相机监控）")
    return img


def prepare_shoe_lr(ctx, img_bgr) -> Tuple[Optional[Any], str]:
    """尽量抠出皮带上的鞋。成功返回抠图；失败返回 (None, 原因)。"""
    if img_bgr is None:
        return None, "无图"
    cropped, why = _crop_with_shoe_vision(ctx, img_bgr)
    if cropped is not None:
        return cropped, why
    return None, why or "未检出鞋子"


def _crop_with_shoe_vision(ctx, img_bgr) -> Tuple[Optional[Any], str]:
    try:
        from vision.legacy_pipeline import get_shoe_vision, last_shoe_vision_error
    except Exception as e:
        return None, f"视觉栈不可用: {e}"

    vis = {}
    if ctx is not None and hasattr(ctx, "cfg"):
        vis = ctx.cfg.get("vision") or {}
    cameras = getattr(ctx, "cameras", None)
    sv = get_shoe_vision(cameras, vis)
    if sv is None:
        err = ""
        try:
            err = last_shoe_vision_error() or ""
        except Exception:
            pass
        return None, f"鞋OBB未就绪，请先挂接皮带-鞋OBB。{err}".strip()

    yolo4d = getattr(sv, "_yolo4d", None)
    if yolo4d is None:
        return None, "鞋OBB检测器未加载"

    depth = None
    cam = (cameras or {}).get("cam1") if cameras else None
    if cam is not None:
        depth = getattr(cam, "last_depth", None)
    try:
        shoe_shift = [[0.0, 0.0] for _ in range(64)]
        results, _ = yolo4d.detect(
            img_bgr,
            depth,
            float(sv.fx),
            float(sv.fy),
            float(sv.cx),
            float(sv.cy),
            shoe_shift,
            draw_result=False,
        )
    except Exception as e:
        return None, f"鞋OBB检测失败: {e}"

    best = None
    best_s = -1.0
    for rec in results or []:
        if rec is None or len(rec) < 9:
            continue
        try:
            score = float(rec[2])
        except Exception:
            score = 0.0
        if score > best_s:
            best_s = score
            best = rec
    if best is None:
        return None, "画面里没检出鞋子，可在预览里框出鞋子"

    try:
        pts = np.asarray(best[8], dtype=np.float32).reshape(-1, 2)
        from shoe_vision_seg import _obb360_style_crop_square

        crop, _, _, _ = _obb360_style_crop_square(
            img_bgr,
            pts,
            target_size=int(getattr(sv, "pad_size", 256) or 256),
            expand_ratio=float(getattr(sv, "crop_expand", 0.0) or 0.0),
            clockwise=False,
            pad_mode=str(getattr(sv, "pad_mode", "constant") or "constant"),
            pad_value=int(getattr(sv, "pad_value", 0) or 0),
        )
    except Exception as e:
        return None, f"抠图失败: {e}"
    if crop is None:
        return None, "抠图失败"
    return crop, f"已按鞋OBB自动抠图（conf={best_s:.2f}）。请把鞋头转到朝上。"
