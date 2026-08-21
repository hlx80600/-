"""HMI 统一按钮/控件样式（工业面板：按用途上色，便于一眼区分）。"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

# 角色 → 底色 / 悬停 / 按下
_ROLE = {
    "primary": ("#1a5276", "#2471a3", "#154360"),  # 蓝：主操作/撤回/应用
    "success": ("#1a7a37", "#229954", "#145a32"),  # 绿：保存/新增/启动
    "danger": ("#c0392b", "#e74c3c", "#922b21"),  # 红：删除/急停/停止
    "warn": ("#b9770e", "#d68910", "#7e5103"),  # 琥珀：暂停/清除/跳过
    "motion": ("#117a65", "#148f77", "#0e6655"),  # 青绿：MoveJ/MoveL
    "neutral": ("#5d6d7e", "#7f8c8d", "#34495e"),  # 灰：次要
    "accent": ("#6c3483", "#8e44ad", "#4a235a"),  # 紫灰：模式切换（少用）
}


def button_qss(role: str = "neutral", *, bold: bool = True, tall: bool = True) -> str:
    """生成单个按钮样式。"""
    bg, hover, pressed = _ROLE.get(role, _ROLE["neutral"])
    weight = "bold" if bold else "normal"
    pad = "8px 12px" if tall else "5px 10px"
    minh = "36px" if tall else "28px"
    return f"""
    QPushButton {{
        background-color: {bg};
        color: #ffffff;
        font-weight: {weight};
        padding: {pad};
        min-height: {minh};
        border: none;
        border-radius: 5px;
    }}
    QPushButton:hover {{
        background-color: {hover};
    }}
    QPushButton:pressed {{
        background-color: {pressed};
    }}
    QPushButton:disabled {{
        background-color: #95a5a6;
        color: #ecf0f1;
    }}
    """


def style_button(btn: QPushButton, role: str = "neutral", **kwargs) -> QPushButton:
    btn.setStyleSheet(button_qss(role, **kwargs))
    from PySide6.QtCore import Qt

    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    return btn


def style_many(pairs: list[tuple[QPushButton, str]]) -> None:
    for btn, role in pairs:
        style_button(btn, role)


def groupbox_qss(accent: str = "#1a5276") -> str:
    return f"""
    QGroupBox {{
        font-weight: bold;
        border: 1px solid #c5d0dc;
        border-radius: 6px;
        margin-top: 12px;
        padding: 10px 8px 8px 8px;
        background: #fafbfc;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 8px;
        color: {accent};
    }}
    QCheckBox {{
        spacing: 8px;
        min-height: 22px;
    }}
    """


def apply_page_chrome(widget: QWidget, *, accent: str = "#1a5276") -> None:
    """给页面套一层统一 GroupBox 风格（不影响内部已单独设色的按钮）。"""
    existing = widget.styleSheet() or ""
    widget.setStyleSheet(existing + "\n" + groupbox_qss(accent))
