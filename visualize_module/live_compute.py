"""监控实时推演：只用缓存帧，忙则跳过，不抢相机 grab。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, Sequence

from .frames import CAM_IDS


class LiveComputeLoop:
    """后台轮询四路 compute_monitor(from_cache=True)。

    - 同一时刻每路最多一个任务（忙则跳过，保证最新帧优先）
    - 路与路之间短休眠，把带宽留给 LiveGrabber
    """

    def __init__(
        self,
        vision: Any,
        *,
        cam_ids: Optional[Sequence[str]] = None,
        on_done: Optional[Callable[[str, str], None]] = None,
        round_sleep_s: float = 0.12,
        cam_sleep_s: float = 0.04,
    ) -> None:
        self.vision = vision
        self.cam_ids = tuple(cam_ids) if cam_ids else CAM_IDS
        self.on_done = on_done
        self.round_sleep_s = float(round_sleep_s)
        self.cam_sleep_s = float(cam_sleep_s)
        self._enabled = False
        self._stop = threading.Event()
        self._stop.set()
        self._thread: threading.Thread | None = None
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        self._last_msg: dict[str, str] = {}

    @property
    def running(self) -> bool:
        th = self._thread
        return bool(self._enabled and th is not None and th.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._enabled = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="viz-live-compute",
        )
        self._thread.start()

    def stop(self) -> None:
        self._enabled = False
        self._stop.set()

    def set_enabled(self, on: bool) -> None:
        if on:
            self.start()
        else:
            self.stop()

    def is_busy(self, cam_id: str = "") -> bool:
        with self._lock:
            if cam_id:
                return cam_id in self._busy
            return bool(self._busy)

    def status_text(self) -> str:
        with self._lock:
            busy = sorted(self._busy)
        if not self._enabled:
            return "推演：关"
        if busy:
            return "推演中：" + ",".join(busy)
        return "推演：实时(缓存帧)"

    def kick_once(self, cam_id: str) -> bool:
        """手动插队推演一路；若该路正忙则返回 False。"""
        with self._lock:
            if cam_id in self._busy:
                return False
            self._busy.add(cam_id)

        def _run() -> None:
            msg = ""
            try:
                msg = self.vision.compute_monitor(cam_id, from_cache=True) or ""
            except Exception as e:
                msg = str(e)
            self._last_msg[cam_id] = msg
            with self._lock:
                self._busy.discard(cam_id)
            if callable(self.on_done):
                try:
                    self.on_done(cam_id, msg)
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True, name=f"viz-compute-{cam_id}").start()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set() and self._enabled:
            for cid in self.cam_ids:
                if self._stop.is_set() or not self._enabled:
                    break
                with self._lock:
                    if cid in self._busy:
                        continue
                    self._busy.add(cid)
                msg = ""
                try:
                    msg = self.vision.compute_monitor(cid, from_cache=True) or ""
                except Exception as e:
                    msg = str(e)
                self._last_msg[cid] = msg
                with self._lock:
                    self._busy.discard(cid)
                if callable(self.on_done):
                    try:
                        self.on_done(cid, msg)
                    except Exception:
                        pass
                time.sleep(self.cam_sleep_s)
            time.sleep(self.round_sleep_s)
