"""HMI 统一按钮/控件样式（工业面板：按用途上色，便于一眼区分）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from hmi import ui_scale

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

# 页内边距（设计像素；实际绘制用 ui_scale.px）
PAGE_MARGIN_L = 12
PAGE_MARGIN_T = 10
PAGE_MARGIN_R = 12
PAGE_MARGIN_B = 12
PAGE_SPACING = 10

_CHROME_MARK = "/*hmi-page-chrome*/"


def _px(design: float, *, min_v: int = 1) -> int:
    return ui_scale.px(design, min_v=min_v)


def _fpx(design: float, *, min_v: int = 12) -> int:
    return ui_scale.font_px(design, min_v=min_v)


def button_qss(role: str = "neutral", *, bold: bool = True, tall: bool = True) -> str:
    """生成单个按钮样式。"""
    bg, hover, pressed = _ROLE.get(role, _ROLE["neutral"])
    weight = "bold" if bold else "normal"
    pad = (
        f"{_px(6)}px {_px(12)}px"
        if tall
        else f"{_px(4)}px {_px(8)}px"
    )
    minh = f"{_px(36, min_v=32)}px" if tall else f"{_px(28, min_v=26)}px"
    return f"""
    QPushButton {{
        background-color: {bg};
        color: #ffffff;
        font-weight: {weight};
        padding: {pad};
        min-height: {minh};
        border: none;
        border-radius: {_px(5)}px;
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
    btn.setProperty("hmi_role", str(role))
    btn.setProperty("hmi_tall", bool(kwargs.get("tall", True)))
    btn.setProperty("hmi_bold", bool(kwargs.get("bold", True)))
    btn.setStyleSheet(button_qss(role, **kwargs))
    from PySide6.QtCore import Qt

    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    return btn


def style_many(pairs: list[tuple[QPushButton, str]]) -> None:
    for btn, role in pairs:
        style_button(btn, role)


def restyle_role_buttons(root: QWidget) -> None:
    """窗口比例变化后，按记下的角色重刷按钮尺寸。"""
    for btn in root.findChildren(QPushButton):
        role = btn.property("hmi_role")
        if not role:
            continue
        tall = btn.property("hmi_tall")
        bold = btn.property("hmi_bold")
        style_button(
            btn,
            str(role),
            tall=True if tall is None else bool(tall),
            bold=True if bold is None else bool(bold),
        )


def groupbox_qss(accent: str = "#1a5276") -> str:
    return f"""
    QGroupBox {{
        font-weight: bold;
        border: 1px solid #c5d0dc;
        border-radius: {_px(6)}px;
        margin-top: {_px(16)}px;
        padding: {_px(16)}px {_px(10)}px {_px(12)}px {_px(10)}px;
        background: #fafbfc;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: {_px(10)}px;
        padding: 0 {_px(8)}px;
        color: {accent};
    }}
    QCheckBox {{
        spacing: {_px(8)}px;
        min-height: {_px(26, min_v=22)}px;
    }}
    """


def chrome_qss() -> str:
    """页签、输入框、表格、滑条：主窗口 stylesheet 一并套上，各页共用。

    Spin/Combo 不能左右对称 padding，否则右侧箭头会被压成两条细线。
    """
    return f"""
    QTabWidget::pane {{
        border: 1px solid #c5d0dc;
        border-radius: {_px(6)}px;
        top: -1px;
        background: #f7f9fb;
        padding: {_px(8)}px;
    }}
    QTabBar::tab {{
        background: #dce3ea;
        color: #2c3e50;
        min-height: {_px(32, min_v=28)}px;
        min-width: {_px(96, min_v=80)}px;
        padding: {_px(6)}px {_px(14)}px;
        margin-right: {_px(3)}px;
        border: 1px solid #c5d0dc;
        border-bottom: none;
        border-top-left-radius: {_px(6)}px;
        border-top-right-radius: {_px(6)}px;
        font-size: {_fpx(14)}px;
    }}
    QTabBar::tab:selected {{
        background: #f7f9fb;
        color: #1a5276;
        font-weight: bold;
    }}
    QTabBar::tab:hover:!selected {{
        background: #e8eef3;
    }}
    QLineEdit {{
        min-height: {_px(32, min_v=28)}px;
        padding: {_px(4)}px {_px(8)}px;
        border: 1px solid #b0bec5;
        border-radius: {_px(4)}px;
        background: #ffffff;
        selection-background-color: #1a5276;
        selection-color: #ffffff;
        font-size: {_fpx(13)}px;
    }}
    QPlainTextEdit, QTextEdit {{
        padding: {_px(6)}px {_px(8)}px;
        border: 1px solid #b0bec5;
        border-radius: {_px(4)}px;
        background: #ffffff;
        selection-background-color: #1a5276;
        selection-color: #ffffff;
        font-size: {_fpx(13)}px;
    }}
    QSpinBox, QDoubleSpinBox {{
        min-height: {_px(32, min_v=28)}px;
        min-width: {_px(72, min_v=64)}px;
        padding-top: {_px(2)}px;
        padding-bottom: {_px(2)}px;
        padding-left: {_px(8)}px;
        padding-right: 2px;
        border: 1px solid #b0bec5;
        border-radius: {_px(4)}px;
        background: #ffffff;
        selection-background-color: #1a5276;
        selection-color: #ffffff;
        font-size: {_fpx(13)}px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        width: {_px(22, min_v=18)}px;
    }}
    QComboBox {{
        min-height: {_px(32, min_v=28)}px;
        padding-top: {_px(2)}px;
        padding-bottom: {_px(2)}px;
        padding-left: {_px(8)}px;
        padding-right: {_px(4)}px;
        border: 1px solid #b0bec5;
        border-radius: {_px(4)}px;
        background: #ffffff;
        selection-background-color: #1a5276;
        selection-color: #ffffff;
        font-size: {_fpx(13)}px;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: {_px(24, min_v=20)}px;
        border: none;
    }}
    QComboBox QAbstractItemView {{
        padding: {_px(4)}px;
        min-height: {_px(28, min_v=24)}px;
        background: #ffffff;
        selection-background-color: #1a5276;
        selection-color: #ffffff;
        outline: none;
        font-size: {_fpx(13)}px;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid #1a5276;
    }}
    QSlider::groove:horizontal {{
        height: {_px(8, min_v=6)}px;
        background: #d5dbe0;
        border-radius: {_px(4)}px;
    }}
    QSlider::handle:horizontal {{
        width: {_px(18, min_v=14)}px;
        height: {_px(18, min_v=14)}px;
        margin: -{_px(6)}px 0;
        background: #1a5276;
        border-radius: {_px(9)}px;
    }}
    QTableWidget {{
        gridline-color: #d5dbe0;
        background: #ffffff;
        alternate-background-color: #f4f7fa;
        border: 1px solid #c5d0dc;
        border-radius: {_px(4)}px;
        font-size: {_fpx(13)}px;
    }}
    QHeaderView::section {{
        background: #e8eef3;
        color: #1a5276;
        padding: {_px(6)}px {_px(8)}px;
        border: none;
        border-right: 1px solid #c5d0dc;
        border-bottom: 1px solid #c5d0dc;
        font-weight: bold;
        font-size: {_fpx(13)}px;
    }}
    QProgressBar {{
        border: 1px solid #c5d0dc;
        border-radius: {_px(4)}px;
        text-align: center;
        background: #eef1f4;
        min-height: {_px(16, min_v=12)}px;
        font-size: {_fpx(12)}px;
    }}
    QProgressBar::chunk {{
        background: #1a7a37;
        border-radius: {_px(3)}px;
    }}
    QToolTip {{
        padding: {_px(6)}px {_px(10)}px;
        background: #273746;
        color: #ecf0f1;
        border: 1px solid #1a5276;
        font-size: {_fpx(13, min_v=12)}px;
    }}
    """


def apply_page_layout(widget: QWidget) -> None:
    """统一页边距与段间距；主动铺满（四边 0）的总页不改。"""
    if bool(widget.property("hmi_lock_margins")):
        return
    lay: QLayout | None = widget.layout()
    if lay is None:
        return
    m = lay.contentsMargins()
    if m.left() == 0 and m.top() == 0 and m.right() == 0 and m.bottom() == 0:
        return
    lay.setContentsMargins(
        _px(PAGE_MARGIN_L),
        _px(PAGE_MARGIN_T),
        _px(PAGE_MARGIN_R),
        _px(PAGE_MARGIN_B),
    )
    lay.setSpacing(_px(PAGE_SPACING))


def _keep_groupbox_tall_enough(widget: QWidget) -> None:
    """分组框可长高、不可被并排布局压矮，避免裁掉按钮和提示。"""
    for box in widget.findChildren(QGroupBox):
        if bool(box.property("hmi_fill")):
            continue
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)


def apply_page_chrome(widget: QWidget, *, accent: str = "#1a5276") -> None:
    """给页面套一层统一 GroupBox 风格（不影响内部已单独设色的按钮）。"""
    apply_page_layout(widget)
    existing = widget.styleSheet() or ""
    if _CHROME_MARK in existing:
        existing = existing.split(_CHROME_MARK)[0].rstrip()
    widget.setStyleSheet(existing + f"\n{_CHROME_MARK}\n" + groupbox_qss(accent))
    _keep_groupbox_tall_enough(widget)


def hbox_pair(
    left: QWidget,
    right: QWidget,
    *,
    stretch_l: int = 1,
    stretch_r: int = 1,
) -> QHBoxLayout:
    """两列并排；高度跟内容走，不被对侧压矮。"""
    left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    row = QHBoxLayout()
    row.setSpacing(_px(PAGE_SPACING))
    row.addWidget(left, stretch_l)
    row.addWidget(right, stretch_r)
    return row
