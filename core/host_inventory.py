"""本机 USB 与网卡/IP 枚举（现场对 serial、机器人 IP 用）。

只读 /sys 与 ``ip`` 命令，不改系统配置。Linux 工控机为主；其它系统返回空表。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

_SYS_USB = Path("/sys/bus/usb/devices")
_SYS_NET = Path("/sys/class/net")
_SERIAL_BY_ID = Path("/dev/serial/by-id")
_V4L_BY_ID = Path("/dev/v4l/by-id")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _run_ip_json(args: list[str]) -> Any:
    try:
        proc = subprocess.run(
            ["ip", "-json", *args],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def list_usb_devices() -> list[dict[str, str]]:
    """物理 USB 设备（有 idVendor 的 sysfs 项）。"""
    if not _SYS_USB.is_dir():
        return []
    rows: list[dict[str, str]] = []
    try:
        kids = sorted(_SYS_USB.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for dev in kids:
        vid = _read_text(dev / "idVendor")
        pid = _read_text(dev / "idProduct")
        if not vid or not pid:
            continue
        bus = _read_text(dev / "busnum")
        addr = _read_text(dev / "devnum")
        name = _read_text(dev / "product") or _read_text(dev / "manufacturer")
        maker = _read_text(dev / "manufacturer")
        serial = _read_text(dev / "serial")
        klass = _read_text(dev / "bDeviceClass")
        rows.append(
            {
                "bus": f"{bus}-{addr}" if bus else dev.name,
                "sys": dev.name,
                "vid_pid": f"{vid}:{pid}",
                "name": name or maker or "USB",
                "maker": maker,
                "serial": serial,
                "class": klass,
                "nodes": _usb_child_nodes(dev),
            }
        )
    return rows


def _usb_child_nodes(dev: Path) -> str:
    names: list[str] = []
    try:
        ifaces = list(dev.glob("[0-9]*:[0-9]*.[0-9]*"))
    except OSError:
        ifaces = []
    for iface in ifaces:
        for sub, prefix in (
            ("tty", "/dev/"),
            ("video4linux", "/dev/"),
            ("net", ""),
        ):
            folder = iface / sub
            if not folder.is_dir():
                continue
            try:
                children = sorted(folder.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for child in children:
                names.append(f"{prefix}{child.name}" if prefix else child.name)
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return ", ".join(seen)


def list_dev_links(kind: str) -> list[dict[str, str]]:
    """kind=serial → /dev/serial/by-id；kind=v4l → /dev/v4l/by-id。"""
    root = _SERIAL_BY_ID if kind == "serial" else _V4L_BY_ID
    if not root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for path in entries:
        try:
            real = str(path.resolve())
        except OSError:
            real = str(path)
        rows.append({"name": path.name, "path": str(path), "target": real})
    return rows


def list_net_ifaces() -> list[dict[str, str]]:
    """网卡：状态、MAC、IPv4/IPv6。"""
    data = _run_ip_json(["addr"])
    if isinstance(data, list) and data:
        return _net_from_ip_json(data)
    return _net_from_sysfs()


def _net_from_ip_json(data: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("ifname") or "")
        if not name:
            continue
        mac = str(item.get("address") or "")
        oper = str(item.get("operstate") or "").lower()
        ipv4: list[str] = []
        ipv6: list[str] = []
        for addr in item.get("addr_info") or []:
            if not isinstance(addr, dict):
                continue
            family = str(addr.get("family") or "")
            local = str(addr.get("local") or "")
            prefix = addr.get("prefixlen")
            if not local:
                continue
            text = f"{local}/{prefix}" if prefix is not None else local
            if family == "inet":
                ipv4.append(text)
            elif family == "inet6":
                ipv6.append(text)
        rows.append(
            {
                "iface": name,
                "state": oper or _read_text(_SYS_NET / name / "operstate"),
                "ipv4": ", ".join(ipv4),
                "ipv6": ", ".join(ipv6),
                "mac": mac,
                "kind": _iface_kind(name),
            }
        )
    rows.sort(key=lambda r: r["iface"])
    return rows


def _net_from_sysfs() -> list[dict[str, str]]:
    if not _SYS_NET.is_dir():
        return []
    rows: list[dict[str, str]] = []
    try:
        ifaces = sorted(_SYS_NET.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    host_ip = ""
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        host_ip = ""
    for iface in ifaces:
        name = iface.name
        rows.append(
            {
                "iface": name,
                "state": _read_text(iface / "operstate"),
                "ipv4": host_ip if name != "lo" else "127.0.0.1",
                "ipv6": "",
                "mac": _read_text(iface / "address"),
                "kind": _iface_kind(name),
            }
        )
    return rows


def _iface_kind(name: str) -> str:
    if name == "lo":
        return "回环"
    if name.startswith("can"):
        return "CAN"
    if name.startswith(("docker", "br-", "veth", "virbr")):
        return "虚拟"
    if name.startswith("wl"):
        return "无线"
    return "以太网"


def default_gateway() -> str:
    data = _run_ip_json(["route"])
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if str(item.get("dst") or "") != "default":
                continue
            gw = str(item.get("gateway") or "")
            dev = str(item.get("dev") or "")
            if gw and dev:
                return f"{gw}  ({dev})"
            return gw or dev
    return ""


def collect() -> dict[str, Any]:
    """一次扫描，供 HMI 与复制文本共用。"""
    usb = list_usb_devices()
    net = list_net_ifaces()
    return {
        "hostname": hostname(),
        "gateway": default_gateway(),
        "usb": usb,
        "net": net,
        "serial": list_dev_links("serial"),
        "v4l": list_dev_links("v4l"),
        "uid": os.geteuid() if hasattr(os, "geteuid") else -1,
    }


def format_report(data: dict[str, Any] | None = None) -> str:
    """整页复制用纯文本。"""
    snap = data if data is not None else collect()
    lines = [
        f"主机名: {snap.get('hostname') or '-'}",
        f"默认网关: {snap.get('gateway') or '-'}",
        "",
        "=== 网络 / IP ===",
    ]
    nets: list[dict[str, str]] = list(snap.get("net") or [])
    if not nets:
        lines.append("（未读到网卡）")
    for row in nets:
        lines.append(
            f"{row['iface']} [{row['kind']}] {row['state']}  "
            f"IPv4={row['ipv4'] or '-'}  IPv6={row['ipv6'] or '-'}  MAC={row['mac'] or '-'}"
        )
    lines.append("")
    lines.append("=== USB ===")
    usbs: list[dict[str, str]] = list(snap.get("usb") or [])
    if not usbs:
        lines.append("（未读到 USB。检查 /sys/bus/usb 或是否在容器内）")
    for row in usbs:
        extra = f"  节点={row['nodes']}" if row.get("nodes") else ""
        ser = f"  serial={row['serial']}" if row.get("serial") else ""
        lines.append(
            f"{row['bus']}  {row['vid_pid']}  {row['name']}{ser}{extra}"
        )
    lines.append("")
    lines.append("=== 串口 by-id ===")
    serials: list[dict[str, str]] = list(snap.get("serial") or [])
    if not serials:
        lines.append("（无 /dev/serial/by-id）")
    for row in serials:
        lines.append(f"{row['name']} → {row['target']}")
    lines.append("")
    lines.append("=== 相机 V4L by-id ===")
    cams: list[dict[str, str]] = list(snap.get("v4l") or [])
    if not cams:
        lines.append("（无 /dev/v4l/by-id）")
    for row in cams:
        lines.append(f"{row['name']} → {row['target']}")
    return "\n".join(lines)


def summary_line(data: dict[str, Any] | None = None) -> str:
    snap = data if data is not None else collect()
    host = str(snap.get("hostname") or "-")
    gw = str(snap.get("gateway") or "-")
    n_usb = len(snap.get("usb") or [])
    n_net = len(snap.get("net") or [])
    uid = snap.get("uid", "-")
    return (
        f"主机 {host}  |  网关 {gw}  |  网卡 {n_net}  |  USB {n_usb}  |  uid={uid}"
    )
