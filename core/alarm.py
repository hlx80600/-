"""报警管理：弹窗用队列 + 历史列表；同时写入落盘错误日志/黑匣子。"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Callable, Deque, List, Optional

log = logging.getLogger(__name__)

ALARM_KIND_ZH: dict[str, str] = {
    "LINK": "设备通讯断开",
    "ROBOT1": "上料机器人本体/运动报警",
    "ROBOT2": "下料机器人本体/运动报警",
    "PRESS": "压鞋机故障",
    "GRIP_LINK": "夹爪通讯失败",
    "GRIP_OPEN": "夹爪张开失败",
    "GRIP_CLOSE": "夹爪夹紧失败",
    "GRIP_DRV": "夹爪驱动故障",
    "INIT": "初始化失败",
    "INIT_HOME": "不在初始位",
    "PAYLOAD": "负载切换失败",
    "VISION1": "皮带相机拍照失败",
    "VISION2": "鞋头对位相机失败",
    "VISION3": "放料槽相机拍照失败",
    "VISION4": "取料槽相机拍照失败",
    "OB1": "本机程序扫描异常",
}


def alarm_kind_zh(code: str) -> str:
    """报警代码转中文故障类型。"""
    raw = str(code or "").strip()
    key = raw.upper()
    if key in ALARM_KIND_ZH:
        return ALARM_KIND_ZH[key]
    if key.startswith("GRIP"):
        return "夹爪故障"
    if key.startswith("VISION"):
        return "视觉/相机故障"
    if key.startswith("ROBOT"):
        return "机器人本体报警"
    if key.startswith("PRESS"):
        return "压鞋机故障"
    return raw or "未知故障"


def alarm_operator_hint(code: str, message: str = "") -> str:
    """弹窗底部给操作员的下一步（不含详情正文）。"""
    key = str(code or "").strip().upper()
    text = str(message or "")
    if key == "LINK":
        return (
            "同一组掉线只弹一次，后台仍会重连。"
            "全部连上后点「报警复位」；若曾自动运行，再「初始化」→「启动」。"
            "地址在「设置 → 通信与设备」。"
        )
    if key == "INIT_HOME":
        return (
            "用示教器把两臂点到 home 附近，或放宽「设置 → 运动融合」里的 mm/°。"
            "点「报警复位」后再「初始化」。"
        )
    if key == "INIT":
        return "看上面「设备」一行；压机未上电/未连也会走这条。处理后报警复位再初始化。"
    if key.startswith("GRIP"):
        return "查该电机 CAN 口、48V、使能。点「报警复位」会尝试重连夹爪。"
    if key in ("ROBOT1", "ROBOT2") or "路径：" in text:
        return (
            "先看示教器是否红灯；消红后点「报警复位」。"
            "若含「路径：从…→…」，到「示教点位」检查这两点或加过渡点再单步试跑。"
        )
    if key.startswith("PRESS"):
        return "查压机网线、IP、联机模式、空闲信号。报警复位后如曾运行需再初始化。"
    if key.startswith("VISION"):
        return "查该路相机 serial、预览是否有图；Mock 可继续空跑。复位后从失败步重试。"
    if key == "PAYLOAD":
        return "负载/工具切换失败，看该臂示教器。复位后再初始化。"
    if key == "OB1":
        return "本机程序扫到未捕获异常已停机。把本窗全文复制给调试；复位后再初始化。"
    return ""


def format_alarm_body(
    code: str,
    station: str,
    step: int,
    message: str,
    *,
    time: str = "",
) -> str:
    """给弹窗/复制/报警页用的完整正文：设备、故障、详情。"""
    device = str(station or "").strip() or "未标明设备"
    kind = alarm_kind_zh(code)
    lines: list[str] = []
    if time:
        lines.append(f"时间: {time}")
    lines.append(f"设备: {device}")
    lines.append(f"故障: {kind}")
    lines.append(f"代码: {code}")
    if int(step or 0):
        lines.append(f"步号: {step}")
    detail = str(message or "").strip()
    if detail:
        lines.append("详情:")
        lines.append(detail)
    hint = alarm_operator_hint(code, message)
    if hint:
        lines.append("处理:")
        lines.append(hint)
    return "\n".join(lines)


@dataclass
class AlarmItem:
    code: str
    message: str
    station: str = ""
    step: int = 0
    time: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    active: bool = True


class AlarmManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self.active: Optional[AlarmItem] = None
        self.history: Deque[AlarmItem] = deque(maxlen=200)
        self._popup_queue: Deque[AlarmItem] = deque()
        self._listeners: List[Callable[[], None]] = []

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def raise_alarm(
        self,
        code: str,
        message: str,
        station: str = "",
        step: int = 0,
        *,
        popup: bool = True,
    ) -> None:
        item = AlarmItem(code=code, message=message, station=station, step=step)
        with self._lock:
            same_active = (
                self.active is not None
                and self.active.code == item.code
                and self.active.station == item.station
                and self.active.message == item.message
            )
            already_queued = any(
                q.code == item.code
                and q.station == item.station
                and q.message == item.message
                for q in self._popup_queue
            )
            self.active = item
            self.history.appendleft(item)
            # 同一条报警只入队一次。夹爪 CAN 重连失败会每几秒再 raise，否则弹窗关不掉。
            if popup and not same_active and not already_queued:
                self._popup_queue.append(item)
        try:
            from core.blackbox import record_alarm

            record_alarm(
                code, message, station, step, popup=popup, active=True
            )
        except Exception:
            pass
        if not same_active:
            log.error("报警 [%s] %s@%s %s", code, station, step, message)
        self._notify()

    def note_event(
        self, code: str, message: str, station: str = "", step: int = 0
    ) -> None:
        """写入报警历史，不停机、不弹阻塞窗（用于「已自动消警」这类瞬态）。"""
        item = AlarmItem(
            code=code, message=message, station=station, step=step, active=False
        )
        with self._lock:
            self.history.appendleft(item)
        try:
            from core.blackbox import record_alarm

            record_alarm(
                code, message, station, step, popup=False, active=False
            )
        except Exception:
            pass
        log.warning("事件 [%s] %s@%s %s", code, station, step, message)
        self._notify()

    def reset(self) -> Optional[AlarmItem]:
        with self._lock:
            item = self.active
            if item:
                item.active = False
            self.active = None
            self._popup_queue.clear()
        self._notify()
        return item

    def pop_popup(self) -> Optional[AlarmItem]:
        with self._lock:
            if self._popup_queue:
                item = self._popup_queue.popleft()
                # 丢掉队列里同一条（重连期间可能已堆了多条）
                rest = deque()
                for q in self._popup_queue:
                    if not (
                        q.code == item.code
                        and q.station == item.station
                        and q.message == item.message
                    ):
                        rest.append(q)
                self._popup_queue = rest
                return item
            return None

    @property
    def has_alarm(self) -> bool:
        with self._lock:
            return self.active is not None
