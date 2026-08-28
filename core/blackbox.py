"""错误日志落盘 + 黑匣子。

退出程序、甚至异常崩溃后，仍可在项目 ``logs/`` 目录（或 HMI「报警记录」）查阅：

- ``app.log``：日常运行日志（按天滚动）
- ``error.log``：WARNING 及以上（按天滚动）
- ``errors.jsonl``：报警 / 错误 / 崩溃的一条条记录（黑匣子故障本）
- ``blackbox.jsonl``：故障前后一段时间的运行轨迹（环形文件）
- ``dumps/``：每次 ERROR/报警/崩溃时，把内存环形缓冲打成一份快照
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import threading
import traceback
from collections import deque
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Deque, Optional

_ROOT = Path(__file__).resolve().parents[1]
_LOG_DIR = _ROOT / "logs"
_DUMP_DIR = _LOG_DIR / "dumps"

_SESSION_ID = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"

# 黑匣子内存圈：故障 dump 时带上最近若干条
_RING_MAX = 500
_BLACKBOX_MAX_BYTES = 8 * 1024 * 1024
_BLACKBOX_KEEP = 3
_ERRORS_MAX_BYTES = 16 * 1024 * 1024
_ERRORS_KEEP = 5
_DUMP_KEEP = 40

_lock = threading.RLock()
_ring: Deque[dict[str, Any]] = deque(maxlen=_RING_MAX)
_snapshot_fn: Optional[Callable[[], dict[str, Any]]] = None
_installed = False
_bb_fp: Optional[Any] = None
_err_fp: Optional[Any] = None


def log_dir() -> Path:
    """日志根目录（项目下 logs/）。"""
    return _LOG_DIR


def session_id() -> str:
    return _SESSION_ID


def register_snapshot(fn: Callable[[], dict[str, Any]] | None) -> None:
    """注册运行快照回调（工位步号、机械臂路径等），供报警/崩溃 dump。"""
    global _snapshot_fn
    _snapshot_fn = fn


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _safe_snapshot() -> dict[str, Any]:
    fn = _snapshot_fn
    if fn is None:
        return {}
    try:
        data = fn()
        return data if isinstance(data, dict) else {"value": str(data)}
    except Exception as e:
        return {"snapshot_error": str(e)}


def _open_append(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a", encoding="utf-8", buffering=1)


def _rotate_if_huge(path: Path, *, max_bytes: int, keep: int) -> None:
    """单文件过大则 blackbox.jsonl → .1 → .2 … 腾出当前文件。"""
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    for idx in range(keep, 0, -1):
        src = path if idx == 1 else Path(f"{path}.{idx - 1}")
        dst = Path(f"{path}.{idx}")
        try:
            if dst.exists():
                dst.unlink()
            if src.exists():
                src.rename(dst)
        except OSError:
            pass


def _prune_dumps() -> None:
    try:
        files = sorted(
            _DUMP_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[_DUMP_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _write_jsonl(fp: Any, rec: dict[str, Any], *, fsync: bool = False) -> None:
    fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    fp.flush()
    if fsync:
        try:
            os.fsync(fp.fileno())
        except OSError:
            pass


def _append_errors(rec: dict[str, Any], *, fsync: bool = True) -> None:
    global _err_fp
    path = _LOG_DIR / "errors.jsonl"
    _rotate_if_huge(path, max_bytes=_ERRORS_MAX_BYTES, keep=_ERRORS_KEEP)
    if _err_fp is None or getattr(_err_fp, "closed", True):
        _err_fp = _open_append(path)
    # 滚动后句柄可能仍指向旧文件名对应的 inode；体积刚超限时重新打开
    try:
        same = Path(_err_fp.name).resolve() == path.resolve() and path.is_file()
    except OSError:
        same = False
    if not same:
        try:
            _err_fp.close()
        except Exception:
            pass
        _err_fp = _open_append(path)
    _write_jsonl(_err_fp, rec, fsync=fsync)


def _append_blackbox(rec: dict[str, Any], *, fsync: bool = False) -> None:
    global _bb_fp
    path = _LOG_DIR / "blackbox.jsonl"
    _rotate_if_huge(path, max_bytes=_BLACKBOX_MAX_BYTES, keep=_BLACKBOX_KEEP)
    if _bb_fp is None or getattr(_bb_fp, "closed", True):
        _bb_fp = _open_append(path)
    try:
        same = Path(_bb_fp.name).resolve() == path.resolve() and path.is_file()
    except OSError:
        same = False
    if not same:
        try:
            _bb_fp.close()
        except Exception:
            pass
        _bb_fp = _open_append(path)
    _write_jsonl(_bb_fp, rec, fsync=fsync)


def _dump_ring(trigger: str, rec: dict[str, Any]) -> None:
    """把内存圈 + 当前记录写成 dumps 快照，关机后仍能打开。"""
    _DUMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = _DUMP_DIR / f"{stamp}_{trigger}.json"
    payload = {
        "ts": rec.get("ts") or _now_text(),
        "trigger": trigger,
        "session_id": _SESSION_ID,
        "record": rec,
        "snapshot": rec.get("snapshot") or _safe_snapshot(),
        "ring": list(_ring),
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        return
    _prune_dumps()


def record_alarm(
    code: str,
    message: str,
    station: str = "",
    step: int = 0,
    *,
    popup: bool = True,
    active: bool = True,
) -> None:
    """报警落盘（错误日志 + 黑匣子 dump）。"""
    rec: dict[str, Any] = {
        "ts": _now_text(),
        "kind": "alarm",
        "session_id": _SESSION_ID,
        "code": str(code),
        "message": str(message),
        "station": str(station),
        "step": int(step),
        "popup": bool(popup),
        "active": bool(active),
        "snapshot": _safe_snapshot(),
    }
    with _lock:
        _ring.append(rec)
        _append_blackbox(rec, fsync=True)
        _append_errors(rec, fsync=True)
        if active:
            _dump_ring("alarm", rec)


def record_crash(exc_type: type, exc: BaseException, tb: Any) -> None:
    """未捕获异常：写入错误日志并 dump 黑匣子。"""
    rec: dict[str, Any] = {
        "ts": _now_text(),
        "kind": "crash",
        "session_id": _SESSION_ID,
        "exc_type": getattr(exc_type, "__name__", str(exc_type)),
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc_type, exc, tb)),
        "snapshot": _safe_snapshot(),
    }
    with _lock:
        _ring.append(rec)
        _append_blackbox(rec, fsync=True)
        _append_errors(rec, fsync=True)
        _dump_ring("crash", rec)


def mark_session_end() -> None:
    """正常退出时打一条会话结束，便于对照两次启动。"""
    rec = {
        "ts": _now_text(),
        "kind": "session",
        "event": "stop",
        "session_id": _SESSION_ID,
    }
    with _lock:
        _ring.append(rec)
        try:
            _append_blackbox(rec, fsync=True)
        except Exception:
            pass
        _flush_files()


def _flush_files() -> None:
    for fp in (_bb_fp, _err_fp):
        if fp is None:
            continue
        try:
            fp.flush()
            os.fsync(fp.fileno())
        except Exception:
            pass


def _close_files() -> None:
    global _bb_fp, _err_fp
    _flush_files()
    for fp in (_bb_fp, _err_fp):
        if fp is None:
            continue
        try:
            fp.close()
        except Exception:
            pass
    _bb_fp = None
    _err_fp = None


class _BlackboxHandler(logging.Handler):
    """把日志写入内存圈 + blackbox.jsonl；WARNING+ 另写入 errors.jsonl。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._emitting = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._emitting:
            return
        # 报警走 record_alarm；本模块的 info 不再回写，避免套娃
        if record.name in ("core.alarm",):
            return
        self._emitting = True
        try:
            msg = record.getMessage()
            rec: dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created).strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )[:-3],
                "kind": "log",
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
                "session_id": _SESSION_ID,
            }
            if record.exc_info and record.exc_info[0] is not None:
                rec["traceback"] = "".join(traceback.format_exception(*record.exc_info))
            fsync = record.levelno >= logging.ERROR
            with _lock:
                _ring.append(rec)
                _append_blackbox(rec, fsync=fsync)
                # 报警由 record_alarm 写 errors.jsonl / dump，避免重复
                if record.name in ("core.alarm", "core.blackbox", "blackbox"):
                    return
                if record.levelno >= logging.WARNING:
                    rec_err = dict(rec)
                    rec_err["kind"] = (
                        "error" if record.levelno >= logging.ERROR else "warning"
                    )
                    rec_err["snapshot"] = _safe_snapshot()
                    _append_errors(rec_err, fsync=fsync)
                    if record.levelno >= logging.ERROR:
                        _dump_ring("error", rec_err)
        except Exception:
            pass
        finally:
            self._emitting = False


def _excepthook(exc_type: type, exc: BaseException, tb: Any) -> None:
    try:
        logging.getLogger("blackbox").critical(
            "未捕获异常 %s: %s",
            getattr(exc_type, "__name__", exc_type),
            exc,
            exc_info=(exc_type, exc, tb),
        )
        record_crash(exc_type, exc, tb)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args: Any) -> None:
    try:
        record_crash(args.exc_type, args.exc_value, args.exc_traceback)
        logging.getLogger("blackbox").critical(
            "线程 %s 未捕获异常 %s: %s",
            getattr(args, "thread", None),
            getattr(args.exc_type, "__name__", args.exc_type),
            args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    except Exception:
        pass


class _FlushTimedRotatingFileHandler(TimedRotatingFileHandler):
    """每次 emit 后 flush，避免异常退出丢掉最后几行。"""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
            if record.levelno >= logging.ERROR and self.stream is not None:
                os.fsync(self.stream.fileno())
        except Exception:
            pass


def install() -> Path:
    """安装控制台 + 文件日志 + 黑匣子。可重复调用。"""
    global _installed, _bb_fp, _err_fp
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _DUMP_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not _installed:
        # 清掉 basicConfig 可能留下的默认 handler，避免重复打印
        for h in list(root.handlers):
            root.removeHandler(h)

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        root.addHandler(console)

        app_h = _FlushTimedRotatingFileHandler(
            str(_LOG_DIR / "app.log"),
            when="midnight",
            backupCount=14,
            encoding="utf-8",
            delay=False,
        )
        app_h.setLevel(logging.INFO)
        app_h.setFormatter(fmt)
        app_h.suffix = "%Y-%m-%d"
        root.addHandler(app_h)

        err_h = _FlushTimedRotatingFileHandler(
            str(_LOG_DIR / "error.log"),
            when="midnight",
            backupCount=30,
            encoding="utf-8",
            delay=False,
        )
        err_h.setLevel(logging.WARNING)
        err_h.setFormatter(fmt)
        err_h.suffix = "%Y-%m-%d"
        root.addHandler(err_h)

        bb = _BlackboxHandler()
        root.addHandler(bb)

        sys.excepthook = _excepthook
        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_excepthook
        atexit.register(_atexit)

        _bb_fp = _open_append(_LOG_DIR / "blackbox.jsonl")
        _err_fp = _open_append(_LOG_DIR / "errors.jsonl")
        start = {
            "ts": _now_text(),
            "kind": "session",
            "event": "start",
            "session_id": _SESSION_ID,
            "pid": os.getpid(),
            "argv": list(sys.argv),
        }
        with _lock:
            _ring.append(start)
            _append_blackbox(start, fsync=True)
        _installed = True
        logging.getLogger(__name__).info(
            "日志与黑匣子已启用 dir=%s session=%s", _LOG_DIR, _SESSION_ID
        )
    return _LOG_DIR


def _atexit() -> None:
    try:
        mark_session_end()
    except Exception:
        pass
    _close_files()


def _iter_jsonl_files(stem: str) -> list[Path]:
    """当前文件 + 滚动备份，新的在前。"""
    base = _LOG_DIR / stem
    files: list[Path] = []
    if base.is_file():
        files.append(base)
    # .1 较新，.N 较旧
    extras: list[tuple[int, Path]] = []
    for p in _LOG_DIR.glob(stem + ".*"):
        suf = p.name[len(stem) + 1 :]
        if suf.isdigit():
            extras.append((int(suf), p))
    extras.sort(key=lambda x: x[0])
    files.extend(p for _, p in extras)
    return files


def _tail_text_lines(path: Path, *, max_bytes: int = 2_000_000) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > max_bytes:
                fp.seek(-max_bytes, os.SEEK_END)
            data = fp.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def read_error_records(limit: int = 300) -> list[dict[str, Any]]:
    """读落盘错误/报警（新→旧），程序退出后再开也能读。"""
    out: list[dict[str, Any]] = []
    for path in _iter_jsonl_files("errors.jsonl"):
        for line in reversed(_tail_text_lines(path)):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = {"ts": "", "kind": "raw", "message": line}
            if not isinstance(rec, dict):
                continue
            kind = str(rec.get("kind") or "")
            if kind in ("session",):
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out


def read_blackbox_records(limit: int = 250) -> list[dict[str, Any]]:
    """读黑匣子轨迹（新→旧）。"""
    out: list[dict[str, Any]] = []
    for path in _iter_jsonl_files("blackbox.jsonl"):
        for line in reversed(_tail_text_lines(path, max_bytes=1_500_000)):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = {"ts": "", "kind": "raw", "message": line}
            if isinstance(rec, dict):
                out.append(rec)
            if len(out) >= limit:
                return out
    return out


def format_error_line(rec: dict[str, Any]) -> str:
    """HMI 列表用一行摘要。"""
    ts = str(rec.get("ts") or "")
    kind = str(rec.get("kind") or "log").upper()
    if kind == "ALARM":
        return (
            f"{ts} [报警] {rec.get('code', '')} "
            f"{rec.get('station', '')}@{rec.get('step', '')} {rec.get('message', '')}"
        )
    if kind == "CRASH":
        return f"{ts} [崩溃] {rec.get('exc_type', '')}: {rec.get('message', '')}"
    level = rec.get("level") or kind
    logger = rec.get("logger") or ""
    msg = rec.get("message") or ""
    if logger:
        return f"{ts} [{level}] {logger}: {msg}"
    return f"{ts} [{level}] {msg}"
