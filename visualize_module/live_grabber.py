"""相机监控后台取流：从相机流线程缓存同步到 vision.last_raw。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, Sequence

from .frames import CAM_IDS, copy_bgr


class LiveGrabber:
    def __init__(
        self,
        vision: Any,
        cameras: Any,
        *,
        skip_cam_fn: Optional[Callable[[], str]] = None,
        cam_ids: Optional[Sequence[str]] = None,
    ) -> None:
        self.vision = vision
        self.cameras = cameras
        self.skip_cam_fn = skip_cam_fn
        self.cam_ids = tuple(cam_ids) if cam_ids else CAM_IDS
        self._enabled = False
        self._stop = threading.Event()
        self._stop.set()
        self._thread: threading.Thread | None = None

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
            name="viz-live-grab",
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

    def _skip_cam(self) -> str:
        if not callable(self.skip_cam_fn):
            return ""
        try:
            return str(self.skip_cam_fn() or "")
        except Exception:
            return ""

    def _adopt_last_color(self, cid: str) -> None:
        cam = self.cameras.get(cid) if self.cameras is not None else None
        if cam is None:
            return
        img = getattr(cam, "last_color", None)
        if img is None:
            return
        self.vision.last_raw[cid] = copy_bgr(img)
        self.vision.last_raw_ts[cid] = time.time()

    def _loop(self) -> None:
        """读相机后台流缓存，不抢 grab 锁；尽量跟满相机帧率。"""
        while not self._stop.is_set() and self._enabled:
            for cid in self.cam_ids:
                if self._stop.is_set() or not self._enabled:
                    break
                self._adopt_last_color(cid)
            time.sleep(0.001)
