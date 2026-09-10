"""训练曲线：读 Ultralytics results.csv，QPainter 折线。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from vision.ultralytics_runner import parse_results_csv


class TrainCurveWidget(QWidget):
    """最多画 3 条曲线（loss / mAP 或 acc）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._series: list[tuple[str, list[float], QColor]] = []
        self.setStyleSheet("background:#1c2833;")

    def load_csv(self, path: Path | None) -> None:
        self._series = []
        if path is None or not Path(path).is_file():
            self.update()
            return
        cols = parse_results_csv(Path(path))
        palette = [QColor("#e74c3c"), QColor("#3498db"), QColor("#2ecc71")]
        prefer = (
            "train/loss",
            "train/cls_loss",
            "train/box_loss",
            "metrics/accuracy_top1",
            "metrics/mAP50(B)",
            "metrics/mAP50-95(B)",
            "metrics/precision(B)",
        )
        picked = 0
        for key in prefer:
            if key in cols and cols[key] and picked < 3:
                self._series.append((key, cols[key], palette[picked]))
                picked += 1
        if not self._series:
            for key, vals in cols.items():
                if key.lower() in ("epoch", "") or not vals:
                    continue
                self._series.append((key, vals, palette[picked]))
                picked += 1
                if picked >= 3:
                    break
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1c2833"))
        p.setPen(QColor("#aab7b8"))
        if not self._series:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "训练开始后这里显示 loss / 精度曲线")
            p.end()
            return
        left, top, right, bottom = 8, 8, self.width() - 8, self.height() - 22
        plot = QRectF(left, top, max(10, right - left), max(10, bottom - top))
        p.setPen(QPen(QColor("#34495e"), 1))
        p.drawRect(plot)
        legend_x = left
        for name, vals, color in self._series:
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            if hi <= lo:
                hi = lo + 1e-6
            p.setPen(QPen(color, 2))
            pts = []
            n = len(vals)
            for i, v in enumerate(vals):
                x = plot.left() + (plot.width() * i / max(1, n - 1))
                y = plot.bottom() - (float(v) - lo) / (hi - lo) * plot.height()
                pts.append((x, y))
            for i in range(1, len(pts)):
                p.drawLine(int(pts[i - 1][0]), int(pts[i - 1][1]), int(pts[i][0]), int(pts[i][1]))
            p.drawText(legend_x, self.height() - 6, name.split("/")[-1][:18])
            legend_x += 90
        p.end()
