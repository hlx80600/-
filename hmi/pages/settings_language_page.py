"""设置 · 语言页。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from hmi import i18n
from hmi.style import apply_page_chrome, style_button


class SettingsLanguagePage(QWidget):
    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx

        root = QVBoxLayout(self)
        self.grp = QGroupBox()
        lay = QVBoxLayout(self.grp)
        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#566573;")
        lay.addWidget(self.lbl_hint)

        form = QFormLayout()
        self.lbl_lang = QLabel()
        self.cmb_lang = QComboBox()
        for code, name_key in i18n.SUPPORTED_LANGUAGES.items():
            self.cmb_lang.addItem(i18n.tr(name_key), code)
        idx = self.cmb_lang.findData(i18n.language())
        if idx >= 0:
            self.cmb_lang.setCurrentIndex(idx)
        form.addRow(self.lbl_lang, self.cmb_lang)
        lay.addLayout(form)

        self.btn_apply = QPushButton()
        style_button(self.btn_apply, "primary")
        self.btn_apply.clicked.connect(self._apply)
        lay.addWidget(self.btn_apply)
        root.addWidget(self.grp)
        root.addStretch(1)
        apply_page_chrome(self)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.grp.setTitle(i18n.tr("settings.lang.title"))
        self.lbl_hint.setText(i18n.tr("settings.lang.hint"))
        cur = self.cmb_lang.currentData()
        self.cmb_lang.blockSignals(True)
        self.cmb_lang.clear()
        for code, name_key in i18n.SUPPORTED_LANGUAGES.items():
            self.cmb_lang.addItem(i18n.tr(name_key), code)
        idx = self.cmb_lang.findData(cur or i18n.language())
        self.cmb_lang.setCurrentIndex(max(0, idx))
        self.cmb_lang.blockSignals(False)
        self.btn_apply.setText(i18n.tr("settings.lang.apply"))
        self.lbl_lang.setText(i18n.tr("settings.lang.label"))

    def _apply(self) -> None:
        code = str(self.cmb_lang.currentData() or i18n.DEFAULT_LANGUAGE)
        i18n.save_language_to_cfg(self.ctx.cfg, code)
        try:
            save_config(self.ctx.cfg)
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("settings.lang.title"), str(e))
            return
        i18n.set_language(code)
        # 同语言重复应用时也刷新字体
        w = self.window()
        fn = getattr(w, "retranslate_ui", None)
        if callable(fn):
            fn()
        QMessageBox.information(self, i18n.tr("settings.lang.title"), i18n.tr("settings.lang.saved"))
