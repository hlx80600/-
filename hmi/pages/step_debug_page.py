"""工位调试：步表、跳步、重发、过渡点试跑。"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from core.machine_state import RunMode
from devices.pose_utils import (
    apply_offset,
    extract_joints,
    joints_to_dict,
    normalize_via_name,
    numeric_pose,
    point_display_name,
    resolve_via_point_key,
    validate_via_name,
)
from hmi.pages.points_page import CORE_POINTS, NoWheelComboBox
from hmi.style import apply_page_chrome, style_many
from stations.step_catalog import (
    AUTO_TITLES,
    auto_title,
    find_step,
    steps_for,
)


class StepDebugPage(QWidget):
    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._dbg_busy = False
        self._filling = False

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        self.cmb_station = NoWheelComboBox()
        for s in coord.stations:
            self.cmb_station.addItem(s.name, s.no)
        self.cmb_station.currentIndexChanged.connect(self._on_station_changed)
        self.cmb_auto = NoWheelComboBox()
        self.cmb_auto.currentIndexChanged.connect(self._reload_step_table)
        row.addWidget(QLabel("Station"))
        row.addWidget(self.cmb_station)
        row.addWidget(QLabel("Auto_A"))
        row.addWidget(self.cmb_auto, stretch=1)
        root.addLayout(row)

        self.chk_bypass = QCheckBox("调试旁路（忽略进入互锁，可单独武装 Auto）")
        self.chk_bypass.stateChanged.connect(
            lambda st: self.ctx.machine.set_debug_bypass(st != 0)
        )
        root.addWidget(self.chk_bypass)

        self.lbl_live = QLabel("-")
        self.lbl_live.setWordWrap(True)
        self.lbl_live.setStyleSheet("padding:6px;background:#f5f5f5;font-weight:bold;")
        root.addWidget(self.lbl_live)

        # —— 步表 ——
        box_tbl = QGroupBox("Auto 步表（黄底=当前步；双击某行=跳到该步）")
        vt = QVBoxLayout(box_tbl)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["步号", "类型", "标题", "关联点", "说明"])
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.doubleClicked.connect(self._on_row_double)
        self.tbl.itemSelectionChanged.connect(self._on_row_select)
        vt.addWidget(self.tbl)
        root.addWidget(box_tbl, stretch=1)

        self.lbl_sel = QLabel("选中步: -")
        self.lbl_sel.setWordWrap(True)
        root.addWidget(self.lbl_sel)

        # —— 程序控制 ——
        box_ctrl = QGroupBox("程序控制")
        bc = QVBoxLayout(box_ctrl)
        r1 = QHBoxLayout()
        self.btn_mode = QPushButton("切到单步模式")
        self.btn_arm = QPushButton("武装 Auto（从头步10）")
        self.btn_arm_sel = QPushButton("武装到选中步")
        self.btn_run = QPushButton("执行当前步/下一步")
        self.btn_abort = QPushButton("中止本站 Auto")
        self.btn_mode.clicked.connect(self._set_single)
        self.btn_arm.clicked.connect(self._arm_start)
        self.btn_arm_sel.clicked.connect(self._arm_selected)
        self.btn_run.clicked.connect(self._pulse)
        self.btn_abort.clicked.connect(self._abort)
        style_many(
            [
                (self.btn_mode, "accent"),
                (self.btn_arm, "primary"),
                (self.btn_arm_sel, "primary"),
                (self.btn_run, "success"),
                (self.btn_abort, "danger"),
            ]
        )
        for b in (self.btn_mode, self.btn_arm, self.btn_arm_sel, self.btn_run, self.btn_abort):
            r1.addWidget(b)
        bc.addLayout(r1)

        r2 = QHBoxLayout()
        self.btn_jump = QPushButton("跳到选中步（清锁存）")
        self.btn_skip = QPushButton("强制跳过当前步")
        self.btn_refire = QPushButton("重发当前步（再Move）")
        self.btn_init = QPushButton("单步初始化")
        self.btn_jump.clicked.connect(self._jump_selected)
        self.btn_skip.clicked.connect(self._skip)
        self.btn_refire.clicked.connect(self._refire)
        self.btn_init.clicked.connect(self._init_step)
        style_many(
            [
                (self.btn_jump, "warn"),
                (self.btn_skip, "warn"),
                (self.btn_refire, "motion"),
                (self.btn_init, "neutral"),
            ]
        )
        for b in (self.btn_jump, self.btn_skip, self.btn_refire, self.btn_init):
            r2.addWidget(b)
        bc.addLayout(r2)
        root.addWidget(box_ctrl)

        # —— 路点 / 过渡点 ——
        box_pt = QGroupBox("路点 / 过渡点联调（现场加中间点）")
        bp = QVBoxLayout(box_pt)
        pr = QHBoxLayout()
        self.cmb_point = NoWheelComboBox()
        pr.addWidget(QLabel("关联/目标点"))
        pr.addWidget(self.cmb_point, stretch=1)
        bp.addLayout(pr)
        pr2 = QHBoxLayout()
        self.btn_move_j = QPushButton("MoveJ→选中点")
        self.btn_move_l = QPushButton("MoveL→选中点")
        self.btn_move_off = QPushButton("MoveL→点+上方偏移")
        self.btn_add_via = QPushButton("新增过渡点…")
        self.btn_del_via = QPushButton("删除过渡点…")
        self.btn_point_undo = QPushButton("撤回路点操作")
        self.btn_stop = QPushButton("停止运动")
        self.btn_move_j.clicked.connect(lambda: self._move_point(linear=False))
        self.btn_move_l.clicked.connect(lambda: self._move_point(linear=True))
        self.btn_move_off.clicked.connect(self._move_with_above_offset)
        self.btn_add_via.clicked.connect(self._add_via_point)
        self.btn_del_via.clicked.connect(self._delete_via_point)
        self.btn_point_undo.clicked.connect(self._undo_point_op)
        self.btn_stop.clicked.connect(self._stop_move)
        style_many(
            [
                (self.btn_move_j, "motion"),
                (self.btn_move_l, "motion"),
                (self.btn_move_off, "motion"),
                (self.btn_add_via, "success"),
                (self.btn_del_via, "danger"),
                (self.btn_point_undo, "primary"),
                (self.btn_stop, "danger"),
            ]
        )
        for b in (
            self.btn_move_j,
            self.btn_move_l,
            self.btn_move_off,
            self.btn_add_via,
            self.btn_del_via,
            self.btn_point_undo,
            self.btn_stop,
        ):
            pr2.addWidget(b)
        bp.addLayout(pr2)
        self.lbl_pt = QLabel("-")
        self.lbl_pt.setWordWrap(True)
        self.lbl_pt.setStyleSheet("color:#1a5276;font-weight:bold;")
        bp.addWidget(self.lbl_pt)
        self.lbl_undo = QLabel("撤回：无")
        self.lbl_undo.setStyleSheet(
            "color:#1a5276;font-weight:bold;padding:4px 8px;"
            "background:#eaf2f8;border-radius:4px;"
        )
        bp.addWidget(self.lbl_undo)
        root.addWidget(box_pt)
        self._refresh_undo_label()

        # —— 各站总览 ——
        box = QGroupBox("各站实时状态")
        grid = QVBoxLayout(box)
        self.labels = {}
        for s in coord.stations:
            lb = QLabel(s.name)
            self.labels[s.name] = lb
            grid.addWidget(lb)
        root.addWidget(box)

        # 手动点动
        man = QGroupBox("手动点动")
        ml = QGridLayout(man)
        b1o = QPushButton("夹爪1张开")
        b1c = QPushButton("夹爪1夹紧")
        b2o = QPushButton("夹爪2张开")
        b2c = QPushButton("夹爪2夹紧")
        b3 = QPushButton("R1回Home")
        b4 = QPushButton("R2回Home")
        b1o.clicked.connect(lambda: self._grip_set(1, True))
        b1c.clicked.connect(lambda: self._grip_set(1, False))
        b2o.clicked.connect(lambda: self._grip_set(2, True))
        b2c.clicked.connect(lambda: self._grip_set(2, False))
        b3.clicked.connect(lambda: self._quick_home("robot1"))
        b4.clicked.connect(lambda: self._quick_home("robot2"))
        style_many(
            [
                (b1o, "success"),
                (b1c, "warn"),
                (b2o, "success"),
                (b2c, "warn"),
                (b3, "motion"),
                (b4, "motion"),
            ]
        )
        ml.addWidget(b1o, 0, 0)
        ml.addWidget(b1c, 0, 1)
        ml.addWidget(b2o, 0, 2)
        ml.addWidget(b2c, 0, 3)
        ml.addWidget(b3, 1, 0, 1, 2)
        ml.addWidget(b4, 1, 2, 1, 2)
        root.addWidget(man)

        apply_page_chrome(self, accent="#1a5276")
        self._on_station_changed()

    # ---------- selection helpers ----------
    def _station_no(self) -> int:
        data = self.cmb_station.currentData()
        return int(data) if data is not None else 1

    def _current_station(self):
        name = self.cmb_station.currentText()
        return self.ctx.stations.get(name)

    def _auto_key(self) -> int:
        data = self.cmb_auto.currentData()
        if data is not None:
            return int(data)
        # fallback parse Auto_A10
        t = self.cmb_auto.currentText()
        m = re.search(r"(\d+)", t or "")
        return int(m.group(1)) if m else 10

    def _selected_step_no(self) -> int | None:
        rows = self.tbl.selectionModel().selectedRows() if self.tbl.selectionModel() else []
        if not rows:
            return None
        item = self.tbl.item(rows[0].row(), 0)
        return int(item.text()) if item else None

    def _robot_key_for_station(self) -> str:
        no = self._station_no()
        if no in (1, 2, 3):
            return "robot1"
        if no in (4, 5, 6):
            return "robot2" if no == 5 else "robot1"
        return "robot1"

    def _on_station_changed(self) -> None:
        self.cmb_auto.blockSignals(True)
        self.cmb_auto.clear()
        no = self._station_no()
        titles = AUTO_TITLES.get(no, {})
        st = self._current_station()
        if st:
            for name, key in st.autos.items():
                title = titles.get(key, name)
                self.cmb_auto.addItem(f"{name}  {title}", key)
        self.cmb_auto.blockSignals(False)
        self._reload_step_table()
        self._reload_point_combo()

    def _reload_step_table(self) -> None:
        self._filling = True
        steps = steps_for(self._station_no(), self._auto_key())
        self.tbl.setRowCount(0)
        for s in steps:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            pts = ",".join(s.get("points") or []) or "-"
            vals = [
                str(s["step"]),
                str(s.get("kind", "")),
                str(s.get("title", "")),
                pts,
                str(s.get("detail", "")),
            ]
            for c, v in enumerate(vals):
                self.tbl.setItem(r, c, QTableWidgetItem(v))
        self.tbl.resizeColumnsToContents()
        self._filling = False
        self._highlight_current()
        self._reload_point_combo_from_step()

    def _reload_point_combo(self) -> None:
        rk = self._robot_key_for_station()
        if self._station_no() == 5:
            rk = "robot2"
        elif self._station_no() in (1, 2):
            rk = "robot1"
        prefer = str(self.cmb_point.currentData() or "")
        self.cmb_point.blockSignals(True)
        self.cmb_point.clear()
        pts = self.ctx.cfg.get("points", {}).get(rk, {}) or {}
        for pname, pose in pts.items():
            if not isinstance(pose, dict):
                continue
            zh = point_display_name(pname, pose, robot_key=rk)
            self.cmb_point.addItem(f"{zh} [{pname}]", pname)
        if prefer:
            for i in range(self.cmb_point.count()):
                if self.cmb_point.itemData(i) == prefer:
                    self.cmb_point.setCurrentIndex(i)
                    break
        self.cmb_point.blockSignals(False)

    def _reload_point_combo_from_step(self) -> None:
        meta = find_step(self._station_no(), self._auto_key(), self._selected_step_no() or -1)
        act = None
        st = self._current_station()
        if st:
            act = st.active_auto_step()
        if meta is None and act and act[0] == self._auto_key():
            meta = find_step(self._station_no(), act[0], act[1])
        self._reload_point_combo()
        if meta and meta.get("points"):
            want = meta["points"][0]
            for i in range(self.cmb_point.count()):
                if self.cmb_point.itemData(i) == want:
                    self.cmb_point.setCurrentIndex(i)
                    break

    def _highlight_current(self) -> None:
        st = self._current_station()
        cur_step = None
        if st:
            act = st.active_auto_step()
            if act and act[0] == self._auto_key():
                cur_step = act[1]
        yellow = QBrush(QColor("#fff3cd"))
        white = QBrush(QColor("#ffffff"))
        for r in range(self.tbl.rowCount()):
            item0 = self.tbl.item(r, 0)
            if not item0:
                continue
            brush = yellow if cur_step is not None and int(item0.text()) == int(cur_step) else white
            for c in range(self.tbl.columnCount()):
                it = self.tbl.item(r, c)
                if it:
                    it.setBackground(brush)

    def _on_row_select(self) -> None:
        if self._filling:
            return
        sn = self._selected_step_no()
        meta = find_step(self._station_no(), self._auto_key(), sn or -1)
        if meta:
            self.lbl_sel.setText(
                f"选中步 {meta['step']}: [{meta.get('kind')}] {meta.get('title')} — {meta.get('detail')} "
                f"点={meta.get('points')}"
            )
            self._reload_point_combo_from_step()
        else:
            self.lbl_sel.setText("选中步: -")

    def _on_row_double(self) -> None:
        self._jump_selected()

    # ---------- program control ----------
    def _set_single(self) -> None:
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        self.lbl_pt.setText("已切到单步模式。请先初始化并启动，再武装 Auto。")

    def _ensure_can_debug(self) -> bool:
        if self.ctx.machine.state.name == "ESTOP":
            QMessageBox.warning(self, "禁止", "急停中，请先复位急停并初始化。")
            return False
        return True

    def _arm_start(self) -> None:
        if not self._ensure_can_debug():
            return
        st = self._current_station()
        if not st:
            return
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        name = self.cmb_auto.currentText().split()[0]
        ok = st.arm_at_step(name, 10, force=True)
        QMessageBox.information(
            self,
            "武装",
            f"{'成功' if ok else '失败'}: {st.name} {name} 从步10开始\n"
            "请点「执行当前步/下一步」推进。",
        )

    def _arm_selected(self) -> None:
        if not self._ensure_can_debug():
            return
        sn = self._selected_step_no()
        if sn is None:
            QMessageBox.warning(self, "未选", "请先在步表中选一行")
            return
        st = self._current_station()
        if not st:
            return
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        name = self.cmb_auto.currentText().split()[0]
        ok = st.arm_at_step(name, sn, force=True)
        self.lbl_pt.setText(f"已武装到步 {sn}: {'OK' if ok else '失败'}")

    def _jump_selected(self) -> None:
        if not self._ensure_can_debug():
            return
        sn = self._selected_step_no()
        if sn is None:
            QMessageBox.warning(self, "未选", "请先选中步表中的一行")
            return
        st = self._current_station()
        if not st:
            return
        auto = self._auto_key()
        st.set_step(auto, sn)
        if self.ctx.machine.state.name in ("READY", "STOPPED", "PAUSED"):
            from core.machine_state import MachineState

            self.ctx.machine.set_state(MachineState.RUNNING)
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        self.lbl_pt.setText(f"已跳到 Auto_A[{auto}] 步{sn}（已清发令锁存，可点执行/重发）")
        self._highlight_current()

    def _skip(self) -> None:
        st = self._current_station()
        if not st or not st.active_auto_step():
            QMessageBox.warning(self, "无", "当前站没有正在运行的 Auto")
            return
        ans = QMessageBox.question(
            self,
            "强制跳过",
            "将不执行本步动作，直接进入下一步（可能不安全）。继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        st.skip_to_next_step()
        self._highlight_current()

    def _refire(self) -> None:
        st = self._current_station()
        if not st:
            return
        if st.refire_current_step():
            self.lbl_pt.setText("已清锁存，下一扫描周期将重新发本步命令；可再点「执行」。")
            st.operator_run_current()
        else:
            QMessageBox.warning(self, "无", "当前无活动步")

    def _pulse(self) -> None:
        st = self._current_station()
        if st:
            st.operator_run_current()

    def _abort(self) -> None:
        st = self._current_station()
        if st:
            st.abort()
            self.lbl_pt.setText(f"{st.name} Auto 已中止")
            self._highlight_current()

    def _init_step(self) -> None:
        self.ctx.machine.set_mode(RunMode.SINGLE_STEP)
        if not self.coord.init_seq.busy:
            err = self.coord.cmd_init()
            if err:
                QMessageBox.warning(self, "无法初始化", err)
                return
        # 初始化 CASE 认 InitStepPulse；顺带保留旧 token 兼容
        self.ctx.gvl.Main.InitStepPulse = True
        self.ctx.request_step_go()

    # ---------- points ----------
    def _point_robot_key(self) -> str:
        no = self._station_no()
        return "robot2" if no == 5 else "robot1"

    def _move_point(self, *, linear: bool) -> None:
        if self._dbg_busy:
            QMessageBox.information(self, "忙", "上一段运动未完成")
            return
        if self.ctx.machine.state.name == "RUNNING" and self.ctx.machine.mode == RunMode.AUTO:
            QMessageBox.warning(self, "禁止", "全自动运行中请先停止/切单步再手动走点")
            return
        pname = str(self.cmb_point.currentData() or "")
        if not pname:
            return
        if "offset" in pname:
            QMessageBox.information(
                self,
                "偏移点",
                "选中的是偏移量。请用「MoveL→点+上方偏移」或到点位页「偏移量试跑」。",
            )
            return
        rk = self._point_robot_key()
        raw = self.ctx.cfg["points"][rk][pname]
        tag = self.ctx.named_point_tag(rk, pname)
        how = "MoveL" if linear else "MoveJ"
        ans = QMessageBox.question(
            self, how, f"臂={rk}\n目标={tag}\n继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return
        robot = self.ctx.robot1 if rk == "robot1" else self.ctx.robot2
        try:
            pose = numeric_pose(raw)
            want = self.ctx.point_blend_kwargs(rk, pname)
            if linear:
                robot.move_l(pose, label=tag, joints=extract_joints(raw), **want)
            else:
                robot.move_j(
                    pose, joints=extract_joints(raw), label=tag, **want
                )
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
            return
        self._dbg_busy = True
        self.lbl_pt.setText(f"运动中: {robot.path_hint()}")

    def _move_with_above_offset(self) -> None:
        """基点=下拉选中点，偏移=同臂默认 *_above_offset。"""
        pname = str(self.cmb_point.currentData() or "")
        if not pname or "offset" in pname:
            QMessageBox.warning(self, "选基点", "请选绝对点（非 offset）作为基点")
            return
        rk = self._point_robot_key()
        # 按点名猜偏移
        off_name = None
        if "place" in pname or pname == "place_slot":
            off_name = "place_above_offset" if rk == "robot1" else "belt_place_above_offset"
        elif "slot_pick" in pname:
            off_name = "slot_pick_above_offset"
        elif "belt_place" in pname:
            off_name = "belt_place_above_offset"
        elif "pick" in pname or pname == "home":
            off_name = "pick_above_offset" if rk == "robot1" else "slot_pick_above_offset"
        else:
            # 本站默认
            off_name = "pick_above_offset" if rk == "robot1" else "slot_pick_above_offset"
        pts = self.ctx.cfg.get("points", {}).get(rk, {})
        if off_name not in pts:
            QMessageBox.warning(self, "无偏移", f"找不到 {rk}.{off_name}")
            return
        base = numeric_pose(pts[pname])
        off = numeric_pose(pts[off_name])
        target = apply_offset(base, off)
        label = f"{pname}+{off_name}"
        ans = QMessageBox.question(
            self,
            "MoveL 上方",
            f"{label}\n" + ", ".join(f"{k}={target[k]:.1f}" for k in ("x", "y", "z")),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        robot = self.ctx.robot1 if rk == "robot1" else self.ctx.robot2
        try:
            robot.move_l(
                target,
                label=label,
                **self.ctx.point_blend_kwargs(rk, off_name),
            )
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
            return
        self._dbg_busy = True
        self.lbl_pt.setText(f"运动中: {label}")

    def _add_via_point(self) -> None:
        """新增/覆盖过渡点：名称可用中文；同名则更新同一点。"""
        rk = self._point_robot_key()
        prefix = "【上料R1】" if rk == "robot1" else "【下料R2】"
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
        pts = self.ctx.cfg.setdefault("points", {}).setdefault(rk, {})
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
        robot = self.ctx.robot1 if rk == "robot1" else self.ctx.robot2
        try:
            pose = numeric_pose(robot.get_actual_tcp_pose())
            joints = robot.get_actual_joint_pos()
        except Exception as e:
            QMessageBox.warning(self, "读位失败", f"请先连上机器人再采点:\n{e}")
            return
        old = pts.get(pname) if isinstance(pts.get(pname), dict) else None
        entry = {
            "name": name,
            "blend": bool((old or {}).get("blend", True)) if is_update else True,
            **pose,
            **joints_to_dict(joints),
        }
        if is_update and old:
            for k in ("blend_t_ms", "blend_r_mm"):
                if k in old:
                    entry[k] = old[k]
        if is_update:
            self.ctx.point_undo.push_update(rk, pname, old, entry)
        else:
            self.ctx.point_undo.push_add(rk, pname, entry)
        pts[pname] = entry
        save_config(self.ctx.cfg)
        self._reload_point_combo()
        self._refresh_undo_label()
        for i in range(self.cmb_point.count()):
            if self.cmb_point.itemData(i) == pname:
                self.cmb_point.setCurrentIndex(i)
                break
        QMessageBox.information(
            self,
            "已更新" if is_update else "已添加",
            f"{'已覆盖' if is_update else '已新建'}「{name}」\n"
            f"points.{rk}.{pname}\n已写入当前 TCP+关节。\n可点「撤回路点操作」恢复。",
        )

    def _delete_via_point(self) -> None:
        """删除下拉框当前选中的点位（流程核心点需二次确认）。"""
        rk = self._point_robot_key()
        pname = str(self.cmb_point.currentData() or "")
        if not pname:
            QMessageBox.warning(self, "未选", "请先在「关联/目标点」中选中要删的点")
            return
        pts = self.ctx.cfg.get("points", {}).get(rk, {})
        if pname not in pts:
            QMessageBox.warning(self, "不存在", f"points.{rk}.{pname} 不存在")
            return
        zh = point_display_name(pname, pts.get(pname), robot_key=rk)
        if pname in CORE_POINTS:
            ans = QMessageBox.warning(
                self,
                "删除流程点位",
                f"「{zh}」[{pname}] 是流程常用点，删除可能导致自动异常。\n"
                f"确定删除 points.{rk}.{pname}？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        else:
            ans = QMessageBox.question(
                self,
                "删除过渡点",
                f"确定删除「{zh}」\npoints.{rk}.{pname} ？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        if ans != QMessageBox.Yes:
            return
        before = pts[pname]
        self.ctx.point_undo.push_delete(rk, pname, before)
        del self.ctx.cfg["points"][rk][pname]
        save_config(self.ctx.cfg)
        self._reload_point_combo()
        self._refresh_undo_label()
        self.lbl_pt.setText(f"已删除 points.{rk}.{pname}")
        QMessageBox.information(
            self, "已删除", f"已从配置移除 points.{rk}.{pname}\n可点「撤回路点操作」恢复。"
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
        self._reload_point_combo()
        self._refresh_undo_label()
        self.lbl_pt.setText(msg)
        QMessageBox.information(self, "已撤回", msg)

    def _stop_move(self) -> None:
        self.ctx.robot1.halt_motion()
        self.ctx.robot2.halt_motion()
        self._dbg_busy = False
        self.lbl_pt.setText("已停止运动")

    def _quick_home(self, rk: str) -> None:
        robot = self.ctx.robot1 if rk == "robot1" else self.ctx.robot2
        raw = self.ctx.cfg["points"][rk]["home"]
        try:
            robot.move_j(
                numeric_pose(raw),
                joints=extract_joints(raw),
                label=self.ctx.named_point_tag(rk, "home"),
            )
            self._dbg_busy = True
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    def _toggle_g1(self) -> None:
        self._grip_set(1, open_=self.ctx.gripper1.closed)

    def _toggle_g2(self) -> None:
        self._grip_set(2, open_=self.ctx.gripper2.closed)

    def _grip_set(self, which: int, open_: bool) -> None:
        g = self.ctx.gripper1 if which == 1 else self.ctx.gripper2
        rk = "robot1" if which == 1 else "robot2"
        try:
            ok = g.open_claw() if open_ else g.close_claw()
            if open_:
                self.ctx.set_robot_holding_shoe(rk, False)
            if not ok:
                QMessageBox.warning(
                    self,
                    "夹爪",
                    f"{'张开' if open_ else '夹紧'}失败: {getattr(g, 'last_error', '') or '反馈超时'}",
                )
        except Exception as e:
            QMessageBox.critical(self, "夹爪", str(e))

    def refresh_fast(self) -> None:
        if not self.isVisible():
            return
        self._highlight_current()
        if self._dbg_busy:
            for robot in (self.ctx.robot1, self.ctx.robot2):
                try:
                    if robot.poll_move_done():
                        self._dbg_busy = False
                        self.lbl_pt.setText(f"到位: {robot._last_arrived_label}")
                        break
                except Exception as e:
                    self._dbg_busy = False
                    self.lbl_pt.setText(f"运动异常: {e}")
                    break

    def refresh(self) -> None:
        if not self.isVisible():
            return
        self.refresh_fast()
        self._refresh_undo_label()
        allow = bool(self.ctx.cfg.get("system", {}).get("allow_debug_bypass", True))
        self.chk_bypass.setEnabled(allow)
        st = self._current_station()
        act = st.active_auto_step() if st else None
        meta = None
        if act:
            meta = find_step(st.no, act[0], act[1])
        title = auto_title(self._station_no(), self._auto_key())
        live = (
            f"模式={self.ctx.machine.mode.name} 状态={self.ctx.machine.state.name} "
            f"旁路={self.ctx.machine.debug_bypass} | 选中程序={title}"
        )
        if act and meta:
            live += (
                f" | 运行中: Auto_A[{act[0]}] 步{act[1]} "
                f"[{meta.get('kind')}] {meta.get('title')} 点={meta.get('points')}"
            )
        elif act:
            live += f" | 运行中: Auto_A[{act[0]}] 步{act[1]}"
        else:
            live += " | 本站无活动 Auto"
        self.lbl_live.setText(live)

        for s in self.coord.stations:
            a = s.active_auto_step()
            extra = ""
            if a:
                m = find_step(s.no, a[0], a[1])
                if m:
                    extra = f" | {m.get('title')}"
            self.labels[s.name].setText(
                f"{s.name}: {s.status_text()} | Busy={s.busy}{extra}"
            )
