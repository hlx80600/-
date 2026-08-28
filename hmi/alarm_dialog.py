"""报警弹窗：全文可选中、一键复制。"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hmi.style import style_button


def format_alarm_text(
    code: str,
    station: str,
    step: int,
    message: str,
    *,
    time: str = "",
) -> str:
    head = f"时间: {time}\n" if time else ""
    return (
        f"{head}"
        f"报警代码: {code}\n"
        f"工位: {station}\n"
        f"步号: {step}\n"
        f"内容:\n{message}"
    )


def show_copyable_alarm(
    parent: QWidget | None,
    *,
    code: str,
    station: str = "",
    step: int = 0,
    message: str = "",
    extra: str = "",
    time: str = "",
) -> None:
    """弹出可复制的报警对话框（QTextEdit + 复制按钮）。"""
    body = format_alarm_text(code, station, step, message, time=time)
    if extra:
        body = f"{body}\n\n{extra}"

    dlg = QDialog(parent)
    dlg.setWindowTitle(f"报警 {code}")
    dlg.resize(640, 420)
    root = QVBoxLayout(dlg)

    tip = QLabel("下方全文可选中；也可点「复制全部」粘贴到聊天/日志。")
    tip.setWordWrap(True)
    root.addWidget(tip)

    edit = QTextEdit()
    edit.setReadOnly(True)
    edit.setPlainText(body)
    edit.selectAll()
    root.addWidget(edit)

    row = QHBoxLayout()
    btn_copy = QPushButton("复制全部")
    style_button(btn_copy, "primary")

    def _copy() -> None:
        QGuiApplication.clipboard().setText(edit.toPlainText())
        btn_copy.setText("已复制")

    btn_copy.clicked.connect(_copy)
    row.addWidget(btn_copy)
    row.addStretch(1)
    root.addLayout(row)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dlg.accept)
    root.addWidget(buttons)

    dlg.exec()


def copy_text_to_clipboard(text: str, parent: QWidget | None = None) -> None:
    QGuiApplication.clipboard().setText(text or "")
    if parent is not None:
        QMessageBox.information(parent, "已复制", "报警信息已复制到剪贴板")
