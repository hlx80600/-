"""错误日志落盘 + 黑匣子。

退出程序、甚至异常崩溃后，仍可在项目 ``logs/`` 目录（或 HMI「报警记录」）查阅：

- ``app_YYYY-MM-DD.log``：日常运行日志（文件名带日期）
- ``error_YYYY-MM-DD.log``：WARNING 及以上
- ``errors_YYYY-MM-DD.jsonl``：报警 / 错误 / 崩溃的一条条记录
- ``blackbox_YYYY-MM-DD.jsonl``：故障前后运行轨迹
- ``dumps/YYYYMMDD_HHMMSS_mmm_*.json``：ERROR/报警/崩溃时的内存圈快照
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
from datetime import datetime, timedelta
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
_bb_day = ""
_err_day = ""


def _day_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _dated_jsonl(kind: str, day: str | None = None) -> Path:
    """errors / blackbox 的当日文件，例如 errors_2026-08-28.jsonl。"""
    return _LOG_DIR / f"{kind}_{day or _day_stamp()}.jsonl"


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
    """单文件过大则 foo.jsonl → foo.jsonl.1 → .2 … 腾出当前文件。"""
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


def _prune_old_files(pattern: str, keep_days: int) -> None:
    """按修改时间删过期的 dated 日志。"""
    cutoff = datetime.now() - timedelta(days=max(1, int(keep_days)))
    cutoff_ts = cutoff.timestamp()
    try:
        for path in _LOG_DIR.glob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff_ts:
                    path.unlink()
            except OSError:
                pass
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
    global _err_fp, _err_day
    day = _day_stamp()
    path = _dated_jsonl("errors", day)
    _rotate_if_huge(path, max_bytes=_ERRORS_MAX_BYTES, keep=_ERRORS_KEEP)
    if _err_fp is None or getattr(_err_fp, "closed", True) or _err_day != day:
        if _err_fp is not None:
            try:
                _err_fp.close()
            except Exception:
                pass
        _err_fp = _open_append(path)
        _err_day = day
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
        _err_day = day
    _write_jsonl(_err_fp, rec, fsync=fsync)


def _append_blackbox(rec: dict[str, Any], *, fsync: bool = False) -> None:
    global _bb_fp, _bb_day
    day = _day_stamp()
    path = _dated_jsonl("blackbox", day)
    _rotate_if_huge(path, max_bytes=_BLACKBOX_MAX_BYTES, keep=_BLACKBOX_KEEP)
    if _bb_fp is None or getattr(_bb_fp, "closed", True) or _bb_day != day:
        if _bb_fp is not None:
            try:
                _bb_fp.close()
            except Exception:
                pass
        _bb_fp = _open_append(path)
        _bb_day = day
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
        _bb_day = day
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
    global _bb_fp, _err_fp, _bb_day, _err_day
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
    _bb_day = ""
    _err_day = ""


class _BlackboxHandler(logging.Handler):
    """把日志写入内存圈 + blackbox_日期.jsonl；WARNING+ 另写入 errors_日期.jsonl。"""

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
                # 报警由 record_alarm 写 errors_日期.jsonl / dump，避免重复
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


class _MsFormatter(logging.Formatter):
    """日志行时间带毫秒：2026-08-28 16:38:05.123。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"


class _DatedFileHandler(logging.Handler):
    """按日写 ``stem_YYYY-MM-DD.ext``，文件名自带日期。"""

    def __init__(self, *, stem: str, ext: str = "log", encoding: str = "utf-8") -> None:
        super().__init__()
        self._stem = stem
        self._ext = ext
        self._encoding = encoding
        self._day = ""
        self.stream: Any = None

    def _reopen(self) -> None:
        day = _day_stamp()
        if self.stream is not None and day == self._day:
            return
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
        self._day = day
        path = _LOG_DIR / f"{self._stem}_{day}.{self._ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(path, "a", encoding=self._encoding, buffering=1)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._reopen()
            if self.stream is None:
                return
            self.stream.write(self.format(record) + "\n")
            self.stream.flush()
            if record.levelno >= logging.ERROR:
                os.fsync(self.stream.fileno())
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        super().close()


def install() -> Path:
    """安装控制台 + 文件日志 + 黑匣子。可重复调用。"""
    global _installed, _bb_fp, _err_fp, _bb_day, _err_day
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _DUMP_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old_files("app_*.log", 14)
    _prune_old_files("error_*.log", 30)
    _prune_old_files("errors_*.jsonl*", 30)
    _prune_old_files("blackbox_*.jsonl*", 14)
    # 旧版无日期文件名（升级前留下的）同样按天数清
    _prune_old_files("app.log", 14)
    _prune_old_files("app.log.*", 14)
    _prune_old_files("error.log", 30)
    _prune_old_files("error.log.*", 30)
    _prune_old_files("errors.jsonl*", 30)
    _prune_old_files("blackbox.jsonl*", 14)

    fmt = _MsFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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

        app_h = _DatedFileHandler(stem="app", ext="log")
        app_h.setLevel(logging.INFO)
        app_h.setFormatter(fmt)
        root.addHandler(app_h)

        err_h = _DatedFileHandler(stem="error", ext="log")
        err_h.setLevel(logging.WARNING)
        err_h.setFormatter(fmt)
        root.addHandler(err_h)

        bb = _BlackboxHandler()
        root.addHandler(bb)

        sys.excepthook = _excepthook
        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_excepthook
        atexit.register(_atexit)

        day = _day_stamp()
        _bb_fp = _open_append(_dated_jsonl("blackbox", day))
        _err_fp = _open_append(_dated_jsonl("errors", day))
        _bb_day = day
        _err_day = day
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


def _iter_jsonl_files(kind: str) -> list[Path]:
    """kind=errors/blackbox：当日 dated 文件 + 体积滚动备份 + 旧版无日期文件，新→旧。"""
    found: list[Path] = []
    try:
        found.extend(_LOG_DIR.glob(f"{kind}_????-??-??.jsonl"))
        found.extend(_LOG_DIR.glob(f"{kind}_????-??-??.jsonl.*"))
        legacy = _LOG_DIR / f"{kind}.jsonl"
        if legacy.is_file():
            found.append(legacy)
        found.extend(_LOG_DIR.glob(f"{kind}.jsonl.*"))
    except OSError:
        return []
    uniq: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    uniq.sort(key=_mtime, reverse=True)
    return uniq


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
    for path in _iter_jsonl_files("errors"):
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
    for path in _iter_jsonl_files("blackbox"):
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
