"""屏蔽取料（cam1 Mock）皮带示教点：vision.belt_pick_mock。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config_loader import save_config
from core.coordinator import Coordinator
from devices.pose_utils import apply_offset, numeric_pose
from hmi.pages.points_page import NoWheelComboBox, NoWheelSpinBox
from hmi.style import apply_page_chrome, style_many


class ShieldPickPage(QWidget):
    """单独编辑屏蔽相机后视觉给出的取料示教点（左右鞋 XYRz + 公共 Z/Rx/Ry）。"""

    def __init__(self, coord: Coordinator):
        super().__init__()
        self.coord = coord
        self.ctx = coord.ctx
        self._dbg_busy = False
        self._loading = False

        root = QVBoxLayout(self)

        # —— 公共姿态（所有鞋共用 Z/Rx/Ry）——
        box_c = QGroupBox("公共姿态（所有屏蔽鞋共用，写入 belt_pick_mock）")
        fc = QFormLayout(box_c)
        self.sp_z = NoWheelSpinBox()
        self.sp_rx = NoWheelSpinBox()
        self.sp_ry = NoWheelSpinBox()
        for sp in (self.sp_z, self.sp_rx, self.sp_ry):
            sp.setRange(-9999, 9999)
            sp.setDecimals(3)
        fc.addRow("Z", self.sp_z)
        fc.addRow("Rx", self.sp_rx)
        fc.addRow("Ry", self.sp_ry)
        root.addWidget(box_c)

        # —— 鞋子列表 ——
        box_s = QGroupBox("屏蔽示教鞋位（shoes：每只鞋的 XY + Rz + 左右）")
        bs = QVBoxLayout(box_s)
        row = QHBoxLayout()
        self.cmb_shoe = NoWheelComboBox()
        self.cmb_shoe.currentIndexChanged.connect(self._load_shoe)
        row.addWidget(QLabel("当前鞋"))
        row.addWidget(self.cmb_shoe, stretch=1)
        btn_add = QPushButton("新增鞋位")
        btn_del = QPushButton("删除当前")
        btn_add.clicked.connect(self._add_shoe)
        btn_del.clicked.connect(self._del_shoe)
        style_many([(btn_add, "success"), (btn_del, "danger")])
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        bs.addLayout(row)

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.chk_left = QCheckBox("左鞋 is_left_shoe")
        self.sp_x = NoWheelSpinBox()
        self.sp_y = NoWheelSpinBox()
        self.sp_rz = NoWheelSpinBox()
        self.sp_toe_y = NoWheelSpinBox()
        for sp in (self.sp_x, self.sp_y, self.sp_rz, self.sp_toe_y):
            sp.setRange(-9999, 9999)
            sp.setDecimals(3)
        form.addRow("中文备注", self.ed_name)
        form.addRow("", self.chk_left)
        form.addRow("X", self.sp_x)
        form.addRow("Y", self.sp_y)
        form.addRow("Rz", self.sp_rz)
        form.addRow("鞋头Y偏移 mm", self.sp_toe_y)
        bs.addLayout(form)

        self.chk_alt = QCheckBox("左右鞋交替给出（推荐联调方向匹配）")
        self.chk_alt.setChecked(True)
        self.chk_alt.toggled.connect(self._on_alt_toggled)
        bs.addWidget(self.chk_alt)
        self.lbl_order = QLabel("模拟取鞋：-")
        self.lbl_order.setStyleSheet("color:#555;")
        bs.addWidget(self.lbl_order)
        root.addWidget(box_s)

        # —— 操作 ——
        edit_row = QHBoxLayout()
        btn_read = QPushButton("读入当前TCP（上料R1）")
        btn_save = QPushButton("保存到配置")
        btn_apply = QPushButton("写入当前PickPose")
        btn_read.clicked.connect(self._read_tcp)
        btn_save.clicked.connect(self._save)
        btn_apply.clicked.connect(self._apply_pick_pose)
        style_many(
            [
                (btn_read, "primary"),
                (btn_save, "success"),
                (btn_apply, "warn"),
            ]
        )
        for b in (btn_read, btn_save, btn_apply):
            edit_row.addWidget(b)
        root.addLayout(edit_row)

        box_m = QGroupBox("试跑（上料机器人 R1）")
        bm = QVBoxLayout(box_m)
        self.lbl_target = QLabel("合成取料点: -")
        self.lbl_target.setWordWrap(True)
        self.lbl_target.setStyleSheet("padding:6px;background:#eef6ff;")
        bm.addWidget(self.lbl_target)
        mv = QHBoxLayout()
        btn_j = QPushButton("MoveJ → 取料点")
        btn_l = QPushButton("MoveL → 取料点")
        btn_above = QPushButton("MoveL → 取料上方(点+偏移)")
        btn_stop = QPushButton("停止")
        btn_j.clicked.connect(lambda: self._move_pick(linear=False, above=False))
        btn_l.clicked.connect(lambda: self._move_pick(linear=True, above=False))
        btn_above.clicked.connect(lambda: self._move_pick(linear=True, above=True))
        btn_stop.clicked.connect(self._stop)
        style_many(
            [
                (btn_j, "motion"),
                (btn_l, "motion"),
                (btn_above, "motion"),
                (btn_stop, "danger"),
            ]
        )
        for b in (btn_j, btn_l, btn_above, btn_stop):
            mv.addWidget(b)
        bm.addLayout(mv)
        root.addWidget(box_m)

        self.lbl_dbg = QLabel("状态: 空闲")
        self.lbl_dbg.setStyleSheet("padding:6px;background:#f5f5f5;")
        root.addWidget(self.lbl_dbg)
        root.addStretch(1)
        apply_page_chrome(self)

        for sp in (
            self.sp_z,
            self.sp_rx,
            self.sp_ry,
            self.sp_x,
            self.sp_y,
            self.sp_rz,
            self.sp_toe_y,
        ):
            sp.valueChanged.connect(self._refresh_preview)
        self.ed_name.textChanged.connect(self._refresh_preview)
        self.chk_left.stateChanged.connect(self._refresh_preview)

        self._reload_all()

    def _mock_blk(self) -> dict:
        vis = self.ctx.cfg.setdefault("vision", {})
        blk = vis.setdefault("belt_pick_mock", {})
        if not isinstance(blk, dict):
            blk = {}
            vis["belt_pick_mock"] = blk
        shoes = blk.setdefault("shoes", [])
        if not isinstance(shoes, list):
            blk["shoes"] = []
        blk.setdefault("alternate_lr", True)
        return blk

    def _on_alt_toggled(self, on: bool) -> None:
        if self._loading:
            return
        self._mock_blk()["alternate_lr"] = bool(on)
        self._refresh_preview()

    def _shoes(self) -> list:
        shoes = self._mock_blk().get("shoes") or []
        return [s for s in shoes if isinstance(s, dict)]

    def _reload_all(self) -> None:
        self._loading = True
        blk = self._mock_blk()
        self.sp_z.setValue(float(blk.get("z", 488)))
        self.sp_rx.setValue(float(blk.get("rx", -178)))
        self.sp_ry.setValue(float(blk.get("ry", -2)))

        cur = self.cmb_shoe.currentIndex()
        self.cmb_shoe.blockSignals(True)
        self.cmb_shoe.clear()
        for i, s in enumerate(self._shoes()):
            name = str(s.get("name") or f"鞋{i+1}")
            side = "左" if s.get("is_left_shoe", True) else "右"
            self.cmb_shoe.addItem(f"[{side}] {name}", i)
        self.cmb_shoe.blockSignals(False)
        if self.cmb_shoe.count():
            self.cmb_shoe.setCurrentIndex(max(0, min(cur, self.cmb_shoe.count() - 1)))
        self._loading = False
        self._load_shoe()
        self._refresh_preview()

    def _load_shoe(self) -> None:
        if self._loading:
            return
        shoes = self._shoes()
        idx = self.cmb_shoe.currentData()
        if idx is None or not shoes or int(idx) >= len(shoes):
            self.ed_name.clear()
            return
        s = shoes[int(idx)]
        self._loading = True
        self.ed_name.setText(str(s.get("name", "")))
        self.chk_left.setChecked(bool(s.get("is_left_shoe", True)))
        self.sp_x.setValue(float(s.get("x", 0)))
        self.sp_y.setValue(float(s.get("y", 0)))
        self.sp_rz.setValue(float(s.get("rz", s.get("angle_deg", 0))))
        toe = s.get("toe_offset_in_grasp_tcp")
        if not (isinstance(toe, (list, tuple)) and len(toe) >= 2):
            d = self._mock_blk().get("toe_offset_in_grasp_tcp") or [0.0, 120.0, 0.0]
            toe = d
        self.sp_toe_y.setValue(float(toe[1]))
        self._loading = False
        self._refresh_preview()

    def _collect_shoe_into_cfg(self) -> None:
        """把界面当前鞋写回 cfg（未 save 文件也可先用于试跑/预览）。"""
        shoes = self._mock_blk().setdefault("shoes", [])
        idx = self.cmb_shoe.currentData()
        if idx is None or int(idx) >= len(shoes):
            return
        shoes[int(idx)] = {
            "name": self.ed_name.text().strip() or f"屏蔽鞋{int(idx)+1}",
            "x": float(self.sp_x.value()),
            "y": float(self.sp_y.value()),
            "rz": float(self.sp_rz.value()),
            "is_left_shoe": bool(self.chk_left.isChecked()),
            "toe_offset_in_grasp_tcp": [0.0, float(self.sp_toe_y.value()), 0.0],
        }
        blk = self._mock_blk()
        blk["z"] = float(self.sp_z.value())
        blk["rx"] = float(self.sp_rx.value())
        blk["ry"] = float(self.sp_ry.value())

    def _full_pick_pose(self) -> dict:
        self._collect_shoe_into_cfg()
        return {
            "x": float(self.sp_x.value()),
            "y": float(self.sp_y.value()),
            "z": float(self.sp_z.value()),
            "rx": float(self.sp_rx.value()),
            "ry": float(self.sp_ry.value()),
            "rz": float(self.sp_rz.value()),
            "is_left_shoe": bool(self.chk_left.isChecked()),
            "name": self.ed_name.text().strip() or "屏蔽取料点",
        }

    def _refresh_preview(self) -> None:
        if self._loading:
            return
        pose = self._full_pick_pose()
        axes = ", ".join(
            f"{k}={pose[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz")
        )
        side = "左鞋" if pose["is_left_shoe"] else "右鞋"
        self.lbl_target.setText(
            f"合成取料点「{pose['name']}」({side})\n{axes}\n"
            f"鞋头Y偏移={float(self.sp_toe_y.value()):.1f}mm（Mock；真机由相机测长）"
        )

        shoes = self._shoes()
        blk = self._mock_blk()
        alt = bool(blk.get("alternate_lr", True))
        if self.chk_alt.isChecked() != alt:
            self.chk_alt.blockSignals(True)
            self.chk_alt.setChecked(alt)
            self.chk_alt.blockSignals(False)
        lefts = [s for s in shoes if s.get("is_left_shoe", True)]
        rights = [s for s in shoes if not s.get("is_left_shoe", True)]
        if not shoes:
            self.lbl_order.setText("模拟取鞋：无鞋位")
        elif alt:
            self.lbl_order.setText(
                f"交替模式：左示教{len(lefts)}只 / 右示教{len(rights)}只 | "
                f"{self.ctx.vision.belt_mock_status_text()}"
            )
        else:
            ordered = sorted(shoes, key=lambda s: float(s.get("x", 0)))
            first = ordered[0]
            side = "左" if first.get("is_left_shoe", True) else "右"
            self.lbl_order.setText(
                f"固定取 X 最小：{side}鞋「{first.get('name', '?')}」 "
                f"X={float(first.get('x', 0)):.1f}"
            )

    def _add_shoe(self) -> None:
        self._collect_shoe_into_cfg()
        shoes = self._mock_blk().setdefault("shoes", [])
        n = len(shoes) + 1
        shoes.append(
            {
                "name": f"屏蔽-新鞋{n}",
                "x": float(self.sp_x.value()),
                "y": float(self.sp_y.value()),
                "rz": float(self.sp_rz.value()),
                "is_left_shoe": True,
                "toe_offset_in_grasp_tcp": [0.0, float(self.sp_toe_y.value() or 120.0), 0.0],
            }
        )
        self._reload_all()
        self.cmb_shoe.setCurrentIndex(self.cmb_shoe.count() - 1)

    def _del_shoe(self) -> None:
        shoes = self._mock_blk().get("shoes") or []
        idx = self.cmb_shoe.currentData()
        if idx is None or not shoes:
            return
        if len(shoes) <= 1:
            QMessageBox.warning(self, "不可删", "至少保留一只屏蔽鞋位")
            return
        ans = QMessageBox.question(
            self, "删除", f"删除「{shoes[int(idx)].get('name')}」？", QMessageBox.Yes | QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return
        shoes.pop(int(idx))
        self._reload_all()

    def _read_tcp(self) -> None:
        try:
            pose = self.ctx.robot1.get_actual_tcp_pose()
        except Exception as e:
            QMessageBox.warning(self, "读TCP失败", str(e))
            return
        self._loading = True
        self.sp_x.setValue(float(pose.get("x", 0)))
        self.sp_y.setValue(float(pose.get("y", 0)))
        self.sp_z.setValue(float(pose.get("z", 0)))
        self.sp_rx.setValue(float(pose.get("rx", 0)))
        self.sp_ry.setValue(float(pose.get("ry", 0)))
        self.sp_rz.setValue(float(pose.get("rz", 0)))
        self._loading = False
        self._refresh_preview()
        self.lbl_dbg.setText("已读入上料R1 TCP（未保存，请点「保存到配置」）")

    def _save(self) -> None:
        self._collect_shoe_into_cfg()
        # 同步 runtime_pick 为当前鞋（启动默认）
        pose = self._full_pick_pose()
        self.ctx.runtime_pick = {
            "name": f"【上料R1】运行时取料位（视觉/屏蔽示教）",
            "x": pose["x"],
            "y": pose["y"],
            "z": pose["z"],
            "rx": pose["rx"],
            "ry": pose["ry"],
            "rz": pose["rz"],
            "is_left_shoe": pose["is_left_shoe"],
        }
        self.ctx.cfg["runtime_pick"] = dict(self.ctx.runtime_pick)
        save_config(self.ctx.cfg)
        self._reload_all()
        QMessageBox.information(
            self,
            "已保存",
            "已写入 vision.belt_pick_mock 与 runtime_pick。\n"
            "相机1 Mock 时下次拍照将使用这些示教点。",
        )
        self.lbl_dbg.setText("已保存屏蔽示教点")

    def _apply_pick_pose(self) -> None:
        pose = self._full_pick_pose()
        for k in ("x", "y", "z", "rx", "ry", "rz"):
            self.ctx.gvl.PickPose[k] = float(pose[k])
        self.ctx.gvl.PickPose["is_left_shoe"] = bool(pose["is_left_shoe"])
        self.ctx.runtime_pick.update(pose)
        self.lbl_dbg.setText(
            f"已写入 PickPose：{pose['name']} "
            + ", ".join(f"{k}={pose[k]:.1f}" for k in ("x", "y", "z", "rz"))
        )

    def _confirm_move(self, title: str, detail: str) -> bool:
        if self.ctx.machine.state.name == "RUNNING":
            QMessageBox.warning(self, "禁止", "自动运行中禁止点位调试，请先停止。")
            return False
        ans = QMessageBox.question(
            self,
            title,
            detail + "\n\n请确认周边安全。继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return ans == QMessageBox.Yes

    def _move_pick(self, *, linear: bool, above: bool) -> None:
        if self._dbg_busy:
            QMessageBox.information(self, "忙", "上一段未完成，可点停止。")
            return
        pose = numeric_pose(self._full_pick_pose())
        label = self.ed_name.text().strip() or "屏蔽取料点"
        if above:
            try:
                off = self.ctx.offset("robot1", "pick_above_offset")
            except Exception:
                off = {"x": 0, "y": 0, "z": 80, "rx": 0, "ry": 0, "rz": 0}
            pose = apply_offset(pose, off)
            label = f"{label}+取料上方偏移"
            linear = True
        how = "MoveL" if linear else "MoveJ"
        if not self._confirm_move(
            f"{how} 屏蔽点",
            f"{label}\n"
            + ", ".join(f"{k}={pose[k]:.1f}" for k in ("x", "y", "z", "rx", "ry", "rz")),
        ):
            return
        robot = self.ctx.robot1
        try:
            if linear:
                robot.move_l(pose, label=label)
            else:
                robot.move_j(pose, label=label)
        except Exception as e:
            QMessageBox.critical(self, "发令失败", str(e))
            return
        self._dbg_busy = True
        self.lbl_dbg.setText(f"调试中: {robot.path_hint() or how + ' → ' + label}")

    def _stop(self) -> None:
        self.ctx.robot1.halt_motion()
        self._dbg_busy = False
        self.lbl_dbg.setText("已停止")

    def refresh(self) -> None:
        if not self._dbg_busy:
            return
        robot = self.ctx.robot1
        try:
            if robot.poll_move_done():
                self._dbg_busy = False
                self.lbl_dbg.setText(f"到位: {robot._last_arrived_label}")
        except Exception as e:
            self._dbg_busy = False
            self.lbl_dbg.setText(f"运动异常: {e}")
