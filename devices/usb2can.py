"""达妙官方 USB2CAN（CDC 串口，不是 SocketCAN can0）。

协议与 ~/达妙调试 相同：串口 921600，把 CAN 帧封进 55 AA 包。
同一 /dev/ttyACM0 可被多路夹爪共享（一进一出，收帧分发给各监听者）。
"""

from __future__ import annotations

import glob
import queue
import struct
import threading
import time
from typing import Any

import can

USB2CAN_SEND_FMT = struct.Struct("<2sBBIIBIBBBB8sB")
USB2CAN_RECV_FMT = struct.Struct("<BBBI8sB")
USB2CAN_RECV_SIZE = USB2CAN_RECV_FMT.size
USB2CAN_BAUD_INDEX = {
    10_000: 0,
    20_000: 1,
    50_000: 2,
    100_000: 3,
    125_000: 4,
    250_000: 5,
    500_000: 6,
    800_000: 7,
    1_000_000: 8,
}

_lock = threading.Lock()
_cores: dict[str, "_Usb2CanCore"] = {}


def is_usb2can_channel(channel: str) -> bool:
    """True：达妙 USB2CAN 串口；False：SocketCAN 网口名。"""
    s = (channel or "").strip()
    if s.lower().startswith("usb2can:"):
        return True
    return s.startswith("/dev/tty") or s.startswith("ttyACM") or s.startswith("ttyUSB")


def normalize_can_channel(channel: str) -> str:
    s = (channel or "").strip()
    if s.lower().startswith("usb2can:"):
        return s.split(":", 1)[1].strip() or "/dev/ttyACM0"
    if s.startswith("ttyACM") or s.startswith("ttyUSB"):
        return "/dev/" + s
    return s


def list_usb2can_ports() -> list[str]:
    """列出可能的达妙 USB2CAN 串口。"""
    found: list[str] = []
    for path in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
        if path not in found:
            found.append(path)
    try:
        from serial.tools import list_ports

        for info in list_ports.comports():
            dev = info.device or ""
            if ("ttyACM" in dev or "ttyUSB" in dev) and dev not in found:
                found.append(dev)
    except Exception:
        pass
    return found


def pack_usb2can_frame(
    can_id: int,
    data: bytes,
    cmd: int = 0x03,
    send_times: int = 1,
    interval_ms: int = 10,
    extended: bool = False,
) -> bytes:
    payload = bytes(data[:8]).ljust(8, b"\x00")
    return USB2CAN_SEND_FMT.pack(
        b"\x55\xaa",
        0x1E,
        cmd,
        send_times,
        interval_ms,
        1 if extended else 0,
        can_id & 0x1FFFFFFF,
        0,
        len(data[:8]) if data else 8,
        0,
        0,
        payload,
        0,
    )


def pack_usb2can_handshake() -> bytes:
    return pack_usb2can_frame(0, b"\x00" * 8, cmd=0x02)


def pack_usb2can_set_baud(bitrate: int) -> bytes:
    return pack_usb2can_frame(0, b"\x00" * 8, cmd=0x04, send_times=int(bitrate))


def pack_usb2can_set_baud_index(index: int) -> bytes:
    return pack_usb2can_frame(
        0, bytes([int(index) & 0xFF, 0, 0, 0, 0, 0, 0, 0]), cmd=0x04
    )


def try_parse_usb2can_recv(buf: bytes) -> tuple[can.Message | None, bytes]:
    start = buf.find(b"\xaa")
    if start < 0:
        return None, buf[-32:] if len(buf) > 32 else buf
    buf = buf[start:]
    if len(buf) < USB2CAN_RECV_SIZE:
        return None, buf
    header, cmd, flags, can_id, data, end = USB2CAN_RECV_FMT.unpack(
        buf[:USB2CAN_RECV_SIZE]
    )
    if header != 0xAA:
        return None, buf[1:]
    if cmd == 0x11:
        dlc = flags & 0x3F
        ide = bool(flags & 0x40)
        payload = data[: dlc if 0 < dlc <= 8 else 8]
        msg = can.Message(
            arbitration_id=can_id & 0x1FFFFFFF,
            data=payload,
            is_extended_id=ide,
        )
        return msg, buf[USB2CAN_RECV_SIZE:]
    if end != 0x55:
        return None, buf[1:]
    return None, buf[USB2CAN_RECV_SIZE:]


class _Usb2CanCore:
    def __init__(self, port: str, serial_baud: int, can_bitrate: int) -> None:
        self.port = port
        self.serial_baud = serial_baud
        self.can_bitrate = can_bitrate
        self.forward_cmd = 0x03
        self._ser: Any = None
        self._rx_thread: threading.Thread | None = None
        self._running = False
        self._wlock = threading.Lock()
        self._listeners: list[queue.Queue] = []
        self._lck = threading.Lock()

    def open(self) -> None:
        import serial
        from serial import SerialException

        try:
            self._ser = serial.Serial(
                self.port,
                self.serial_baud,
                timeout=0.05,
                write_timeout=0.2,
            )
        except SerialException as exc:
            err = str(exc)
            if "Permission denied" in err or getattr(exc, "errno", None) == 13:
                raise RuntimeError(
                    f"没有权限打开 {self.port}。执行：sudo usermod -aG dialout $USER 后重新登录，"
                    f"或 sudo chmod 666 {self.port}"
                ) from exc
            raise RuntimeError(f"打不开达妙 USB2CAN {self.port}：{exc}") from exc
        try:
            self._ser.dtr = True
            self._ser.rts = True
        except Exception:
            pass
        time.sleep(0.08)
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass
        self._write(pack_usb2can_handshake())
        time.sleep(0.05)
        self.set_can_bitrate(self.can_bitrate)
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name="usb2can-rx", daemon=True
        )
        self._rx_thread.start()

    def set_can_bitrate(self, bitrate: int) -> None:
        self.can_bitrate = int(bitrate)
        self._write(pack_usb2can_set_baud(self.can_bitrate))
        time.sleep(0.05)
        idx = USB2CAN_BAUD_INDEX.get(self.can_bitrate)
        if idx is not None:
            self._write(pack_usb2can_set_baud_index(idx))
            time.sleep(0.05)

    def _write(self, payload: bytes) -> None:
        if self._ser is None:
            return
        with self._wlock:
            self._ser.write(payload)

    def send_can(self, can_id: int, data: bytes, extended: bool = False) -> None:
        self._write(
            pack_usb2can_frame(
                can_id, data, cmd=self.forward_cmd, extended=extended
            )
        )

    def add_listener(self, q: queue.Queue) -> None:
        with self._lck:
            self._listeners.append(q)

    def remove_listener(self, q: queue.Queue) -> None:
        with self._lck:
            if q in self._listeners:
                self._listeners.remove(q)

    def _rx_loop(self) -> None:
        buf = b""
        while self._running and self._ser is not None:
            try:
                chunk = self._ser.read(256)
            except Exception:
                time.sleep(0.05)
                continue
            if chunk:
                buf += chunk
                if len(buf) > 4096:
                    buf = buf[-512:]
            msg, buf = try_parse_usb2can_recv(buf)
            while msg is not None:
                with self._lck:
                    listeners = list(self._listeners)
                for q in listeners:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            q.put_nowait(msg)
                        except queue.Full:
                            pass
                msg, buf = try_parse_usb2can_recv(buf)
            if not chunk:
                time.sleep(0.002)

    def close(self) -> None:
        self._running = False
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=0.5)
            self._rx_thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None


class Usb2CanBus:
    """与 python-can Bus 相同的 send/recv/shutdown，供夹爪控制器使用。"""

    def __init__(self, core: _Usb2CanCore, q: queue.Queue) -> None:
        self._core = core
        self._q = q
        self.channel = core.port

    @property
    def forward_cmd(self) -> int:
        return self._core.forward_cmd

    @forward_cmd.setter
    def forward_cmd(self, value: int) -> None:
        self._core.forward_cmd = int(value)

    def send(self, msg: can.Message, timeout: float | None = None) -> None:
        del timeout
        self._core.send_can(
            int(msg.arbitration_id),
            bytes(msg.data),
            bool(getattr(msg, "is_extended_id", False)),
        )

    def recv(self, timeout: float | None = None) -> can.Message | None:
        try:
            if timeout is None or float(timeout) <= 0:
                return self._q.get_nowait()
            return self._q.get(timeout=float(timeout))
        except queue.Empty:
            return None

    def shutdown(self) -> None:
        """注销本路监听；无人再用时关闭串口。"""
        core = self._core
        core.remove_listener(self._q)
        with _lock:
            if not core._listeners:
                core.close()
                _cores.pop(core.port, None)

    def set_can_bitrate(self, bitrate: int) -> None:
        self._core.set_can_bitrate(bitrate)


def acquire_can_bus(
    channel: str,
    *,
    serial_baud: int = 921600,
    can_bitrate: int = 1_000_000,
) -> Any:
    """打开 SocketCAN 或达妙 USB2CAN。USB 口按路径共享。"""
    raw = (channel or "").strip() or "can0"
    if not is_usb2can_channel(raw):
        return can.interface.Bus(
            channel=raw,
            interface="socketcan",
            receive_own_messages=False,
        )
    port = normalize_can_channel(raw)
    q: queue.Queue = queue.Queue(maxsize=64)
    with _lock:
        core = _cores.get(port)
        if core is None:
            core = _Usb2CanCore(port, serial_baud, can_bitrate)
            core.open()
            _cores[port] = core
        core.add_listener(q)
    bus = Usb2CanBus(core, q)
    return bus


def release_can_bus(bus: Any) -> None:
    if bus is None:
        return
    try:
        bus.shutdown()
    except Exception:
        pass
