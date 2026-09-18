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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from devices.plc_cas_points import (
    PROTOCOL_IP_HINT,
    PROTOCOL_PORT_HINT,
    CasPoint,
    hmi_modbus_dec,
)
from devices.press_modbus import PressMachine
from hmi import ui_scale
from hmi.scroll_util import disable_tab_bar_wheel, wrap_in_scroll
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
    addr_spin: QSpinBox | None = None


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
        self._pick_done_row: _RowUi | None = None
        self._pick_done_slot_lbl: QLabel | None = None

        root = QVBoxLayout(self)
        root.addWidget(self._build_header())

        points = self.ctx.press.cas_point_list()
        by_group: OrderedDict[str, list[CasPoint]] = OrderedDict()
        for pt in points:
            by_group.setdefault(pt.group, []).append(pt)
        by_id = {p.id: p for p in points}

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        disable_tab_bar_wheel(tabs)

        online_page = QWidget()
        online_lay = QVBoxLayout(online_page)
        online_lay.setContentsMargins(4, 4, 4, 4)
        if "联机" in by_group:
            online_pts = by_group["联机"]
            slot_done = [p for p in online_pts if p.id.endswith("_slot_done")]
            core = [p for p in online_pts if p not in slot_done]
            online_lay.addWidget(self._build_online(core, slot_done))
        if "公共参数" in by_group:
            online_lay.addWidget(
                self._build_station_box(
                    "公共参数", by_group["公共参数"], edit_addr=True
                )
            )
        online_lay.addStretch(1)
        tabs.addTab(wrap_in_scroll(online_page, horizontal=False), "联机/公共")

        man_boxes = [
            self._build_station_box(title, pts)
            for title, pts in by_group.items()
            if title.startswith("压机手动")
        ]
        if man_boxes:
            man_page = QWidget()
            man_lay = QVBoxLayout(man_page)
            man_lay.setContentsMargins(4, 4, 4, 4)
            man_lay.addWidget(self._grid_cards(man_boxes, columns=2))
            man_lay.addStretch(1)
            tabs.addTab(wrap_in_scroll(man_page, horizontal=False), "压机手动")

        rod_boxes = [
            self._build_station_box(title, pts)
            for title, pts in by_group.items()
            if title.startswith("压杆操作")
        ]
        if rod_boxes:
            rod_page = QWidget()
            rod_lay = QVBoxLayout(rod_page)
            rod_lay.setContentsMargins(4, 4, 4, 4)
            rod_lay.addWidget(self._grid_cards(rod_boxes, columns=2))
            rod_lay.addStretch(1)
            tabs.addTab(wrap_in_scroll(rod_page, horizontal=False), "压杆")

        hold_page = QWidget()
        hold_lay = QVBoxLayout(hold_page)
        hold_lay.setContentsMargins(4, 4, 4, 4)
        hold_pts = [by_id[i] for _, ids in _HOLD_ROWS for i in ids if i in by_id]
        if hold_pts:
            hold_lay.addWidget(self._build_hold_table(by_id))
        placed = {"联机", "公共参数", "压着时间与计数"}
        for title, pts in by_group.items():
            if title in placed or title.startswith("压机手动") or title.startswith("压杆操作"):
                continue
            leftover = [p for p in pts if p.id not in _HOLD_IDS]
            if leftover:
                hold_lay.addWidget(self._build_station_box(title, leftover))
        hold_lay.addStretch(1)
        tabs.addTab(wrap_in_scroll(hold_page, horizontal=False), "压着时间")

        root.addWidget(tabs, 1)
        apply_page_chrome(self)

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
        btn_refresh.clicked.connect(lambda: self.refresh())
        btn_all.clicked.connect(lambda: self.refresh(all_tabs=True))
        row.addWidget(btn_refresh)
        row.addWidget(btn_all)
        lay.addLayout(row)

        chips = QGridLayout()
        chips.setSpacing(_px(8))
        chip_texts = (
            f"协议默认 {PROTOCOL_IP_HINT}:{PROTOCOL_PORT_HINT}",
            "本页不改通信配置里的压机 IP",
            "空闲/放鞋完成：1=有效",
            "启动：1=空转　2=启动",
            "地址为协议 Modbus Dec",
        )
        for i, text in enumerate(chip_texts):
            chip = QLabel(text)
            chip.setWordWrap(True)
            chip.setStyleSheet(
                f"background:#eef3f7;color:#34495e;padding:{_px(4)}px {_px(10)}px;"
                f"border-radius:{_px(12)}px;font-size:{_fpx(12)}px;"
            )
            chips.addWidget(chip, i // 3, i % 3)
        chips.setColumnStretch(0, 1)
        chips.setColumnStretch(1, 1)
        chips.setColumnStretch(2, 1)
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

    def _build_online(
        self, core: list[CasPoint], slot_done: list[CasPoint]
    ) -> QGroupBox:
        box = QGroupBox("联机")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(_px(10))
        grid.setVerticalSpacing(_px(10))
        cols = 3
        cards: list[QWidget] = [self._build_pick_done_card()]
        cards.extend(self._online_card(pt) for pt in core)
        cards.extend(self._online_card(pt) for pt in slot_done)
        for i, card in enumerate(cards):
            r, c = divmod(i, cols)
            grid.addWidget(card, r, c)
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        return box

    def _online_card(self, pt: CasPoint) -> QFrame:
        card = QFrame()
        card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        card.setMinimumWidth(0)
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
        inner.addWidget(self._addr_editor(pt, ui))
        return card

    def _build_pick_done_card(self) -> QFrame:
        """取料槽工作完成：工控机发出，随当前工位（放料槽号）切换。"""
        card = QFrame()
        card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        card.setStyleSheet(
            f"QFrame {{ background:#ffffff;border:1px solid #d5dde5;"
            f"border-radius:{_px(8)}px; }}"
        )
        inner = QVBoxLayout(card)
        inner.setContentsMargins(_px(10), _px(8), _px(10), _px(8))
        inner.setSpacing(_px(8))
        title = QLabel("取料槽工作完成")
        title.setStyleSheet(
            f"font-weight:bold;color:#1c2833;font-size:{_fpx(14)}px;"
        )
        hint = QLabel("工控机发出；随当前工位（放料槽号）改写该槽线圈")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:#7f8c8d;font-size:{_fpx(12)}px;")
        slot_lbl = QLabel("当前工位 #-")
        slot_lbl.setStyleSheet(f"color:#1a5276;font-size:{_fpx(12)}px;")
        self._pick_done_slot_lbl = slot_lbl
        inner.addWidget(title)
        inner.addWidget(hint)
        inner.addWidget(slot_lbl)
        pt = self._slot_done_point(self._press().current_station_no())
        if pt is None:
            inner.addWidget(QLabel("无完成点"))
            return card
        ui = self._make_row(pt)
        self._pick_done_row = ui
        inner.addWidget(ui.value_lbl)
        acts = self._action_widget(pt, ui)
        if acts is not None:
            inner.addWidget(acts)
        inner.addWidget(self._addr_editor(pt, ui))
        return card

    def _slot_done_point(self, slot: int) -> CasPoint | None:
        sid = max(1, min(4, int(slot)))
        return next(
            (p for p in self._press().cas_point_list() if p.id == f"s{sid}_slot_done"),
            None,
        )

    def _build_station_box(
        self,
        title: str,
        points: list[CasPoint],
        *,
        edit_addr: bool = False,
    ) -> QGroupBox:
        short = title.replace("压机手动 · ", "").replace("压杆操作 · ", "")
        box = QGroupBox(short)
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(_px(8))
        grid.setVerticalSpacing(_px(8))
        for r, pt in enumerate(points):
            self._place_row(grid, r, pt, show_name=True, edit_addr=edit_addr)
            grid.setRowMinimumHeight(r, _px(36))
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
        edit_addr: bool = False,
    ) -> None:
        ui = self._make_row(pt)
        col = 0
        if show_name:
            grid.addWidget(self._name_block(pt), row, 0)
            col = 1
        grid.addWidget(ui.value_lbl, row, col)
        right = QWidget()
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(_px(6))
        acts = self._action_widget(pt, ui)
        if acts is not None:
            rv.addWidget(acts)
        if edit_addr:
            rv.addWidget(self._addr_editor(pt, ui))
        if acts is not None or edit_addr:
            grid.addWidget(right, row, col + 1)
        grid.setColumnStretch(col + 1, 1)

    def _action_widget(self, pt: CasPoint, ui: _RowUi) -> QWidget | None:
        if pt.rw == "rw" and pt.plc_kind == "M":
            if pt.id.endswith("_rod_fwd") or pt.id.endswith("_rod_back"):
                return self._hold_coil_button(ui)
            on_txt, off_txt = "开", "关"
            if pt.id.endswith("_slot_done"):
                on_txt, off_txt = "置1", "置0"
            btn_on = style_button(QPushButton(on_txt), "success", tall=False)
            btn_off = style_button(QPushButton(off_txt), "neutral", tall=False)
            btn_on.clicked.connect(lambda _=False, u=ui: self._write_m(u.point, True))
            btn_off.clicked.connect(lambda _=False, u=ui: self._write_m(u.point, False))
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
            sp.setMinimumWidth(_px(72))
            sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
            ui.spin = sp
            btn_w = style_button(QPushButton("写入"), "motion", tall=False)
            btn_w.clicked.connect(lambda _=False, u=ui, s=sp: self._write_d(u.point, s))
            cell = QWidget()
            wr = QHBoxLayout(cell)
            wr.setContentsMargins(0, 0, 0, 0)
            wr.setSpacing(_px(6))
            wr.addWidget(sp, 1)
            wr.addWidget(btn_w)
            extra: list[tuple[str, str, int]] = []
            if pt.id == "start":
                extra = [("空转", "neutral", 1), ("启动", "success", 2)]
            elif pt.id == "shoe_done":
                extra = [("置1", "success", 1), ("置0", "neutral", 0)]
            if extra:
                row2 = QHBoxLayout()
                row2.setContentsMargins(0, 0, 0, 0)
                row2.setSpacing(_px(6))
                for text, role, val in extra:
                    btn = style_button(QPushButton(text), role, tall=False)
                    btn.clicked.connect(
                        lambda _=False, u=ui, s=sp, v=val: self._write_d_preset(u.point, s, v)
                    )
                    row2.addWidget(btn, 1)
                wrap = QWidget()
                wrap_lay = QVBoxLayout(wrap)
                wrap_lay.setContentsMargins(0, 0, 0, 0)
                wrap_lay.setSpacing(_px(6))
                wrap_lay.addWidget(cell)
                wrap_lay.addLayout(row2)
                return wrap
            return cell
        return None

    def _hold_coil_button(self, ui: _RowUi) -> QWidget:
        """点进/点退：按下写 1、松开写 0。"""
        is_fwd = ui.point.id.endswith("_rod_fwd")
        btn = style_button(
            QPushButton("点进" if is_fwd else "点退"),
            "success" if is_fwd else "warn",
            tall=False,
        )
        btn.setAutoRepeat(False)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("按下为 1，松开为 0")
        btn.pressed.connect(lambda u=ui, b=btn: self._on_hold_coil_pressed(u, b))
        btn.released.connect(lambda u=ui, b=btn: self._on_hold_coil_released(u, b))
        return btn

    def _rod_peer(self, point: CasPoint) -> CasPoint | None:
        pid = point.id
        if pid.endswith("_rod_fwd"):
            want = pid.replace("_rod_fwd", "_rod_back")
        elif pid.endswith("_rod_back"):
            want = pid.replace("_rod_back", "_rod_fwd")
        else:
            return None
        for row in self._rows:
            if row.point.id == want:
                return row.point
        return None

    def _on_hold_coil_pressed(self, ui: _RowUi, btn: QPushButton) -> None:
        btn.grabMouse()
        peer = self._rod_peer(ui.point)
        if peer is not None:
            self._write_m_hold(peer, False)
        self._write_m_hold(ui.point, True)
        self.refresh()

    def _on_hold_coil_released(self, ui: _RowUi, btn: QPushButton) -> None:
        try:
            self._write_m_hold(ui.point, False)
            self.refresh()
        finally:
            btn.releaseMouse()

    def _write_m_hold(self, point: CasPoint, on: bool) -> None:
        """保持型线圈写入；失败不弹窗，避免按住期间连弹。"""
        try:
            self._press().cas_write(point, on)
        except Exception:
            return

    def _addr_editor(self, pt: CasPoint, ui: _RowUi) -> QWidget:
        """改协议 Modbus 地址（Excel Dec），不是 PLC 的 M/D 号。"""
        cell = QWidget()
        wr = QHBoxLayout(cell)
        wr.setContentsMargins(0, 0, 0, 0)
        wr.setSpacing(_px(6))
        lbl = QLabel("Modbus")
        lbl.setStyleSheet("color:#5d6d7e;")
        wr.addWidget(lbl)
        sp = QSpinBox()
        sp.setRange(0, 999999)
        sp.setValue(int(hmi_modbus_dec(pt)))
        sp.setMinimumWidth(_px(88))
        sp.setToolTip("协议 Modbus 地址（Dec），与点表一致")
        sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
        ui.addr_spin = sp
        wr.addWidget(sp, 1)
        btn = style_button(QPushButton("保存地址"), "primary", tall=False)
        btn.clicked.connect(lambda _=False, pid=pt.id, s=sp: self._save_cas_addr(pid, s))
        wr.addWidget(btn)
        return cell

    def _save_cas_addr(self, point_id: str, spin: QSpinBox) -> None:
        """把 Modbus Dec 写入 press.cas_points 并立刻用于读写。"""
        press = self.ctx.cfg.setdefault("press", {})
        if not isinstance(press, dict):
            press = {}
            self.ctx.cfg["press"] = press
        ov = press.setdefault("cas_points", {})
        if not isinstance(ov, dict):
            ov = {}
            press["cas_points"] = ov
        ov[str(point_id)] = int(spin.value())
        try:
            save_config(self.ctx.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "保存地址失败", str(exc))
            return
        self._reload_cas_points()
        self.refresh()

    def _reload_cas_points(self) -> None:
        by_id = {p.id: p for p in self._press().cas_point_list()}
        for ui in self._rows:
            npt = by_id.get(ui.point.id)
            if npt is None:
                continue
            ui.point = npt
            if ui.addr_spin is not None and not ui.addr_spin.hasFocus():
                ui.addr_spin.setValue(int(hmi_modbus_dec(npt)))

    def _press(self) -> PressMachine:
        return self.ctx.press

    def _write_m(self, point: CasPoint, on: bool) -> None:
        """写线圈。急停信号与本机急停/急停复位同一路径（Mock 同样联动）。"""
        try:
            if point.id == "host_estop":
                if on:
                    self.coord.cmd_estop()
                else:
                    self.coord.cmd_reset_estop()
            elif str(point.id).endswith("_slot_done"):
                slot: int | None = None
                head = str(point.id).split("_", 1)[0]
                if head.startswith("s") and head[1:].isdigit():
                    slot = int(head[1:])
                self._press().set_pick_slot_work_done(bool(on), slot=slot)
            else:
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
        if point.id not in ("start", "shoe_done") or spin is None:
            return
        try:
            back = int(self._press().cas_read(point))
        except Exception:
            return
        want = int(spin.value())
        if back != want:
            title = "启动信号已下发" if point.id == "start" else "放鞋完成已下发"
            QMessageBox.information(
                self,
                title,
                f"已写入 {want}，当前显示 {back}。\n"
                "请确认「联机模式」已开。\n"
                "若 PLC 程序会把该字清零，只要动作发生就算成功。",
            )

    def _write_d_preset(self, point: CasPoint, spin: QSpinBox, value: int) -> None:
        spin.setValue(int(value))
        self._write_d(point, spin)

    def _sync_pick_done_binding(self, press: PressMachine) -> None:
        """取料槽工作完成卡片绑定到当前工位（放料槽）的线圈。"""
        slot = press.current_station_no()
        if self._pick_done_slot_lbl is not None:
            self._pick_done_slot_lbl.setText(f"当前工位 #{slot}（放料槽）")
        ui = self._pick_done_row
        pt = self._slot_done_point(slot)
        if ui is None or pt is None:
            return
        ui.point = pt
        if ui.addr_spin is not None and not ui.addr_spin.hasFocus():
            ui.addr_spin.setValue(int(hmi_modbus_dec(pt)))

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

    def refresh(self, all_tabs: bool = False) -> None:
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
        ids: list[str] = ["station_no"]
        for ui in self._rows:
            if all_tabs or ui.value_lbl.isVisible():
                ids.append(ui.point.id)
        if hasattr(p, "request_cas_poll"):
            p.request_cas_poll(ids, full=all_tabs)
        self._sync_pick_done_binding(p)

        for ui in self._rows:
            if (not all_tabs) and (not ui.value_lbl.isVisible()):
                continue
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
