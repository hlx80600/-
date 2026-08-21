"""运动参数：按工位/Auto/步 单独设速度、加速度、平滑（同一示教点不同步互不影响）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from core.motion_steps import ensure_motion_steps, list_move_steps, read_step_motion
from hmi.style import apply_page_chrome, style_many


class NoWheelSpin(QSpinBox):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e) -> None:
        e.ignore()


class NoWheelDSpin(QDoubleSpinBox):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e) -> None:
        e.ignore()


class MotionStepsPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._rows = list_move_steps()
        self._cur_key = ""

        root = QVBoxLayout(self)

        body = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.currentItemChanged.connect(self._on_sel)
        body.addWidget(self.lst, stretch=2)

        right = QVBoxLayout()
        self.lbl = QLabel("未选择")
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet("font-weight:bold;")
        right.addWidget(self.lbl)

        form = QFormLayout()
        self.sp_vel = NoWheelSpin()
        self.sp_vel.setRange(1, 100)
        self.sp_vel.setSuffix(" %")
        form.addRow("本步速度 vel", self.sp_vel)
        self.sp_acc = NoWheelSpin()
        self.sp_acc.setRange(0, 100)
        self.sp_acc.setSuffix(" %")
        form.addRow("本步加速度 acc/oacc", self.sp_acc)
        self.chk_blend = QCheckBox("本步平滑（段间交融）")
        form.addRow("路径平滑", self.chk_blend)
        self.sp_bt = NoWheelSpin()
        self.sp_bt.setRange(-1, 500)
        self.sp_bt.setSpecialValueText("用全局")
        self.sp_bt.setValue(-1)
        form.addRow("本步 blendT(ms)", self.sp_bt)
        self.sp_br = NoWheelDSpin()
        self.sp_br.setRange(-1, 1000)
        self.sp_br.setDecimals(1)
        self.sp_br.setSpecialValueText("用全局")
        self.sp_br.setValue(-1)
        form.addRow("本步 blendR(mm)", self.sp_br)
        self.chk_blend.toggled.connect(self._sync_blend_en)
        box = QGroupBox("选中步的参数")
        box.setLayout(form)
        right.addWidget(box)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存本步")
        btn_save.clicked.connect(self._save)
        btn_reload = QPushButton("刷新列表")
        btn_reload.clicked.connect(self._reload_list)
        style_many([(btn_save, "success"), (btn_reload, "neutral")])
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_reload)
        right.addLayout(btn_row)
        right.addStretch(1)
        body.addLayout(right, stretch=1)
        root.addLayout(body)
        apply_page_chrome(self)

        self._reload_list()
        self._sync_blend_en(False)

    def _sync_blend_en(self, on: bool | None = None) -> None:
        en = bool(self.chk_blend.isChecked() if on is None else on)
        self.sp_bt.setEnabled(en)
        self.sp_br.setEnabled(en)

    def _reload_list(self) -> None:
        ensure_motion_steps(self.ctx.cfg)
        self._rows = list_move_steps()
        prefer = self._cur_key
        self.lst.blockSignals(True)
        self.lst.clear()
        sel = None
        for row in self._rows:
            key = row["key"]
            raw = (self.ctx.cfg.get("motion_steps") or {}).get(key) or {}
            vel = raw.get("vel", 100)
            blend = "平滑" if raw.get("blend") else "到位"
            text = (
                f"[{key}] S{row['station']} {row['auto_title']} · 步{row['step']} "
                f"{row['title']}  ({row['kind']})  vel={vel}% {blend}"
            )
            if row["station"] == 0:
                text = (
                    f"[{key}] {row['auto_title']} · {row['title']}  "
                    f"({row['kind']})  vel={vel}% {blend}"
                )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.lst.addItem(item)
            if key == prefer:
                sel = item
        self.lst.blockSignals(False)
        if sel:
            self.lst.setCurrentItem(sel)
        elif self.lst.count():
            self.lst.setCurrentRow(0)

    def _on_sel(self, cur: QListWidgetItem | None, _prev=None) -> None:
        if not cur:
            self._cur_key = ""
            self.lbl.setText("未选择")
            return
        key = str(cur.data(Qt.ItemDataRole.UserRole) or "")
        self._cur_key = key
        row = next((r for r in self._rows if r["key"] == key), None)
        pts = ",".join(row["points"]) if row and row.get("points") else "-"
        robot = (row or {}).get("robot") or "-"
        self.lbl.setText(
            f"{key}\n{(row or {}).get('title','')}\n"
            f"关联示教点: {pts}  臂: {robot}\n"
            f"{(row or {}).get('detail','')}"
        )
        ensure_motion_steps(self.ctx.cfg)
        raw = (self.ctx.cfg.get("motion_steps") or {}).get(key) or {}
        self.sp_vel.setValue(int(round(float(raw.get("vel", 100)))))
        self.sp_acc.setValue(int(round(float(raw.get("acc", 100)))))
        self.chk_blend.setChecked(bool(raw.get("blend", False)))
        if raw.get("blend_t_ms") is not None:
            self.sp_bt.setValue(int(round(float(raw["blend_t_ms"]))))
        else:
            self.sp_bt.setValue(-1)
        if raw.get("blend_r_mm") is not None:
            self.sp_br.setValue(float(raw["blend_r_mm"]))
        else:
            self.sp_br.setValue(-1)
        self._sync_blend_en()

    def _save(self) -> None:
        key = self._cur_key
        if not key:
            QMessageBox.warning(self, "未选", "请先在左侧选中一个程序步")
            return
        ensure_motion_steps(self.ctx.cfg)
        entry = {
            "vel": float(self.sp_vel.value()),
            "acc": float(self.sp_acc.value()),
            "blend": bool(self.chk_blend.isChecked()),
        }
        if entry["blend"] and self.sp_bt.value() >= 0:
            entry["blend_t_ms"] = float(self.sp_bt.value())
        if entry["blend"] and self.sp_br.value() >= 0:
            entry["blend_r_mm"] = float(self.sp_br.value())
        self.ctx.cfg.setdefault("motion_steps", {})[key] = entry
        save_config(self.ctx.cfg)
        opts = read_step_motion(self.ctx.cfg, key)
        QMessageBox.information(
            self,
            "已保存",
            f"{key}\nvel={opts['vel']:.0f}%  acc={opts['acc']:.0f}%  "
            f"blend={'开' if opts.get('blend') else '关'}\n"
            "仅影响这一程序步；同名示教点的其它步不变。",
        )
        self._reload_list()

    def refresh(self) -> None:
        pass
