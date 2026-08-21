#!/usr/bin/env python3
"""沿 X 正方向平移旧配置中的 slot_line。

用法:
    python3 press_shoes/scripts/shift_slot_line_x.py
    # 然后在终端输入平移量 (mm)
说明:
    当前主流程已经改用 target_point，不再通过 SlotConfig 读取 slot_line。
    本脚本仅用于维护仍保留 slot_line 的旧配置，因此直接按原始 YAML 字典读写。
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SLOT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "slot.json"


def main():
    dx = float(input("请输入沿 X 正方向的平移量 (mm): "))

    loaded = yaml.safe_load(SLOT_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise RuntimeError(f"配置文件不是映射结构: {SLOT_CONFIG_PATH}")

    line_data = loaded.get("slot_line")
    if not isinstance(line_data, dict):
        raise RuntimeError(f"配置文件缺少 slot_line: {SLOT_CONFIG_PATH}")

    p1 = list(line_data["point1"])
    p2 = list(line_data["point2"])
    normal = list(line_data["normal"])

    print(f"\n平移前:")
    print(f"  point1 = {p1}")
    print(f"  point2 = {p2}")
    print(f"  c      = {line_data['c']}")

    p1[0] += dx
    p2[0] += dx
    updated_line = {
        "point1": p1,
        "point2": p2,
        "direction": line_data["direction"],
        "normal": normal,
        "c": round(-(normal[0] * p1[0] + normal[1] * p1[1]), 4),
        "angle_deg": line_data["angle_deg"],
    }
    loaded["slot_line"] = updated_line

    print(f"\n平移后 (dx = {dx} mm):")
    print(f"  point1 = {p1}")
    print(f"  point2 = {p2}")
    print(f"  c      = {updated_line['c']}")

    with SLOT_CONFIG_PATH.open("w", encoding="utf-8") as file:
        yaml.safe_dump(loaded, file, allow_unicode=True, sort_keys=False)
    print(f"\n[OK] 已保存到 {SLOT_CONFIG_PATH}")


if __name__ == "__main__":
    main()
