"""相机监控后台取流：四路连续 grab，避让视觉调试当前相机。"""

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
        while not self._stop.is_set() and self._enabled:
            skip = self._skip_cam()
            any_ok = False
            for cid in self.cam_ids:
                if self._stop.is_set() or not self._enabled:
                    break
                if cid == skip:
                    self._adopt_last_color(cid)
                    continue
                try:
                    img = self.vision.grab_raw(cid, wait_s=0.0)
                    if img is not None:
                        any_ok = True
                except Exception:
                    pass
                time.sleep(0.008)
            if not any_ok:
                time.sleep(0.03)
            else:
                time.sleep(0.01)
