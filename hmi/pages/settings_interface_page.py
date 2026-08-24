"""设置 · 界面刷新 / 运动融合 / 手机监控。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from hmi import i18n
from hmi.style import apply_page_chrome, style_button


def _spin_int(lo: int, hi: int, val: int) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setValue(int(val))
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
    return sp


def _spin_float(lo: float, hi: float, val: float, *, step: float = 0.01, dec: int = 2) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(dec)
    sp.setSingleStep(step)
    sp.setValue(float(val))
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
    return sp


class SettingsInterfacePage(QWidget):
    """界面刷新 + 运动融合 + mobile_web（子页签内容）。"""

    def __init__(self, coord: Coordinator, *, section: str) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self.section = section
        cfg = self.ctx.cfg
        hmi = (cfg.get("system") or {}).get("hmi") or {}
        motion = cfg.get("motion") or {}
        mobile = (cfg.get("system") or {}).get("mobile_web") or {}

        root = QVBoxLayout(self)

        if section == "interface":
            self.grp = QGroupBox()
            form = QFormLayout(self.grp)
            self.sp_fast = _spin_int(16, 500, int(hmi.get("refresh_fast_ms", 33)))
            self.sp_slow = _spin_int(50, 2000, int(hmi.get("refresh_slow_ms", 100)))
            self.sp_inact = _spin_int(100, 5000, int(hmi.get("refresh_inactive_ms", 250)))
            self.sp_prev = _spin_int(5, 120, int(hmi.get("preview_max_fps", 60)))
            self.sp_vis = _spin_int(5, 60, int(hmi.get("vision_debug_max_fps", 15)))
            self._rows: list[tuple[QLabel, QWidget]] = [
                (QLabel(), self.sp_fast),
                (QLabel(), self.sp_slow),
                (QLabel(), self.sp_inact),
                (QLabel(), self.sp_prev),
                (QLabel(), self.sp_vis),
            ]
            for lb, w in self._rows:
                form.addRow(lb, w)
            self.lbl_hint = QLabel()
            self.lbl_hint.setWordWrap(True)
            self.lbl_hint.setStyleSheet("color:#566573;")
            form.addRow(self.lbl_hint)
            self.btn_save = QPushButton()
            style_button(self.btn_save, "primary")
            self.btn_save.clicked.connect(self._save_ui)
            root.addWidget(self.grp)
            root.addWidget(self.btn_save)

        elif section == "motion":
            self.grp = QGroupBox()
            form = QFormLayout(self.grp)
            self.chk_blend = QCheckBox()
            self.chk_blend.setChecked(bool(motion.get("blend_enable", True)))
            self.sp_bt = _spin_int(0, 2000, int(motion.get("blend_t_ms", 100)))
            self.sp_br = _spin_float(0.0, 200.0, float(motion.get("blend_r_mm", 30.0)), step=1.0, dec=1)
            self.sp_bd = _spin_float(0.0, 2.0, float(motion.get("blend_queue_delay_s", 0.08)), step=0.01, dec=3)
            self._rows = [
                (QLabel(), self.chk_blend),
                (QLabel(), self.sp_bt),
                (QLabel(), self.sp_br),
                (QLabel(), self.sp_bd),
            ]
            for lb, w in self._rows:
                form.addRow(lb, w)
            self.btn_save = QPushButton()
            style_button(self.btn_save, "success")
            self.btn_save.clicked.connect(self._save_motion)
            root.addWidget(self.grp)
            root.addWidget(self.btn_save)

        else:  # mobile
            self.grp = QGroupBox()
            form = QFormLayout(self.grp)
            self.chk_en = QCheckBox()
            self.chk_en.setChecked(bool(mobile.get("enabled", False)))
            self.ed_host = QLineEdit(str(mobile.get("host", "0.0.0.0")))
            self.sp_port = _spin_int(1, 65535, int(mobile.get("port", 8765)))
            self.ed_token = QLineEdit(str(mobile.get("token", "") or ""))
            self._rows = [
                (QLabel(), self.chk_en),
                (QLabel(), self.ed_host),
                (QLabel(), self.sp_port),
                (QLabel(), self.ed_token),
            ]
            for lb, w in self._rows:
                form.addRow(lb, w)
            self.btn_save = QPushButton()
            style_button(self.btn_save, "warn")
            self.btn_save.clicked.connect(self._save_mobile)
            root.addWidget(self.grp)
            root.addWidget(self.btn_save)

        root.addStretch(1)
        apply_page_chrome(self)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        if self.section == "interface":
            self.grp.setTitle(i18n.tr("settings.ui.title"))
            keys = [
                "settings.ui.refresh_fast",
                "settings.ui.refresh_slow",
                "settings.ui.refresh_inactive",
                "settings.ui.preview_max_fps",
                "settings.ui.vision_debug_fps",
            ]
            for (lb, _), key in zip(self._rows, keys, strict=True):
                lb.setText(i18n.tr(key))
            self.lbl_hint.setText(i18n.tr("settings.ui.restart_hint"))
            self.btn_save.setText(i18n.tr("settings.ui.save"))
        elif self.section == "motion":
            self.grp.setTitle(i18n.tr("settings.motion.title"))
            keys = [
                "settings.motion.blend_enable",
                "settings.motion.blend_t_ms",
                "settings.motion.blend_r_mm",
                "settings.motion.blend_delay_s",
            ]
            for (lb, w), key in zip(self._rows, keys, strict=True):
                if isinstance(w, QCheckBox):
                    w.setText(i18n.tr(key))
                    lb.setText("")
                else:
                    lb.setText(i18n.tr(key))
            self.btn_save.setText(i18n.tr("settings.motion.save"))
        else:
            self.grp.setTitle(i18n.tr("settings.mobile.title"))
            keys = [
                "settings.mobile.enabled",
                "settings.mobile.host",
                "settings.mobile.port",
                "settings.mobile.token",
            ]
            for (lb, w), key in zip(self._rows, keys, strict=True):
                if isinstance(w, QCheckBox):
                    w.setText(i18n.tr(key))
                    lb.setText("")
                else:
                    lb.setText(i18n.tr(key))
            self.btn_save.setText(i18n.tr("settings.mobile.save"))

    def _save_ui(self) -> None:
        hmi = self.ctx.cfg.setdefault("system", {}).setdefault("hmi", {})
        hmi["refresh_fast_ms"] = int(self.sp_fast.value())
        hmi["refresh_slow_ms"] = int(self.sp_slow.value())
        hmi["refresh_inactive_ms"] = int(self.sp_inact.value())
        hmi["preview_max_fps"] = int(self.sp_prev.value())
        hmi["vision_debug_max_fps"] = int(self.sp_vis.value())
        try:
            save_config(self.ctx.cfg)
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("settings.ui.title"), str(e))
            return
        w = self.window()
        fn = getattr(w, "apply_hmi_refresh_settings", None)
        if callable(fn):
            fn()
        QMessageBox.information(self, i18n.tr("settings.ui.title"), i18n.tr("settings.ui.saved"))

    def _save_motion(self) -> None:
        motion = self.ctx.cfg.setdefault("motion", {})
        motion["blend_enable"] = bool(self.chk_blend.isChecked())
        motion["blend_t_ms"] = int(self.sp_bt.value())
        motion["blend_r_mm"] = float(self.sp_br.value())
        motion["blend_queue_delay_s"] = float(self.sp_bd.value())
        try:
            save_config(self.ctx.cfg)
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("settings.motion.title"), str(e))
            return
        QMessageBox.information(self, i18n.tr("settings.motion.title"), i18n.tr("settings.motion.saved"))

    def _save_mobile(self) -> None:
        mob = self.ctx.cfg.setdefault("system", {}).setdefault("mobile_web", {})
        mob["enabled"] = bool(self.chk_en.isChecked())
        mob["host"] = self.ed_host.text().strip() or "0.0.0.0"
        mob["port"] = int(self.sp_port.value())
        mob["token"] = self.ed_token.text().strip()
        try:
            save_config(self.ctx.cfg)
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("settings.mobile.title"), str(e))
            return
        QMessageBox.information(self, i18n.tr("settings.mobile.title"), i18n.tr("settings.mobile.saved"))
