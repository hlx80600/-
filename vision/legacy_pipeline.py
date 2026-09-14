"""把旧压鞋机视觉接到本程序：YOLO OBB + 分类 + 深度 + 手眼 → 机器人毫米。

旧链路：
  皮带：ShoeVision（鞋OBB / 左右脚 / 鞋楦 / 手眼）
  放槽：槽相机鞋头分类伺服 + 有无鞋分类
  取槽：Position 压杆测距微调取鞋 XY

本模块可在未装 YOLO/RSDT 时返回明确错误，由 VisionService 走 Mock。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from vision.numpy_compat import np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHOE_CFG = ROOT / "shoe_vision_config.json"

_STACK_STATUS_CACHE: Dict[str, Any] | None = None
_STACK_STATUS_TS: float = 0.0


class FrameAdapter:
    """把本程序 OrbbecCamera 伪装成旧程序 camera.get_one_frame()。"""

    def __init__(self, cam, fallback_z_mm: float = 400.0):
        self._cam = cam
        self.fallback_z_mm = float(fallback_z_mm)
        # True：监控实时推演用 last_color，不抢 grab（避免原图掉帧）
        self.prefer_last = False

    def get_one_frame(self):
        img = None
        if self.prefer_last:
            img = getattr(self._cam, "last_color", None)
            if img is None:
                img = getattr(self._cam, "_last_bgr", None)
        if img is None:
            try:
                img = self._cam.grab()
            except Exception as e:
                log.warning("取帧失败: %s", e)
        depth = getattr(self._cam, "last_depth", None)
        if img is not None and depth is None:
            h, w = img.shape[:2]
            depth = np.full((h, w), self.fallback_z_mm, dtype=np.float32)
        return img, depth, None

    def connect_camera(self, *a, **k) -> bool:
        return bool(getattr(self._cam, "opened", False) or getattr(self._cam, "use_mock", False))

    def stop(self) -> None:
        pass


class SnapshotAdapter:
    """用已缓存的一帧做推演，完全不访问相机。"""

    def __init__(self, img, depth=None, fallback_z_mm: float = 400.0):
        self._img = img
        self._depth = depth
        self.fallback_z_mm = float(fallback_z_mm)

    def get_one_frame(self):
        img = self._img
        depth = self._depth
        if img is not None and depth is None:
            h, w = img.shape[:2]
            depth = np.full((h, w), self.fallback_z_mm, dtype=np.float32)
        return img, depth, None

    def connect_camera(self, *a, **k) -> bool:
        return self._img is not None

    def stop(self) -> None:
        pass


def _dir_has_pkg(base: Path, name: str) -> bool:
    """目录里是否有包/模块文件。不 import、不走 meta_path（避免卡住 GIL）。"""
    try:
        return (base / name / "__init__.py").is_file() or (base / f"{name}.py").is_file()
    except OSError:
        return False


def _ultralytics_present() -> bool:
    """只扫本机 site-packages，不 import torch/YOLO。"""
    if "ultralytics" in sys.modules or "ultralytics_obb360" in sys.modules:
        return True
    paths: list[Path] = []
    try:
        import site

        for item in list(site.getsitepackages() or []) + [site.getusersitepackages()]:
            if item:
                paths.append(Path(item))
    except Exception:
        pass
    for entry in sys.path[:8]:
        if entry:
            paths.append(Path(entry))
    seen: set[str] = set()
    for base in paths:
        key = str(base)
        if key in seen:
            continue
        seen.add(key)
        if _dir_has_pkg(base, "ultralytics") or _dir_has_pkg(base, "ultralytics_obb360"):
            return True
    return False


def stack_status() -> Dict[str, Any]:
    """检查旧视觉依赖是否在磁盘上。禁止 import 重库，避免卡死界面。"""
    global _STACK_STATUS_CACHE, _STACK_STATUS_TS
    now = time.monotonic()
    if _STACK_STATUS_CACHE is not None and (now - _STACK_STATUS_TS) < 300.0:
        return dict(_STACK_STATUS_CACHE)
    out: Dict[str, Any] = {
        "shoe_vision": (ROOT / "shoe_vision_seg.py").is_file(),
        "ultralytics": _ultralytics_present(),
        "slot_check": (ROOT / "slot_check_dect.py").is_file() or (ROOT / "slot_check.py").is_file(),
        "position": (ROOT / "position.py").is_file() or (ROOT / "position_obb.py").is_file(),
        "message": "",
    }
    errs: list[str] = []
    if not out["shoe_vision"]:
        errs.append("shoe_vision_seg: 未找到")
    if not out["ultralytics"]:
        errs.append("ultralytics: 未安装")
    if not out["slot_check"]:
        errs.append("slot_check_dect: 未找到")
    if not out["position"]:
        errs.append("position: 未找到")
    out["message"] = " | ".join(errs) if errs else "依赖已找到（检测时再加载）"
    _STACK_STATUS_CACHE = dict(out)
    _STACK_STATUS_TS = now
    return out


def vision_method(vis_cfg: Optional[dict]) -> str:
    """检测一律 YOLO。yaml 可写 yolo / legacy，其它值也按 YOLO 跑。"""
    return "yolo"


def listed_model_paths(vis_cfg: Optional[dict] = None) -> list[tuple[str, Path]]:
    """HMI 检查清单用：名称 + 路径。"""
    vis = vis_cfg if isinstance(vis_cfg, dict) else {}
    shoe = vis.get("shoe_vision") if isinstance(vis.get("shoe_vision"), dict) else {}
    toe = vis.get("toe_align") if isinstance(vis.get("toe_align"), dict) else {}
    slot = vis.get("slot_check") if isinstance(vis.get("slot_check"), dict) else {}
    pos = vis.get("position") if isinstance(vis.get("position"), dict) else {}

    def _p(raw, fallback: str) -> Path:
        text = str(raw or fallback or "").strip()
        p = Path(text) if text else Path(fallback)
        if not p.is_absolute():
            p = ROOT / p
        return p

    cfg_json = _shoe_cfg_path(vis)
    shoe_obb = last_cls = last_obb = ""
    try:
        import json

        data = json.loads(cfg_json.read_text(encoding="utf-8")) if cfg_json.exists() else {}
        if isinstance(data, dict):
            shoe_obb = str(data.get("shoe_model_path") or "")
            last_cls = str(data.get("shoe_cls_model_path") or "")
            last_obb = str(data.get("shoe_tree_model_path") or "")
    except Exception:
        pass
    return [
        ("皮带-鞋OBB", _p(shoe.get("shoe_model_path"), shoe_obb)),
        ("皮带-左右脚", _p(shoe.get("cls_model_path"), last_cls)),
        ("皮带-鞋楦OBB", _p(shoe.get("tree_model_path"), last_obb)),
        ("鞋头对位-ImgAct", _p(toe.get("model_path"), "models/toe_align/0907best.pt")),
        ("鞋头对位-侧向", _p(toe.get("later_model_path"), "models/toe_align/0907best_later.pt")),
        ("鞋头对位-分类兜底", _p(toe.get("classify_model_path"), "models/toe_align/0722best.pt")),
        ("槽有无鞋-检测", _p(slot.get("model_path"), "models/slot_check/7.13_dect_1.pt")),
        ("槽有无鞋-分类兜底", _p(slot.get("classify_model_path"), "models/slot_check/7.10slot_check.pt")),
        ("取槽压杆", _p(pos.get("rod_model_path"), "models/position/rod/obb.pt")),
        ("ShoeVision配置", cfg_json),
    ]


def model_status_text(vis_cfg: Optional[dict] = None) -> str:
    bits = []
    for name, path in listed_model_paths(vis_cfg):
        mark = "✓" if path.exists() else "✗"
        bits.append(f"{mark}{name}")
    return " ".join(bits)


_sv = None
_sv_err = ""


def reset_shoe_vision() -> None:
    """json/模型改完后丢掉缓存，下次检测重新 from_config_file。"""
    global _sv, _sv_err
    _sv = None
    _sv_err = ""
    try:
        from vision.toe_imgact import reset_toe_imgact

        reset_toe_imgact()
    except Exception:
        pass


def _shoe_cfg_path(vis_cfg: Dict[str, Any]) -> Path:
    raw = (vis_cfg.get("shoe_vision") or {}).get("config") if isinstance(vis_cfg.get("shoe_vision"), dict) else None
    p = Path(str(raw)) if raw else DEFAULT_SHOE_CFG
    if not p.is_absolute():
        p = ROOT / p
    return p


def get_shoe_vision(cameras: Optional[dict] = None, vis_cfg: Optional[dict] = None):
    """懒加载 ShoeVision，并把 cam1 接到 get_one_frame。"""
    global _sv, _sv_err
    if _sv is not None:
        if cameras and cameras.get("cam1") is not None:
            z = 400.0
            mock = vis_cfg.get("belt_pick_mock") if isinstance(vis_cfg, dict) else {}
            if isinstance(mock, dict):
                z = float(mock.get("z", z))
            _sv.camera = FrameAdapter(cameras["cam1"], fallback_z_mm=z)
        return _sv
    try:
        from shoe_vision_seg import ShoeVision

        path = _shoe_cfg_path(vis_cfg or {})
        _sv = ShoeVision.from_config_file(str(path), connect_camera=False)
        _sv_err = ""
        if cameras and cameras.get("cam1") is not None:
            z = 400.0
            mock = vis_cfg.get("belt_pick_mock") if isinstance(vis_cfg, dict) else {}
            if isinstance(mock, dict):
                z = float(mock.get("z", z))
            _sv.camera = FrameAdapter(cameras["cam1"], fallback_z_mm=z)
        return _sv
    except Exception as e:
        _sv_err = str(e)
        log.warning("ShoeVision 未能启动: %s", e)
        return None


def last_shoe_vision_error() -> str:
    return _sv_err


def detect_belt_legacy(cameras, vis_cfg, default_z, default_rx, default_ry):
    """
    皮带抓鞋：旧 ShoeVision → 基座 XYZ + yaw + 左右脚 + 鞋头偏移（机器人毫米）。
    返回 (result_dict, vis_bgr)
    """
    fail = {"ok": False, "message": "", "source": "legacy_yolo_handeye"}
    sv = get_shoe_vision(cameras, vis_cfg)
    if sv is None:
        fail["message"] = f"旧视觉未就绪（YOLO/手眼/模型）: {_sv_err or stack_status()['message']}"
        return fail, None

    try:
        from shoe_seg.shoes_seg import get_shoe_base_pose_toe_and_arc_points
    except Exception:
        left, right = sv.get_all_shoe_points()
        chosen_side = "left" if left else "right"
        poses = left if left else right
        if not poses:
            fail["message"] = "YOLO未检出鞋子"
            return fail, None
        x, y, z, yaw = poses[0]
        L = 120.0
        return {
            "ok": True,
            "x": float(x),
            "y": float(y),
            "z": float(z if z else default_z),
            "rx": float(default_rx),
            "ry": float(default_ry),
            "rz": float(yaw),
            "is_left_shoe": chosen_side == "left",
            "message": f"旧视觉抓鞋 {chosen_side} 楦心=({x:.1f},{y:.1f},{z:.1f}) yaw={yaw:.1f}（无鞋头分割）",
            "source": "legacy_yolo_handeye",
            "toe_offset_in_grasp_tcp": [0.0, L, 0.0],
            "shoe_length_mm": L,
        }, None

    chosen = get_shoe_base_pose_toe_and_arc_points(vision=sv, side="left", max_retries=3)
    vis = chosen.get("vis_frame")
    side = str(chosen.get("selected_side") or "")
    if side not in ("left", "right"):
        fail["message"] = "旧视觉未选到左右脚"
        return fail, vis
    prefix = "left" if side == "left" else "right"
    poses = chosen.get(f"{prefix}_base_poses") or []
    toes = chosen.get(f"{prefix}_toe_base_points") or []
    if not poses:
        fail["message"] = "旧视觉无楦心位姿"
        return fail, vis
    x, y, z, yaw = poses[0]
    toe = toes[0] if toes else None
    if toe is not None and len(toe) >= 2:
        length = float(np.hypot(float(toe[0]) - float(x), float(toe[1]) - float(y)))
        dz = float(toe[2]) - float(z) if len(toe) >= 3 else 0.0
    else:
        length = 120.0
        dz = 0.0
    msg = (
        f"旧视觉抓鞋 {('左' if side=='left' else '右')} "
        f"楦心XY=({x:.1f},{y:.1f}) Z={z:.1f} yaw={yaw:.1f} "
        f"鞋头距={length:.1f}mm（示教器）"
    )
    return {
        "ok": True,
        "x": float(x),
        "y": float(y),
        "z": float(z if abs(float(z)) > 1e-6 else default_z),
        "rx": float(default_rx),
        "ry": float(default_ry),
        "rz": float(yaw),
        "is_left_shoe": side == "left",
        "message": msg,
        "source": "legacy_yolo_handeye",
        "toe_offset_in_grasp_tcp": [0.0, float(length), float(dz)],
        "shoe_length_mm": float(length),
    }, vis


def _slot_check_extra(num_boxes: int = 0, source: str = "") -> dict[str, Any]:
    return {"num_boxes": int(num_boxes), "source": str(source or "")}


def _slot_classify_fallback_blk(blk: dict) -> dict:
    """检测失败时改用分类权重，避免把 detect 的 .pt 丢给分类器。"""
    cls_blk = dict(blk)
    fallback = blk.get("classify_model_path")
    if fallback:
        cls_blk["model_path"] = fallback
    # 检测门槛不要套到分类 top1
    cls_blk["conf"] = 0.0
    return cls_blk


def _classify_slot_occupied_cls(
    image_bgr,
    blk: dict,
) -> Tuple[Optional[bool], str, float, dict[str, Any]]:
    """旧二分类兜底：0=空槽，1=有鞋。"""
    try:
        from slot_check import SlotChecker
    except Exception as e:
        return None, f"slot_check 分类不可用: {e}", 0.0, _slot_check_extra(source="classify")
    path = blk.get("classify_model_path") or blk.get("model_path")
    try:
        kw: dict[str, Any] = {}
        resolved = _resolve_model_path(path) if path else None
        if resolved is not None:
            kw["model_path"] = resolved
        if blk.get("imgsz") is not None:
            try:
                kw["imgsz"] = int(blk.get("imgsz") or 640)
            except (TypeError, ValueError):
                pass
        checker = SlotChecker(**kw) if kw else SlotChecker()
        r = checker.classify(image_bgr)
        cid = int(getattr(r, "class_id", -1))
        conf = float(getattr(r, "confidence", 0.0) or 0.0)
        min_conf = 0.0
        try:
            min_conf = float(blk.get("conf", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_conf = 0.0
        extra = _slot_check_extra(source="classify")
        if min_conf > 0 and conf < min_conf:
            return None, f"置信度不足 {conf:.2f}<{min_conf:.2f}", conf, extra
        if cid == 1:
            return True, f"分类有鞋 conf={conf:.2f}", conf, extra
        if cid == 0:
            return False, f"分类空槽 conf={conf:.2f}", conf, extra
        return None, f"未知类别 id={cid}", conf, extra
    except Exception as e:
        return None, str(e), 0.0, _slot_check_extra(source="classify")


def classify_slot_occupied(
    image_bgr, vis_cfg: Optional[dict] = None
) -> Tuple[Optional[bool], str, float, dict[str, Any]]:
    """有鞋=True 没鞋=False；失败 (None, msg, 0, extra)。

    默认对标双槽：检测有框=有鞋。``vision.slot_check.mode=classify`` 才走旧二分类。
    """
    blk = (vis_cfg or {}).get("slot_check") if isinstance(vis_cfg, dict) else {}
    if not isinstance(blk, dict):
        blk = {}
    mode = str(blk.get("mode") or "detect").strip().lower()
    if mode == "classify":
        return _classify_slot_occupied_cls(image_bgr, blk)

    try:
        from slot_check_dect import SlotChecker as DetectChecker
    except Exception as e:
        log.warning("slot_check_dect 不可用，退回分类: %s", e)
        return _classify_slot_occupied_cls(image_bgr, _slot_classify_fallback_blk(blk))

    path = blk.get("model_path") or "models/slot_check/7.13_dect_1.pt"
    resolved = _resolve_model_path(path)
    if resolved is None or not resolved.exists():
        fallback = _resolve_model_path(blk.get("classify_model_path") or "models/slot_check/7.10slot_check.pt")
        if fallback is not None and fallback.exists():
            log.warning("槽检测权重不存在 %s，退回分类 %s", resolved, fallback)
            return _classify_slot_occupied_cls(image_bgr, _slot_classify_fallback_blk(blk))
        return None, (
            f"槽检测模型不存在: {resolved}；分类兜底也不存在: {fallback}"
        ), 0.0, _slot_check_extra(source="detect")

    try:
        imgsz = int(blk.get("imgsz") or 640)
    except (TypeError, ValueError):
        imgsz = 640
    try:
        conf = float(blk.get("conf") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf <= 0:
        conf = 0.25
    try:
        checker = DetectChecker(model_path=str(resolved), imgsz=imgsz, conf=conf)
        r = checker.classify(image_bgr)
        has = int(getattr(r, "has_shoe", 0)) == 1
        nbox = int(getattr(r, "num_boxes", 0) or 0)
        score = float(getattr(r, "confidence", 0.0) or 0.0)
        extra = _slot_check_extra(num_boxes=nbox, source="detect")
        msg = f"检测{'有鞋' if has else '空槽'} boxes={nbox} conf={score:.2f}"
        return has, msg, score, extra
    except Exception as e:
        log.warning("槽检测失败，退回分类: %s", e)
        return _classify_slot_occupied_cls(image_bgr, _slot_classify_fallback_blk(blk))


def _toe_compat_label(x_label: str, y_label: str) -> str:
    """把 ImgAct 双头收成旧 Station 能读的单个 label。

    Y 优先（左右），再 X 前进；后退暂标 ``back``（旧 Station 会当成默认前进，已知限制）。
    """
    y_lab = str(y_label).strip()
    x_lab = str(x_label).strip()
    if y_lab == "1":
        return "left"
    if y_lab == "2":
        return "right"
    if x_lab == "0" and y_lab == "0":
        return "0"
    if x_lab == "1":
        return "1"
    if x_lab == "2":
        return "back"
    return x_lab or "0"


def classify_toe_align(
    image_bgr, vis_cfg: Optional[dict] = None
) -> Tuple[str, str, dict[str, Any]]:
    """鞋头对位。优先 ImgAct 双头；否则 YOLO classify。

    返回 (label, message, extra)，extra 含 x_label/y_label/source。
    """
    empty_extra: dict[str, Any] = {"x_label": "", "y_label": "", "source": ""}
    blk = (vis_cfg or {}).get("toe_align") if isinstance(vis_cfg, dict) else {}
    if not isinstance(blk, dict):
        blk = {}
    backend = str(blk.get("backend") or "imgact").strip().lower()
    imgsz = int(blk.get("imgsz", 256) or 256)

    if backend != "classify":
        raw = blk.get("model_path")
        model = _resolve_model_path(raw) if raw else _resolve_model_path("models/toe_align/0907best.pt")
        later_raw = blk.get("later_model_path")
        later = _resolve_model_path(later_raw) if later_raw else (
            model.with_name(f"{model.stem}_later{model.suffix}") if model is not None else None
        )
        if model is not None and model.exists() and later is not None and later.exists():
            try:
                from vision.toe_imgact import get_toe_imgact

                infer = get_toe_imgact(model, later, imgsz=imgsz)
                x_label, x_conf, y_label, y_conf = infer.predict(image_bgr)
                label = _toe_compat_label(x_label, y_label)
                extra = {
                    "x_label": x_label,
                    "y_label": y_label,
                    "source": "imgact",
                }
                msg = (
                    f"ImgAct 鞋头 X={x_label}({x_conf:.2f}) "
                    f"Y={y_label}({y_conf:.2f}) compat={label}"
                )
                return label, msg, extra
            except Exception as e:
                log.warning("ImgAct 鞋头对位失败，退回分类: %s", e)

    cls_raw = blk.get("classify_model_path") or blk.get("model_path")
    model = _resolve_model_path(cls_raw)
    if model is None:
        return "", "未配置 vision.toe_align 模型", empty_extra
    if not model.exists():
        return "", f"鞋头对位模型不存在: {model}", empty_extra
    try:
        from ultralytics import YOLO

        m = YOLO(str(model))
        res = m.predict(source=image_bgr, imgsz=imgsz, verbose=False)[0]
        names = getattr(res, "names", None) or getattr(m, "names", {}) or {}
        if hasattr(res, "probs") and res.probs is not None:
            idx = int(res.probs.top1)
            conf = float(res.probs.top1conf)
            label = str(names.get(idx, idx))
            extra = {"x_label": "", "y_label": "", "source": "classify"}
            return label, f"鞋头对位 {label}  conf={conf:.2f}", extra
        return "", "模型无分类输出", empty_extra
    except Exception as e:
        return "", f"鞋头对位失败: {e}", empty_extra


def _resolve_model_path(raw) -> Optional[Path]:
    if raw is None or str(raw).strip() == "":
        return None
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return p


def _cov_from_rotation_deg(degrees) -> list:
    """把 [1,0,0] 转到机器人基座（与旧 Position 相同）。"""
    vals = [float(v) for v in (degrees or [0, 0, 0])]
    while len(vals) < 3:
        vals.append(0.0)
    x_d, y_d, z_d = vals[:3]
    vec = np.array([[1.0], [0.0], [0.0]])
    rx = np.array(
        [
            [1, 0, 0],
            [0, np.cos(np.radians(x_d)), -np.sin(np.radians(x_d))],
            [0, np.sin(np.radians(x_d)), np.cos(np.radians(x_d))],
        ]
    )
    ry = np.array(
        [
            [np.cos(np.radians(y_d)), 0, np.sin(np.radians(y_d))],
            [0, 1, 0],
            [-np.sin(np.radians(y_d)), 0, np.cos(np.radians(y_d))],
        ]
    )
    rz = np.array(
        [
            [np.cos(np.radians(z_d)), -np.sin(np.radians(z_d)), 0],
            [np.sin(np.radians(z_d)), np.cos(np.radians(z_d)), 0],
            [0, 0, 1],
        ]
    )
    out = (rz @ ry @ rx @ vec).reshape(-1)
    n = float(np.linalg.norm(out))
    if n < 1e-9:
        return [1.0, 0.0, 0.0]
    return [float(out[0] / n), float(out[1] / n), float(out[2] / n)]


def _rod_side_from_opening(opening: str, camera_id: int) -> int:
    """build_robot_xyz_offset 仍要 1/2；放料口当 1，取料口当 2。未声明开口则沿用 yaml camera_id。"""
    if opening == "place":
        return 1
    if opening == "pick":
        return 2
    return 1 if int(camera_id) != 2 else 2


def _rod_profile(
    pos_cfg: dict,
    blk: dict,
    vis_cfg: Optional[dict],
    *,
    cam_key: str,
    slot_id: int,
) -> tuple[int, list, list, list, Any, str]:
    """选 K / gripper preset / 旋转 / ROI。优先 opening_* 与 slots[n]，否则保持原 left/right。"""
    from vision.opening import slot_cfg_block

    opening = str(blk.get("opening") or "").strip().lower()
    camera_id = int(blk.get("camera_id") or 1)
    if opening not in ("place", "pick"):
        # 未声明开口时保持旧行为：只看 camera_id 1=left / 2=right
        opening = ""
        side_id = 1 if camera_id != 2 else 2
    else:
        side_id = _rod_side_from_opening(opening, camera_id)

    named = pos_cfg.get(f"opening_{opening}") if opening else None
    if not isinstance(named, dict):
        named = {}

    if named:
        k = named.get("K") or (pos_cfg.get("cam_left_K") if side_id == 1 else pos_cfg.get("cam_right_K"))
        preset = named.get("gripper_preset_xyz") or (
            pos_cfg.get("cam_left_gripper_preset_xyz") if side_id == 1 else pos_cfg.get("cam_right_gripper_preset_xyz")
        )
        rot = named.get("robot_base_rotation_degrees") or (
            pos_cfg.get("cam_left_robot_base_rotation_degrees")
            if side_id == 1
            else pos_cfg.get("cam_right_robot_base_rotation_degrees")
        )
        roi_lt = named.get("rod_roi")
    elif side_id == 2:
        k = pos_cfg.get("cam_right_K") or [600, 600, 640, 360]
        preset = pos_cfg.get("cam_right_gripper_preset_xyz") or [-0.08, 0.05, 0.37]
        rot = pos_cfg.get("cam_right_robot_base_rotation_degrees") or [0, 0, -43]
        roi_lt = pos_cfg.get("cam_right_rod_roi")
    else:
        k = pos_cfg.get("cam_left_K") or [600, 600, 640, 360]
        preset = pos_cfg.get("cam_left_gripper_preset_xyz") or [0.079, 0.037, 0.38]
        rot = pos_cfg.get("cam_left_robot_base_rotation_degrees") or [0, 0, 137]
        roi_lt = pos_cfg.get("cam_left_rod_roi")
        side_id = 1

    overlay = slot_cfg_block(vis_cfg, slot_id)
    if overlay.get("gripper_preset_xyz"):
        preset = overlay.get("gripper_preset_xyz")
    if overlay.get("rod_roi"):
        roi_lt = overlay.get("rod_roi")
    if overlay.get("K"):
        k = overlay.get("K")
    tag = opening or cam_key or f"cam_id{side_id}"
    if slot_id:
        tag = f"{tag}/slot{slot_id}"
    return side_id, list(k), list(preset), list(rot), roi_lt, tag


def measure_rod_offset_mm(
    cameras: Optional[dict],
    vis_cfg: Optional[dict],
    image_bgr: Any = None,
    slot_id: int = 0,
) -> Tuple[bool, float, float, float, Any, str]:
    """
    取料开口压杆/夹爪 X 距 → 机器人基座 XY 毫米（旧 Position 公式）。
    默认 cam4；image_bgr 传入则不再 grab。
    slot_id: 当前开口下物理槽 1–4，用于可选 preset 覆盖。
    返回 (ok, dx_mm, dy_mm, dz_mm, vis_bgr, message)
    """
    vis = vis_cfg if isinstance(vis_cfg, dict) else {}
    blk = vis.get("position") if isinstance(vis.get("position"), dict) else {}
    if not isinstance(blk, dict):
        blk = {}
    cam_key = str(blk.get("camera") or "cam4")
    cam = (cameras or {}).get(cam_key) if cameras else None
    if image_bgr is not None:
        img = image_bgr
    else:
        if cam is None:
            return False, 0.0, 0.0, 0.0, None, f"没有相机 {cam_key}"
        try:
            img = cam.grab()
        except Exception as e:
            return False, 0.0, 0.0, 0.0, None, f"取图失败: {e}"
    if img is None:
        return False, 0.0, 0.0, 0.0, None, f"{cam_key} 无图"
    depth = getattr(cam, "last_depth", None) if cam is not None else None
    if depth is None:
        h, w = img.shape[:2]
        z = float(blk.get("fallback_z_mm") or 400.0)
        depth = np.full((h, w), z, dtype=np.float32)

    cfg_path = blk.get("config") or "position_config.yaml"
    p = _resolve_model_path(cfg_path) or (ROOT / "position_config.yaml")
    try:
        import yaml

        pos_cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"读 position_config 失败: {e}"

    model = blk.get("rod_model_path") or pos_cfg.get("rod_obb_model_path")
    model_p = _resolve_model_path(model)
    if model_p is None or not model_p.exists():
        return False, 0.0, 0.0, 0.0, img, f"压杆模型不存在: {model_p}"

    try:
        from position_detector import detect_with_roi_filter
        from position_geometry import build_robot_xyz_offset
        from position_obb import OBBOnlyDetector
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"Position 栈不可用: {e}"

    camera_id, k, preset, rot, roi_lt, tag = _rod_profile(
        pos_cfg, blk, vis, cam_key=cam_key, slot_id=int(slot_id or 0)
    )

    cov = _cov_from_rotation_deg(rot)
    det = OBBOnlyDetector(str(model_p))
    det.set_parameters(
        obb_img_size=int(pos_cfg.get("rod_obb_img_size") or 640),
        obb_detection_conf=float(pos_cfg.get("rod_obb_detection_conf") or 0.4),
    )
    roi = None
    rw = int(pos_cfg.get("roi_width") or 600)
    rh = int(pos_cfg.get("roi_height") or 300)
    if isinstance(roi_lt, list) and roi_lt and isinstance(roi_lt[0], (list, tuple)):
        x1, y1 = int(roi_lt[0][0]), int(roi_lt[0][1])
        roi = [[x1, y1], [x1 + rw, y1 + rh]]
    shift = pos_cfg.get("rod_default_shift") or [[0, 0]]
    try:
        result, vis = detect_with_roi_filter(
            detector=det,
            rgb_image=img,
            depth_image=depth,
            fx=float(k[0]),
            fy=float(k[1]),
            cx=float(k[2]),
            cy=float(k[3]),
            shift=shift,
            roi=roi,
        )
    except Exception as e:
        return False, 0.0, 0.0, 0.0, img, f"压杆检测失败: {e}"
    xie_x = None
    for detection in result or []:
        if not detection or len(detection) < 6:
            continue
        if not detection[0]:
            continue
        if int(detection[1]) == 0:
            xie_x = float(detection[5])
            break
    if xie_x is None:
        return False, 0.0, 0.0, 0.0, vis if vis is not None else img, "未检出鞋或缺少深度"
    distance = xie_x - float(preset[0])
    xyz_m = build_robot_xyz_offset(camera_id, distance, cov, cov)
    dx, dy, dz = float(xyz_m[0]) * 1000.0, float(xyz_m[1]) * 1000.0, float(xyz_m[2]) * 1000.0
    msg = (
        f"压杆偏移 {tag} dx={dx:.1f} dy={dy:.1f} mm（示教器） dist={distance:.4f}m"
    )
    return True, dx, dy, dz, vis if vis is not None else img, msg
