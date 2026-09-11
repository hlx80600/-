"""压机信号 · 中科院协议点表：只显示中文含义与当前值，不展示 PLC 符号。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from devices.plc_cas_points import (
    PROTOCOL_IP_HINT,
    PROTOCOL_PORT_HINT,
    CasPoint,
)
from devices.press_modbus import PressMachine
from hmi import ui_scale
from hmi.style import apply_page_chrome, style_button

_HOLD_ROWS: tuple[tuple[str, tuple[str, str, str, str]], ...] = (
    ("压着时间", ("s1_hold_set", "s2_hold_set", "s3_hold_set", "s4_hold_set")),
    ("时间显示", ("s1_hold_show", "s2_hold_show", "s3_hold_show", "s4_hold_show")),
    ("压着计数", ("s1_count", "s2_count", "s3_count", "s4_count")),
    ("计数清零", ("s1_count_clr", "s2_count_clr", "s3_count_clr", "s4_count_clr")),
)
_HOLD_IDS = {pid for _, ids in _HOLD_ROWS for pid in ids}


@dataclass
class _RowUi:
    point: CasPoint
    value_lbl: QLabel
    spin: QSpinBox | None = None


def _px(design: float, *, min_v: int = 1) -> int:
    return ui_scale.px(design, min_v=min_v)


def _fpx(design: float, *, min_v: int = 12) -> int:
    return ui_scale.font_px(design, min_v=min_v)


def _chip_qss(*, mode: str, on: bool = False) -> str:
    pad = f"{_px(6)}px {_px(12)}px"
    radius = _px(8)
    if mode == "fail":
        return (
            f"background:#fadbd8;color:#922b21;padding:{pad};"
            f"border-radius:{radius}px;font-weight:bold;font-size:{_fpx(15)}px;"
        )
    if mode == "bit":
        if on:
            return (
                f"background:#d5f5e3;color:#145a32;padding:{pad};"
                f"border-radius:{radius}px;font-weight:bold;font-size:{_fpx(16)}px;"
            )
        return (
            f"background:#eaecee;color:#5d6d7e;padding:{pad};"
            f"border-radius:{radius}px;font-weight:bold;font-size:{_fpx(16)}px;"
        )
    return (
        f"background:#eaf2f8;color:#1a5276;padding:{pad};"
        f"border-radius:{radius}px;font-weight:bold;font-size:{_fpx(18)}px;"
    )


class CasPlcPointsWidget(QWidget):
    """中科院点表：分组卡片 + 工位并排，适合工控屏。"""

    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._rows: list[_RowUi] = []

        root = QVBoxLayout(self)
        root.addWidget(self._build_header())

        points = self.ctx.press.cas_point_list()
        by_group: OrderedDict[str, list[CasPoint]] = OrderedDict()
        for pt in points:
            by_group.setdefault(pt.group, []).append(pt)
        by_id = {p.id: p for p in points}

        if "联机" in by_group:
            root.addWidget(self._build_online(by_group["联机"]))

        man_boxes = [
            self._build_station_box(title, pts)
            for title, pts in by_group.items()
            if title.startswith("压机手动")
        ]
        if man_boxes:
            root.addWidget(self._section_label("压机手动（四工位）"))
            root.addWidget(self._grid_cards(man_boxes, columns=2))

        rod_boxes = [
            self._build_station_box(title, pts)
            for title, pts in by_group.items()
            if title.startswith("压杆操作")
        ]
        if rod_boxes:
            root.addWidget(self._section_label("压杆（四工位）"))
            root.addWidget(self._grid_cards(rod_boxes, columns=2))

        if "公共参数" in by_group:
            root.addWidget(self._build_station_box("公共参数", by_group["公共参数"]))

        hold_pts = [by_id[i] for _, ids in _HOLD_ROWS for i in ids if i in by_id]
        if hold_pts:
            root.addWidget(self._build_hold_table(by_id))

        placed = {"联机", "公共参数", "压着时间与计数"}
        for title, pts in by_group.items():
            if title in placed or title.startswith("压机手动") or title.startswith("压杆操作"):
                continue
            leftover = [p for p in pts if p.id not in _HOLD_IDS]
            if leftover:
                root.addWidget(self._build_station_box(title, leftover))

        root.addStretch(1)
        apply_page_chrome(self)

    def _section_label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(
            f"color:#1a5276;font-weight:bold;font-size:{_fpx(16)}px;padding-top:{_px(4)}px;"
        )
        return lb

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("casHeader")
        bar.setStyleSheet(
            f"""
            QFrame#casHeader {{
                background: #ffffff;
                border: 1px solid #c5d0dc;
                border-radius: {_px(8)}px;
            }}
            """
        )
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(_px(12), _px(10), _px(12), _px(10))
        lay.setSpacing(_px(8))

        row = QHBoxLayout()
        title = QLabel("中科院四工位点表")
        title.setStyleSheet(
            f"color:#1a5276;font-weight:bold;font-size:{_fpx(18)}px;"
        )
        row.addWidget(title)
        row.addStretch(1)
        self.lbl_link = QLabel("—")
        self.lbl_link.setAlignment(Qt.AlignCenter)
        self.lbl_link.setMinimumWidth(_px(140))
        row.addWidget(self.lbl_link)
        btn_refresh = style_button(QPushButton("刷新"), "primary")
        btn_all = style_button(QPushButton("全部读取"), "success")
        btn_refresh.clicked.connect(self.refresh)
        btn_all.clicked.connect(self.refresh)
        row.addWidget(btn_refresh)
        row.addWidget(btn_all)
        lay.addLayout(row)

        chips = QHBoxLayout()
        chips.setSpacing(_px(8))
        for text in (
            f"协议默认 {PROTOCOL_IP_HINT}:{PROTOCOL_PORT_HINT}",
            "本页不改通信配置里的压机 IP",
            "空闲/放鞋完成：1=有效",
            "启动：1=空转　2=启动",
        ):
            chip = QLabel(text)
            chip.setStyleSheet(
                f"background:#eef3f7;color:#34495e;padding:{_px(4)}px {_px(10)}px;"
                f"border-radius:{_px(12)}px;font-size:{_fpx(12)}px;"
            )
            chips.addWidget(chip)
        chips.addStretch(1)
        lay.addLayout(chips)
        return bar

    def _grid_cards(self, boxes: list[QWidget], *, columns: int) -> QWidget:
        wrap = QWidget()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_px(10))
        for i, box in enumerate(boxes):
            grid.addWidget(box, i // columns, i % columns)
            grid.setColumnStretch(i % columns, 1)
        return wrap

    def _build_online(self, points: list[CasPoint]) -> QGroupBox:
        box = QGroupBox("联机")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(_px(12))
        grid.setVerticalSpacing(_px(10))
        for col, pt in enumerate(points):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background:#ffffff;border:1px solid #d5dde5;"
                f"border-radius:{_px(8)}px; }}"
            )
            inner = QVBoxLayout(card)
            inner.setContentsMargins(_px(10), _px(8), _px(10), _px(8))
            inner.setSpacing(_px(8))
            inner.addWidget(self._name_block(pt))
            ui = self._make_row(pt)
            inner.addWidget(ui.value_lbl)
            acts = self._action_widget(pt, ui)
            if acts is not None:
                inner.addWidget(acts)
            inner.addStretch(1)
            grid.addWidget(card, 0, col)
            grid.setColumnStretch(col, 1)
        return box

    def _build_station_box(self, title: str, points: list[CasPoint]) -> QGroupBox:
        short = title.replace("压机手动 · ", "").replace("压杆操作 · ", "")
        box = QGroupBox(short)
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(_px(8))
        grid.setVerticalSpacing(_px(8))
        for r, pt in enumerate(points):
            self._place_row(grid, r, pt, show_name=True)
        return box

    def _build_hold_table(self, by_id: dict[str, CasPoint]) -> QGroupBox:
        box = QGroupBox("压着时间与计数")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(_px(8))
        grid.setVerticalSpacing(_px(10))
        head_qss = (
            f"color:#1a5276;font-weight:bold;font-size:{_fpx(14)}px;"
            f"padding:{_px(4)}px;"
        )
        grid.addWidget(QLabel(""), 0, 0)
        for col, name in enumerate(("工位1", "工位2", "工位3", "工位4"), start=1):
            lb = QLabel(name)
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(head_qss)
            grid.addWidget(lb, 0, col)
            grid.setColumnStretch(col, 1)
        for r, (row_name, ids) in enumerate(_HOLD_ROWS, start=1):
            name = QLabel(row_name)
            name.setStyleSheet("font-weight:bold;color:#1c2833;")
            grid.addWidget(name, r, 0)
            for col, pid in enumerate(ids, start=1):
                pt = by_id.get(pid)
                if pt is None:
                    continue
                cell = QWidget()
                v = QVBoxLayout(cell)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(_px(6))
                ui = self._make_row(pt)
                v.addWidget(ui.value_lbl)
                acts = self._action_widget(pt, ui)
                if acts is not None:
                    v.addWidget(acts)
                grid.addWidget(cell, r, col)
        return box

    def _name_block(self, pt: CasPoint) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(_px(2))
        title = QLabel(pt.label)
        title.setStyleSheet(
            f"font-weight:bold;color:#1c2833;font-size:{_fpx(14)}px;"
        )
        title.setWordWrap(True)
        v.addWidget(title)
        if pt.hint:
            hint = QLabel(pt.hint)
            hint.setStyleSheet(f"color:#7f8c8d;font-size:{_fpx(12)}px;")
            hint.setWordWrap(True)
            v.addWidget(hint)
        return w

    def _make_row(self, pt: CasPoint) -> _RowUi:
        val = QLabel("—")
        val.setAlignment(Qt.AlignCenter)
        val.setMinimumWidth(_px(72))
        val.setMinimumHeight(_px(36))
        val.setStyleSheet(_chip_qss(mode="num"))
        val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ui = _RowUi(point=pt, value_lbl=val)
        self._rows.append(ui)
        return ui

    def _place_row(
        self,
        grid: QGridLayout,
        row: int,
        pt: CasPoint,
        *,
        show_name: bool,
    ) -> None:
        ui = self._make_row(pt)
        col = 0
        if show_name:
            grid.addWidget(self._name_block(pt), row, 0)
            col = 1
        grid.addWidget(ui.value_lbl, row, col)
        acts = self._action_widget(pt, ui)
        if acts is not None:
            grid.addWidget(acts, row, col + 1)
        else:
            grid.setColumnStretch(col, 1)

    def _action_widget(self, pt: CasPoint, ui: _RowUi) -> QWidget | None:
        if pt.rw == "rw" and pt.plc_kind == "M":
            btn_on = style_button(QPushButton("开"), "success")
            btn_off = style_button(QPushButton("关"), "neutral")
            btn_on.clicked.connect(lambda _=False, p=pt: self._write_m(p, True))
            btn_off.clicked.connect(lambda _=False, p=pt: self._write_m(p, False))
            cell = QWidget()
            wr = QHBoxLayout(cell)
            wr.setContentsMargins(0, 0, 0, 0)
            wr.setSpacing(_px(6))
            wr.addWidget(btn_on)
            wr.addWidget(btn_off)
            return cell
        if pt.rw == "rw" and pt.plc_kind == "D":
            sp = QSpinBox()
            sp.setRange(0, 65535)
            sp.setMinimumWidth(_px(96))
            sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
            ui.spin = sp
            btn_w = style_button(QPushButton("写入"), "motion")
            btn_w.clicked.connect(lambda _=False, p=pt, s=sp: self._write_d(p, s))
            cell = QWidget()
            wr = QHBoxLayout(cell)
            wr.setContentsMargins(0, 0, 0, 0)
            wr.setSpacing(_px(6))
            wr.addWidget(sp, 1)
            wr.addWidget(btn_w)
            return cell
        return None

    def _press(self) -> PressMachine:
        return self.ctx.press

    def _write_m(self, point: CasPoint, on: bool) -> None:
        try:
            self._press().cas_write(point, on)
        except Exception as exc:
            QMessageBox.warning(self, "写入失败", str(exc))
            return
        self.refresh()

    def _write_d(self, point: CasPoint, spin: QSpinBox) -> None:
        try:
            self._press().cas_write(point, int(spin.value()))
        except Exception as exc:
            QMessageBox.warning(self, "写入失败", str(exc))
            return
        self.refresh()

    def _set_link_chip(self, text: str, *, ok: bool, mock: bool) -> None:
        pad = f"{_px(6)}px {_px(12)}px"
        radius = _px(14)
        if mock:
            bg, fg = "#fdebd0", "#7e5103"
        elif ok:
            bg, fg = "#d5f5e3", "#145a32"
        else:
            bg, fg = "#fadbd8", "#922b21"
        self.lbl_link.setText(text)
        self.lbl_link.setStyleSheet(
            f"background:{bg};color:{fg};padding:{pad};border-radius:{radius}px;"
            f"font-weight:bold;font-size:{_fpx(13)}px;"
        )

    def refresh(self) -> None:
        p = self._press()
        mock = bool(p.use_mock)
        ok = bool(p.connected) or mock
        if mock:
            status = "压机 Mock"
        elif p.connected:
            status = "压机已连接"
        else:
            status = "压机未连接"
        err = str(p.last_error or "").strip()
        if err and not mock:
            status = f"{status}  {err}"
        self._set_link_chip(status, ok=ok, mock=mock)

        for ui in self._rows:
            try:
                raw = p.cas_read(ui.point)
            except Exception as exc:
                ui.value_lbl.setText("读失败")
                ui.value_lbl.setStyleSheet(_chip_qss(mode="fail"))
                ui.value_lbl.setToolTip(str(exc))
                continue
            ui.value_lbl.setToolTip("")
            kind = ui.point.plc_kind
            if kind in ("M", "X"):
                on = bool(int(raw))
                ui.value_lbl.setText("开" if on else "关")
                ui.value_lbl.setStyleSheet(_chip_qss(mode="bit", on=on))
            else:
                ui.value_lbl.setText(str(int(raw)))
                ui.value_lbl.setStyleSheet(_chip_qss(mode="num"))
                if ui.spin is not None and not ui.spin.hasFocus():
                    ui.spin.setValue(int(raw) & 0xFFFF)
