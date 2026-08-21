"""形状模板匹配（海康 VM 风格）：搜索 ROI ≠ 模型。

绿框只是搜索范围；模型是鞋的掩膜轮廓，背景不参与打分。
匹配：带掩膜的归一化相关 + 旋转搜索 + 边缘（Chamfer）复核。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vision.numpy_compat import np

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

from vision.template_match import TEMPLATE_DIR, MatchResult, template_path


def model_json_path(name: str) -> Path:
    return TEMPLATE_DIR / f"{name}.json"


def model_mask_path(name: str) -> Path:
    return TEMPLATE_DIR / f"{name}_mask.png"


def shape_match_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    vis = cfg if isinstance(cfg, dict) else {}
    if "shape_match" not in vis and isinstance(vis.get("vision"), dict):
        vis = vis["vision"]
    blk = vis.get("shape_match") if isinstance(vis.get("shape_match"), dict) else {}
    return dict(blk)


def _to_gray(img):
    if img is None:
        return None
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def auto_shoe_mask(bgr) -> Tuple[Any, Optional[Any], str]:
    """
    在矩形补丁内自动抠鞋：取面积合适的前景轮廓作掩膜。
    避免把整块 ROI 矩形当成模型。
    """
    if cv2 is None or bgr is None:
        return None, None, "无图"
    gray = _to_gray(bgr)
    h, w = gray.shape[:2]
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, t1 = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    t2 = cv2.bitwise_not(t1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    best_c = None
    best_a = 0.0
    roi_a = float(h * w)
    for m in (t1, t2):
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel, iterations=1)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = float(cv2.contourArea(c))
            if a < 600 or a > 0.88 * roi_a:
                continue
            x, y, cw, ch = cv2.boundingRect(c)
            if cw >= w - 4 and ch >= h - 4:
                continue
            if a > best_a:
                best_a = a
                best_c = c
    mask = np.zeros((h, w), dtype=np.uint8)
    if best_c is None:
        return mask, None, "ROI里抠不出独立鞋轮廓，请改用多边形勾鞋，不要用整块矩形"
    cv2.drawContours(mask, [best_c], -1, 255, thickness=-1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    frac = float(np.count_nonzero(mask)) / max(roi_a, 1.0)
    return mask, best_c, f"自动抠鞋 面积占ROI {frac*100:.0f}%"


def mask_from_polygon(h: int, w: int, pts: Sequence[Tuple[float, float]]) -> Any:
    mask = np.zeros((h, w), dtype=np.uint8)
    if cv2 is None or len(pts) < 3:
        return mask
    arr = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask, [arr], 255)
    return mask


def _tight_crop(bgr, mask, pad: int = 4):
    ys, xs = np.where(mask > 0)
    if len(xs) < 8:
        return bgr, mask, (0, 0)
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(bgr.shape[1], int(xs.max()) + 1 + pad)
    y1 = min(bgr.shape[0], int(ys.max()) + 1 + pad)
    return bgr[y0:y1, x0:x1].copy(), mask[y0:y1, x0:x1].copy(), (x0, y0)


def save_shape_model(
    name: str,
    patch_bgr,
    mask,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    if cv2 is None:
        raise RuntimeError("需要 OpenCV")
    patch, mask, _ = _tight_crop(patch_bgr, mask)
    if int(np.count_nonzero(mask)) < 80:
        raise RuntimeError("掩膜太小，模型无效")
    png = template_path(name)
    cv2.imwrite(str(png), patch)
    cv2.imwrite(str(model_mask_path(name)), mask)
    h, w = patch.shape[:2]
    M = cv2.moments(mask, binaryImage=True)
    if M["m00"] > 1:
        cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
    else:
        cx, cy = w / 2.0, h / 2.0
    meta = {
        "name": name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "size": [int(w), int(h)],
        "center": [float(cx), float(cy)],
        "mask_pixels": int(np.count_nonzero(mask)),
        "method": "shape_ncc",
    }
    if extra:
        meta.update(extra)
    model_json_path(name).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return png


def load_model_meta(name: str) -> Dict[str, Any]:
    path = model_json_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_template_and_mask(name: str):
    if cv2 is None:
        return None, None, {}
    png = template_path(name)
    if not png.exists():
        return None, None, {}
    tmpl = cv2.imread(str(png), cv2.IMREAD_COLOR)
    if tmpl is None:
        return None, None, {}
    meta = load_model_meta(name)
    mp = model_mask_path(name)
    mask = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE) if mp.exists() else None
    if mask is None or mask.shape[:2] != tmpl.shape[:2]:
        mask, _, _ = auto_shoe_mask(tmpl)
        if mask is None or int(np.count_nonzero(mask)) < 80:
            mask = np.full(tmpl.shape[:2], 255, dtype=np.uint8)
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.erode(mask, k, iterations=2)
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    return tmpl, mask, meta


def delete_shape_model(name: str) -> bool:
    gone = False
    for p in (template_path(name), model_mask_path(name), model_json_path(name)):
        if p.exists():
            p.unlink()
            gone = True
    return gone


def _rotate(img, mask, angle_deg: float):
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    M = cv2.getRotationMatrix2D((cx, cy), float(angle_deg), 1.0)
    cos, sin = abs(float(M[0, 0])), abs(float(M[0, 1]))
    nw = int(h * sin + w * cos) + 2
    nh = int(h * cos + w * sin) + 2
    M[0, 2] += nw / 2.0 - cx
    M[1, 2] += nh / 2.0 - cy
    rot = cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    msk = cv2.warpAffine(mask, M, (nw, nh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
    cxy = np.array([cx, cy, 1.0], dtype=np.float64)
    rc = M @ cxy
    return rot, msk, (float(rc[0]), float(rc[1]))


def _ncc_masked(scene_gray, tmpl_gray, mask) -> Tuple[float, Tuple[int, int]]:
    th, tw = tmpl_gray.shape[:2]
    sh, sw = scene_gray.shape[:2]
    if th < 4 or tw < 4 or sh < th or sw < tw:
        return -1.0, (0, 0)
    m = mask
    if m is None or int(np.count_nonzero(m)) < 20:
        res = cv2.matchTemplate(scene_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    else:
        try:
            res = cv2.matchTemplate(
                scene_gray, tmpl_gray, cv2.TM_CCORR_NORMED, mask=m
            )
        except Exception:
            res = cv2.matchTemplate(scene_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return float(max_val), (int(max_loc[0]), int(max_loc[1]))


def _edge_score(scene_gray, edge_xy: np.ndarray) -> float:
    if edge_xy is None or len(edge_xy) < 8:
        return 0.0
    edges = cv2.Canny(scene_gray, 40, 120)
    inv = cv2.bitwise_not(edges)
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, 3)
    h, w = dist.shape[:2]
    xs = np.clip(np.round(edge_xy[:, 0]).astype(int), 0, w - 1)
    ys = np.clip(np.round(edge_xy[:, 1]).astype(int), 0, h - 1)
    mean_d = float(np.mean(dist[ys, xs]))
    return float(max(0.0, 1.0 - mean_d / 10.0))


def _mask_edge_points(mask, max_pts: int = 180) -> np.ndarray:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return np.zeros((0, 2), dtype=np.float32)
    c = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    if len(c) > max_pts:
        idx = np.linspace(0, len(c) - 1, max_pts).astype(int)
        c = c[idx]
    return c


def _angles(amin: float, amax: float, step: float) -> List[float]:
    if step <= 0.2:
        step = 1.0
    if amax < amin:
        amin, amax = amax, amin
    n = int(round((amax - amin) / step)) + 1
    return [float(amin + i * step) for i in range(max(n, 1))]


@dataclass
class PoseHit:
    score: float
    ncc: float
    edge: float
    x: float
    y: float
    angle_deg: float
    box: Tuple[int, int, int, int] = (0, 0, 0, 0)
    contour: List[Tuple[int, int]] = field(default_factory=list)


def match_shape_model(
    image,
    template_name: str,
    *,
    threshold: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> MatchResult:
    if cv2 is None:
        return MatchResult(ok=False, label="no_cv2")
    tmpl, mask, meta = load_template_and_mask(template_name)
    if tmpl is None or image is None:
        return MatchResult(ok=False, label="no_template")
    blk = shape_match_cfg(cfg)
    amin = float(meta.get("angle_min", blk.get("angle_min", -25)))
    amax = float(meta.get("angle_max", blk.get("angle_max", 25)))
    astep = float(blk.get("angle_step", 5))
    edge_w = float(blk.get("edge_weight", 0.4))
    edge_w = min(0.8, max(0.0, edge_w))
    ncc_w = 1.0 - edge_w
    if threshold is None:
        threshold = float(blk.get("score", 0.55))
    thr = float(threshold)

    scene = _to_gray(image)
    tmpl_g = _to_gray(tmpl)
    th0, tw0 = tmpl_g.shape[:2]
    if scene.shape[0] < 8 or scene.shape[1] < 8:
        return MatchResult(ok=False, label="image_small")

    pyramid = int(blk.get("pyramid", 2) or 1)
    pyramid = max(1, min(3, pyramid))
    scale = 0.5 if pyramid >= 2 and min(scene.shape[:2]) >= 160 else 1.0

    best: Optional[PoseHit] = None

    def _search(scn, tg, msk, angles, scale_s: float) -> Optional[PoseHit]:
        local = None
        for ang in angles:
            rot, rm, rc = _rotate(tg, msk, ang)
            ncc, loc = _ncc_masked(scn, rot, rm)
            if ncc < 0:
                continue
            cx = loc[0] + rc[0]
            cy = loc[1] + rc[1]
            hit = PoseHit(score=ncc, ncc=ncc, edge=0.0, x=cx / scale_s, y=cy / scale_s, angle_deg=ang)
            if local is None or hit.ncc > local.ncc:
                local = hit
                local.box = (
                    int(loc[0] / scale_s),
                    int(loc[1] / scale_s),
                    int(rot.shape[1] / scale_s),
                    int(rot.shape[0] / scale_s),
                )
        return local

    scn_s = cv2.resize(scene, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else scene
    tg_s = cv2.resize(tmpl_g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else tmpl_g
    mk_s = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST) if scale < 1 else mask
    coarse = _search(scn_s, tg_s, mk_s, _angles(amin, amax, astep), scale)
    if coarse is None:
        return MatchResult(ok=False, score=0.0, label=template_name)

    refine_step = max(1.0, astep / 5.0)
    ra0, ra1 = coarse.angle_deg - astep, coarse.angle_deg + astep
    best_ang = _search(scene, tmpl_g, mask, _angles(ra0, ra1, refine_step), 1.0) or coarse

    rot, rm, rc = _rotate(tmpl_g, mask, best_ang.angle_deg)
    ncc, loc = _ncc_masked(scene, rot, rm)
    cx = loc[0] + rc[0]
    cy = loc[1] + rc[1]
    pts = _mask_edge_points(rm)
    if len(pts):
        pts = pts + np.array([loc[0], loc[1]], dtype=np.float32)
    edge = _edge_score(scene, pts)
    score = float(ncc_w * ncc + edge_w * edge)

    contour = []
    cnts, _ = cv2.findContours(rm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        c = max(cnts, key=cv2.contourArea).reshape(-1, 2)
        contour = [(int(x + loc[0]), int(y + loc[1])) for x, y in c[:: max(1, len(c) // 80)]]

    hit = PoseHit(
        score=score,
        ncc=float(ncc),
        edge=float(edge),
        x=float(cx),
        y=float(cy),
        angle_deg=float(best_ang.angle_deg),
        box=(int(loc[0]), int(loc[1]), int(rot.shape[1]), int(rot.shape[0])),
        contour=contour,
    )
    ok = hit.score >= thr and hit.ncc >= max(0.25, thr * 0.5)
    msg = template_name if ok else f"low_score ncc={hit.ncc:.2f} edge={hit.edge:.2f}"
    r = MatchResult(
        ok=ok,
        x=hit.x,
        y=hit.y,
        angle_deg=hit.angle_deg,
        score=hit.score,
        label=msg if not ok else template_name,
    )
    r.tmpl_w = int(tw0)
    r.tmpl_h = int(th0)
    r.ncc = hit.ncc
    r.edge = hit.edge
    r.box = hit.box
    r.contour = hit.contour
    return r
