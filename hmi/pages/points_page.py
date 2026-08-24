"""点位编辑 + 单点/路径调试。上料R1 / 下料R2 分页，避免混淆。"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from devices.pose_utils import (
    JOINT_AXES,
    apply_offset,
    extract_joints,
    has_taught_joints,
    joints_to_dict,
    normalize_via_name,
    numeric_pose,
    point_display_name,
    resolve_via_point_key,
    validate_via_name,
)
from hmi.style import apply_page_chrome, style_button, style_many


class NoWheelSpinBox(QDoubleSpinBox):
    """禁用滚轮改值，避免滚动页面时误改坐标/关节。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.setStyleSheet(
            "QDoubleSpinBox{padding:4px 8px;min-height:30px;font-size:13px;}"
            "QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:24px;}"
        )

    def wheelEvent(self, event) -> None:
        # ignore → 滚轮交给外层 ScrollArea，不改变数值
        event.ignore()


class NoWheelComboBox(QComboBox):
    """禁用滚轮切换选项，避免误触。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("QComboBox{padding:4px 8px;min-height:30px;}")

    def wheelEvent(self, event) -> None:
        event.ignore()


CORE_POINTS = {
    "home",
    "pick_entry",
    "pick_above_offset",
    "place_entry",
    "place_slot",
    "place_above_offset",
    "slot_pick_entry",
    "slot_pick",
    "slot_pick_above_offset",
    "belt_place_entry",
    "belt_place",
    "belt_place_above_offset",
}

# 偏移键 → 默认基点（流程里实际怎么用）
# __pick_pose__ = 运行时 PickPose（视觉/屏蔽取料点）
OFFSET_DEFAULT_BASE = {
    "pick_above_offset": "__pick_pose__",
    "place_above_offset": "place_slot",
    "slot_pick_above_offset": "slot_pick",
    "belt_place_above_offset": "belt_place",
}

PICK_POSE_KEY = "__pick_pose__"

ROBOT_TABS = (
    (
        "robot1",
        "上料机器人 R1",
        "仅 points.robot1：皮带取料进入/上方偏移、鞋槽放料进入/放料点等。\n"
        "取料 XYRz 来自视觉/屏蔽示教(PickPose)，不在本表；本表是路径固定点。",
        "#1a5276",
    ),
    (
        "robot2",
        "下料机器人 R2",
        "仅 points.robot2：鞋槽取料进入/取料点、皮带放料进入/放料点等。\n"
        "取料与放料坐标均为固定示教点（与 R1 视觉取料无关）。",
        "#196f3d",
    ),
)


class PointsPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self.spins = {}
        self.joint_spins = {}
        self._joints_valid = False  # 是否已示教关节（避免把 0,0,0… 当成真关节）
        self._point_keys: list[str] = []
        self._dbg_busy = False
        self._dbg_linear = False
        self._path_pending_to = None
        self._robot_key_cur = "robot1"

        outer = QVBoxLayout(self)

        # 只用标签条切换 R1/R2；内容共用下方滚动区（避免 QTabWidget 空页占一大片空白）
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        for _key, title, _, _ in ROBOT_TABS:
            self.tabs.addTab(title)
        outer.addWidget(self.tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        root = QVBoxLayout(body)

        self.lbl_scope = QLabel("")
        self.lbl_scope.setWordWrap(True)
        self.lbl_scope.setStyleSheet(
            "color:white;padding:8px;border-radius:4px;font-weight:bold;"
        )
        root.addWidget(self.lbl_scope)

        row = QHBoxLayout()
        self.cmb_point = NoWheelComboBox()
        self.cmb_point.currentIndexChanged.connect(self._load_values)
        row.addWidget(QLabel("本臂点位"))
        row.addWidget(self.cmb_point, stretch=1)
        root.addLayout(row)

        self.lbl_key = QLabel("配置键: -")
        self.lbl_key.setStyleSheet("color:#666;")
        root.addWidget(self.lbl_key)

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("中文备注，建议带【上料R1】或【下料R2】前缀")
        form.addRow("中文备注", self.ed_name)
        self.chk_blend = QCheckBox("到此点平滑（段间交融；需监视页总开关）")
        self.chk_blend.setToolTip(
            "勾选后，自动流程/试跑「到该点」时可用法奥 blend。"
            "偏移点勾选表示走到 基点+本偏移 时平滑。"
            "取放工作点建议不勾；进入点/过渡点/上方偏移可勾。"
        )
        form.addRow("路径平滑", self.chk_blend)
        self.sp_pt_blend_t = NoWheelSpinBox()
        self.sp_pt_blend_t.setRange(-1, 500)
        self.sp_pt_blend_t.setDecimals(0)
        self.sp_pt_blend_t.setSpecialValueText("用全局")
        self.sp_pt_blend_t.setValue(-1)
        self.sp_pt_blend_t.setToolTip("MoveJ 平滑时间 ms；选「用全局」则用监视页 blendT")
        form.addRow("本点 blendT(ms)", self.sp_pt_blend_t)
        self.sp_pt_blend_r = NoWheelSpinBox()
        self.sp_pt_blend_r.setRange(-1, 1000)
        self.sp_pt_blend_r.setDecimals(1)
        self.sp_pt_blend_r.setSpecialValueText("用全局")
        self.sp_pt_blend_r.setValue(-1)
        self.sp_pt_blend_r.setToolTip("MoveL 平滑半径 mm；选「用全局」则用监视页 blendR")
        form.addRow("本点 blendR(mm)", self.sp_pt_blend_r)
        self.chk_blend.toggled.connect(self._sync_blend_param_enabled)
        self._sync_blend_param_enabled(False)
        for k in ("x", "y", "z", "rx", "ry", "rz"):
            sp = NoWheelSpinBox()
            sp.setRange(-9999, 9999)
            sp.setDecimals(3)
            self.spins[k] = sp
            form.addRow(k.upper(), sp)
            sp.valueChanged.connect(self._on_spin_changed)
        self.lbl_joint_hint = QLabel("关节角（MoveJ 必填，单位 °；滚轮已禁用，请点右侧箭头或键盘输入）")
        self.lbl_joint_hint.setStyleSheet("color:#a04000;font-weight:bold;")
        form.addRow(self.lbl_joint_hint)
        self.joint_spins = {}
        for k in JOINT_AXES:
            sp = NoWheelSpinBox()
            sp.setRange(-3600, 3600)
            sp.setDecimals(3)
            self.joint_spins[k] = sp
            form.addRow(k.upper(), sp)
        root.addLayout(form)

        edit_row = QHBoxLayout()
        btn_save = QPushButton("保存当前点")
        btn_add = QPushButton("新增点位…")
        btn_del = QPushButton("删除当前点")
        btn_read = QPushButton("读入当前TCP+关节")
        btn_clear_j = QPushButton("清除关节角")
        btn_save.clicked.connect(self._save)
        btn_add.clicked.connect(self._add_point)
        btn_del.clicked.connect(self._delete_point)
        btn_read.clicked.connect(self._read_tcp)
        btn_clear_j.clicked.connect(self._clear_joints)
        style_many(
            [
                (btn_save, "success"),
                (btn_add, "success"),
                (btn_del, "danger"),
                (btn_read, "primary"),
                (btn_clear_j, "warn"),
            ]
        )
        for b in (btn_save, btn_add, btn_del, btn_read, btn_clear_j):
            edit_row.addWidget(b)
        root.addLayout(edit_row)

        # 过渡点：现场加中间点 / 删掉不用的
        box_via = QGroupBox("过渡点（现场加中间路点：读当前位姿+关节 → 保存；不用了可删除）")
        bv = QVBoxLayout(box_via)
        via_row = QHBoxLayout()
        btn_via_add = QPushButton("新增过渡点…")
        btn_via_del = QPushButton("删除过渡点…")
        self.btn_point_undo = QPushButton("撤回路点操作")
        style_many(
            [
                (btn_via_add, "success"),
                (btn_via_del, "danger"),
                (self.btn_point_undo, "primary"),
            ]
        )
        btn_via_add.clicked.connect(self._add_via_point)
        btn_via_del.clicked.connect(self._delete_via_point)
        self.btn_point_undo.clicked.connect(self._undo_point_op)
        via_row.addWidget(btn_via_add)
        via_row.addWidget(btn_via_del)
        via_row.addWidget(self.btn_point_undo)
        bv.addLayout(via_row)
        self.lbl_undo = QLabel("撤回：无")
        self.lbl_undo.setStyleSheet(
            "color:#1a5276;font-weight:bold;padding:4px 8px;"
            "background:#eaf2f8;border-radius:4px;"
        )
        bv.addWidget(self.lbl_undo)
        root.addWidget(box_via)
        self._refresh_undo_label()

        box1 = QGroupBox("单点调试（只动当前标签页对应的那台臂）")
        b1 = QVBoxLayout(box1)
        self.lbl_pose = QLabel("当前TCP: -")
        self.lbl_pose.setWordWrap(True)
        b1.addWidget(self.lbl_pose)
        mv = QHBoxLayout()
        btn_j = QPushButton("MoveJ 到此点")
        btn_l = QPushButton("MoveL 到此点")
        btn_stop = QPushButton("停止运动")
        btn_j.clicked.connect(lambda: self._move_to_selected(linear=False))
        btn_l.clicked.connect(lambda: self._move_to_selected(linear=True))
        btn_stop.clicked.connect(self._stop_move)
        style_many([(btn_j, "motion"), (btn_l, "motion"), (btn_stop, "danger")])
        mv.addWidget(btn_j)
        mv.addWidget(btn_l)
        mv.addWidget(btn_stop)
        b1.addLayout(mv)
        root.addWidget(box1)

        box_off = QGroupBox(
            "偏移量试跑（基点 + 偏移 = 实际目标；偏移不是绝对坐标，请用这里走点）"
        )
        bo = QVBoxLayout(box_off)
        off_row = QHBoxLayout()
        self.cmb_off_base = NoWheelComboBox()
        self.cmb_off_offset = NoWheelComboBox()
        self.cmb_off_base.currentIndexChanged.connect(self._refresh_offset_preview)
        self.cmb_off_offset.currentIndexChanged.connect(self._refresh_offset_preview)
        off_row.addWidget(QLabel("基点"))
        off_row.addWidget(self.cmb_off_base, stretch=1)
        off_row.addWidget(QLabel("+ 偏移"))
        off_row.addWidget(self.cmb_off_offset, stretch=1)
        bo.addLayout(off_row)
        self.lbl_off_result = QLabel("合成目标: -")
        self.lbl_off_result.setWordWrap(True)
        self.lbl_off_result.setStyleSheet(
            "padding:6px;background:#fff8e7;border:1px solid #e0c080;"
        )
        bo.addWidget(self.lbl_off_result)
        off_btns = QHBoxLayout()
        btn_oj = QPushButton("MoveJ → 基点+偏移")
        btn_ol = QPushButton("MoveL → 基点+偏移")
        btn_oj.clicked.connect(lambda: self._move_offset_target(linear=False))
        btn_ol.clicked.connect(lambda: self._move_offset_target(linear=True))
        style_many([(btn_oj, "motion"), (btn_ol, "motion")])
        off_btns.addWidget(btn_oj)
        off_btns.addWidget(btn_ol)
        bo.addLayout(off_btns)
        root.addWidget(box_off)

        box2 = QGroupBox("路径试跑（仅本臂点位之间）")
        b2 = QVBoxLayout(box2)
        path_row = QHBoxLayout()
        self.cmb_from = NoWheelComboBox()
        self.cmb_to = NoWheelComboBox()
        path_row.addWidget(QLabel("从"))
        path_row.addWidget(self.cmb_from, stretch=1)
        path_row.addWidget(QLabel("到"))
        path_row.addWidget(self.cmb_to, stretch=1)
        b2.addLayout(path_row)
        path_btns = QHBoxLayout()
        btn_pj = QPushButton("MoveJ 试跑 从→到")
        btn_pl = QPushButton("MoveL 试跑 从→到")
        btn_pj.clicked.connect(lambda: self._move_path(linear=False))
        btn_pl.clicked.connect(lambda: self._move_path(linear=True))
        style_many([(btn_pj, "motion"), (btn_pl, "motion")])
        path_btns.addWidget(btn_pj)
        path_btns.addWidget(btn_pl)
        b2.addLayout(path_btns)
        root.addWidget(box2)

        self.lbl_dbg = QLabel("调试状态: 空闲")
        self.lbl_dbg.setWordWrap(True)
        self.lbl_dbg.setStyleSheet("padding:6px;background:#f5f5f5;border-radius:4px;")
        root.addWidget(self.lbl_dbg)

        scroll.setWidget(body)
        from hmi.scroll_util import attach_page_scroll, harden_wheel

        attach_page_scroll(scroll)
        harden_wheel(body)
        outer.addWidget(scroll)
        apply_page_chrome(self, accent="#1a5276")

        self._apply_tab_style(0)
        self._reload_points()

    def _on_tab_changed(self, idx: int) -> None:
        if idx < 0 or idx >= len(ROBOT_TABS):
            return
        self._robot_key_cur = ROBOT_TABS[idx][0]
        self._apply_tab_style(idx)
        self._reload_points()

    def _apply_tab_style(self, idx: int) -> None:
        key, title, desc, color = ROBOT_TABS[idx]
        self.lbl_scope.setText(f"{title}\n{desc}")
        self.lbl_scope.setStyleSheet(
            f"color:white;background:{color};padding:8px;border-radius:4px;font-weight:bold;"
        )

    def _on_spin_changed(self, *_args) -> None:
        if self._is_offset_key(self._current_point_key()):
            self._refresh_offset_preview()

    def _sync_blend_param_enabled(self, on: bool | None = None) -> None:
        en = bool(self.chk_blend.isChecked() if on is None else on)
        self.sp_pt_blend_t.setEnabled(en)
        self.sp_pt_blend_r.setEnabled(en)

    def _ui_blend_kwargs(self) -> dict:
        """界面当前平滑选项（可含未保存的本点 T/R）。"""
        out: dict = {"blend": bool(self.chk_blend.isChecked())}
        t = float(self.sp_pt_blend_t.value())
        r = float(self.sp_pt_blend_r.value())
        if t >= 0:
            out["blend_t_ms"] = t
        if r >= 0:
            out["blend_r_mm"] = r
        return out

    def _robot_key(self) -> str:
        return self._robot_key_cur

    def _robot(self):
        return self.ctx.robot1 if self._robot_key() == "robot1" else self.ctx.robot2

    def _fill_point_combo(self, combo: QComboBox, prefer: str = "") -> None:
        combo.blockSignals(True)
        combo.clear()
        key = self._robot_key()
        pts = self.ctx.cfg.get("points", {}).get(key, {}) or {}
        sel = -1
        for i, (pname, pose) in enumerate(pts.items()):
            if not isinstance(pose, dict):
                continue
            zh = point_display_name(pname, pose, robot_key=key)
            combo.addItem(f"{zh}  [{pname}]", pname)
            if pname == prefer:
                sel = i
        if sel >= 0:
            combo.setCurrentIndex(sel)
        combo.blockSignals(False)

    def _reload_points(self) -> None:
        cur = self._current_point_key() if self.cmb_point.count() else ""
        self._fill_point_combo(self.cmb_point, prefer=cur)
        self._point_keys = [
            str(self.cmb_point.itemData(i)) for i in range(self.cmb_point.count())
        ]
        from_pref = str(self.cmb_from.currentData() or "home")
        to_pref = str(self.cmb_to.currentData() or cur or "home")
        self._fill_point_combo(self.cmb_from, prefer=from_pref)
        self._fill_point_combo(self.cmb_to, prefer=to_pref)
        self._reload_offset_combos(prefer_offset=cur if self._is_offset_key(cur) else "")
        self._load_values()

    def _is_offset_key(self, pname: str) -> bool:
        return bool(pname) and ("offset" in pname)

    def _reload_offset_combos(self, prefer_offset: str = "") -> None:
        key = self._robot_key()
        pts = self.ctx.cfg.get("points", {}).get(key, {}) or {}

        # 偏移下拉：仅 *offset*
        self.cmb_off_offset.blockSignals(True)
        self.cmb_off_offset.clear()
        off_sel = -1
        for i, (pname, pose) in enumerate(pts.items()):
            if not isinstance(pose, dict) or not self._is_offset_key(pname):
                continue
            zh = point_display_name(pname, pose, robot_key=key)
            self.cmb_off_offset.addItem(f"{zh}  [{pname}]", pname)
            if pname == prefer_offset:
                off_sel = self.cmb_off_offset.count() - 1
        if off_sel < 0 and self.cmb_off_offset.count():
            off_sel = 0
        if off_sel >= 0:
            self.cmb_off_offset.setCurrentIndex(off_sel)
        self.cmb_off_offset.blockSignals(False)

        off_key = str(self.cmb_off_offset.currentData() or "")
        prefer_base = OFFSET_DEFAULT_BASE.get(off_key, "home")

        self.cmb_off_base.blockSignals(True)
        self.cmb_off_base.clear()
        if key == "robot1":
            self.cmb_off_base.addItem("当前PickPose（视觉/屏蔽取料点）", PICK_POSE_KEY)
        base_sel = 0 if prefer_base == PICK_POSE_KEY and key == "robot1" else -1
        for pname, pose in pts.items():
            if not isinstance(pose, dict) or self._is_offset_key(pname):
                continue
            zh = point_display_name(pname, pose, robot_key=key)
            self.cmb_off_base.addItem(f"{zh}  [{pname}]", pname)
            if pname == prefer_base:
                base_sel = self.cmb_off_base.count() - 1
        if base_sel < 0 and self.cmb_off_base.count():
            base_sel = 0
        if base_sel >= 0:
            self.cmb_off_base.setCurrentIndex(base_sel)
        self.cmb_off_base.blockSignals(False)
        self._refresh_offset_preview()

    def _base_pose_dict(self, base_key: str) -> dict:
        """取基点绝对 TCP；PickPose 来自 gvl。"""
        if base_key == PICK_POSE_KEY:
            return numeric_pose(dict(self.ctx.gvl.PickPose))
        key = self._robot_key()
        raw = self.ctx.cfg["points"][key][base_key]
        return numeric_pose(raw)

    def _offset_pose_dict(self, off_key: str, *, use_spins_if_current: bool = True) -> dict:
        """取偏移量；若当前正在编辑该偏移，优先用界面 spin（未保存也可试）。"""
        cur = self._current_point_key()
        if use_spins_if_current and cur == off_key:
            return self._spin_pose()
        key = self._robot_key()
        raw = self.ctx.cfg["points"][key][off_key]
        return numeric_pose(raw)

    def _compute_offset_target(self) -> tuple[dict, str, str] | None:
        base_key = str(self.cmb_off_base.currentData() or "")
        off_key = str(self.cmb_off_offset.currentData() or "")
        if not base_key or not off_key:
            return None
        try:
            base = self._base_pose_dict(base_key)
            off = self._offset_pose_dict(off_key)
            target = apply_offset(base, off)
        except Exception:
            return None
        if base_key == PICK_POSE_KEY:
            base_tag = "当前PickPose"
        else:
            base_tag = self.ctx.named_point_tag(self._robot_key(), base_key)
        off_tag = self.ctx.named_point_tag(self._robot_key(), off_key)
        return target, base_tag, off_tag

    def _refresh_offset_preview(self) -> None:
        got = self._compute_offset_target()
        if not got:
            self.lbl_off_result.setText("合成目标: （请选择基点与偏移）")
            return
        target, base_tag, off_tag = got
        axes = ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
        self.lbl_off_result.setText(
            f"合成目标 = {base_tag} + {off_tag}\n{axes}"
        )

    def _move_offset_target(self, *, linear: bool) -> None:
        if self._dbg_busy:
            QMessageBox.information(self, "忙", "上一段调试未完成，可点「停止运动」。")
            return
        got = self._compute_offset_target()
        if not got:
            QMessageBox.warning(self, "无效", "请先选择基点与偏移点")
            return
        target, base_tag, off_tag = got
        how = "MoveL" if linear else "MoveJ"
        arm = "上料R1" if self._robot_key() == "robot1" else "下料R2"
        label = f"{base_tag}+偏移({off_tag})"
        if not self._confirm_move(
            f"{how} 偏移合成点",
            f"臂={arm}\n{label}\n"
            + ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz")),
        ):
            return
        off_key = str(self.cmb_off_offset.currentData() or "")
        if self._current_point_key() == off_key:
            blend_kw = self._ui_blend_kwargs()
        else:
            blend_kw = self.ctx.point_blend_kwargs(self._robot_key(), off_key)
        robot = self._robot()
        try:
            if linear:
                robot.move_l(target, label=label, **blend_kw)
            else:
                robot.move_j(target, label=label, **blend_kw)
        except Exception as e:
            QMessageBox.critical(self, "发令失败", str(e))
            return
        self._dbg_busy = True
        self._dbg_linear = linear
        self.lbl_dbg.setText(f"偏移试跑[{arm}]: {robot.path_hint() or (how + ' → ' + label)}")

    def _current_point_key(self) -> str:
        data = self.cmb_point.currentData()
        return str(data) if data else ""

    def _combo_point_key(self, combo: QComboBox) -> str:
        data = combo.currentData()
        return str(data) if data else ""

    def _load_values(self) -> None:
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            self.lbl_key.setText("配置键: -")
            self.ed_name.clear()
            self.chk_blend.setChecked(False)
            self.sp_pt_blend_t.setValue(-1)
            self.sp_pt_blend_r.setValue(-1)
            self._sync_blend_param_enabled(False)
            return
        pose = self.ctx.cfg["points"][key][pname]
        self.lbl_key.setText(f"配置键: points.{key}.{pname}（只属于该臂）")
        self.ed_name.setText(point_display_name(pname, pose, robot_key=key))
        self.chk_blend.setChecked(bool(pose.get("blend", False)))
        if pose.get("blend_t_ms") is not None:
            try:
                self.sp_pt_blend_t.setValue(float(pose["blend_t_ms"]))
            except (TypeError, ValueError):
                self.sp_pt_blend_t.setValue(-1)
        else:
            self.sp_pt_blend_t.setValue(-1)
        if pose.get("blend_r_mm") is not None:
            try:
                self.sp_pt_blend_r.setValue(float(pose["blend_r_mm"]))
            except (TypeError, ValueError):
                self.sp_pt_blend_r.setValue(-1)
        else:
            self.sp_pt_blend_r.setValue(-1)
        self._sync_blend_param_enabled(bool(pose.get("blend", False)))
        for k, sp in self.spins.items():
            sp.setValue(float(pose.get(k, 0)))
        joints = extract_joints(pose)
        if joints:
            for i, k in enumerate(JOINT_AXES):
                self.joint_spins[k].setValue(float(joints[i]))
            self._joints_valid = True
            self.lbl_joint_hint.setText("关节角（已示教，MoveJ 将走真关节）")
            self.lbl_joint_hint.setStyleSheet("color:#1a7a37;font-weight:bold;")
        else:
            for sp in self.joint_spins.values():
                sp.setValue(0.0)
            self._joints_valid = False
            self.lbl_joint_hint.setText("关节角（未示教！请读入后保存，否则 MoveJ 路径可能怪）")
            self.lbl_joint_hint.setStyleSheet("color:#a04000;font-weight:bold;")
        if self._is_offset_key(pname):
            self.lbl_key.setText(
                f"配置键: points.{key}.{pname}  ★相对偏移（不是绝对坐标）\n"
                "请用下方「偏移量试跑」走到 基点+偏移。"
            )
            self._reload_offset_combos(prefer_offset=pname)
            for k in JOINT_AXES:
                self.joint_spins[k].setEnabled(False)
            self.lbl_joint_hint.setText("偏移点无需关节角；请用「偏移量试跑」")
            self.lbl_joint_hint.setStyleSheet("color:#1a5276;font-weight:bold;")
        else:
            for k in JOINT_AXES:
                self.joint_spins[k].setEnabled(True)
            self._refresh_offset_preview()
        idx = self.cmb_point.currentIndex()
        if 0 <= idx < self.cmb_to.count():
            self.cmb_to.setCurrentIndex(idx)

    def _spin_pose(self) -> dict:
        return {k: float(sp.value()) for k, sp in self.spins.items()}

    def _spin_joints(self) -> dict:
        return {k: float(sp.value()) for k, sp in self.joint_spins.items()}

    def _save(self) -> None:
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            return
        entry = self.ctx.cfg["points"][key].setdefault(pname, {})
        name = self.ed_name.text().strip() or point_display_name(
            pname, entry, robot_key=key
        )
        entry["name"] = name
        entry["blend"] = bool(self.chk_blend.isChecked())
        t = float(self.sp_pt_blend_t.value())
        r = float(self.sp_pt_blend_r.value())
        if entry["blend"] and t >= 0:
            entry["blend_t_ms"] = t
        else:
            entry.pop("blend_t_ms", None)
        if entry["blend"] and r >= 0:
            entry["blend_r_mm"] = r
        else:
            entry.pop("blend_r_mm", None)
        for k, sp in self.spins.items():
            entry[k] = float(sp.value())
        if "offset" in pname:
            for k in JOINT_AXES:
                entry.pop(k, None)
            entry.pop("joints", None)
        elif self._joints_valid:
            for k, sp in self.joint_spins.items():
                entry[k] = float(sp.value())
            entry.pop("joints", None)
        else:
            for k in JOINT_AXES:
                entry.pop(k, None)
            entry.pop("joints", None)
        save_config(self.ctx.cfg)
        self._reload_points()
        tip = ""
        if "offset" not in pname and not self._joints_valid:
            tip = "\n（注意：未写入关节角，MoveJ 会回退 MoveCart）"
        elif "offset" not in pname:
            tip = "\n（已含示教关节，MoveJ 走真关节角）"
        if entry.get("blend"):
            t_s = (
                f"{entry['blend_t_ms']:.0f}ms"
                if "blend_t_ms" in entry
                else "用全局"
            )
            r_s = (
                f"{entry['blend_r_mm']:.1f}mm"
                if "blend_r_mm" in entry
                else "用全局"
            )
            tip += f"\n（到点平滑: 开  T={t_s}  R={r_s}）"
        else:
            tip += "\n（到点平滑: 关）"
        QMessageBox.information(
            self, "保存", f"已写入\n{name}\n(points.{key}.{pname}){tip}"
        )

    def _clear_joints(self) -> None:
        for sp in self.joint_spins.values():
            sp.setValue(0.0)
        self._joints_valid = False
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            return
        entry = self.ctx.cfg["points"][key].get(pname)
        if isinstance(entry, dict):
            for k in JOINT_AXES:
                entry.pop(k, None)
            entry.pop("joints", None)
            save_config(self.ctx.cfg)
        self.lbl_joint_hint.setText("关节角已清除（未示教）")
        self.lbl_joint_hint.setStyleSheet("color:#a04000;font-weight:bold;")
        self.lbl_dbg.setText("已清除关节角并写回配置")

    def _add_point(self) -> None:
        key = self._robot_key()
        prefix = "【上料R1】" if key == "robot1" else "【下料R2】"
        raw, ok = QInputDialog.getText(
            self,
            f"新增{prefix}点位",
            "配置键（英文/数字/下划线，如 mid1）:",
        )
        if not ok:
            return
        pname = raw.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pname or ""):
            QMessageBox.warning(self, "无效", "配置键须以字母/下划线开头，只含字母数字下划线")
            return
        if pname in self.ctx.cfg.get("points", {}).get(key, {}):
            QMessageBox.warning(self, "已存在", f"points.{key}.{pname} 已有")
            return
        zh, ok2 = QInputDialog.getText(
            self, "中文备注", "显示名称:", text=f"{prefix}自定义 {pname}"
        )
        if not ok2:
            return
        pose = self._spin_pose()
        joints = None
        try:
            pose = numeric_pose(self._robot().get_actual_tcp_pose())
            joints = self._robot().get_actual_joint_pos()
        except Exception:
            pass
        # 普通新增点默认不平滑；过渡点用「新增过渡点」默认开平滑
        entry = {"name": (zh.strip() or f"{prefix}{pname}"), "blend": False, **pose}
        if joints:
            entry.update(joints_to_dict(joints))
        self.ctx.cfg.setdefault("points", {}).setdefault(key, {})[pname] = entry
        self.ctx.point_undo.push_add(key, pname, entry)
        save_config(self.ctx.cfg)
        self._reload_points()
        self._refresh_undo_label()
        for i in range(self.cmb_point.count()):
            if self.cmb_point.itemData(i) == pname:
                self.cmb_point.setCurrentIndex(i)
                break

    def _delete_point(self) -> None:
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            return
        if pname in CORE_POINTS:
            ans = QMessageBox.warning(
                self,
                "删除流程点位",
                f"「{pname}」是流程常用点，删除可能导致异常。\n确定删除 points.{key}.{pname}？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        else:
            ans = QMessageBox.question(
                self, "删除", f"删除 points.{key}.{pname} ?", QMessageBox.Yes | QMessageBox.No
            )
            if ans != QMessageBox.Yes:
                return
        before = self.ctx.cfg["points"][key][pname]
        self.ctx.point_undo.push_delete(key, pname, before)
        del self.ctx.cfg["points"][key][pname]
        save_config(self.ctx.cfg)
        self._reload_points()
        self._refresh_undo_label()

    def _add_via_point(self) -> None:
        """新增/覆盖过渡点：名称可用中文；同名则更新同一点。"""
        key = self._robot_key()
        prefix = "【上料R1】" if key == "robot1" else "【下料R2】"
        raw, ok = QInputDialog.getText(
            self,
            "新增过渡点",
            "点名称（可用中文；同名则覆盖更新该点）:",
            text=f"{prefix}过渡",
        )
        if not ok:
            return
        name = normalize_via_name(raw)
        err = validate_via_name(name)
        if err:
            QMessageBox.warning(self, "无效", err)
            return
        pts = self.ctx.cfg.setdefault("points", {}).setdefault(key, {})
        pname, is_update = resolve_via_point_key(pts, name)
        if pname in CORE_POINTS:
            QMessageBox.warning(
                self, "禁止", f"「{name}」对应流程核心点 [{pname}]，不能当过渡点覆盖"
            )
            return
        if is_update:
            ans = QMessageBox.question(
                self,
                "覆盖同名点",
                f"已存在「{name}」[{pname}]，将用当前位姿覆盖更新。继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans != QMessageBox.Yes:
                return
        robot = self._robot()
        try:
            pose = numeric_pose(robot.get_actual_tcp_pose())
            joints = robot.get_actual_joint_pos()
        except Exception as e:
            QMessageBox.warning(self, "读位失败", f"请先连接机器人再采过渡点：\n{e}")
            return
        old = pts.get(pname) if isinstance(pts.get(pname), dict) else None
        entry = {
            "name": name,
            "blend": bool((old or {}).get("blend", True)) if is_update else True,
            **pose,
            **joints_to_dict(joints),
        }
        # 保留已有本点平滑参数
        if is_update and old:
            for k in ("blend_t_ms", "blend_r_mm"):
                if k in old:
                    entry[k] = old[k]
        if is_update:
            self.ctx.point_undo.push_update(key, pname, old, entry)
        else:
            self.ctx.point_undo.push_add(key, pname, entry)
        pts[pname] = entry
        save_config(self.ctx.cfg)
        self._reload_points()
        self._refresh_undo_label()
        for i in range(self.cmb_point.count()):
            if self.cmb_point.itemData(i) == pname:
                self.cmb_point.setCurrentIndex(i)
                break
        self._joints_valid = True
        self.chk_blend.setChecked(bool(entry.get("blend", True)))
        QMessageBox.information(
            self,
            "已更新" if is_update else "已添加过渡点",
            f"{'已覆盖' if is_update else '已新建'}「{name}」\n"
            f"points.{key}.{pname}\n已写入当前 TCP + 关节角。\n可点「撤回路点操作」恢复。",
        )

    def _delete_via_point(self) -> None:
        """删除当前选中点（过渡点/自定义点优先；核心点二次确认）。"""
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            QMessageBox.warning(self, "未选", "请先在「本臂点位」下拉框选中要删的点")
            return
        pose = self.ctx.cfg.get("points", {}).get(key, {}).get(pname)
        if not isinstance(pose, dict):
            QMessageBox.warning(self, "不存在", f"points.{key}.{pname} 不存在")
            return
        zh = point_display_name(pname, pose, robot_key=key)
        is_core = pname in CORE_POINTS
        is_via = (
            pname.startswith("via_")
            or ("过渡" in zh)
            or (pname not in CORE_POINTS and pname == zh)
        )
        title = "删除流程核心点" if is_core else ("删除过渡点" if is_via else "删除点位")
        tip = (
            f"「{zh}」[{pname}] 是流程常用点，删除可能导致自动异常。\n确定删除？"
            if is_core
            else f"确定删除「{zh}」\npoints.{key}.{pname}？"
        )
        box = QMessageBox.warning if is_core else QMessageBox.question
        ans = box(
            self,
            title,
            tip,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.ctx.point_undo.push_delete(key, pname, pose)
        del self.ctx.cfg["points"][key][pname]
        save_config(self.ctx.cfg)
        self._reload_points()
        self._refresh_undo_label()
        self.lbl_dbg.setText(f"已删除 points.{key}.{pname}")
        QMessageBox.information(
            self, "已删除", f"已从配置移除 points.{key}.{pname}\n可点「撤回路点操作」恢复。"
        )

    def _refresh_undo_label(self) -> None:
        n = self.ctx.point_undo.depth()
        self.lbl_undo.setText(f"可撤回步数: {n}" if n else "撤回：无")
        self.btn_point_undo.setEnabled(n > 0)

    def _undo_point_op(self) -> None:
        if not self.ctx.point_undo.can_undo():
            QMessageBox.information(self, "撤回", "没有可撤回的路点操作")
            return
        try:
            msg = self.ctx.point_undo.undo(self.ctx.cfg)
        except ValueError as e:
            QMessageBox.warning(self, "撤回失败", str(e))
            return
        save_config(self.ctx.cfg)
        self._reload_points()
        self._refresh_undo_label()
        self.lbl_dbg.setText(msg)
        QMessageBox.information(self, "已撤回", msg)

    def _read_tcp(self) -> None:
        robot = self._robot()
        try:
            pose = robot.get_actual_tcp_pose()
        except Exception as e:
            QMessageBox.warning(self, "读TCP失败", str(e))
            return
        for k, sp in self.spins.items():
            sp.setValue(float(pose.get(k, 0)))
        try:
            joints = robot.get_actual_joint_pos()
            for i, k in enumerate(JOINT_AXES):
                self.joint_spins[k].setValue(float(joints[i]))
            self._joints_valid = True
            self.lbl_joint_hint.setText("关节角（已读入，请点「保存当前点」）")
            self.lbl_joint_hint.setStyleSheet("color:#1a7a37;font-weight:bold;")
            self.lbl_dbg.setText(
                "已读入当前臂 TCP + 关节角（未保存，确认后点「保存当前点」）"
            )
        except Exception as e:
            self._joints_valid = False
            self.lbl_dbg.setText(f"已读入 TCP；读关节失败: {e}")
            QMessageBox.warning(
                self, "读关节失败", f"TCP 已读入，关节未读到：\n{e}\nMoveJ 仍可能走怪路径。"
            )

    def _confirm_move(self, title: str, detail: str) -> bool:
        if self.ctx.machine.state.name == "RUNNING":
            QMessageBox.warning(self, "禁止", "自动运行中禁止点位调试，请先停止/暂停。")
            return False
        ans = QMessageBox.question(
            self,
            title,
            detail + "\n\n请确认周边安全、速度合适。继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return ans == QMessageBox.Yes

    def _move_to_selected(self, *, linear: bool) -> None:
        if self._dbg_busy:
            QMessageBox.information(self, "忙", "上一段调试未完成，可点「停止运动」。")
            return
        key = self._robot_key()
        pname = self._current_point_key()
        if not pname:
            return
        # 偏移点不是绝对坐标：改走「基点+偏移」
        if self._is_offset_key(pname):
            self._reload_offset_combos(prefer_offset=pname)
            ans = QMessageBox.question(
                self,
                "这是偏移量",
                f"「{pname}」是相对偏移（如 Z+80），不是绝对点位。\n"
                "将按下方「偏移量试跑」：基点 + 当前偏移 运动。\n继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans != QMessageBox.Yes:
                return
            self._move_offset_target(linear=linear)
            return
        entry = self.ctx.cfg["points"][key][pname]
        entry["name"] = self.ed_name.text().strip() or entry.get("name", pname)
        for k, sp in self.spins.items():
            entry[k] = float(sp.value())
        tag = self.ctx.named_point_tag(key, pname)
        how = "MoveL" if linear else "MoveJ"
        arm = "上料R1" if key == "robot1" else "下料R2"
        joints = extract_joints(entry) if not linear else None
        if not linear and not joints:
            ans = QMessageBox.warning(
                self,
                "无示教关节",
                f"「{tag}」未保存 j1..j6。\n"
                "MoveJ 将回退 MoveCart，路径可能与示教不同。\n仍要继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        if not self._confirm_move(f"{how} 单点", f"臂={arm}\n目标={tag}"):
            return
        blend_kw = self._ui_blend_kwargs()
        robot = self._robot()
        try:
            if linear:
                robot.move_l(
                    self.ctx.pose(key, pname), label=tag, **blend_kw
                )
            else:
                # 用界面当前值（含未保存的关节）
                pose = self._spin_pose()
                j = (
                    [float(self.joint_spins[k].value()) for k in JOINT_AXES]
                    if self._joints_valid
                    else extract_joints(entry)
                )
                robot.move_j(pose, joints=j, label=tag, **blend_kw)
        except Exception as e:
            QMessageBox.critical(self, "发令失败", str(e))
            return
        self._dbg_busy = True
        self._dbg_linear = linear
        self.lbl_dbg.setText(f"调试中[{arm}]: {robot.path_hint() or (how + ' → ' + tag)}")

    def _move_path(self, *, linear: bool) -> None:
        if self._dbg_busy:
            QMessageBox.information(self, "忙", "上一段调试未完成。")
            return
        key = self._robot_key()
        a = self._combo_point_key(self.cmb_from)
        b = self._combo_point_key(self.cmb_to)
        if not a or not b:
            return
        if a == b:
            QMessageBox.warning(self, "无效", "起点和终点不能相同")
            return
        if self._is_offset_key(a) or self._is_offset_key(b):
            QMessageBox.warning(
                self,
                "偏移点不能直接路径试跑",
                "起点/终点若是 *_offset，请改用「偏移量试跑：基点+偏移」。",
            )
            return
        ta = self.ctx.named_point_tag(key, a)
        tb = self.ctx.named_point_tag(key, b)
        how = "MoveL" if linear else "MoveJ"
        arm = "上料R1" if key == "robot1" else "下料R2"
        if not linear:
            missing = [
                n
                for n in (a, b)
                if not has_taught_joints(self.ctx.cfg["points"][key].get(n))
            ]
            if missing:
                ans = QMessageBox.warning(
                    self,
                    "无示教关节",
                    f"点 {missing} 无 j1..j6，MoveJ 可能走怪路径。仍继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    return
        if not self._confirm_move(f"{how} 路径试跑", f"臂={arm}\n从 {ta}\n到 {tb}"):
            return
        robot = self._robot()
        try:
            raw_a = self.ctx.cfg["points"][key][a]
            robot.move_j(
                numeric_pose(raw_a),
                joints=extract_joints(raw_a),
                label=ta,
                from_label="调试当前位置",
                **self.ctx.point_blend_kwargs(key, a),
            )
            self._path_pending_to = (key, b, tb, linear)
        except Exception as e:
            QMessageBox.critical(self, "发令失败", str(e))
            return
        self._dbg_busy = True
        self._dbg_linear = linear
        self.lbl_dbg.setText(f"[{arm}] 第1段→{ta}，到位后→{tb}")

    def _stop_move(self) -> None:
        self._robot().halt_motion()
        self._dbg_busy = False
        self._path_pending_to = None
        self.lbl_dbg.setText("已停止调试运动")

    def refresh_fast(self) -> None:
        if not self.isVisible():
            return
        if not self._dbg_busy:
            return
        robot = self._robot()
        try:
            if robot.poll_move_done():
                pending = getattr(self, "_path_pending_to", None)
                if pending:
                    key, b, tb, linear = pending
                    self._path_pending_to = None
                    ta = robot._last_arrived_label
                    try:
                        raw_b = self.ctx.cfg["points"][key][b]
                        bkw = self.ctx.point_blend_kwargs(key, b)
                        if linear:
                            robot.move_l(
                                numeric_pose(raw_b),
                                label=tb,
                                from_label=ta,
                                **bkw,
                            )
                        else:
                            robot.move_j(
                                numeric_pose(raw_b),
                                joints=extract_joints(raw_b),
                                label=tb,
                                from_label=ta,
                                **bkw,
                            )
                    except Exception as e:
                        self._dbg_busy = False
                        self.lbl_dbg.setText(f"第2段发令失败: {e}")
                        QMessageBox.critical(self, "路径试跑失败", str(e))
                        return
                    self.lbl_dbg.setText(f"路径试跑第2段: {robot.path_hint()}")
                    return
                self._dbg_busy = False
                self.lbl_dbg.setText(
                    f"调试到位: {robot._last_arrived_label}\n可用「读入当前TCP」核对。"
                )
        except Exception as e:
            self._dbg_busy = False
            self._path_pending_to = None
            self.lbl_dbg.setText(f"调试失败: {e}")
            QMessageBox.critical(self, "点位调试报警", str(e))

    def refresh(self) -> None:
        if not self.isVisible():
            return
        self.refresh_fast()
        self._refresh_undo_label()
        robot = self._robot()
        arm = "上料R1" if self._robot_key() == "robot1" else "下料R2"
        try:
            pose = numeric_pose(robot.current_pose)
            self.lbl_pose.setText(
                f"{arm} TCP: "
                + ", ".join(f"{k}={pose[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz"))
                + f"  | 最近到达: {getattr(robot, '_last_arrived_label', '-')}"
            )
        except Exception:
            pass
        self._refresh_offset_preview()
