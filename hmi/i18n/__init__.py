"""HMI 多语言：从 yaml system.hmi.language 读取，运行时 tr() 取文案。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtGui import QFont

from hmi.i18n.locales import de_de, en_us, ja_jp, monitor, ru_ru, vi_vn, zh_cn, zh_tw

SUPPORTED_LANGUAGES: dict[str, str] = {
    "zh-CN": "settings.lang.zh_cn",
    "zh-TW": "settings.lang.zh_tw",
    "en-US": "settings.lang.en_us",
    "vi-VN": "settings.lang.vi_vn",
    "ja-JP": "settings.lang.ja_jp",
    "ru-RU": "settings.lang.ru_ru",
    "de-DE": "settings.lang.de_de",
}

_LOCALES: dict[str, dict[str, str]] = {
    "zh-CN": zh_cn.MESSAGES,
    "zh-TW": zh_tw.MESSAGES,
    "en-US": en_us.MESSAGES,
    "vi-VN": vi_vn.MESSAGES,
    "ja-JP": ja_jp.MESSAGES,
    "ru-RU": ru_ru.MESSAGES,
    "de-DE": de_de.MESSAGES,
}

DEFAULT_LANGUAGE = "zh-CN"

_listeners: list[Callable[[], None]] = []
_current = DEFAULT_LANGUAGE


def _lookup_text(key: str) -> str | None:
    """当前语言 → 主表 → 监控页表 → 英文监控表 → 中文主表。"""
    text = _LOCALES.get(_current, {}).get(key)
    if text is not None:
        return text
    mon = monitor.MONITOR_BY_LANG.get(_current, {})
    if key in mon:
        return mon[key]
    text = _LOCALES.get("en-US", {}).get(key)
    if text is not None:
        return text
    mon_en = monitor.MONITOR_BY_LANG.get("en-US", {})
    if key in mon_en:
        return mon_en[key]
    text = _LOCALES.get(DEFAULT_LANGUAGE, {}).get(key)
    if text is not None:
        return text
    return monitor.MONITOR_BY_LANG.get(DEFAULT_LANGUAGE, {}).get(key)


def tr(key: str, **kwargs: Any) -> str:
    """取当前语言文案；缺 key 时回退中文再回退 key 本身。"""
    text = _lookup_text(key)
    if text is None:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def memory_label(index: int) -> str:
    """Mem[1..10] 显示名（运行监控页）。"""
    return tr(f"monitor.mem.{index}")


def language() -> str:
    return _current


def set_language(lang: str) -> None:
    """切换语言并通知监听者（如 MainWindow.retranslate_ui）。"""
    global _current
    code = lang if lang in _LOCALES else DEFAULT_LANGUAGE
    changed = code != _current
    _current = code
    if not changed:
        return
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def init_from_cfg(cfg: dict[str, Any] | None) -> None:
    """启动时从 cfg 恢复语言（不触发 listener）。"""
    global _current
    hmi = ((cfg or {}).get("system") or {}).get("hmi") or {}
    lang = str(hmi.get("language") or DEFAULT_LANGUAGE)
    _current = lang if lang in _LOCALES else DEFAULT_LANGUAGE


def save_language_to_cfg(cfg: dict[str, Any], lang: str) -> None:
    code = lang if lang in _LOCALES else DEFAULT_LANGUAGE
    cfg.setdefault("system", {}).setdefault("hmi", {})["language"] = code


def add_listener(fn: Callable[[], None]) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[], None]) -> None:
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def nav_label(nav_id: str) -> str:
    return tr(f"nav.{nav_id}")


def apply_ui_font() -> tuple[QFont, str]:
    """按当前语言刷新 QApplication 默认字体。"""
    from hmi.i18n import fonts

    return fonts.apply_ui_font(_current)
