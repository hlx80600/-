"""视觉运行快照：在 HMI 里翻历史图，并打开 logs/vision_snaps 文件夹。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hmi.style import style_button, style_many
from vision.vision_journal import (
    format_snap_detail,
    format_snap_label,
    list_snap_records,
    load_snap_meta,
    snap_image_paths,
    snap_root,
)

_PAGE_SIZE = 20
_KIND_FILTERS: list[tuple[str, str]] = [
    ("", "全部类型"),
    ("belt_pick", "皮带取料"),
    ("place_slot", "放料槽"),
    ("pick_slot", "取料槽"),
]


class FitImageLabel(QLabel):
    """按控件大小等比显示 jpg。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._pix: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 160)
        self.setStyleSheet("background:#1c1c1c;color:#888;border:1px solid #444;")
        self.setText(title)

    def set_image_path(self, path: Path | None) -> None:
        """加载磁盘 jpg；文件尚未写出时提示刷新。"""
        if path is None:
            self._pix = None
            self.clear()
            self.setText(self._title)
            return
        if not path.is_file():
            self._pix = None
            self.clear()
            self.setText(f"{self._title}\n暂无（写入中可点刷新）")
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            self._pix = None
            self.clear()
            self.setText(f"{self._title}\n无法显示")
            return
        self._pix = pix
        self._apply()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        if self._pix is None:
            return
        scaled = self._pix.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)


class VisionSnapPage(QWidget):
    """历史运行快照浏览器（原图 / 叠图 / 检测 + 运送结果）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._page = 0
        self._current_id = ""
        self._current_dir: Path | None = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        self.tip = QLabel()
        self.tip.setWordWrap(True)
        self.tip.setStyleSheet("color:#566573;")
        self.tip.setText(
            "自动流程拍照与「检测测试」会落盘到 logs/vision_snaps/。"
            "图片文件名含相机与时间（如 cam1_20260828_140455_635_belt_pick_raw.jpg）。"
            "点一条可看原图、叠图、检测结果；放入鞋槽 / 下料完成后同一条会显示运送结果。"
            "监视画面刷新不存。本页在「报警记录」里。"
        )
        root.addWidget(self.tip)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cmb_cam = QComboBox()
        self.cmb_cam.addItem("全部相机", "")
        for cam in ("cam1", "cam2", "cam3", "cam4"):
            self.cmb_cam.addItem(cam, cam)
        self.cmb_kind = QComboBox()
        for key, label in _KIND_FILTERS:
            self.cmb_kind.addItem(label, key)
        self.btn_reload = QPushButton("刷新")
        self.btn_open_log = QPushButton("打开视觉log文件夹")
        self.btn_open_item = QPushButton("打开本条目录")
        self.btn_copy = QPushButton("复制详情")
        style_many(
            [
                (self.btn_reload, "primary"),
                (self.btn_open_log, "success"),
                (self.btn_open_item, "neutral"),
                (self.btn_copy, "neutral"),
            ]
        )
        self.cmb_cam.currentIndexChanged.connect(self.reload)
        self.cmb_kind.currentIndexChanged.connect(self.reload)
        self.btn_reload.clicked.connect(self.reload)
        self.btn_open_log.clicked.connect(self._open_log_dir)
        self.btn_open_item.clicked.connect(self._open_item_dir)
        self.btn_copy.clicked.connect(self._copy_detail)
        bar.addWidget(QLabel("相机"), 0)
        bar.addWidget(self.cmb_cam, 0)
        bar.addWidget(QLabel("类型"), 0)
        bar.addWidget(self.cmb_kind, 0)
        bar.addWidget(self.btn_reload, 0)
        bar.addWidget(self.btn_open_log, 0)
        bar.addWidget(self.btn_open_item, 0)
        bar.addWidget(self.btn_copy, 0)
        bar.addStretch(1)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setStyleSheet("font-size:14px;")
        self.list.currentItemChanged.connect(self._on_item)
        left_lay.addWidget(self.list, 1)
        pager = QHBoxLayout()
        self.btn_first = QPushButton("首页")
        self.btn_prev = QPushButton("上一页")
        self.lbl_page = QLabel()
        self.lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page.setMinimumWidth(160)
        self.lbl_page.setStyleSheet("color:#1a5276;font-weight:bold;")
        self.btn_next = QPushButton("下一页")
        self.btn_last = QPushButton("末页")
        for btn, role in (
            (self.btn_first, "neutral"),
            (self.btn_prev, "neutral"),
            (self.btn_next, "neutral"),
            (self.btn_last, "neutral"),
        ):
            style_button(btn, role)
        self.btn_first.clicked.connect(lambda: self._goto(0))
        self.btn_prev.clicked.connect(lambda: self._goto(self._page - 1))
        self.btn_next.clicked.connect(lambda: self._goto(self._page + 1))
        self.btn_last.clicked.connect(lambda: self._goto(self._page_count() - 1))
        pager.addWidget(self.btn_first)
        pager.addWidget(self.btn_prev)
        pager.addWidget(self.lbl_page, 1)
        pager.addWidget(self.btn_next)
        pager.addWidget(self.btn_last)
        left_lay.addLayout(pager)
        split.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        imgs = QHBoxLayout()
        self.img_raw = FitImageLabel("原图")
        self.img_vis = FitImageLabel("叠图")
        imgs.addWidget(self.img_raw, 1)
        imgs.addWidget(self.img_vis, 1)
        right_lay.addLayout(imgs, 3)
        box = QGroupBox("检测结果与运送回写")
        box_lay = QVBoxLayout(box)
        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setPlaceholderText("点左侧一条快照查看详情…")
        box_lay.addWidget(self.txt)
        right_lay.addWidget(box, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([380, 720])
        root.addWidget(split, 1)

        self.reload()

    def reload(self, *_args: object) -> None:
        """从磁盘重扫列表（新→旧）。"""
        if self._loading:
            return
        self._loading = True
        try:
            cam = str(self.cmb_cam.currentData() or "")
            kind = str(self.cmb_kind.currentData() or "")
            reset_page = self.sender() in (self.cmb_cam, self.cmb_kind)
            self._rows = list_snap_records(cam_id=cam, kind=kind)
            if reset_page:
                self._page = 0
            else:
                self._page = min(self._page, max(0, self._page_count() - 1))
            self._render_list()
        finally:
            self._loading = False

    def _page_count(self) -> int:
        n = len(self._rows)
        if n <= 0:
            return 1
        return max(1, (n + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def _goto(self, page: int) -> None:
        self._page = min(max(0, page), self._page_count() - 1)
        self._render_list()

    def _render_list(self) -> None:
        keep_id = self._current_id
        self.list.blockSignals(True)
        self.list.clear()
        if not self._rows:
            item = QListWidgetItem("还没有运行快照。跑自动流程或「检测测试」后会出现在这里。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            self.list.blockSignals(False)
            self._show_empty()
            self._refresh_page_label()
            return
        start = self._page * _PAGE_SIZE
        page_rows = self._rows[start : start + _PAGE_SIZE]
        select_row = 0
        for i, rec in enumerate(page_rows):
            item = QListWidgetItem(format_snap_label(rec))
            item.setData(Qt.ItemDataRole.UserRole, str(rec.get("id") or ""))
            self.list.addItem(item)
            if keep_id and str(rec.get("id") or "") == keep_id:
                select_row = i
        self.list.setCurrentRow(select_row)
        self.list.blockSignals(False)
        self._refresh_page_label()
        cur = self.list.currentItem()
        if cur is not None:
            self._on_item(cur, None)

    def _refresh_page_label(self) -> None:
        total = len(self._rows)
        pages = self._page_count()
        self.lbl_page.setText(f"第 {self._page + 1} / {pages} 页（共 {total} 条）")

    def _show_empty(self) -> None:
        self._current_id = ""
        self._current_dir = None
        self.img_raw.set_image_path(None)
        self.img_vis.set_image_path(None)
        self.txt.setPlainText("")

    def _on_item(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current is None:
            return
        snap_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not snap_id:
            return
        meta = load_snap_meta(snap_id)
        if meta is None:
            self._current_id = snap_id
            self._current_dir = None
            self.img_raw.set_image_path(None)
            self.img_vis.set_image_path(None)
            self.txt.setPlainText(f"找不到快照文件：{snap_id}")
            return
        self._current_id = snap_id
        shot = Path(str(meta.get("_dir") or ""))
        self._current_dir = shot if shot.is_dir() else None
        raw_path, vis_path = snap_image_paths(meta)
        self.img_raw.set_image_path(raw_path)
        self.img_vis.set_image_path(vis_path)
        self.txt.setPlainText(format_snap_detail(meta))

    def _open_log_dir(self) -> None:
        _open_dir(snap_root(), self)

    def _open_item_dir(self) -> None:
        if self._current_dir is None or not self._current_dir.is_dir():
            QMessageBox.information(self, "运行快照", "请先点左侧一条快照。")
            return
        _open_dir(self._current_dir, self)

    def _copy_detail(self) -> None:
        text = self.txt.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "运行快照", "没有可复制的内容。")
            return
        QGuiApplication.clipboard().setText(text)


def _open_dir(path: Path, parent: QWidget) -> None:
    """用系统文件管理器打开目录。"""
    path.mkdir(parents=True, exist_ok=True)
    url = QUrl.fromLocalFile(str(path))
    if QDesktopServices.openUrl(url):
        return
    QMessageBox.information(parent, "打开目录", f"无法自动打开，请手动进入：\n{path}")
