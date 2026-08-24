"""按界面语言选择字体族并应用到整个 Qt 应用。"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

# 每种语言优先字体栈（从左到右，取系统已安装的第一个）
_FONT_CANDIDATES: dict[str, list[str]] = {
    "zh-CN": [
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei",
        "PingFang SC",
        "Sans Serif",
    ],
    "zh-TW": [
        "Noto Sans CJK TC",
        "Source Han Sans TC",
        "PingFang TC",
        "Microsoft JhengHei",
        "WenQuanYi Micro Hei",
        "Sans Serif",
    ],
    "ja-JP": [
        "Noto Sans CJK JP",
        "Source Han Sans JP",
        "IPAPGothic",
        "Yu Gothic UI",
        "Meiryo",
        "Sans Serif",
    ],
    "ru-RU": [
        "Noto Sans",
        "Roboto",
        "DejaVu Sans",
        "Ubuntu",
        "Sans Serif",
    ],
    "de-DE": [
        "Noto Sans",
        "Roboto",
        "Segoe UI",
        "Ubuntu",
        "Sans Serif",
    ],
    "vi-VN": [
        "Noto Sans",
        "Roboto",
        "Segoe UI",
        "Ubuntu",
        "Sans Serif",
    ],
    "en-US": [
        "Noto Sans",
        "Roboto",
        "Segoe UI",
        "Ubuntu",
        "Sans Serif",
    ],
}

# CJK 略大一点，避免笔画挤在一起
_POINT_SIZE_CJK = 11
_POINT_SIZE_DEFAULT = 10


def _available_families() -> set[str]:
    return set(QFontDatabase.families())


def font_family_for(lang: str) -> str:
    """返回当前语言应使用的 font-family 名称。"""
    cands = _FONT_CANDIDATES.get(lang) or _FONT_CANDIDATES["en-US"]
    installed = _available_families()
    for name in cands:
        if name in installed:
            return name
    return cands[-1]


def build_app_font(lang: str) -> QFont:
    """构造应用级 QFont。"""
    family = font_family_for(lang)
    pt = _POINT_SIZE_CJK if lang in ("zh-CN", "zh-TW", "ja-JP") else _POINT_SIZE_DEFAULT
    font = QFont(family, pt)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    return font


def apply_to_app(app: QApplication | None, lang: str) -> str:
    """设置 QApplication 默认字体；返回 font-family 供 QSS 使用。"""
    if app is None:
        return font_family_for(lang)
    font = build_app_font(lang)
    app.setFont(font)
    return font.family()


def apply_font_to_widget(root: QWidget | None, font: QFont) -> None:
    """递归 setFont；保留显式 monospace 的数据区。"""
    if root is None:
        return
    for w in [root, *root.findChildren(QWidget)]:
        ss = (w.styleSheet() or "").lower()
        if "monospace" in ss:
            continue
        w.setFont(font)


def apply_ui_font(lang: str) -> tuple[QFont, str]:
    """应用语言字体到 QApplication；返回 (QFont, family)。"""
    app = QApplication.instance()
    family = font_family_for(lang)
    font = build_app_font(lang)
    if app is not None:
        app.setFont(font)
    return font, family
