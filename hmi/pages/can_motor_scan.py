"""HMI：扫描 SocketCAN 上的达妙电机，把 interface/can_id 填回配置表。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devices.gripper_can import scan_damiao_motors
from hmi.style import style_button


def _hits(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in list(report.get("motors") or []):
        if m.get("error"):
            continue
        if int(m.get("can_id", 0) or 0) <= 0:
            continue
        rows.append(m)
    return rows


def run_can_motor_scan(parent: QWidget | None) -> list[dict[str, Any]] | None:
    """弹出进度并扫描。确定返回命中列表（可空）；取消扫描或关窗返回 None。"""
    prog = QProgressDialog("正在扫描 CAN 电机…", "取消", 0, 100, parent)
    prog.setWindowTitle("扫描达妙电机")
    prog.setWindowModality(Qt.WindowModality.ApplicationModal)
    prog.setMinimumDuration(0)
    prog.setValue(0)

    def _progress(done: int, total: int, text: str) -> None:
        prog.setMaximum(max(int(total), 1))
        prog.setValue(int(done))
        prog.setLabelText(text)
        QApplication.processEvents()

    report = scan_damiao_motors(
        progress=_progress,
        cancelled=prog.wasCanceled,
    )
    if prog.wasCanceled():
        prog.close()
        return None
    prog.close()

    dlg = QDialog(parent)
    dlg.setWindowTitle("扫描结果")
    dlg.resize(720, 420)
    root = QVBoxLayout(dlg)
    hint = QLabel(str(report.get("hint") or ""))
    hint.setWordWrap(True)
    root.addWidget(hint)

    ifaces = list(report.get("ifaces") or [])
    if ifaces:
        parts = [f"{r.get('name')}({r.get('state')})" for r in ifaces]
        lb = QLabel("本机 CAN 口：" + "、".join(parts))
        lb.setWordWrap(True)
        root.addWidget(lb)

    tbl = QTableWidget(0, 5)
    tbl.setHorizontalHeaderLabels(
        ["接口", "can_id", "电机号", "反馈ID", "说明"]
    )
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    hits = _hits(report)
    errors = [m for m in list(report.get("motors") or []) if m.get("error")]
    tbl.setRowCount(len(hits) + len(errors))
    for i, m in enumerate(hits):
        cid = int(m.get("can_id", 0))
        fid = int(m.get("feedback_id", 0))
        vals = [
            str(m.get("interface", "")),
            f"0x{cid:X}",
            str(int(m.get("motor_id", 0))),
            f"0x{fid:X}",
            str(m.get("source") or "ok"),
        ]
        for c, text in enumerate(vals):
            tbl.setItem(i, c, QTableWidgetItem(text))
    for j, m in enumerate(errors):
        r = len(hits) + j
        vals = [
            str(m.get("interface", "")),
            "-",
            "-",
            "-",
            str(m.get("error", "")),
        ]
        for c, text in enumerate(vals):
            tbl.setItem(r, c, QTableWidgetItem(text))
    root.addWidget(tbl)

    row = QHBoxLayout()
    btn_apply = QPushButton("填入通信配置电机表")
    style_button(btn_apply, "success")
    row.addWidget(btn_apply)
    row.addStretch(1)
    root.addLayout(row)

    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dlg.reject)
    box.accepted.connect(dlg.reject)
    root.addWidget(box)

    chosen: list[dict[str, Any]] = []

    def _apply() -> None:
        chosen.clear()
        chosen.extend(hits)
        dlg.accept()

    btn_apply.clicked.connect(_apply)
    if not hits:
        btn_apply.setEnabled(False)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return chosen
