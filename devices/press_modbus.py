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
  压杆/底座等发到当前放料槽号；取料完成看当前取料槽号。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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

SLOT_ADDR_KEYS = (
    "addr_shoe_placed",
    "addr_motor_start",
    "addr_motor_done",
    "addr_move_distance",
    "addr_slot_up",
    "addr_rod_aligned",
    "addr_rod_in_pos",
    "addr_base_down",
    "addr_rod_home",
    "addr_work_status",
    "addr_estop",
    "addr_rod_forward",
    "addr_rod_back",
    "addr_rod_go_home",
    "addr_press_up",
    "addr_press_down",
)


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

    def connect(self) -> bool:
        if self.use_mock:
            self.connected = True
            self.last_error = ""
            self._init_mock_slots()
            log.info("[压鞋机] Mock 已连接 %s:%s", self.cfg.get("ip"), self.cfg.get("port"))
            return True
        try:
            from pymodbus.client import ModbusTcpClient

            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
            self.client = ModbusTcpClient(self.cfg["ip"], port=int(self.cfg.get("port", 502)))
            self.connected = bool(self.client.connect())
            if self.connected:
                self.last_error = ""
                self.enable_host_control(True)
            else:
                self.last_error = f"无法连接 {self.cfg.get('ip')}:{self.cfg.get('port')}"
                log.error("[压鞋机] 连接失败 %s", self.last_error)
            return self.connected
        except Exception as e:
            log.error("[压鞋机] 连接失败: %s", e)
            self.last_error = str(e)
            self.connected = False
            self.client = None
            return False

    def refresh_link(self) -> bool:
        if self.use_mock:
            self.connected = True
            return True
        if self.client is None:
            self.connected = False
            return False
        try:
            if hasattr(self.client, "is_socket_open"):
                self.connected = bool(self.client.is_socket_open())
        except Exception:
            self.connected = False
        return self.connected

    def reconnect(self) -> bool:
        return self.connect()

    def _read_coil(self, addr: int, default: bool = False) -> bool:
        if addr <= 0 or self.use_mock or not self.client:
            return default
        try:
            rr = self.client.read_coils(int(addr), count=1, device_id=self._unit())
            if rr.isError():
                return default
            return bool(rr.bits[0])
        except Exception as e:
            log.debug("[压鞋机] 读线圈 0x%X 失败: %s", addr, e)
            return default

    def _write_coil(self, addr: int, value: bool, tag: str = "") -> None:
        self.last_tx[tag or f"coil_{addr}"] = bool(value)
        if addr <= 0 or self.use_mock or not self.client:
            return
        try:
            self.client.write_coil(int(addr), bool(value), device_id=self._unit())
        except Exception as e:
            log.warning("[压鞋机] 写线圈 0x%X 失败: %s", addr, e)

    def _read_holding(self, addr: int, default: int = 0) -> int:
        if addr <= 0 or self.use_mock or not self.client:
            return int(default)
        try:
            rr = self.client.read_holding_registers(int(addr), count=1, device_id=self._unit())
            if rr.isError():
                return int(default)
            return int(rr.registers[0])
        except Exception as e:
            log.debug("[压鞋机] 读寄存器 0x%X 失败: %s", addr, e)
            return int(default)

    def _write_holding(self, addr: int, value: int, tag: str = "") -> None:
        self.last_tx[tag or f"reg_{addr}"] = int(value)
        if addr <= 0 or self.use_mock or not self.client:
            return
        try:
            self.client.write_register(int(addr), int(value) & 0xFFFF, device_id=self._unit())
        except Exception as e:
            log.warning("[压鞋机] 写寄存器 0x%X 失败: %s", addr, e)

    def _read_discrete(self, addr: int, default: bool = False) -> bool:
        if addr <= 0 or self.use_mock or not self.client:
            return default
        try:
            rr = self.client.read_discrete_inputs(int(addr), count=1, device_id=self._unit())
            if not rr.isError():
                return bool(rr.bits[0])
        except Exception:
            pass
        return self._read_coil(addr, default)

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
        if self.use_mock:
            self._refresh_slot_numbers()
            return
        if not self.connected or self.client is None:
            return
        try:
            self.power_ok = self._read_coil(self._addr("addr_power_ok"), False)
            self.rotate_done = self._read_coil(self._addr("addr_rotate_done"), False)
            self.press_done = self._read_coil(self._addr("addr_press_done"), True)
            self.host_control = self._read_coil(self._addr("addr_host_control"), False)
            self._refresh_slot_numbers()
            for i in range(1, self.slot_count() + 1):
                self._refresh_slot(i)
        except Exception as e:
            log.warning("[压鞋机] 刷新失败，标记未连接: %s", e)
            self.connected = False

    def _refresh_slot(self, slot: int) -> None:
        sc = self._slot_cfg(slot)
        st = self.slots.setdefault(slot, _empty_slot_state())
        st["motor_done"] = self._read_coil(int(sc.get("addr_motor_done", 0) or 0), True)
        st["rod_aligned"] = self._read_discrete(int(sc.get("addr_rod_aligned", 0) or 0), True)
        st["rod_in_pos"] = self._read_discrete(int(sc.get("addr_rod_in_pos", 0) or 0), True)
        st["base_down"] = self._read_discrete(int(sc.get("addr_base_down", 0) or 0), True)
        st["rod_home"] = self._read_discrete(int(sc.get("addr_rod_home", 0) or 0), False)
        st["work_status"] = self._read_holding(int(sc.get("addr_work_status", 0) or 0), 0)
        st["estop"] = self._read_coil(int(sc.get("addr_estop", 0) or 0), False)

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
        self._write_coil(self._addr("addr_host_control"), bool(on), "host_control")

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
        self._write_coil(self._addr("addr_cmd_rotate"), value, "cmd_rotate")

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
        self._write_coil(self._addr("addr_cmd_start_press"), value, "cmd_start_press")
        if value and bool(self._four().get("enabled", True)):
            self.begin_place_press()

    def begin_place_press(self) -> None:
        self.refresh_inputs()
        slot = int(self.place_slot)
        sc = self._slot_cfg(slot)
        log.info("[压鞋机] 放料口(左口) 压合 → 槽#%s", slot)
        self._write_coil(
            int(sc.get("addr_shoe_placed", 0) or 0), True, f"slot{slot}.shoe_placed"
        )
        dist = int(sc.get("default_move_distance_mm", 0) or 0)
        if dist and int(sc.get("addr_move_distance", 0) or 0) > 0:
            self._write_holding(
                int(sc["addr_move_distance"]), dist, f"slot{slot}.move_distance"
            )
        if bool(sc.get("auto_motor_on_press", True)):
            self._write_coil(
                int(sc.get("addr_motor_start", 0) or 0), True, f"slot{slot}.motor_start"
            )
        self._write_coil(int(sc.get("addr_slot_up", 0) or 0), True, f"slot{slot}.slot_up")
        st = self.slots.setdefault(slot, _empty_slot_state())
        st["shoe_placed_cmd"] = True
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
        sc = self._slot_cfg(slot)
        self._write_coil(
            int(sc.get("addr_motor_start", 0) or 0), False, f"slot{slot}.motor_start"
        )
        self._write_coil(int(sc.get("addr_slot_up", 0) or 0), False, f"slot{slot}.slot_up")
        st = self.slots.setdefault(slot, _empty_slot_state())
        st["motor_start_cmd"] = False
        st["slot_up_cmd"] = False

    def is_place_press_idle(self) -> bool:
        st = self.slots.get(int(self.place_slot), {})
        ws = int(st.get("work_status", 0))
        return ws in (0, 9) and bool(st.get("motor_done", True))

    @property
    def pick_ready(self) -> bool:
        if self.use_mock:
            return bool(self.press_done) and bool(self.rotate_done)
        st = self.slots.get(int(self.pick_slot), {})
        ws = int(st.get("work_status", 0))
        return (
            ws in (0, 9)
            and bool(st.get("motor_done", True))
            and not bool(st.get("estop", False))
        )

    def set_rod_move(self, slot: int, direction: str, on: bool) -> None:
        sc = self._slot_cfg(int(slot))
        addr_map = {
            "forward": "addr_rod_forward",
            "back": "addr_rod_back",
            "home": "addr_rod_go_home",
        }
        key = addr_map.get(direction, "addr_rod_forward")
        self._write_coil(
            int(sc.get(key, 0) or 0), bool(on), f"slot{slot}.rod_{direction}"
        )

    def set_base(self, slot: int, up: bool, on: bool = True) -> None:
        sc = self._slot_cfg(int(slot))
        key = "addr_press_up" if up else "addr_press_down"
        self._write_coil(
            int(sc.get(key, 0) or 0),
            bool(on),
            f"slot{slot}.base_{'up' if up else 'down'}",
        )

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
