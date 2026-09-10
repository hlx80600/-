"""上料/下料夹爪组：主爪 + 配置为跟随的电机同时开合。"""

from __future__ import annotations

import time
from typing import Any


class GripperGroup:
    """对外仍当一只夹爪用；open/close/poll_done 作用到全部成员。"""

    def __init__(self, primary: Any, members: list[Any]) -> None:
        object.__setattr__(self, "primary", primary)
        uniq: list[Any] = []
        seen: set[int] = set()
        for g in [primary, *list(members or [])]:
            if g is None:
                continue
            gid = id(g)
            if gid in seen:
                continue
            seen.add(gid)
            uniq.append(g)
        if not uniq and primary is not None:
            uniq = [primary]
        object.__setattr__(self, "members", uniq)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.primary, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("primary", "members"):
            object.__setattr__(self, name, value)
            return
        setattr(self.primary, name, value)

    @property
    def busy(self) -> bool:
        return any(bool(getattr(g, "busy", False)) for g in self.members)

    @property
    def open_done(self) -> bool:
        return bool(self.members) and all(
            bool(getattr(g, "open_done", False)) for g in self.members
        )

    @property
    def close_done(self) -> bool:
        return bool(self.members) and all(
            bool(getattr(g, "close_done", False)) for g in self.members
        )

    @property
    def connected(self) -> bool:
        if not self.members:
            return False
        return all(
            bool(getattr(g, "use_mock", False) or getattr(g, "connected", False))
            for g in self.members
        )

    @connected.setter
    def connected(self, value: bool) -> None:
        self.primary.connected = bool(value)

    @property
    def last_error(self) -> str:
        parts = [
            str(getattr(g, "last_error", "") or "").strip()
            for g in self.members
        ]
        return "；".join(p for p in parts if p)

    @last_error.setter
    def last_error(self, value: str) -> None:
        self.primary.last_error = str(value or "")

    @property
    def last_ok(self) -> bool:
        return all(bool(getattr(g, "last_ok", True)) for g in self.members)

    @last_ok.setter
    def last_ok(self, value: bool) -> None:
        self.primary.last_ok = bool(value)

    def open(self) -> None:
        """非阻塞：组内同时发张开。"""
        for g in self.members:
            g.open()

    def close(self) -> None:
        """非阻塞：组内同时发夹紧。"""
        for g in self.members:
            g.close()

    def poll_done(self) -> bool:
        return all(bool(g.poll_done()) for g in self.members)

    def open_claw(self) -> bool:
        """阻塞：组内同时张开，全部到位才返回。"""
        return self._run_all("open")

    def close_claw(self) -> bool:
        """阻塞：组内同时夹紧，全部到位才返回。"""
        return self._run_all("close")

    def _run_all(self, action: str) -> bool:
        if action == "open":
            self.open()
        else:
            self.close()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self.poll_done():
                return bool(self.last_ok)
            time.sleep(0.02)
        return False

    def connect(self, *, announce_fault: bool = True) -> bool:
        oks = [bool(g.connect(announce_fault=announce_fault)) for g in self.members]
        return all(oks)

    def reconnect(self) -> bool:
        oks = [bool(g.reconnect()) for g in self.members]
        return all(oks)
