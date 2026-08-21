"""
加载 / 保存 YAML 配置。

★★★ 机器人 IP、压鞋机 IP、点位、夹爪 CAN 等现场参数 ★★★
全部写在：项目根目录 / config / default.yaml
本模块只负责读写该文件，不要在这里写死 IP。

改地址步骤：
  1. 打开 config/default.yaml
  2. 找到 robots.robot1.ip / robots.robot2.ip
  3. 改完保存；若改了 use_mock，重启 main.py
也可在 HMI「通信配置」页改 IP 后点「保存配置」（调用本文件的 save_config）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]
# ★ 唯一默认配置文件路径（机器人地址就在这个 yaml 里）
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"


def load_config(path: Path | None = None) -> Dict[str, Any]:
    """读取 default.yaml（或指定 path）。"""
    p = path or DEFAULT_CONFIG
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    data: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    return data


def save_config(data: Dict[str, Any], path: Path | None = None) -> None:
    """把内存里的 cfg 写回 default.yaml（HMI 保存按钮会调这里）。"""
    p = path or DEFAULT_CONFIG
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
