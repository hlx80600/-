"""产量统计：CT / UPH / 每小时产量 + 实时直方图。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from hmi.style import apply_page_chrome, style_many


class HourlyHistogram(QWidget):
    """最近 N 小时产量柱状图（纯 Qt 绘制，不依赖额外库）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[tuple[str, int]] = []
        self.setMinimumHeight(260)
        self.setStyleSheet("background:#1e1e1e;")

    def set_data(self, bars: list[tuple[str, int]]) -> None:
        self._bars = list(bars)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin_l, margin_r, margin_t, margin_b = 40, 16, 24, 36
        plot_w = max(1, w - margin_l - margin_r)
        plot_h = max(1, h - margin_t - margin_b)

        p.fillRect(self.rect(), QColor("#1e1e1e"))
        p.setPen(QColor("#888"))
        p.drawText(8, 16, "每小时产量（件）")

        if not self._bars:
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据（完成一次下料后开始统计）")
            return

        max_v = max((v for _, v in self._bars), default=1)
        max_v = max(max_v, 1)
        n = len(self._bars)
        gap = 4
        bar_w = max(8, (plot_w - gap * (n + 1)) / n)

        # 网格线
        p.setPen(QPen(QColor("#333"), 1))
        for i in range(5):
            y = margin_t + plot_h * i / 4
            p.drawLine(margin_l, int(y), w - margin_r, int(y))
            val = int(max_v * (1 - i / 4))
            p.setPen(QColor("#777"))
            p.drawText(4, int(y + 4), str(val))
            p.setPen(QPen(QColor("#333"), 1))

        for i, (label, val) in enumerate(self._bars):
            x = margin_l + gap + i * (bar_w + gap)
            bh = plot_h * (val / max_v) if max_v else 0
            y = margin_t + plot_h - bh
            # 当前小时高亮
            color = QColor("#2ecc71") if i == n - 1 else QColor("#3498db")
            p.fillRect(QRectF(x, y, bar_w, bh), color)
            p.setPen(QColor("#ccc"))
            # 柱顶数值
            if val > 0:
                p.drawText(QRectF(x - 2, y - 16, bar_w + 4, 14), Qt.AlignCenter, str(val))
            # 横轴小时
            p.setPen(QColor("#aaa"))
            p.drawText(QRectF(x - 4, h - margin_b + 4, bar_w + 8, 20), Qt.AlignCenter, label)

        p.end()


class ProductionPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx

        root = QVBoxLayout(self)

        # 数字看板
        board = QGroupBox("实时产量")
        grid = QHBoxLayout(board)
        self.lbl_total = QLabel("总产量\n0")
        self.lbl_ct = QLabel("CT\n-- s")
        self.lbl_uph = QLabel("UPH(即时)\n--")
        self.lbl_uph_avg = QLabel("UPH(平均)\n--")
        self.lbl_hour = QLabel("本小时\n0")
        for lb in (self.lbl_total, self.lbl_ct, self.lbl_uph, self.lbl_uph_avg, self.lbl_hour):
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(
                "background:#2c3e50;color:#ecf0f1;padding:12px;border-radius:6px;"
                "font-size:16px;font-weight:bold;min-width:110px;"
            )
            grid.addWidget(lb)
        root.addWidget(board)

        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("清零产量统计")
        self.btn_reset.clicked.connect(self._reset)
        self.btn_sim = QPushButton("模拟记一件下料（调试）")
        self.btn_sim.clicked.connect(lambda: self.ctx.production.record_unload())
        style_many([(self.btn_reset, "warn"), (self.btn_sim, "neutral")])
        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_sim)
        btn_row.addStretch()
        root.addLayout(btn_row)

        hist_box = QGroupBox("每小时产量直方图（最近 12 小时，实时）")
        hl = QVBoxLayout(hist_box)
        self.hist = HourlyHistogram()
        hl.addWidget(self.hist)
        root.addWidget(hist_box, stretch=1)
        apply_page_chrome(self)

    def _reset(self) -> None:
        self.ctx.production.reset()

    def refresh(self) -> None:
        s = self.ctx.production.snapshot()
        self.lbl_total.setText(f"总产量\n{s['total']}")
        ct = s["last_ct_s"]
        self.lbl_ct.setText(f"CT\n{ct:.2f} s" if ct > 0 else "CT\n-- s")
        self.lbl_uph.setText(f"UPH(即时)\n{s['uph_instant']:.1f}" if s["uph_instant"] > 0 else "UPH(即时)\n--")
        self.lbl_uph_avg.setText(f"UPH(平均)\n{s['uph_avg']:.1f}" if s["uph_avg"] > 0 else "UPH(平均)\n--")
        self.lbl_hour.setText(f"本小时\n{s['hour_count']}")
        self.hist.set_data(self.ctx.production.recent_hours(12))
