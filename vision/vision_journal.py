"""生产视觉快照：拍照当时存图+检测结果，运送完成后再回写结果。

目录：项目 ``logs/vision_snaps/YYYY-MM-DD/<snap_id>/``
  - 原图/叠图文件名含相机与时间，例如 ``cam1_20260828_140455_635_belt_pick_raw.jpg``
  - ``meta.json`` 检测结果 + ``transport``（place / slot_check / unload）
总索引：``logs/vision_snaps/index.jsonl``（退出程序后仍可查）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from queue import Full, Queue
from typing import Any, Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
SNAP_ROOT = _ROOT / "logs" / "vision_snaps"

_JPEG_QUALITY = 85
_KEEP_DAYS_DEFAULT = 7
_MAX_QUEUE = 12

_lock = threading.RLock()
_paths: dict[str, Path] = {}
_img_q: Queue | None = None
_worker: threading.Thread | None = None
_saves = 0


def snap_root() -> Path:
    return SNAP_ROOT


def enabled(vision_cfg: dict | None) -> bool:
    if not isinstance(vision_cfg, dict):
        return True
    v = vision_cfg.get("save_runtime_snaps", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off")
    return bool(v)


def keep_days(vision_cfg: dict | None) -> int:
    if not isinstance(vision_cfg, dict):
        return _KEEP_DAYS_DEFAULT
    try:
        return max(1, int(vision_cfg.get("snap_keep_days", _KEEP_DAYS_DEFAULT)))
    except (TypeError, ValueError):
        return _KEEP_DAYS_DEFAULT


def _ensure_worker() -> None:
    global _img_q, _worker
    if _worker is not None and _worker.is_alive() and _img_q is not None:
        return
    _img_q = Queue(maxsize=_MAX_QUEUE)
    _worker = threading.Thread(target=_img_loop, name="vision-snap", daemon=True)
    _worker.start()


def _img_loop() -> None:
    while True:
        job = _img_q.get() if _img_q is not None else None
        if job is None:
            return
        shot_dir, raw, vis, raw_name, vis_name = job
        try:
            _write_jpg(shot_dir / str(raw_name), raw)
            _write_jpg(shot_dir / str(vis_name), vis)
        except Exception as e:
            log.warning("[视觉快照] 写图失败 %s: %s", shot_dir, e)


def _write_jpg(path: Path, img: Any) -> None:
    if img is None:
        return
    try:
        import cv2
    except ImportError:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY])
    if not ok:
        log.warning("[视觉快照] imwrite 失败 %s", path)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_index(rec: dict[str, Any]) -> None:
    SNAP_ROOT.mkdir(parents=True, exist_ok=True)
    path = SNAP_ROOT / "index.jsonl"
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line)
        fp.flush()
        try:
            os.fsync(fp.fileno())
        except OSError:
            pass


def _prune(days: int) -> None:
    cutoff = datetime.now() - timedelta(days=int(days))
    try:
        for day_dir in SNAP_ROOT.iterdir():
            if not day_dir.is_dir() or day_dir.name.startswith("."):
                continue
            try:
                day = datetime.strptime(day_dir.name, "%Y-%m-%d")
            except ValueError:
                continue
            if day >= cutoff:
                continue
            for child in day_dir.iterdir():
                if child.is_dir():
                    for f in child.glob("*"):
                        try:
                            f.unlink()
                        except OSError:
                            pass
                    try:
                        child.rmdir()
                    except OSError:
                        pass
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
            try:
                day_dir.rmdir()
            except OSError:
                pass
    except OSError as e:
        log.debug("[视觉快照] 清理旧目录: %s", e)


def _file_token(text: str, fallback: str) -> str:
    """文件名片段：只留字母数字、下划线、连字符。"""
    cleaned = "".join(
        ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(text or "").strip()
    )
    return cleaned.strip("_") or fallback


def jpg_names(cam_id: str, kind: str, when: datetime) -> tuple[str, str]:
    """原图/叠图文件名：相机_日期时刻毫秒_类型_raw/vis.jpg。"""
    base = (
        f"{_file_token(cam_id, 'cam')}_"
        f"{when.strftime('%Y%m%d_%H%M%S_%f')[:-3]}_"
        f"{_file_token(kind, 'shot')}"
    )
    return f"{base}_raw.jpg", f"{base}_vis.jpg"


def save_vision_shot(
    *,
    cam_id: str,
    kind: str,
    ok: bool,
    message: str,
    raw: Any = None,
    vis: Any = None,
    extra: Optional[dict[str, Any]] = None,
    keep_days_n: int = _KEEP_DAYS_DEFAULT,
) -> str:
    """拍照当时落盘。返回 snap_id；运送结果稍后用 record_transport 回写。"""
    now = datetime.now()
    cam = _file_token(cam_id, "cam")
    kind_tok = _file_token(kind, "shot")
    snap_id = f"{now.strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{cam}_{kind_tok}"
    raw_name, vis_name = jpg_names(cam_id, kind, now)
    shot_dir = SNAP_ROOT / now.strftime("%Y-%m-%d") / snap_id
    shot_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "id": snap_id,
        "ts": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "cam_id": str(cam_id),
        "kind": str(kind),
        "ok": bool(ok),
        "message": str(message or ""),
        "vision": dict(extra or {}),
        "transport": {},
        "files": {"raw": raw_name, "vis": vis_name},
    }
    _write_json(shot_dir / "meta.json", meta)
    _append_index(
        {
            "event": "vision",
            "ts": meta["ts"],
            "id": snap_id,
            "cam_id": cam_id,
            "kind": kind,
            "ok": bool(ok),
            "message": str(message or "")[:200],
        }
    )
    with _lock:
        _paths[snap_id] = shot_dir
        global _saves
        _saves += 1
        do_prune = _saves % 20 == 1
    _ensure_worker()
    if _img_q is not None:
        try:
            _img_q.put_nowait((shot_dir, raw, vis, raw_name, vis_name))
        except Full:
            try:
                _img_q.get_nowait()
            except Exception:
                pass
            try:
                _img_q.put_nowait((shot_dir, raw, vis, raw_name, vis_name))
            except Full:
                log.warning("[视觉快照] 写图队列满，跳过图像 id=%s", snap_id)
    if do_prune:
        _prune(keep_days_n)
    log.info("[视觉快照] 已存 %s ok=%s %s", snap_id, ok, str(message or "")[:80])
    return snap_id


def _shot_dir(snap_id: str) -> Path | None:
    if not snap_id:
        return None
    with _lock:
        cached = _paths.get(snap_id)
    if cached is not None and (cached / "meta.json").is_file():
        return cached
    # 退出后再开：按日期目录扫
    try:
        day = snap_id.split("_", 1)[0]
        if len(day) == 8:
            day_name = f"{day[0:4]}-{day[4:6]}-{day[6:8]}"
            cand = SNAP_ROOT / day_name / snap_id
            if (cand / "meta.json").is_file():
                with _lock:
                    _paths[snap_id] = cand
                return cand
        for day_dir in SNAP_ROOT.glob("????-??-??"):
            cand = day_dir / snap_id
            if (cand / "meta.json").is_file():
                with _lock:
                    _paths[snap_id] = cand
                return cand
    except OSError:
        return None
    return None


def record_transport(
    snap_id: str,
    *,
    stage: str,
    ok: bool,
    message: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """把运送之后的结果写回该视觉快照（place / slot_check / unload）。"""
    if not snap_id:
        return False
    shot = _shot_dir(snap_id)
    if shot is None:
        log.warning("[视觉快照] 回写运送结果失败，找不到 id=%s", snap_id)
        return False
    meta_path = shot / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[视觉快照] 读 meta 失败 %s: %s", meta_path, e)
        return False
    if not isinstance(meta, dict):
        return False
    transport = meta.get("transport")
    if not isinstance(transport, dict):
        transport = {}
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "ok": bool(ok),
        "message": str(message or ""),
        "detail": dict(extra or {}),
    }
    transport[str(stage)] = entry
    meta["transport"] = transport
    try:
        _write_json(meta_path, meta)
    except OSError as e:
        log.warning("[视觉快照] 写运送结果失败: %s", e)
        return False
    _append_index(
        {
            "event": "transport",
            "ts": entry["ts"],
            "id": snap_id,
            "stage": str(stage),
            "ok": bool(ok),
            "message": str(message or "")[:200],
            "detail": dict(extra or {}),
        }
    )
    log.info(
        "[视觉快照] 运送结果 %s stage=%s ok=%s %s",
        snap_id,
        stage,
        ok,
        str(message or "")[:80],
    )
    return True


_KIND_ZH: dict[str, str] = {
    "belt_pick": "皮带取料",
    "place_slot": "放料槽",
    "pick_slot": "取料槽",
}

_STAGE_ZH: dict[str, str] = {
    "place": "放入鞋槽",
    "slot_check": "槽判定",
    "unload": "下料到皮带",
}

_LIST_LIMIT_DEFAULT = 400
_SCAN_CAP = 2500


def kind_label(kind: str) -> str:
    """拍照类型中文名。"""
    return _KIND_ZH.get(str(kind), str(kind or "-"))


def stage_label(stage: str) -> str:
    """运送阶段中文名。"""
    return _STAGE_ZH.get(str(stage), str(stage or "-"))


def load_snap_meta(snap_id: str) -> dict[str, Any] | None:
    """读一份快照的 meta.json；附带 ``_dir`` 目录路径。找不到返回 None。"""
    shot = _shot_dir(snap_id)
    if shot is None:
        return None
    meta_path = shot / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    meta["_dir"] = str(shot)
    return meta


def snap_image_paths(meta: dict[str, Any]) -> tuple[Path | None, Path | None]:
    """解析原图/叠图路径；兼容旧版 raw.jpg / vis.jpg。"""
    shot = Path(str(meta.get("_dir") or ""))
    if not shot.is_dir():
        return None, None
    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    raw_name = str(files.get("raw") or "raw.jpg")
    vis_name = str(files.get("vis") or "vis.jpg")
    raw_path = shot / raw_name
    vis_path = shot / vis_name
    if not raw_path.is_file():
        legacy = shot / "raw.jpg"
        if legacy.is_file():
            raw_path = legacy
        else:
            found = sorted(shot.glob("*_raw.jpg"))
            raw_path = found[0] if found else raw_path
    if not vis_path.is_file():
        legacy = shot / "vis.jpg"
        if legacy.is_file():
            vis_path = legacy
        else:
            found = sorted(shot.glob("*_vis.jpg"))
            vis_path = found[0] if found else vis_path
    return raw_path, vis_path


def list_snap_records(
    *,
    cam_id: str = "",
    kind: str = "",
    limit: int = _LIST_LIMIT_DEFAULT,
) -> list[dict[str, Any]]:
    """扫描 ``logs/vision_snaps``，新→旧。

    Args:
        cam_id: 空=全部；否则只保留该相机。
        kind: 空=全部；否则只保留该拍照类型。
        limit: 最多返回条数。

    Returns:
        每项含 id/ts/cam_id/kind/ok/message/transport/_dir。
    """
    want_cam = str(cam_id or "").strip()
    want_kind = str(kind or "").strip()
    cap = max(1, int(limit))
    out: list[dict[str, Any]] = []
    scanned = 0
    if not SNAP_ROOT.is_dir():
        return out
    try:
        day_dirs = sorted(
            (p for p in SNAP_ROOT.iterdir() if p.is_dir() and len(p.name) == 10),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return out
    for day_dir in day_dirs:
        try:
            children = sorted(
                (p for p in day_dir.iterdir() if p.is_dir()),
                key=lambda p: p.name,
                reverse=True,
            )
        except OSError:
            continue
        for shot in children:
            scanned += 1
            if scanned > _SCAN_CAP:
                return out
            rec = _read_list_record(shot)
            if rec is None:
                continue
            if want_cam and str(rec.get("cam_id") or "") != want_cam:
                continue
            if want_kind and str(rec.get("kind") or "") != want_kind:
                continue
            out.append(rec)
            if len(out) >= cap:
                return out
    return out


def _read_list_record(shot: Path) -> dict[str, Any] | None:
    meta_path = shot / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    transport = meta.get("transport")
    if not isinstance(transport, dict):
        transport = {}
    snap_id = str(meta.get("id") or shot.name)
    return {
        "id": snap_id,
        "ts": str(meta.get("ts") or ""),
        "cam_id": str(meta.get("cam_id") or ""),
        "kind": str(meta.get("kind") or ""),
        "ok": bool(meta.get("ok")),
        "message": str(meta.get("message") or ""),
        "transport": transport,
        "_dir": str(shot),
    }


def transport_brief(kind: str, transport: Any) -> str:
    """列表右侧的运送摘要（已放槽 / 未运送 等）。"""
    if not isinstance(transport, dict):
        transport = {}
    if kind == "belt_pick":
        order: tuple[str, ...] = ("place", "slot_check")
    elif kind == "place_slot":
        order = ("slot_check",)
    elif kind == "pick_slot":
        order = ("unload",)
    else:
        order = ("place", "unload", "slot_check")
    for stage in order:
        ent = transport.get(stage)
        if not isinstance(ent, dict):
            continue
        ok = bool(ent.get("ok"))
        detail = ent.get("detail") if isinstance(ent.get("detail"), dict) else {}
        if stage == "place":
            slot = detail.get("place_slot")
            tag = f"槽#{slot}" if slot not in (None, "") else ""
            return ("已放" if ok else "放失败") + tag
        if stage == "unload":
            slot = detail.get("pick_slot")
            tag = f"槽#{slot}" if slot not in (None, "") else ""
            return ("已下料" if ok else "下料失败") + tag
        if stage == "slot_check":
            msg = str(ent.get("message") or "")
            if "可放料" in msg:
                return "可放料"
            if "禁止" in msg:
                return "禁止放料"
            return "已判定"
    return "未运送"


def format_snap_label(rec: dict[str, Any]) -> str:
    """HMI 列表一行。"""
    ts = str(rec.get("ts") or "")
    clock = ts[11:19] if len(ts) >= 19 else ts
    cam = str(rec.get("cam_id") or "-")
    kind = kind_label(str(rec.get("kind") or ""))
    flag = "OK" if rec.get("ok") else "FAIL"
    brief = transport_brief(str(rec.get("kind") or ""), rec.get("transport"))
    msg = str(rec.get("message") or "").replace("\n", " ")
    if len(msg) > 36:
        msg = msg[:36] + "…"
    return f"{clock}  {cam} {kind}  {flag}  {brief}  {msg}".rstrip()


def format_snap_detail(meta: dict[str, Any]) -> str:
    """详情区全文：检测结果 + 运送回写。"""
    cam = str(meta.get("cam_id") or "-")
    kind = str(meta.get("kind") or "")
    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    raw_name = str(files.get("raw") or "raw.jpg")
    vis_name = str(files.get("vis") or "vis.jpg")
    lines = [
        f"时间: {meta.get('ts') or '-'}",
        f"编号: {meta.get('id') or '-'}",
        f"相机: {cam}",
        f"类型: {kind_label(kind)} ({kind or '-'})",
        f"检测: {'成功' if meta.get('ok') else '失败'}",
        f"说明: {meta.get('message') or '-'}",
        f"目录: {meta.get('_dir') or '-'}",
        f"原图文件: {raw_name}",
        f"叠图文件: {vis_name}",
        "",
        "—— 检测数据 ——",
    ]
    vision = meta.get("vision")
    if isinstance(vision, dict) and vision:
        lines.append(json.dumps(vision, ensure_ascii=False, indent=2, default=str))
    else:
        lines.append("(无)")
    lines.append("")
    lines.append("—— 运送结果 ——")
    transport = meta.get("transport")
    if not isinstance(transport, dict) or not transport:
        lines.append("尚未回写（拍照后尚未完成放入鞋槽 / 下料，或监视测试未走流程）")
        return "\n".join(lines)
    for stage in ("place", "slot_check", "unload"):
        ent = transport.get(stage)
        if not isinstance(ent, dict):
            continue
        flag = "成功" if ent.get("ok") else "失败"
        lines.append(f"[{stage_label(stage)}] {flag}  {ent.get('ts') or ''}")
        msg = str(ent.get("message") or "").strip()
        if msg:
            lines.append(f"  {msg}")
        detail = ent.get("detail")
        if isinstance(detail, dict) and detail:
            lines.append("  " + json.dumps(detail, ensure_ascii=False, default=str))
    extra_stages = [k for k in transport.keys() if k not in ("place", "slot_check", "unload")]
    for stage in extra_stages:
        lines.append(f"[{stage}] {transport.get(stage)}")
    return "\n".join(lines)

