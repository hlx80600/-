"""主窗口顶栏三色灯：各页共用、大圆灯，快刷跟随 TowerLight 快照。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from hmi import i18n
from hmi import ui_scale

_ON = {"red": "#ff2d2d", "yellow": "#ffd400", "green": "#19e05a"}
_OFF_RING = {"red": "#8b1a1a", "yellow": "#8a7a12", "green": "#1a6b38"}


class TowerLightBar(QWidget):
    """红/黄/绿三盏大圆灯，放在页面顶栏正中。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("towerLightBar")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(
            ui_scale.px(12), ui_scale.px(6), ui_scale.px(14), ui_scale.px(6)
        )
        root.setSpacing(ui_scale.px(10))

        self.lbl_title = QLabel()
        self.lbl_title.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_title)

        self._lamps: dict[str, QLabel] = {}
        self._captions: dict[str, QLabel] = {}
        for key in ("red", "yellow", "green"):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(ui_scale.px(2))
            lamp = QLabel()
            lamp.setAlignment(Qt.AlignCenter)
            lamp.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            cap = QLabel()
            cap.setAlignment(Qt.AlignCenter)
            col.addWidget(lamp, 0, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(cap, 0, Qt.AlignmentFlag.AlignHCenter)
            root.addLayout(col)
            self._lamps[key] = lamp
            self._captions[key] = cap

        self.apply_ui_scale()
        self.retranslate_ui()
        self._last_snap = {"red": False, "yellow": False, "green": False}
        self.apply_snapshot(self._last_snap)

    def _lamp_size(self) -> int:
        return ui_scale.px(76, min_v=64)

    def apply_ui_scale(self) -> None:
        """窗口比例变化后重设圆灯直径与字号。"""
        size = self._lamp_size()
        radius = max(8, size // 2)
        title_fs = ui_scale.font_px(15, min_v=13)
        cap_fs = ui_scale.font_px(13, min_v=12)
        lamp_fs = ui_scale.font_px(18, min_v=15)
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(
                ui_scale.px(12), ui_scale.px(6), ui_scale.px(14), ui_scale.px(6)
            )
            lay.setSpacing(ui_scale.px(10))
        self.lbl_title.setStyleSheet(
            f"color:#1a5276;font-weight:bold;font-size:{title_fs}px;padding-right:{ui_scale.px(8)}px;"
        )
        for lamp in self._lamps.values():
            lamp.setFixedSize(size, size)
            lamp.setProperty("hmi_radius", radius)
            lamp.setProperty("hmi_lamp_fs", lamp_fs)
        for cap in self._captions.values():
            cap.setStyleSheet(f"color:#34495e;font-weight:bold;font-size:{cap_fs}px;")
        # 尺寸变了要按当前亮灭重刷样式
        snap = getattr(self, "_last_snap", None)
        if isinstance(snap, dict):
            self.apply_snapshot(snap)

    def retranslate_ui(self) -> None:
        self.lbl_title.setText(i18n.tr("tower.title"))
        self._captions["red"].setText(i18n.tr("monitor.light.red"))
        self._captions["yellow"].setText(i18n.tr("monitor.light.yellow"))
        self._captions["green"].setText(i18n.tr("monitor.light.green"))
        tip = i18n.tr("tower.tip")
        self.setToolTip(tip)
        for w in (self.lbl_title, *self._lamps.values(), *self._captions.values()):
            w.setToolTip(tip)

    def apply_snapshot(self, snap: dict) -> None:
        """按 TowerLight.snapshot() 刷新亮灭。"""
        self._last_snap = {
            "red": bool(snap.get("red")),
            "yellow": bool(snap.get("yellow")),
            "green": bool(snap.get("green")),
        }
        size = self._lamp_size()
        radius = max(8, size // 2)
        lamp_fs = ui_scale.font_px(18, min_v=15)
        names = {
            "red": i18n.tr("monitor.light.red"),
            "yellow": i18n.tr("monitor.light.yellow"),
            "green": i18n.tr("monitor.light.green"),
        }
        for key, lamp in self._lamps.items():
            on = bool(self._last_snap[key])
            lamp.setText(names[key])
            if on:
                lamp.setStyleSheet(
                    f"background:{_ON[key]};color:#111;font-size:{lamp_fs}px;font-weight:bold;"
                    f"border:{ui_scale.px(3)}px solid #111;border-radius:{radius}px;"
                )
            else:
                lamp.setStyleSheet(
                    f"background:#1c2833;color:#95a5a6;font-size:{lamp_fs}px;font-weight:bold;"
                    f"border:{ui_scale.px(3)}px solid {_OFF_RING[key]};border-radius:{radius}px;"
                )
            lamp.setFixedSize(size, size)
