"""
手机 Web 监控/控制：同一台工控机局域网内用浏览器打开。

启动：main.py 读取 system.mobile_web；或独立调试。
默认 http://<本机局域网IP>:8765/
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from core.memory import MEMORY_LABELS

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def lan_ip() -> str:
    """尽量取本机局域网 IP（供手机访问提示）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def build_snapshot(coord) -> Dict[str, Any]:
    """手机页轮询用的状态快照。"""
    ctx = coord.ctx
    m = ctx.machine
    gvl = ctx.gvl
    mem = ctx.memory.snapshot()
    alarm = None
    if ctx.alarms.active:
        a = ctx.alarms.active
        alarm = {
            "code": a.code,
            "message": a.message,
            "station": a.station,
            "step": a.step,
        }
    stations = []
    for st in coord.stations:
        stations.append(
            {
                "name": st.name,
                "status": st.status_text(),
                "step": st.current_step_name(),
                "busy": bool(st.busy),
            }
        )
    links = []
    try:
        links = ctx.device_link_snapshot()
    except Exception:
        pass
    prod = {}
    try:
        prod = ctx.production.snapshot()
    except Exception:
        pass
    press = {}
    try:
        press = ctx.press.status_dict() if hasattr(ctx.press, "status_dict") else {
            "rotate_done": ctx.press.rotate_done,
            "press_done": ctx.press.press_done,
            "connected": ctx.press.connected,
        }
    except Exception:
        press = {
            "rotate_done": getattr(ctx.press, "rotate_done", None),
            "press_done": getattr(ctx.press, "press_done", None),
        }
    belt_di = int(ctx.cfg.get("robots", {}).get("robot1", {}).get("di_belt_sensor", 0))
    try:
        belt_on = bool(ctx.robot1.get_di(belt_di))
    except Exception:
        belt_on = False
    dry = getattr(ctx, "dry_run", None)
    return {
        "ok": True,
        "state": m.state.name,
        "mode": m.mode.name,
        "init_done": bool(gvl.Main.InitDone or m.init_ok),
        "initializing": bool(gvl.Main.Initializing),
        "init_step": int(gvl.Main.Init_Auto or 0),
        "init_message": str(getattr(ctx, "init_message", "") or ""),
        "running": m.state.name == "RUNNING",
        "paused": m.state.name == "PAUSED",
        "alarm": alarm,
        "memory": {str(k): bool(v) for k, v in mem.items()},
        "memory_labels": {str(k): v for k, v in MEMORY_LABELS.items()},
        "stations": stations,
        "links": links,
        "production": prod,
        "press": press,
        "belt_di": belt_di,
        "belt_on": belt_on,
        "dry_run": bool(dry.enabled) if dry else False,
        "robot1": {
            "name": ctx.robot1.name,
            "mock": bool(ctx.robot1.use_mock),
            "connected": bool(ctx.robot1.connected),
            "vel": float(ctx.robot1.vel),
            "tool": int(ctx.robot1.tool),
            "payload_mode": ctx.robot1.payload_mode() if hasattr(ctx.robot1, "payload_mode") else "",
        },
        "robot2": {
            "name": ctx.robot2.name,
            "mock": bool(ctx.robot2.use_mock),
            "connected": bool(ctx.robot2.connected),
            "vel": float(ctx.robot2.vel),
            "tool": int(ctx.robot2.tool),
            "payload_mode": ctx.robot2.payload_mode() if hasattr(ctx.robot2, "payload_mode") else "",
        },
        "mock_status": ctx.mock_status_text() if hasattr(ctx, "mock_status_text") else "",
    }


class MobileWebServer:
    def __init__(self, coord, host: str = "0.0.0.0", port: int = 8765, token: str = ""):
        self.coord = coord
        self.host = host
        self.port = int(port)
        self.token = str(token or "").strip()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def start(self) -> str:
        if self._thread and self._thread.is_alive():
            return self.access_url()

        server = self
        coord = self.coord

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # noqa: N802
                log.debug("mobile_web: " + fmt, *args)

            def _check_token(self) -> bool:
                if not server.token:
                    return True
                # Header 或 query
                auth = self.headers.get("X-Token") or self.headers.get("Authorization") or ""
                if auth.lower().startswith("bearer "):
                    auth = auth[7:].strip()
                if auth == server.token:
                    return True
                q = parse_qs(urlparse(self.path).query)
                if (q.get("token") or [""])[0] == server.token:
                    return True
                return False

            def _json(self, code: int, obj: Any) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _html(self, path: Path) -> None:
                data = path.read_bytes()
                self.send_response(200)
                ctype = "text/html; charset=utf-8"
                if path.suffix == ".js":
                    ctype = "application/javascript; charset=utf-8"
                elif path.suffix == ".css":
                    ctype = "text/css; charset=utf-8"
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                if path in ("/", "/index.html"):
                    self._html(STATIC_DIR / "index.html")
                    return
                if path == "/api/ping":
                    self._json(200, {"ok": True, "need_token": bool(server.token)})
                    return
                if not self._check_token():
                    self._json(401, {"ok": False, "error": "需要口令 token"})
                    return
                if path == "/api/status":
                    try:
                        self._json(200, build_snapshot(coord))
                    except Exception as e:
                        log.exception("mobile status")
                        self._json(500, {"ok": False, "error": str(e)})
                    return
                self._json(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._check_token():
                    self._json(401, {"ok": False, "error": "需要口令 token"})
                    return
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                try:
                    result = server.handle_command(path, payload)
                    self._json(200 if result.get("ok") else 400, result)
                except Exception as e:
                    log.exception("mobile cmd %s", path)
                    self._json(500, {"ok": False, "error": str(e)})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="MobileWeb", daemon=True)
        self._thread.start()
        url = self.access_url()
        log.info("[手机Web] 已启动 %s  （局域网用手机浏览器打开）token=%s", url, "已设置" if self.token else "无")
        return url

    def access_url(self) -> str:
        ip = lan_ip() if self.host in ("0.0.0.0", "") else self.host
        return f"http://{ip}:{self.port}/"

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None
        self._thread = None

    def handle_command(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        coord = self.coord
        ctx = coord.ctx
        with self._lock:
            if path == "/api/cmd/init":
                err = coord.cmd_init()
                return {"ok": err is None, "error": err}
            if path == "/api/cmd/start":
                err = coord.cmd_start()
                return {"ok": err is None, "error": err}
            if path == "/api/cmd/pause":
                coord.cmd_pause()
                return {"ok": True}
            if path == "/api/cmd/stop":
                coord.cmd_stop()
                return {"ok": True}
            if path == "/api/cmd/estop":
                coord.cmd_estop()
                return {"ok": True}
            if path == "/api/cmd/reset_estop":
                coord.cmd_reset_estop()
                return {"ok": True}
            if path == "/api/cmd/alarm_reset":
                tips = coord.cmd_alarm_reset()
                return {"ok": True, "tips": tips}
            if path == "/api/cmd/dry_run":
                on = bool(payload.get("enabled", True))
                if on:
                    ctx.dry_run.enable()
                else:
                    ctx.dry_run.disable()
                return {"ok": True, "dry_run": ctx.dry_run.enabled}
            if path == "/api/cmd/belt":
                on = bool(payload.get("on", True))
                belt_di = int(ctx.cfg.get("robots", {}).get("robot1", {}).get("di_belt_sensor", 0))
                ctx.robot1.set_di_force_mock(belt_di, True)
                ctx.robot1.set_di_mock(belt_di, on)
                return {"ok": True, "belt_on": on}
            return {"ok": False, "error": f"未知命令 {path}"}


def start_mobile_web_from_cfg(coord) -> Optional[MobileWebServer]:
    cfg = (coord.ctx.cfg.get("system") or {}).get("mobile_web") or {}
    if not bool(cfg.get("enabled", False)):
        log.info("[手机Web] 未启用（system.mobile_web.enabled=false）")
        return None
    host = str(cfg.get("host", "0.0.0.0"))
    port = int(cfg.get("port", 8765))
    token = str(cfg.get("token", "") or "")
    srv = MobileWebServer(coord, host=host, port=port, token=token)
    try:
        url = srv.start()
        print(f"\n★ 手机监控地址: {url}")
        if token:
            print(f"★ 访问口令 token: {token}\n")
        else:
            print("★ 未设置 token（局域网内任何人可控制，建议在 yaml 设置 mobile_web.token）\n")
    except OSError as e:
        log.error("[手机Web] 启动失败: %s", e)
        return None
    return srv
