"""视觉参数页：按当前相机只显示这一路常用项，一个保存按钮。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from hmi.style import apply_page_chrome, groupbox_qss, style_button, style_many
from vision import calib
from vision.shoe_cfg import k_from_calib
from vision.shoe_cfg import load as load_shoe_json
from vision.shoe_cfg import update as patch_shoe_json
from vision.vision_params import (
    load_position,
    parse_gripper_x,
    parse_k,
    parse_rod_roi,
    rod_camera_id,
    save_position,
    write_rod_fields,
)

_CAM_TITLES = {
    "cam1": "皮带上料",
    "cam2": "鞋头对位",
    "cam3": "放料槽有无鞋",
    "cam4": "取料槽 / 压杆",
}

_HINT = (
    "上方选好相机后，这里只出现这一路要调的数。"
    "检测区请到「相机与ROI」拖绿框。"
    "点「保存」后，「检测测试」立刻用新值。"
)


def _spin_int(lo: int, hi: int, val: int, *, step: int = 1) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    sp.setValue(int(val))
    sp.setMinimumWidth(120)
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
    return sp


def _spin_float(
    lo: float,
    hi: float,
    val: float,
    *,
    step: float = 0.01,
    dec: int = 2,
    suffix: str = "",
) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(dec)
    sp.setSingleStep(step)
    sp.setValue(float(val))
    if suffix:
        sp.setSuffix(suffix)
    sp.setMinimumWidth(140)
    sp.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
    return sp


def _form_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    box.setStyleSheet(groupbox_qss())
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(10)
    return box, form


class VisionParamsPage(QWidget):
    """视觉总页子页签：皮带 / 鞋头 / 槽 / 压杆常用参数。"""

    def __init__(
        self,
        coord: Coordinator,
        *,
        cam_id_fn: Callable[[], str],
    ) -> None:
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._cam_id_fn = cam_id_fn
        self._syncing = False
        self._pos_cache: dict[str, Any] = {}
        self._rod_side = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        tip = QLabel(_HINT)
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#566573;font-size:13px;")
        root.addWidget(tip)

        self.lbl_banner = QLabel()
        self.lbl_banner.setWordWrap(True)
        self.lbl_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_banner.setMinimumHeight(44)
        self.lbl_banner.setStyleSheet(
            "background:#1a5276;color:#ecf0f1;padding:10px;border-radius:6px;"
            "font-size:16px;font-weight:bold;"
        )
        root.addWidget(self.lbl_banner)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_cam1())
        self.stack.addWidget(self._build_cam2())
        self.stack.addWidget(self._build_cam3())
        self.stack.addWidget(self._build_cam4())
        root.addWidget(self.stack)

        common, cf = _form_group("四路共用")
        vis = self.ctx.cfg.get("vision") or {}
        self.sp_retry = _spin_int(0, 10, int(vis.get("photo_retry", 3) or 3))
        self.sp_retry_s = _spin_float(
            0.2, 10.0, float(vis.get("photo_retry_interval_s", 1.0) or 1.0), step=0.1, suffix=" s"
        )
        self.chk_snaps = QCheckBox("保存运行快照（原图 / 叠图）")
        self.chk_snaps.setChecked(bool(vis.get("save_runtime_snaps", True)))
        self.sp_keep = _spin_int(1, 60, int(vis.get("snap_keep_days", 7) or 7))
        cf.addRow("拍照失败重试", self.sp_retry)
        cf.addRow("重试间隔", self.sp_retry_s)
        cf.addRow(self.chk_snaps)
        cf.addRow("快照保留天数", self.sp_keep)
        root.addWidget(common)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("保存")
        self.btn_reload = QPushButton("从文件重新读入")
        style_many([(self.btn_save, "success"), (self.btn_reload, "neutral")])
        self.btn_save.clicked.connect(self._save)
        self.btn_reload.clicked.connect(self.reload_from_disk)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_reload)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#1a5276;")
        root.addWidget(self.lbl_status)
        root.addStretch(1)

        apply_page_chrome(self)
        self.reload_from_disk()
        self.show_cam(self._cam_id_fn())

    def _build_cam1(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, form = _form_group("皮带检测（写入 shoe_vision_config.json）")
        self.sp_belt_conf = _spin_float(0.05, 0.95, 0.50, step=0.05)
        self.sp_belt_iou = _spin_float(0.05, 0.95, 0.30, step=0.05)
        form.addRow("置信度", self.sp_belt_conf)
        form.addRow("框重叠 iou", self.sp_belt_iou)
        note = QLabel("数字越大越严、越不容易误检；过严会漏检。换模型请到「采图训练」。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        form.addRow(note)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _build_cam2(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, form = _form_group("鞋头对位（写入 default.yaml）")
        vis = self.ctx.cfg.get("vision") or {}
        toe = vis.get("toe_align") if isinstance(vis.get("toe_align"), dict) else {}
        adv = vis.get("toe_align_advance_mm") or [0.0, 8.0, 0.0]
        self.sp_toe_imgsz = _spin_int(64, 1280, int(toe.get("imgsz", 256) or 256), step=32)
        self.sp_toe_y = _spin_float(
            0.0,
            80.0,
            float(adv[1] if len(adv) > 1 else 8.0),
            step=1.0,
            dec=1,
            suffix=" mm",
        )
        self.sp_heel_n = _spin_int(1, 40, int(vis.get("heel_down_steps", 12) or 12))
        self.sp_heel_dz = _spin_float(
            0.0,
            20.0,
            float(vis.get("heel_down_dz_mm", 0.0) or 0.0),
            step=0.5,
            suffix=" mm",
        )
        form.addRow("输入边长", self.sp_toe_imgsz)
        form.addRow("鞋头前推", self.sp_toe_y)
        form.addRow("跟压次数", self.sp_heel_n)
        form.addRow("每次下降", self.sp_heel_dz)
        note = QLabel("跟压下降填 0 表示不对机械臂下压，只做次数占位。换模型请到「采图训练」。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        form.addRow(note)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _build_cam3(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, form = _form_group("放料槽有无鞋（写入 default.yaml）")
        slot = (self.ctx.cfg.get("vision") or {}).get("slot_check") or {}
        if not isinstance(slot, dict):
            slot = {}
        self.sp_slot_imgsz = _spin_int(64, 1280, int(slot.get("imgsz", 640) or 640), step=32)
        self.sp_slot_conf = _spin_float(0.0, 0.95, float(slot.get("conf", 0.0) or 0.0), step=0.05)
        form.addRow("输入边长", self.sp_slot_imgsz)
        form.addRow("最低置信度", self.sp_slot_conf)
        note = QLabel("填 0 表示不限制。大于 0 时，低于此值当作检测失败（会按配置重试）。换模型请到「采图训练」。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        form.addRow(note)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _build_cam4(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        box, form = _form_group("取料槽压杆（写入 position_config.yaml）")
        self.cmb_rod_side = QComboBox()
        self.cmb_rod_side.addItem("左口（camera_id=1）", 1)
        self.cmb_rod_side.addItem("右口（camera_id=2）", 2)
        self.cmb_rod_side.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]
        self.cmb_rod_side.currentIndexChanged.connect(self._on_rod_side_changed)
        self.sp_rod_conf = _spin_float(0.05, 0.95, 0.40, step=0.05)
        self.sp_rod_imgsz = _spin_int(64, 1280, 640, step=32)
        self.sp_grip_x = _spin_float(-0.5, 0.5, 0.08, step=0.001, dec=4, suffix=" m")
        self.sp_rod_x = _spin_int(0, 4000, 400)
        self.sp_rod_y = _spin_int(0, 3000, 280)
        self.sp_rod_w = _spin_int(40, 4000, 600)
        self.sp_rod_h = _spin_int(40, 3000, 300)
        self.sp_fx = _spin_float(50.0, 4000.0, 610.0, step=0.1, dec=2)
        self.sp_fy = _spin_float(50.0, 4000.0, 610.0, step=0.1, dec=2)
        self.sp_cx = _spin_float(0.0, 4000.0, 640.0, step=0.1, dec=2)
        self.sp_cy = _spin_float(0.0, 4000.0, 360.0, step=0.1, dec=2)
        form.addRow("压机开口", self.cmb_rod_side)
        form.addRow("压杆置信度", self.sp_rod_conf)
        form.addRow("输入边长", self.sp_rod_imgsz)
        form.addRow("夹爪示教 X", self.sp_grip_x)
        xy = QWidget()
        xy_l = QHBoxLayout(xy)
        xy_l.setContentsMargins(0, 0, 0, 0)
        xy_l.addWidget(QLabel("X"))
        xy_l.addWidget(self.sp_rod_x)
        xy_l.addWidget(QLabel("Y"))
        xy_l.addWidget(self.sp_rod_y)
        form.addRow("检测区左上角", xy)
        wh = QWidget()
        wh_l = QHBoxLayout(wh)
        wh_l.setContentsMargins(0, 0, 0, 0)
        wh_l.addWidget(QLabel("宽"))
        wh_l.addWidget(self.sp_rod_w)
        wh_l.addWidget(QLabel("高"))
        wh_l.addWidget(self.sp_rod_h)
        form.addRow("检测区大小", wh)
        krow = QWidget()
        k_l = QHBoxLayout(krow)
        k_l.setContentsMargins(0, 0, 0, 0)
        for name, sp in (
            ("fx", self.sp_fx),
            ("fy", self.sp_fy),
            ("cx", self.sp_cx),
            ("cy", self.sp_cy),
        ):
            k_l.addWidget(QLabel(name))
            k_l.addWidget(sp)
        form.addRow("压杆内参", krow)
        btn_k = QPushButton("用 cam4 棋盘格内参填入")
        style_button(btn_k, "primary")
        btn_k.clicked.connect(self._fill_k_from_calib)
        form.addRow(btn_k)
        note = QLabel(
            "压杆计算用本页内参，和「棋盘格内参」文件不是同一份。"
            "检测区是品红框（与绿框 ROI 文件分开）。"
            "保存会重写 position_config.yaml（文件里的注释不会保留）。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        form.addRow(note)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def show_cam(self, cam_id: str) -> None:
        """跟视觉页上方相机下拉同步，只显示这一路表单。"""
        cid = str(cam_id or "cam1")
        idx = {"cam1": 0, "cam2": 1, "cam3": 2, "cam4": 3}.get(cid, 0)
        self.stack.setCurrentIndex(idx)
        title = _CAM_TITLES.get(cid, cid)
        self.lbl_banner.setText(f"正在设置　{cid}　{title}")

    def reload_from_disk(self) -> None:
        """控件跟磁盘 / 内存配置对齐。"""
        self._syncing = True
        try:
            vis = self.ctx.cfg.get("vision") or {}
            shoe = load_shoe_json(vis)
            self.sp_belt_conf.setValue(float(shoe.get("conf", 0.5) or 0.5))
            self.sp_belt_iou.setValue(float(shoe.get("iou", 0.3) or 0.3))

            toe = vis.get("toe_align") if isinstance(vis.get("toe_align"), dict) else {}
            adv = vis.get("toe_align_advance_mm") or [0.0, 8.0, 0.0]
            self.sp_toe_imgsz.setValue(int(toe.get("imgsz", 256) or 256))
            self.sp_toe_y.setValue(float(adv[1] if len(adv) > 1 else 8.0))
            self.sp_heel_n.setValue(int(vis.get("heel_down_steps", 12) or 12))
            self.sp_heel_dz.setValue(float(vis.get("heel_down_dz_mm", 0.0) or 0.0))

            slot = vis.get("slot_check") if isinstance(vis.get("slot_check"), dict) else {}
            self.sp_slot_imgsz.setValue(int(slot.get("imgsz", 640) or 640))
            self.sp_slot_conf.setValue(float(slot.get("conf", 0.0) or 0.0))

            self.sp_retry.setValue(int(vis.get("photo_retry", 3) or 3))
            self.sp_retry_s.setValue(float(vis.get("photo_retry_interval_s", 1.0) or 1.0))
            self.chk_snaps.setChecked(bool(vis.get("save_runtime_snaps", True)))
            self.sp_keep.setValue(int(vis.get("snap_keep_days", 7) or 7))

            pos = load_position(vis)
            self._pos_cache = pos
            cid = rod_camera_id(vis)
            self._rod_side = cid
            self.cmb_rod_side.blockSignals(True)
            self.cmb_rod_side.setCurrentIndex(1 if cid == 2 else 0)
            self.cmb_rod_side.blockSignals(False)
            self._fill_rod_side(pos, cid)
        finally:
            self._syncing = False
        self.lbl_status.setText("已从配置读入。")

    def _fill_rod_side(self, pos: dict[str, Any], camera_id: int) -> None:
        self.sp_rod_conf.setValue(float(pos.get("rod_obb_detection_conf", 0.4) or 0.4))
        self.sp_rod_imgsz.setValue(int(pos.get("rod_obb_img_size", 640) or 640))
        self.sp_grip_x.setValue(parse_gripper_x(pos, camera_id))
        x, y, w, h = parse_rod_roi(pos, camera_id)
        self.sp_rod_x.setValue(x)
        self.sp_rod_y.setValue(y)
        self.sp_rod_w.setValue(w)
        self.sp_rod_h.setValue(h)
        fx, fy, cx, cy = parse_k(pos, camera_id)
        self.sp_fx.setValue(fx)
        self.sp_fy.setValue(fy)
        self.sp_cx.setValue(cx)
        self.sp_cy.setValue(cy)

    def _stash_rod_widgets(self) -> None:
        """把当前开口的旋钮写进内存表，换左/右口时不丢未保存的改动。"""
        if not self._pos_cache:
            self._pos_cache = load_position(self.ctx.cfg.get("vision") or {})
        write_rod_fields(
            self._pos_cache,
            camera_id=int(self._rod_side),
            conf=float(self.sp_rod_conf.value()),
            imgsz=int(self.sp_rod_imgsz.value()),
            gripper_x=float(self.sp_grip_x.value()),
            roi_xywh=(
                int(self.sp_rod_x.value()),
                int(self.sp_rod_y.value()),
                int(self.sp_rod_w.value()),
                int(self.sp_rod_h.value()),
            ),
            k=(
                float(self.sp_fx.value()),
                float(self.sp_fy.value()),
                float(self.sp_cx.value()),
                float(self.sp_cy.value()),
            ),
        )

    def _on_rod_side_changed(self, _idx: int = 0) -> None:
        if self._syncing:
            return
        self._stash_rod_widgets()
        nxt = int(self.cmb_rod_side.currentData() or 1)
        self._rod_side = nxt
        self._fill_rod_side(self._pos_cache, nxt)

    def _fill_k_from_calib(self) -> None:
        data = calib.load_calib("cam4")
        k = k_from_calib(data) if data else None
        if not k:
            QMessageBox.information(self, "没有内参", "请先在「棋盘格内参」对 cam4 计算并保存。")
            return
        self.sp_fx.setValue(float(k["fx"]))
        self.sp_fy.setValue(float(k["fy"]))
        self.sp_cx.setValue(float(k["cx"]))
        self.sp_cy.setValue(float(k["cy"]))
        self.lbl_status.setText("已填入 cam4 棋盘格内参，请再点「保存」。")

    def _save(self) -> None:
        vis = self.ctx.cfg.setdefault("vision", {})
        if not isinstance(vis, dict):
            self.ctx.cfg["vision"] = {}
            vis = self.ctx.cfg["vision"]

        vis.setdefault("toe_align", {})
        if not isinstance(vis["toe_align"], dict):
            vis["toe_align"] = {}
        vis["toe_align"]["imgsz"] = int(self.sp_toe_imgsz.value())
        adv = list(vis.get("toe_align_advance_mm") or [0.0, 8.0, 0.0])
        while len(adv) < 3:
            adv.append(0.0)
        adv[1] = float(self.sp_toe_y.value())
        vis["toe_align_advance_mm"] = adv
        vis["heel_down_steps"] = int(self.sp_heel_n.value())
        vis["heel_down_dz_mm"] = float(self.sp_heel_dz.value())

        vis.setdefault("slot_check", {})
        if not isinstance(vis["slot_check"], dict):
            vis["slot_check"] = {}
        vis["slot_check"]["imgsz"] = int(self.sp_slot_imgsz.value())
        vis["slot_check"]["conf"] = float(self.sp_slot_conf.value())

        vis["photo_retry"] = int(self.sp_retry.value())
        vis["photo_retry_interval_s"] = float(self.sp_retry_s.value())
        vis["save_runtime_snaps"] = bool(self.chk_snaps.isChecked())
        vis["snap_keep_days"] = int(self.sp_keep.value())

        vis.setdefault("position", {})
        if not isinstance(vis["position"], dict):
            vis["position"] = {}
        camera_id = int(self.cmb_rod_side.currentData() or 1)
        vis["position"]["camera_id"] = camera_id

        try:
            save_config(self.ctx.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"写 default.yaml 失败：{exc}")
            return

        try:
            patch_shoe_json(
                {
                    "conf": float(self.sp_belt_conf.value()),
                    "iou": float(self.sp_belt_iou.value()),
                },
                vis,
            )
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"写皮带 json 失败：{exc}")
            return

        try:
            self._stash_rod_widgets()
            self._rod_side = camera_id
            save_position(self._pos_cache, vis)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"写压杆 yaml 失败：{exc}")
            return

        try:
            from vision.legacy_pipeline import reset_shoe_vision

            reset_shoe_vision()
        except Exception:
            pass

        self.lbl_status.setText("已保存。可到「检测测试」验证。")
