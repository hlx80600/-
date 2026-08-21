"""
产量统计：
- CT：两次下料完成之间的周期时间（秒）
- UPH：按最近 CT 换算的每小时产量（3600/CT），以及滚动平均 UPH
- 每小时产量：按自然小时累计件数
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Deque, Dict, List, Optional, Tuple


@dataclass
class ProductionStats:
    """下料完成时调用 record_unload()。"""

    _lock: RLock = field(default_factory=RLock)
    total_count: int = 0
    last_unload_ts: Optional[float] = None
    last_ct_s: float = 0.0
    # 最近若干次 CT，用于平均 UPH
    _ct_window: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    # 按小时桶：key="YYYY-MM-DD HH" -> count
    hourly: Dict[str, int] = field(default_factory=dict)
    # 当日按小时 0..23
    today_date: str = ""
    today_by_hour: List[int] = field(default_factory=lambda: [0] * 24)

    def reset(self) -> None:
        with self._lock:
            self.total_count = 0
            self.last_unload_ts = None
            self.last_ct_s = 0.0
            self._ct_window.clear()
            self.hourly.clear()
            self.today_date = ""
            self.today_by_hour = [0] * 24

    def record_unload(self, now: Optional[float] = None) -> None:
        """Station5 下料皮带放料完成时调用。"""
        import time

        t = time.time() if now is None else float(now)
        with self._lock:
            if self.last_unload_ts is not None:
                ct = t - self.last_unload_ts
                if ct > 0.05:  # 忽略异常过短
                    self.last_ct_s = ct
                    self._ct_window.append(ct)
            self.last_unload_ts = t
            self.total_count += 1

            dt = datetime.fromtimestamp(t)
            day = dt.strftime("%Y-%m-%d")
            hour_key = dt.strftime("%Y-%m-%d %H")
            self.hourly[hour_key] = self.hourly.get(hour_key, 0) + 1

            if self.today_date != day:
                self.today_date = day
                self.today_by_hour = [0] * 24
            self.today_by_hour[dt.hour] += 1

    @property
    def uph_instant(self) -> float:
        """按最近一次 CT 换算：UPH = 3600 / CT。"""
        with self._lock:
            if self.last_ct_s <= 0:
                return 0.0
            return 3600.0 / self.last_ct_s

    @property
    def uph_avg(self) -> float:
        """最近若干次 CT 的平均 UPH。"""
        with self._lock:
            if not self._ct_window:
                return 0.0
            avg_ct = sum(self._ct_window) / len(self._ct_window)
            if avg_ct <= 0:
                return 0.0
            return 3600.0 / avg_ct

    def current_hour_count(self) -> int:
        with self._lock:
            key = datetime.now().strftime("%Y-%m-%d %H")
            return int(self.hourly.get(key, 0))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total": self.total_count,
                "last_ct_s": self.last_ct_s,
                "uph_instant": self.uph_instant if self.last_ct_s > 0 else 0.0,
                "uph_avg": (3600.0 / (sum(self._ct_window) / len(self._ct_window))) if self._ct_window else 0.0,
                "hour_count": self.hourly.get(datetime.now().strftime("%Y-%m-%d %H"), 0),
                "today_date": self.today_date or datetime.now().strftime("%Y-%m-%d"),
                "today_by_hour": list(self.today_by_hour),
                "hour_now": datetime.now().hour,
            }

    def recent_hours(self, n: int = 12) -> List[Tuple[str, int]]:
        """最近 n 个整点产量（用于直方图横轴标签）。"""
        from datetime import timedelta

        with self._lock:
            now = datetime.now()
            out: List[Tuple[str, int]] = []
            for i in range(n - 1, -1, -1):
                t = now - timedelta(hours=i)
                key = t.strftime("%Y-%m-%d %H")
                cnt = int(self.hourly.get(key, 0))
                out.append((t.strftime("%H:00"), cnt))
            return out
