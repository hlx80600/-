"""
压鞋机 ModbusTCP —— 四槽独立接口 + 公共口。

物理开口：
  左口 = 放料口；右口 = 取料口

槽号顺序（HMI 可选）：
  12341 正序：旋转后 1→2→3→4→1；取料 n → 放料 n%4+1（取1放2）
  43214 反序：旋转后 4→3→2→1→4；取料 n → 放料 上一号（取1放4）

自动运行默认「自行计算槽号」：每次旋转到位后按所选顺序推进，
不依赖 PLC 槽号寄存器（仍可选手动锁定/改号）。

控制对象是「槽位 1~4」：
  压杆/底座等发到当前放料槽号；取料槽工作完成由工控机写到当前工位。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

from dataclasses import replace

from devices.plc_cas_points import (
    CasPoint,
    SLOT_CAS_ROWS,
    apply_cas_overrides,
    default_cas_points,
    pdu_addr,
    slot_cas_point_id,
)

log = logging.getLogger(__name__)

WORK_STATUS_NAMES = {
    0: "准备就绪",
    1: "摆杆进/进料",
    2: "一次压",
    3: "前后束紧",
    4: "压边",
    5: "二次压",
    6: "压着计时",
    7: "压边还原",
    8: "三次压",
    9: "还原",
    10: "压力异常",
}

SEQ_FORWARD = "12341"
SEQ_REVERSE = "43214"

SLOT_ADDR_KEYS = tuple(k for k, _suf, _lab in SLOT_CAS_ROWS)


def normalize_slot_sequence(raw: Any) -> str:
    s = str(raw or SEQ_FORWARD).strip().lower()
    if s in (SEQ_REVERSE, "reverse", "rev", "-1", "反序", "4321"):
        return SEQ_REVERSE
    return SEQ_FORWARD


def step_slot(n: int, count: int = 4, *, reverse: bool = False) -> int:
    """按顺序推进一格。正序 1→2→3→4→1；反序 4→3→2→1→4。"""
    n = int(n)
    if n < 1 or n > count:
        n = 1
    if reverse:
        return count if n <= 1 else n - 1
    return n % count + 1


def next_slot(n: int, count: int = 4) -> int:
    return step_slot(n, count, reverse=False)


def prev_slot(n: int, count: int = 4) -> int:
    return step_slot(n, count, reverse=True)


def place_from_pick(
    pick: int, count: int = 4, sequence: str = SEQ_FORWARD
) -> int:
    """取料口槽号 → 放料口槽号（相邻一格，方向随 slot_sequence）。"""
    reverse = normalize_slot_sequence(sequence) == SEQ_REVERSE
    return step_slot(pick, count, reverse=reverse)


def pick_from_place(
    place: int, count: int = 4, sequence: str = SEQ_FORWARD
) -> int:
    """放料口槽号 → 取料口槽号（反向一格）。"""
    reverse = normalize_slot_sequence(sequence) == SEQ_REVERSE
    return step_slot(place, count, reverse=not reverse)


def _empty_slot_state() -> Dict[str, Any]:
    return {
        "shoe_placed_cmd": False,
        "motor_start_cmd": False,
        "slot_up_cmd": False,
        "motor_done": True,
        "rod_aligned": True,
        "rod_in_pos": True,
        "base_down": True,
        "rod_home": False,
        "slot_done": False,
        "work_status": 0,
        "estop": False,
    }


class PressMachine:
    def __init__(self, cfg: Dict[str, Any], use_mock: bool = True):
        self.cfg = cfg
        self.use_mock = use_mock
        self.client = None
        self.connected = False
        self.last_error = ""

        self.power_ok = True
        self.rotate_done = True
        self.press_done = True
        self.cmd_rotate = False
        self.cmd_start_press = False
        self.host_control = False
        self._rotating = False

        self.place_slot = 2
        self.pick_slot = 1
        self.manual_slot_lock = False

        self.slots: Dict[int, Dict[str, Any]] = {
            i: _empty_slot_state() for i in range(1, 5)
        }
        self.last_tx: Dict[str, Any] = {}
        # 中科院点表 Mock 内存（与 Station6 旧线圈互不共用）
        self._cas_coils: Dict[int, bool] = {}
        self._cas_holdings: Dict[int, int] = {}
        self._cas_discretes: Dict[int, bool] = {}
        self._io_lock = threading.RLock()
        self._fail_streak = 0
        self._last_ok_io = 0.0
        self._last_slot_refresh = 0.0
        self._sticky_rx: Dict[tuple[str, int], tuple[float, int]] = {}
        self._tx_q: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=128)
        self._io_stop = threading.Event()
        self._io_thread: threading.Thread | None = None
        self._connect_tried = False
        self._last_reconnect_try = 0.0
        self._pending_host_estop: bool | None = None
        self._watch_lock = threading.Lock()
        self._watch_ids: list[str] = [
            "online",
            "idle",
            "start",
            "shoe_done",
            "station_no",
            "host_estop",
            "s1_slot_done",
            "s2_slot_done",
            "s3_slot_done",
            "s4_slot_done",
        ]
        self._watch_cursor = 0

    def _unit(self) -> int:
        return int(self.cfg.get("unit_id", 1))

    def _addr(self, key: str, default: int = 0) -> int:
        return int(self.cfg.get(key, default) or 0)

    def _four(self) -> Dict[str, Any]:
        return dict(self.cfg.get("four_slot") or {})

    def slot_count(self) -> int:
        return int(self._four().get("slot_count", 4) or 4)

    def slot_sequence(self) -> str:
        return normalize_slot_sequence(self._four().get("slot_sequence", SEQ_FORWARD))

    def is_reverse_sequence(self) -> bool:
        return self.slot_sequence() == SEQ_REVERSE

    def auto_compute_slots(self) -> bool:
        return bool(self._four().get("auto_compute_slots", True))

    def _slot_cfg(self, slot: int) -> Dict[str, Any]:
        slots = self.cfg.get("slots") or {}
        return dict(slots.get(slot) or slots.get(str(slot)) or {})

    def opening_place_name(self) -> str:
        op = self.cfg.get("opening") or {}
        return str(op.get("place", "left"))

    def opening_pick_name(self) -> str:
        op = self.cfg.get("opening") or {}
        return str(op.get("pick", "right"))

    def _ensure_io_thread(self) -> None:
        """压机 TCP 只在本线程做，避免卡住 OB1 / HMI。"""
        if self.use_mock:
            return
        th = self._io_thread
        if th is not None and th.is_alive():
            return
        self._io_stop.clear()
        self._io_thread = threading.Thread(
            target=self._io_loop, name="press-modbus", daemon=True
        )
        self._io_thread.start()

    def _enqueue(self, fn: Callable[[], None]) -> None:
        if self.use_mock:
            fn()
            return
        self._ensure_io_thread()
        try:
            self._tx_q.put_nowait(fn)
        except queue.Full:
            log.warning("[压鞋机] 发送队列满，本笔稍后由周期任务补发")

    def request_cas_poll(self, ids: list[str], *, full: bool = False) -> None:
        """HMI 指定要刷的点；IO 线程去读，调用方不占总线。"""
        with self._watch_lock:
            if full:
                self._watch_ids = [p.id for p in self.cas_point_list()]
                return
            base = [
                "online",
                "idle",
                "start",
                "shoe_done",
                "station_no",
                "host_estop",
                "s1_slot_done",
                "s2_slot_done",
                "s3_slot_done",
                "s4_slot_done",
            ]
            self._watch_ids = list(dict.fromkeys([*base, *[str(i) for i in ids if i]]))

    def _io_loop(self) -> None:
        while not self._io_stop.is_set():
            t0 = time.monotonic()
            try:
                self._io_tick()
            except Exception:
                log.exception("[压鞋机] IO 线程异常")
            time.sleep(max(0.01, 0.03 - (time.monotonic() - t0)))

    def _io_tick(self) -> None:
        if self._pending_host_estop is not None:
            try:
                self._flush_host_estop_io()
            except Exception as e:
                log.warning("[压鞋机] 急停线圈下发失败: %s", e)
        n = 0
        while n < 16:
            try:
                job = self._tx_q.get_nowait()
            except queue.Empty:
                break
            try:
                job()
            except Exception as e:
                log.warning("[压鞋机] IO 任务失败: %s", e)
            n += 1
        if not self.connected or self.client is None:
            now = time.monotonic()
            if now - self._last_reconnect_try >= 3.0:
                self._last_reconnect_try = now
                self._connect_blocking()
            return
        if not self._tx_q.empty():
            return
        self._poll_runtime()

    def _cas_read_io(self, point: CasPoint) -> int:
        """仅 IO 线程：真读并写入缓存。"""
        addr = pdu_addr(point)
        kind = str(point.plc_kind)
        if kind == "M":
            val = int(bool(self._read_coil(addr, False)))
            self._cas_coils[addr] = bool(val)
            return val
        if kind == "X":
            val = int(bool(self._read_discrete(addr, False)))
            self._cas_discretes[addr] = bool(val)
            return val
        val = int(self._read_holding(addr, 0)) & 0xFFFF
        self._cas_holdings[addr] = val
        return val

    def _cas_write_io(self, point: CasPoint, value: int | bool) -> None:
        addr = pdu_addr(point)
        kind = str(point.plc_kind)
        tag = str(point.id or f"cas_{addr}")
        if kind == "M":
            self._write_coil(addr, bool(value), tag=tag)
            return
        val = int(value) & 0xFFFF
        last_err = ""
        for attempt in range(2):
            try:
                self._write_holding(addr, val, tag=tag, strict=True)
                back = int(self._read_holding(addr, -1))
                if back == val or back < 0:
                    return
                last_err = f"回读 {back}"
            except Exception as e:
                last_err = str(e)
            if attempt == 0:
                time.sleep(0.03)
        log.warning("[压鞋机] 点 %s 下发 %s：%s", tag, val, last_err)

    def _poll_runtime(self) -> None:
        """运行必需的槽到位 + HMI 关注点，每圈限量，把带宽留给写。"""
        try:
            for i in range(1, self.slot_count() + 1):
                self._refresh_slot_io(i)
        except Exception as e:
            log.debug("[压鞋机] 槽状态轮询: %s", e)
            return
        with self._watch_lock:
            ids = list(self._watch_ids)
        if not ids:
            return
        n = len(ids)
        start = self._watch_cursor % max(1, n)
        by_id = {p.id: p for p in self.cas_point_list()}
        budget = 6
        for k in range(min(budget, n)):
            pid = ids[(start + k) % n]
            pt = by_id.get(pid)
            if pt is None:
                continue
            try:
                self._cas_read_io(pt)
            except Exception:
                break
        self._watch_cursor = start + budget

    def _refresh_slot_io(self, slot: int) -> None:
        st = self.slots.setdefault(slot, _empty_slot_state())
        done_pt = self._slot_cas_point(slot, "addr_motor_done")
        home_pt = self._slot_cas_point(slot, "addr_rod_home")
        slot_done_pt = self._slot_cas_point(slot, "addr_slot_done")
        if done_pt is not None:
            st["motor_done"] = bool(self._cas_read_io(done_pt))
        if home_pt is not None:
            st["rod_home"] = bool(self._cas_read_io(home_pt))
        if slot_done_pt is not None:
            st["slot_done"] = bool(self._cas_read_io(slot_done_pt))
        st["rod_aligned"] = True
        st["rod_in_pos"] = True
        st["base_down"] = True
        st["work_status"] = 0
        st["estop"] = False
        if "slot_done" not in st:
            st["slot_done"] = False

    def _flush_host_estop_io(self) -> None:
        bit = self._pending_host_estop
        if bit is None:
            return
        pt = next((p for p in self.cas_point_list() if p.id == "host_estop"), None)
        if pt is None:
            self._pending_host_estop = None
            return
        self._cas_write_io(pt, bit)
        if self.last_tx.get("host_estop") == bit:
            self._pending_host_estop = None

    def connect(self, *, wait: bool = False) -> bool:
        """连压机。默认丢给 IO 线程，不阻塞运行扫描。HMI 点重连时 wait=True。"""
        if self.use_mock:
            self.connected = True
            self.last_error = ""
            self._init_mock_slots()
            log.info("[压鞋机] Mock 已连接 %s:%s", self.cfg.get("ip"), self.cfg.get("port"))
            return True
        self._ensure_io_thread()
        self._last_reconnect_try = time.monotonic()
        if wait:
            return self._connect_blocking()
        self._enqueue(self._connect_blocking)
        return bool(self.connected)

    def _connect_blocking(self) -> bool:
        try:
            from pymodbus.client import ModbusTcpClient

            with self._io_lock:
                if self.client is not None:
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
                timeout_s = float(self.cfg.get("timeout_s", 0.4) or 0.4)
                kw: Dict[str, Any] = {
                    "port": int(self.cfg.get("port", 502)),
                    "timeout": timeout_s,
                }
                try:
                    self.client = ModbusTcpClient(self.cfg["ip"], retries=0, **kw)
                except TypeError:
                    self.client = ModbusTcpClient(self.cfg["ip"], **kw)
                self.connected = bool(self.client.connect())
                self._connect_tried = True
                if self.connected:
                    self.last_error = ""
                    self._io_ok()
                else:
                    self.last_error = f"无法连接 {self.cfg.get('ip')}:{self.cfg.get('port')}"
                    log.error("[压鞋机] 连接失败 %s", self.last_error)
                    return self.connected
            if self.connected:
                self.host_control = True
                self._write_coil(
                    self._addr("addr_host_control"), True, "host_control"
                )
            return self.connected
        except Exception as e:
            log.error("[压鞋机] 连接失败: %s", e)
            self.last_error = str(e)
            self.connected = False
            self.client = None
            self._connect_tried = True
            return False

    def _mark_comm_down(self, err: str) -> None:
        """TCP/Modbus 失败：标未连接，供 LINK 报警与重连。"""
        self.connected = False
        text = str(err or "压机通信中断").strip()
        self.last_error = text
        log.warning("[压鞋机] 通信中断: %s", text)
        self.last_tx.pop("host_estop", None)

    def _io_ok(self) -> None:
        self._fail_streak = 0
        self._last_ok_io = time.monotonic()

    def _io_fail(self, err: Exception | str, *, mark_down: bool = False) -> None:
        """单笔超时不立刻拆链路；连续失败或明确断线才标掉线。"""
        text = str(err or "压机通信失败")
        low = text.lower()
        hard = any(
            s in low
            for s in ("connection", "broken", "reset", "closed", "not connected", "断开")
        )
        if mark_down or hard:
            self._mark_comm_down(text)
            return
        self._fail_streak += 1
        if self._fail_streak >= 4:
            self._mark_comm_down(text)

    def _sticky_get(self, kind: str, addr: int) -> int | None:
        hit = self._sticky_rx.get((kind, addr))
        if hit is None:
            return None
        until, val = hit
        if time.monotonic() > until:
            self._sticky_rx.pop((kind, addr), None)
            return None
        return int(val)

    def _sticky_put(self, kind: str, addr: int, val: int, ttl_s: float = 0.8) -> None:
        self._sticky_rx[(kind, addr)] = (time.monotonic() + float(ttl_s), int(val))

    def refresh_link(self) -> bool:
        """给 LINK 用：只看缓存，不在扫描线程里打 Modbus。"""
        if self.use_mock:
            self.connected = True
            return True
        if not self._connect_tried:
            return True
        return bool(self.connected)

    def reconnect(self) -> bool:
        return self.connect(wait=True)

    def _read_coil(self, addr: int, default: bool = False) -> bool:
        if addr <= 0 or self.use_mock or not self.client:
            return default
        with self._io_lock:
            try:
                rr = self.client.read_coils(int(addr), count=1, device_id=self._unit())
                if rr.isError():
                    return default
                self._io_ok()
                return bool(rr.bits[0])
            except Exception as e:
                log.debug("[压鞋机] 读线圈 0x%X 失败: %s", addr, e)
                self._io_fail(e)
                return default

    def _write_coil(self, addr: int, value: bool, tag: str = "") -> None:
        self.last_tx[tag or f"coil_{addr}"] = bool(value)
        if addr <= 0 or self.use_mock or not self.client:
            return
        with self._io_lock:
            try:
                self.client.write_coil(int(addr), bool(value), device_id=self._unit())
                self._io_ok()
            except Exception as e:
                log.warning("[压鞋机] 写线圈 0x%X 失败: %s", addr, e)
                self._io_fail(e)

    def _read_holding(self, addr: int, default: int = 0) -> int:
        if addr <= 0 or self.use_mock or not self.client:
            return int(default)
        with self._io_lock:
            try:
                rr = self.client.read_holding_registers(
                    int(addr), count=1, device_id=self._unit()
                )
                if rr.isError():
                    return int(default)
                self._io_ok()
                return int(rr.registers[0])
            except Exception as e:
                log.debug("[压鞋机] 读寄存器 0x%X 失败: %s", addr, e)
                self._io_fail(e)
                return int(default)

    def _holding_write_failed(self, resp: Any) -> bool:
        """pymodbus 写成功时可能返回 None，异常响应才算失败。"""
        if resp is None:
            return False
        check = getattr(resp, "isError", None)
        return bool(check()) if callable(check) else False

    def _write_holding(
        self, addr: int, value: int, tag: str = "", *, strict: bool = False
    ) -> None:
        """写保持寄存器。优先 FC16（台达 DVP 对奇数地址 FC06 常拒写），失败再试 FC06。"""
        val = int(value) & 0xFFFF
        self.last_tx[tag or f"reg_{addr}"] = val
        if addr < 0 or self.use_mock or not self.client:
            return
        unit = self._unit()
        last_err: str = ""
        with self._io_lock:
            try:
                wr = self.client.write_registers(int(addr), [val], device_id=unit)
                if not self._holding_write_failed(wr):
                    self._io_ok()
                    log.info("[压鞋机] FC16 写寄存器 addr=%s val=%s tag=%s", addr, val, tag)
                    return
                last_err = str(wr)
                wr2 = self.client.write_register(int(addr), val, device_id=unit)
                if not self._holding_write_failed(wr2):
                    self._io_ok()
                    log.info("[压鞋机] FC06 写寄存器 addr=%s val=%s tag=%s", addr, val, tag)
                    return
                last_err = f"{wr} / {wr2}"
            except Exception as e:
                last_err = str(e)
                self._io_fail(e)
        log.warning("[压鞋机] 写寄存器 addr=%s val=%s 失败: %s", addr, val, last_err)
        if strict:
            raise RuntimeError(f"写保持寄存器地址 {addr}（值 {val}）失败：{last_err}")

    def _read_discrete(self, addr: int, default: bool = False) -> bool:
        if addr <= 0 or self.use_mock or not self.client:
            return default
        with self._io_lock:
            try:
                rr = self.client.read_discrete_inputs(
                    int(addr), count=1, device_id=self._unit()
                )
                if not rr.isError():
                    self._io_ok()
                    return bool(rr.bits[0])
            except Exception:
                pass
        return self._read_coil(addr, default)

    def cas_point_list(self) -> list[CasPoint]:
        """当前点表（yaml press.cas_points 可覆盖 Excel Dec）。"""
        return apply_cas_overrides(default_cas_points(), self.cfg.get("cas_points"))

    def _slot_cas_point(self, slot: int, yaml_key: str) -> CasPoint | None:
        """槽号对应工位点；地址优先槽 yaml，否则点表。"""
        try:
            pid = slot_cas_point_id(slot, yaml_key)
        except KeyError:
            return None
        pt = next((p for p in self.cas_point_list() if p.id == pid), None)
        if pt is None:
            return None
        raw = self._slot_cfg(slot).get(yaml_key)
        if raw is None:
            return pt
        try:
            dec = int(raw)
        except (TypeError, ValueError):
            return pt
        if dec <= 0:
            return pt
        return replace(pt, modbus_dec=dec)

    def _slot_cas_read(self, slot: int, yaml_key: str, default: int | bool) -> int:
        pt = self._slot_cas_point(slot, yaml_key)
        if pt is None:
            return int(default)
        try:
            return int(self.cas_read(pt))
        except Exception:
            return int(default)

    def _slot_cas_write(self, slot: int, yaml_key: str, value: int | bool) -> None:
        pt = self._slot_cas_point(slot, yaml_key)
        if pt is None or pt.rw != "rw":
            return
        self.cas_write(pt, value)

    def cas_read(self, point: CasPoint, *, modbus_dec: int | None = None) -> int:
        """读中科院点：只走缓存，不阻塞调用线程。"""
        addr = pdu_addr(point, modbus_dec=modbus_dec)
        kind = str(point.plc_kind)
        sticky = self._sticky_get(kind, addr)
        if sticky is not None:
            return sticky
        if kind == "M":
            return int(bool(self._cas_coils.get(addr, False)))
        if kind == "X":
            return int(bool(self._cas_discretes.get(addr, False)))
        return int(self._cas_holdings.get(addr, 0)) & 0xFFFF

    def cas_write(
        self, point: CasPoint, value: int | bool, *, modbus_dec: int | None = None
    ) -> None:
        """写中科院可写点：先改缓存再投递 IO 线程。"""
        if point.rw != "rw":
            raise RuntimeError("该点只读")
        addr = pdu_addr(point, modbus_dec=modbus_dec)
        kind = str(point.plc_kind)
        tag = str(point.id or f"cas_{addr}")
        if kind == "M":
            bit = bool(value)
            self.last_tx[tag] = bit
            self._cas_coils[addr] = bit
            ttl = 2.0 if str(point.id).endswith("_slot_done") else 0.8
            self._sticky_put(kind, addr, int(bit), ttl_s=ttl)
            if not self.use_mock:
                self._enqueue(lambda p=point, v=bit: self._cas_write_io(p, v))
            return
        if kind != "D":
            raise RuntimeError("该点不可写")
        val = int(value) & 0xFFFF
        if str(point.id) in ("start", "shoe_done"):
            online = next((p for p in self.cas_point_list() if p.id == "online"), None)
            if online is not None and online.id != point.id:
                self.cas_write(online, True)
        self.last_tx[tag] = val
        self._cas_holdings[addr] = val
        self._sticky_put(kind, addr, val)
        if not self.use_mock:
            self._enqueue(lambda p=point, v=val: self._cas_write_io(p, v))

    def _init_mock_slots(self) -> None:
        fs = self._four()
        self.pick_slot = int(fs.get("mock_pick_slot", 1) or 1)
        if bool(fs.get("derive_place_from_pick", True)):
            self.place_slot = place_from_pick(
                self.pick_slot, self.slot_count(), self.slot_sequence()
            )
        else:
            self.place_slot = int(fs.get("mock_place_slot", 2) or 2)

    def pair_from_pick(self) -> None:
        """按当前顺序：取料槽 → 放料槽。"""
        self.place_slot = place_from_pick(
            self.pick_slot, self.slot_count(), self.slot_sequence()
        )

    def pair_from_place(self) -> None:
        """按当前顺序：放料槽 → 取料槽。"""
        self.pick_slot = pick_from_place(
            self.place_slot, self.slot_count(), self.slot_sequence()
        )

    def _sync_derived_slots(self) -> None:
        self.pair_from_pick()

    def _refresh_slot_numbers(self) -> None:
        """周期刷新不改槽号。

        自动计算模式下槽号只在「旋转到位推进」或 HMI 改号时变化；
        若每次 refresh 都从取料口反推放料口，放料槽永远改不了。
        """
        if self.manual_slot_lock or self.auto_compute_slots() or self.use_mock:
            return
        fs = self._four()
        pick_a = int(fs.get("addr_pick_slot", 0) or 0)
        place_a = int(fs.get("addr_place_slot", 0) or 0)
        if pick_a > 0:
            self.pick_slot = max(
                1, min(self.slot_count(), self._read_holding(pick_a, self.pick_slot))
            )
        if bool(fs.get("derive_place_from_pick", True)):
            self.place_slot = place_from_pick(
                self.pick_slot, self.slot_count(), self.slot_sequence()
            )
        elif place_a > 0:
            self.place_slot = max(
                1, min(self.slot_count(), self._read_holding(place_a, self.place_slot))
            )

    def refresh_inputs(self) -> None:
        """扫描线程只读缓存；真机轮询在 IO 线程。"""
        if self.use_mock:
            self._refresh_slot_numbers()

    def advance_slots_after_rotate(self) -> None:
        """旋转到位后：按所选顺序推进取料/放料槽号。"""
        if not self.auto_compute_slots():
            return
        rev = self.is_reverse_sequence()
        old_pick, old_place = self.pick_slot, self.place_slot
        self.pick_slot = step_slot(self.pick_slot, self.slot_count(), reverse=rev)
        if bool(self._four().get("derive_place_from_pick", True)):
            self.place_slot = place_from_pick(
                self.pick_slot, self.slot_count(), self.slot_sequence()
            )
        else:
            self.place_slot = step_slot(self.place_slot, self.slot_count(), reverse=rev)
        log.info(
            "[压鞋机] 旋转推进槽号 顺序=%s 取料#%s→#%s 放料#%s→#%s",
            self.slot_sequence(),
            old_pick,
            self.pick_slot,
            old_place,
            self.place_slot,
        )

    def enable_host_control(self, on: bool = True) -> None:
        self.host_control = bool(on)
        if self.use_mock:
            return
        addr = self._addr("addr_host_control")
        self._enqueue(lambda a=addr, v=bool(on): self._write_coil(a, v, "host_control"))

    def set_rotate(self, value: bool) -> None:
        self.cmd_rotate = bool(value)
        self.last_tx["cmd_rotate"] = bool(value)
        if self.use_mock:
            if value:
                self.rotate_done = False
                self._rotating = True
            else:
                self._rotating = False
            log.info("[压鞋机] Mock 旋转命令=%s", value)
            return
        addr = self._addr("addr_cmd_rotate")
        self._enqueue(
            lambda a=addr, v=bool(value): self._write_coil(a, v, "cmd_rotate")
        )

    def set_start_press(self, value: bool) -> None:
        self.cmd_start_press = bool(value)
        self.last_tx["cmd_start_press"] = bool(value)
        if self.use_mock:
            if value:
                self.press_done = False
                self._mock_place_busy()
            log.info(
                "[压鞋机] Mock 开始压鞋=%s 放料槽=#%s（左口）",
                value,
                self.place_slot,
            )
            return
        addr = self._addr("addr_cmd_start_press")
        self._enqueue(
            lambda a=addr, v=bool(value): self._write_coil(a, v, "cmd_start_press")
        )
        if value and bool(self._four().get("enabled", True)):
            self.begin_place_press()

    def begin_place_press(self) -> None:
        self.refresh_inputs()
        slot = int(self.place_slot)
        sc = self._slot_cfg(slot)
        log.info("[压鞋机] 放料口(左口) 压合 → 槽#%s", slot)
        if bool(sc.get("auto_motor_on_press", True)):
            self._slot_cas_write(slot, "addr_rod_forward", True)
        self._slot_cas_write(slot, "addr_press_up", True)
        st = self.slots.setdefault(slot, _empty_slot_state())
        st["shoe_placed_cmd"] = False
        st["motor_start_cmd"] = True
        st["slot_up_cmd"] = True
        if self.use_mock:
            self._mock_place_busy()

    def _mock_place_busy(self) -> None:
        st = self.slots.setdefault(int(self.place_slot), _empty_slot_state())
        st["work_status"] = 2
        st["motor_done"] = False
        st["base_down"] = False

    def clear_place_press_cmds(self) -> None:
        slot = int(self.place_slot)
        self._slot_cas_write(slot, "addr_rod_forward", False)
        self._slot_cas_write(slot, "addr_press_up", False)
        st = self.slots.setdefault(slot, _empty_slot_state())
        st["motor_start_cmd"] = False
        st["slot_up_cmd"] = False

    def is_place_press_idle(self) -> bool:
        st = self.slots.get(int(self.place_slot), {})
        return bool(st.get("motor_done", True))

    def current_station_no(self) -> int:
        """当前工位：优先「当前工位显示」，否则放料槽号。"""
        pt = next((p for p in self.cas_point_list() if p.id == "station_no"), None)
        if pt is not None:
            try:
                raw = int(self.cas_read(pt))
                if 1 <= raw <= 4:
                    return raw
            except Exception:
                pass
        return max(1, min(4, int(self.place_slot or 1)))

    def set_pick_slot_work_done(self, on: bool, *, slot: int | None = None) -> None:
        """工控机改写取料槽工作完成（当前工位对应线圈）。"""
        sid = int(slot) if slot is not None else self.current_station_no()
        sid = max(1, min(4, sid))
        if on:
            for other in range(1, 5):
                if other != sid:
                    self._write_slot_work_done(other, False)
        self._write_slot_work_done(sid, bool(on))

    def _write_slot_work_done(self, slot: int, on: bool) -> None:
        pt = self._slot_cas_point(int(slot), "addr_slot_done")
        if pt is not None:
            self.cas_write(pt, bool(on))
        self.slots.setdefault(int(slot), _empty_slot_state())["slot_done"] = bool(on)

    @property
    def pick_ready(self) -> bool:
        if self.use_mock:
            return bool(self.press_done) and bool(self.rotate_done)
        st = self.slots.get(int(self.pick_slot), {})
        return not bool(st.get("estop", False))

    def set_rod_move(self, slot: int, direction: str, on: bool) -> None:
        addr_map = {
            "forward": "addr_rod_forward",
            "back": "addr_rod_back",
            "home": "addr_rod_back",
        }
        key = addr_map.get(direction, "addr_rod_forward")
        self._slot_cas_write(int(slot), key, bool(on))

    def set_base(self, slot: int, up: bool, on: bool = True) -> None:
        key = "addr_press_up" if up else "addr_press_down"
        self._slot_cas_write(int(slot), key, bool(on))

    def simulate_press_done(self) -> None:
        self.press_done = True
        self.cmd_start_press = False
        st = self.slots.setdefault(int(self.place_slot), _empty_slot_state())
        st["work_status"] = 0
        st["motor_done"] = True
        st["base_down"] = True
        st["rod_in_pos"] = True
        self.clear_place_press_cmds()
        log.info("[压鞋机] Mock 压鞋完成 放料槽=#%s", self.place_slot)

    def simulate_rotate_done(self, *, advance_slots: bool = True) -> None:
        self.rotate_done = True
        self._rotating = False
        self.cmd_rotate = False
        if self.cmd_start_press or not self.press_done:
            self.simulate_press_done()
        if advance_slots:
            self.advance_slots_after_rotate()
        log.info(
            "[压鞋机] 旋转完成 取料=#%s 放料=#%s 顺序=%s",
            self.pick_slot,
            self.place_slot,
            self.slot_sequence(),
        )

    def set_rotate_done_mock(self, value: bool) -> None:
        self.rotate_done = bool(value)
        if value:
            self._rotating = False

    def set_press_done_mock(self, value: bool) -> None:
        self.press_done = bool(value)

    def set_current_slots(
        self,
        pick: Optional[int] = None,
        place: Optional[int] = None,
        *,
        lock: Optional[bool] = None,
        derive_place: Optional[bool] = None,
    ) -> None:
        if pick is not None:
            self.pick_slot = max(1, min(self.slot_count(), int(pick)))
        do_derive = (
            bool(self._four().get("derive_place_from_pick", True))
            if derive_place is None
            else bool(derive_place)
        )
        if place is not None:
            self.place_slot = max(1, min(self.slot_count(), int(place)))
        elif pick is not None and do_derive:
            self._sync_derived_slots()
        if lock is not None:
            self.manual_slot_lock = bool(lock)
        log.info(
            "[压鞋机] 当前槽号 取料=#%s 放料=#%s 顺序=%s 手动锁定=%s",
            self.pick_slot,
            self.place_slot,
            self.slot_sequence(),
            self.manual_slot_lock,
        )

    def set_mock_slots(self, pick: Optional[int] = None, place: Optional[int] = None) -> None:
        self.set_current_slots(pick=pick, place=place, lock=True)

    def set_host_estop(self, on: bool, *, force: bool = False) -> None:
        """把工控机急停发给压机（联机点 host_estop，默认 Modbus Dec 51）。

        Mock 时写独立线圈内存，与真机同一套点表；last_tx 跳过不得留下过期 Mock 值。
        """
        bit = bool(on)
        pt = next((p for p in self.cas_point_list() if p.id == "host_estop"), None)
        if pt is None:
            log.warning("[压鞋机] 点表无急停信号 host_estop，未下发")
            return
        if self.use_mock:
            addr = pdu_addr(pt)
            same = self.last_tx.get("host_estop") == bit and bool(
                self._cas_coils.get(addr, False)
            ) == bit
            if same and not force:
                return
        elif (not force) and self.last_tx.get("host_estop") == bit:
            return
        if not self.use_mock:
            self._pending_host_estop = bit
        self.cas_write(pt, bit)
        log.info("[压鞋机] 工控机急停 → 压机 %s (Modbus %s)", bit, pt.modbus_dec)

    def estop_outputs_off(self) -> None:
        self.set_rotate(False)
        self.set_start_press(False)
        self.clear_place_press_cmds()

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "power_ok": self.power_ok,
            "rotate_done": self.rotate_done,
            "press_done": self.press_done,
            "pick_ready": self.pick_ready,
            "cmd_rotate": self.cmd_rotate,
            "cmd_start_press": self.cmd_start_press,
            "host_control": self.host_control,
            "place_slot": self.place_slot,
            "pick_slot": self.pick_slot,
            "manual_slot_lock": self.manual_slot_lock,
            "slot_sequence": self.slot_sequence(),
            "auto_compute_slots": self.auto_compute_slots(),
            "opening_place": self.opening_place_name(),
            "opening_pick": self.opening_pick_name(),
            "slots": {k: dict(v) for k, v in self.slots.items()},
            "last_tx": dict(self.last_tx),
            "place_side": self.opening_place_name(),
            "pick_side": self.opening_pick_name(),
            "sides": {},
        }

    def status_lines(self) -> list[str]:
        ps = self.slots.get(int(self.place_slot), {})
        ks = self.slots.get(int(self.pick_slot), {})
        return [
            f"顺序={self.slot_sequence()} 自算槽号={self.auto_compute_slots()}",
            f"左口=放料 槽#{self.place_slot} "
            f"状态={WORK_STATUS_NAMES.get(int(ps.get('work_status', 0)), ps.get('work_status'))} "
            f"电机完成={ps.get('motor_done')} 大座下={ps.get('base_down')} 压杆到位={ps.get('rod_in_pos')}",
            f"右口=取料 槽#{self.pick_slot} "
            f"状态={WORK_STATUS_NAMES.get(int(ks.get('work_status', 0)), ks.get('work_status'))} "
            f"可取={self.pick_ready} 电机完成={ks.get('motor_done')}",
            f"旋转到位={self.rotate_done} 压合完成={self.press_done} 上电={self.power_ok}",
        ]
