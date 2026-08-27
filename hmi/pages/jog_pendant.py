"""独立示教器窗口：封装机械臂点动，可与主界面（示教点位/视觉）同时开。

点按一步按设定行程；默认按住连续：StartJOG，松开 ImmStopJOG 立刻停。
自动连续运行、急停、报警时禁止点动。关窗即停。

点动区样式参考 FANUC 成对 ± 键、遨博/埃斯顿红绿蓝坐标：
XY 做成水平十字盘，Z 单独升高/降低，旋转绕同色轴。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont, QGuiApplication, QMoveEvent, QPainter, QPen, QPolygonF, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.coordinator import Coordinator
from core.machine_state import MachineState, RunMode
from devices.pose_utils import POSE_AXES, angle_diff_deg, pose_tcp_parallel_to_base
from hmi import i18n

# 法奥 StartJOG ref
_REF_JOINT = 0
_REF_BASE = 2
_REF_TOOL = 4

# 与机器人坐标贴纸同色：X红 Y绿 Z蓝（FANUC/遨博/埃斯顿通用）
# plus, minus, hover_plus, hover_minus
_AXIS_PALETTE: dict[str, tuple[str, str, str, str]] = {
    "x": ("#e74c3c", "#922b21", "#f1948a", "#7b241c"),
    "y": ("#27ae60", "#196f3d", "#58d68d", "#145a32"),
    "z": ("#3498db", "#1a5276", "#85c1e9", "#154360"),
    "j": ("#2e86c1", "#1a5276", "#5dade2", "#154360"),
}

_JOINT_HINTS = (
    (1, "J1", "底座回转"),
    (2, "J2", "肩部"),
    (3, "J3", "肘部"),
    (4, "J4", "腕旋转"),
    (5, "J5", "腕摆"),
    (6, "J6", "法兰"),
)

_SETTINGS_ORG = "CasbotFourSlot"
_SETTINGS_APP = "HMI"
_GEOM_KEY = "jog_pendant/geometry"
_ALIGN_HOLD_MS = 200
_ALIGN_EPS_DEG = 1.0

_PENDANT_QSS = """
QWidget#jogPendantRoot, QMainWindow#jogPendantWin {
    background: #1b2631;
    color: #ecf0f1;
}
QScrollArea#jogPendantScroll {
    background: #1b2631;
    border: none;
}
QLabel { color: #ecf0f1; }
QGroupBox {
    color: #aed6f1;
    border: 1px solid #5d6d7e;
    border-radius: 6px;
    margin-top: 10px;
    padding: 8px 6px 6px 6px;
    font-weight: bold;
    background: #212f3c;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 6px;
    color: #85c1e9;
}
QPushButton {
    color: #ffffff;
    font-weight: bold;
    border: none;
    border-radius: 6px;
    min-height: 36px;
    padding: 6px 8px;
}
QPushButton:disabled { background: #566573; color: #bdc3c7; }
QPushButton#segBtn, QPushButton#segArmR1, QPushButton#segArmR2,
QPushButton#segInch, QPushButton#segHold {
    background-color: #2c3e50;
    color: #c5d0dc;
    border: 2px solid #1c2833;
    min-height: 48px;
    font-size: 15px;
    padding: 8px 6px;
}
QPushButton#segBtn:hover:!checked, QPushButton#segArmR1:hover:!checked,
QPushButton#segArmR2:hover:!checked, QPushButton#segInch:hover:!checked,
QPushButton#segHold:hover:!checked {
    background-color: #3d566e;
    color: #ecf0f1;
}
QPushButton#segBtn:checked {
    background-color: #f4d03f;
    color: #111111;
    border: 3px solid #f4d03f;
    font-size: 15px;
}
QPushButton#segArmR1:checked {
    background-color: #2980b9;
    color: #ffffff;
    border: 3px solid #7ecef4;
    font-size: 15px;
}
QPushButton#segArmR2:checked {
    background-color: #1e8449;
    color: #ffffff;
    border: 3px solid #7dcea0;
    font-size: 15px;
}
QPushButton#segInch:checked {
    background-color: #27ae60;
    color: #ffffff;
    border: 3px solid #7dcea0;
    font-size: 15px;
}
QPushButton#segHold:checked {
    background-color: #e67e22;
    color: #ffffff;
    border: 3px solid #f5cba7;
    font-size: 15px;
}
QPushButton#estopJog {
    background: #c0392b;
    font-size: 18px;
    min-height: 56px;
}
QPushButton#estopJog:hover { background: #e74c3c; }
QPushButton#estopJog:pressed { background: #922b21; }
QPushButton#alignBtn {
    background-color: #6c3483;
    color: #ffffff;
    font-size: 16px;
    min-height: 52px;
    font-weight: bold;
    border-radius: 8px;
}
QPushButton#alignBtn:hover { background-color: #8e44ad; }
QPushButton#alignBtn:pressed { background-color: #4a235a; }
QPushButton#alignBtn:disabled { background-color: #566573; color: #bdc3c7; }
QCheckBox, QRadioButton {
    color: #f4d03f;
    font-weight: bold;
    font-size: 14px;
    spacing: 8px;
    min-height: 28px;
}
QCheckBox::indicator {
    width: 20px;
    height: 20px;
}
QDoubleSpinBox {
    background: #273746;
    color: #ecf0f1;
    min-height: 32px;
    padding: 2px 6px;
    border: 1px solid #5d6d7e;
    border-radius: 4px;
}
QSlider::groove:horizontal {
    height: 8px;
    background: #34495e;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -6px 0;
    background: #5dade2;
    border-radius: 9px;
}
QSlider::sub-page:horizontal {
    background: #1a5276;
    border-radius: 4px;
}
"""


def _axis_btn_qss(axis_key: str, *, positive: bool) -> str:
    plus, minus, hover_p, hover_m = _AXIS_PALETTE[axis_key]
    bg = plus if positive else minus
    hover = hover_p if positive else hover_m
    press = minus if positive else "#0e1a24"
    return f"""
    QPushButton {{
        background-color: {bg};
        color: #ffffff;
        font-weight: bold;
        font-size: 14px;
        border: 2px solid #0b1a24;
        border-radius: 10px;
        padding: 4px 2px;
    }}
    QPushButton:hover {{ background-color: {hover}; }}
    QPushButton:pressed {{ background-color: {press}; }}
    QPushButton:disabled {{
        background-color: #566573;
        color: #bdc3c7;
        border-color: #34495e;
    }}
    """


def open_jog_pendant_from(widget: QWidget, *, robot_key: str | None = None) -> None:
    """从任意页打开主窗口上的示教器（找不到则忽略）。"""
    win = widget.window()
    fn = getattr(win, "show_jog_pendant", None)
    if callable(fn):
        fn(robot_key=robot_key)


class AxisTriadWidget(QWidget):
    """红X / 绿Y / 蓝Z 等轴测三角标，对应机器人坐标贴纸。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(96, 96)
        self.setToolTip("与机器人底座/法兰坐标贴纸同色：X红 Y绿 Z蓝")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0e1a24"))
        origin = QPointF(48.0, 62.0)
        axes = (
            (QPointF(40.0, 6.0), QColor("#e74c3c"), "X"),
            (QPointF(-34.0, 4.0), QColor("#27ae60"), "Y"),
            (QPointF(0.0, -48.0), QColor("#3498db"), "Z"),
        )
        for delta, color, name in axes:
            end = origin + delta
            self._draw_arrow(painter, origin, end, color)
            painter.setPen(color)
            painter.setFont(QFont("sans", 12, QFont.Weight.Bold))
            painter.drawText(end + QPointF(-7, -8), name)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ecf0f1"))
        painter.drawEllipse(origin, 5, 5)

    def _draw_arrow(
        self,
        painter: QPainter,
        start: QPointF,
        end: QPointF,
        color: QColor,
    ) -> None:
        painter.setPen(QPen(color, 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(start, end)
        vec = end - start
        length = max((vec.x() ** 2 + vec.y() ** 2) ** 0.5, 1.0)
        ux, uy = vec.x() / length, vec.y() / length
        hx, hy = -uy, ux
        head = 11.0
        p1 = end
        p2 = QPointF(end.x() - ux * head + hx * 5.0, end.y() - uy * head + hy * 5.0)
        p3 = QPointF(end.x() - ux * head - hx * 5.0, end.y() - uy * head - hy * 5.0)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([p1, p2, p3]))


class JogPendantPanel(QWidget):
    """示教器面板：基座/工具笛卡尔或关节点动。"""

    def __init__(self, coord: Coordinator) -> None:
        super().__init__()
        self.setObjectName("jogPendantRoot")
        self.coord = coord
        self.ctx = coord.ctx
        self._held: tuple[int, bool] | None = None
        self._align_pending = False
        self._align_moving = False
        self._align_robot: object | None = None
        self._pose_cache: dict | None = None
        self._joint_cache: list[float] | None = None
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(40)
        self._hold_timer.timeout.connect(self._on_hold_tick)
        self._align_hold_timer = QTimer(self)
        self._align_hold_timer.setSingleShot(True)
        self._align_hold_timer.setInterval(_ALIGN_HOLD_MS)
        self._align_hold_timer.timeout.connect(self._start_align_move)
        self._align_watch = QTimer(self)
        self._align_watch.setInterval(40)
        self._align_watch.timeout.connect(self._on_align_watch)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.lbl_now = QLabel("当前：上料 R1")
        self.lbl_now.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_now.setWordWrap(True)
        self.lbl_now.setStyleSheet(
            "background:#2980b9;color:#ffffff;font-size:17px;font-weight:bold;"
            "padding:12px 8px;border-radius:6px;border:2px solid #ffffff;"
        )
        root.addWidget(self.lbl_now)

        tip = QLabel(
            "十字盘=水平面 XY，右侧蓝键=升高/降低。"
            "颜色与机器人坐标贴纸一致：红X 绿Y 蓝Z。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#85c1e9;font-weight:bold;")
        root.addWidget(tip)

        box_arm = QGroupBox("机器人")
        self.box_arm = box_arm
        ha = QHBoxLayout(box_arm)
        self.btn_r1 = QPushButton("上料 R1")
        self.btn_r2 = QPushButton("下料 R2")
        self.bg_robot = QButtonGroup(self)
        self.bg_robot.setExclusive(True)
        self.btn_r1.setObjectName("segArmR1")
        self.btn_r2.setObjectName("segArmR2")
        for btn, rid in ((self.btn_r1, 1), (self.btn_r2, 2)):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.bg_robot.addButton(btn, rid)
            ha.addWidget(btn, 1)
        self.btn_r1.setChecked(True)
        self.bg_robot.idClicked.connect(self._on_robot_changed)
        root.addWidget(box_arm)

        box_frame = QGroupBox("坐标系")
        hf = QHBoxLayout(box_frame)
        self.btn_base = QPushButton("基座 XYZ")
        self.btn_tool = QPushButton("工具 XYZ")
        self.btn_joint = QPushButton("关节 J1–J6")
        self.bg_frame = QButtonGroup(self)
        self.bg_frame.setExclusive(True)
        for btn, fid in ((self.btn_base, 2), (self.btn_tool, 4), (self.btn_joint, 0)):
            btn.setCheckable(True)
            btn.setObjectName("segBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.bg_frame.addButton(btn, fid)
            hf.addWidget(btn, 1)
        self.btn_base.setChecked(True)
        self.bg_frame.idClicked.connect(self._on_frame_changed)
        root.addWidget(box_frame)

        box_spd = QGroupBox("速度与步距")
        gs = QGridLayout(box_spd)
        self.sld_vel = QSlider(Qt.Orientation.Horizontal)
        self.sld_vel.setRange(1, 25)
        self.sld_vel.setValue(8)
        self.lbl_vel = QLabel("速度 8%")
        self.lbl_vel.setStyleSheet("font-size:15px;font-weight:bold;color:#f4d03f;")
        self.sld_vel.valueChanged.connect(self._on_vel_changed)
        gs.addWidget(self.lbl_vel, 0, 0)
        gs.addWidget(self.sld_vel, 0, 1, 1, 3)

        gs.addWidget(QLabel("直线 mm"), 1, 0)
        self.sp_mm = QDoubleSpinBox()
        self.sp_mm.setRange(0.2, 50.0)
        self.sp_mm.setDecimals(1)
        self.sp_mm.setSingleStep(1.0)
        self.sp_mm.setValue(3.0)
        gs.addWidget(self.sp_mm, 1, 1)
        gs.addWidget(QLabel("角度 °"), 1, 2)
        self.sp_deg = QDoubleSpinBox()
        self.sp_deg.setRange(0.1, 15.0)
        self.sp_deg.setDecimals(1)
        self.sp_deg.setSingleStep(0.5)
        self.sp_deg.setValue(1.0)
        gs.addWidget(self.sp_deg, 1, 3)
        for sp in (self.sp_mm, self.sp_deg):
            sp.wheelEvent = lambda e: e.ignore()  # type: ignore

        self.btn_inch = QPushButton("点按一步")
        self.btn_hold = QPushButton("按住连续")
        self.bg_mode = QButtonGroup(self)
        self.bg_mode.setExclusive(True)
        self.btn_inch.setObjectName("segInch")
        self.btn_hold.setObjectName("segHold")
        for btn in (self.btn_inch, self.btn_hold):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.bg_mode.addButton(btn)
        self.btn_hold.setChecked(True)
        self.bg_mode.buttonClicked.connect(self._on_mode_changed)
        gs.addWidget(self.btn_inch, 2, 0, 1, 2)
        gs.addWidget(self.btn_hold, 2, 2, 1, 2)
        root.addWidget(box_spd)

        self.box_cart = QGroupBox("基座坐标系")
        self._cart_btns = self._build_cart_pad(self.box_cart)
        root.addWidget(self.box_cart, 1)

        self.box_joint = QGroupBox("关节点动")
        self._joint_btns = self._build_joint_pad(self.box_joint)
        self.box_joint.hide()
        root.addWidget(self.box_joint, 1)

        self.btn_align = QPushButton("按住：TCP 与基座平行（松开即停）")
        self.btn_align.setObjectName("alignBtn")
        self.btn_align.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_align.setAutoRepeat(False)
        self.btn_align.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_align.setToolTip(
            "按住才动：XYZ 和 Rz 不变，Rx/Ry 打平。"
            "松开立即停止。点按不会动。"
        )
        self.btn_align.pressed.connect(self._on_align_pressed)
        self.btn_align.released.connect(self._on_align_released)
        root.addWidget(self.btn_align)
        self.lbl_align = QLabel("")
        self.lbl_align.setWordWrap(True)
        self.lbl_align.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_align.setStyleSheet("color:#aed6f1;font-weight:bold;min-height:22px;")
        root.addWidget(self.lbl_align)

        self.btn_stop = QPushButton("停止点动")
        self.btn_stop.setObjectName("estopJog")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self._stop_all)
        root.addWidget(self.btn_stop)

        self.lbl_pose = QLabel("TCP: —")
        self.lbl_pose.setWordWrap(True)
        pose_font = QFont("monospace")
        pose_font.setStyleHint(QFont.StyleHint.TypeWriter)
        pose_font.setPointSize(11)
        self.lbl_pose.setFont(pose_font)
        self.lbl_pose.setMinimumHeight(88)
        self.lbl_pose.setStyleSheet(
            "background:#0e1a24;color:#d5dbdb;padding:10px;border-radius:4px;"
        )
        root.addWidget(self.lbl_pose)
        self.lbl_lock = QLabel("")
        self.lbl_lock.setWordWrap(True)
        self.lbl_lock.setStyleSheet("color:#f1948a;font-weight:bold;")
        root.addWidget(self.lbl_lock)
        self._refresh_selection_ui()
        self._sync_frame_panels()

    def _build_cart_pad(self, box: QGroupBox) -> list[QPushButton]:
        """XY 十字盘 + Z 升高/降低 + 绕轴旋转（FANUC 成对 ±）。"""
        outer = QVBoxLayout(box)
        outer.setSpacing(8)
        self.lbl_cart_hint = QLabel("")
        self.lbl_cart_hint.setWordWrap(True)
        self.lbl_cart_hint.setStyleSheet("color:#aeb6bf;font-weight:normal;")
        outer.addWidget(self.lbl_cart_hint)

        row = QHBoxLayout()
        row.setSpacing(10)

        xy_box = QFrame()
        xy_box.setStyleSheet("QFrame { background:#0e1a24; border-radius:8px; }")
        xy = QGridLayout(xy_box)
        xy.setContentsMargins(8, 8, 8, 8)
        xy.setSpacing(6)
        btn_yp = self._make_axis_btn("▲\nY+", 2, True, "y")
        btn_yn = self._make_axis_btn("Y−\n▼", 2, False, "y")
        btn_xm = self._make_axis_btn("◄  X−", 1, False, "x", min_w=92)
        btn_xp = self._make_axis_btn("X+  ►", 1, True, "x", min_w=92)
        triad = AxisTriadWidget()
        xy.addWidget(btn_yp, 0, 1)
        xy.addWidget(btn_xm, 1, 0)
        xy.addWidget(triad, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        xy.addWidget(btn_xp, 1, 2)
        xy.addWidget(btn_yn, 2, 1)
        lbl_xy = QLabel("水平面 XY")
        lbl_xy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_xy.setStyleSheet("color:#85929e;font-weight:normal;")
        xy.addWidget(lbl_xy, 3, 0, 1, 3)
        row.addWidget(xy_box, 3)

        z_box = QFrame()
        z_box.setStyleSheet("QFrame { background:#0e1a24; border-radius:8px; }")
        zv = QVBoxLayout(z_box)
        zv.setContentsMargins(8, 8, 8, 8)
        zv.setSpacing(6)
        lbl_z = QLabel("高度 Z")
        lbl_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_z.setStyleSheet("color:#85c1e9;font-weight:bold;")
        btn_zp = self._make_axis_btn("▲ 上\nZ+", 3, True, "z", min_w=88, min_h=72)
        btn_zn = self._make_axis_btn("Z−\n▼ 下", 3, False, "z", min_w=88, min_h=72)
        zv.addWidget(lbl_z)
        zv.addWidget(btn_zp, 1)
        zv.addStretch(1)
        zv.addWidget(btn_zn, 1)
        row.addWidget(z_box, 1)
        outer.addLayout(row)

        rot_title = QLabel("旋转 · 绕同色轴（右手螺旋）")
        rot_title.setStyleSheet("color:#aed6f1;")
        outer.addWidget(rot_title)
        rot = QGridLayout()
        rot.setSpacing(6)
        btn_rxm = self._make_axis_btn("↺  Rx−", 4, False, "x", min_h=52)
        btn_rxp = self._make_axis_btn("Rx+  ↻", 4, True, "x", min_h=52)
        btn_rym = self._make_axis_btn("↺  Ry−", 5, False, "y", min_h=52)
        btn_ryp = self._make_axis_btn("Ry+  ↻", 5, True, "y", min_h=52)
        btn_rzm = self._make_axis_btn("↺  Rz−", 6, False, "z", min_h=52)
        btn_rzp = self._make_axis_btn("Rz+  ↻", 6, True, "z", min_h=52)
        rot.addWidget(btn_rxm, 0, 0)
        rot.addWidget(self._axis_mid_label("绕 X", "#e74c3c"), 0, 1)
        rot.addWidget(btn_rxp, 0, 2)
        rot.addWidget(btn_rym, 1, 0)
        rot.addWidget(self._axis_mid_label("绕 Y", "#27ae60"), 1, 1)
        rot.addWidget(btn_ryp, 1, 2)
        rot.addWidget(btn_rzm, 2, 0)
        rot.addWidget(self._axis_mid_label("绕 Z", "#3498db"), 2, 1)
        rot.addWidget(btn_rzp, 2, 2)
        rot.setColumnStretch(0, 2)
        rot.setColumnStretch(1, 1)
        rot.setColumnStretch(2, 2)
        outer.addLayout(rot)

        return [
            btn_xp,
            btn_xm,
            btn_yp,
            btn_yn,
            btn_zp,
            btn_zn,
            btn_rxp,
            btn_rxm,
            btn_ryp,
            btn_rym,
            btn_rzp,
            btn_rzm,
        ]

    def _build_joint_pad(self, box: QGroupBox) -> list[QPushButton]:
        """FANUC 式：每轴一行 − / 名称 / +。"""
        grid = QGridLayout(box)
        grid.setSpacing(6)
        hint = QLabel("每行一个关节。−/+ 为该关节负/正转（看轴上箭头）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#aeb6bf;font-weight:normal;")
        grid.addWidget(hint, 0, 0, 1, 3)
        buttons: list[QPushButton] = []
        for row, (axis, code, name) in enumerate(_JOINT_HINTS, start=1):
            bn = self._make_axis_btn("−", axis, False, "j", min_w=72, min_h=48)
            bp = self._make_axis_btn("+", axis, True, "j", min_w=72, min_h=48)
            mid = QLabel(f"{code}\n{name}")
            mid.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mid.setStyleSheet(
                "color:#d4e6f1;background:#1c2833;border-radius:6px;padding:4px;"
            )
            grid.addWidget(bn, row, 0)
            grid.addWidget(mid, row, 1)
            grid.addWidget(bp, row, 2)
            buttons.extend((bp, bn))
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 2)
        return buttons

    def _axis_mid_label(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{color};background:#0e1a24;border-radius:6px;"
            "font-weight:bold;padding:6px;"
        )
        return lbl

    def _make_axis_btn(
        self,
        text: str,
        axis: int,
        positive: bool,
        axis_key: str,
        *,
        min_w: int = 76,
        min_h: int = 64,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoRepeat(False)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        btn.setMinimumSize(min_w, min_h)
        btn.setStyleSheet(_axis_btn_qss(axis_key, positive=positive))
        btn.pressed.connect(lambda a=axis, p=positive: self._on_pressed(a, p))
        btn.released.connect(self._on_released)
        btn.clicked.connect(lambda _=False, a=axis, p=positive: self._on_clicked(a, p))
        return btn

    def select_robot(self, robot_key: str) -> None:
        """与示教点位页当前臂对齐。"""
        want_r2 = robot_key == "robot2"
        if want_r2 == self.btn_r2.isChecked():
            return
        self._stop_all()
        if want_r2:
            self.btn_r2.setChecked(True)
        else:
            self.btn_r1.setChecked(True)
        self._refresh_selection_ui()

    def _on_robot_changed(self, *_a) -> None:
        self._stop_all()
        self._refresh_selection_ui()

    def _on_mode_changed(self, *_a) -> None:
        self._stop_all()
        self._refresh_selection_ui()

    def _on_vel_changed(self, value: int) -> None:
        self.lbl_vel.setText(f"速度 {int(value)}%")
        self._refresh_selection_ui()

    def _refresh_selection_ui(self) -> None:
        """选中态：高亮条 + 窗口标题。按钮文案保持短，避免挤没字。"""
        r2 = self.btn_r2.isChecked()
        arm = "下料 R2" if r2 else "上料 R1"
        self.btn_r1.setText("上料 R1")
        self.btn_r2.setText("下料 R2")
        self.box_arm.setTitle(f"机器人　当前 {arm}")
        self._apply_seg_style(self.btn_r1, on=not r2, kind="r1")
        self._apply_seg_style(self.btn_r2, on=r2, kind="r2")

        self.btn_base.setText("基座 XYZ")
        self.btn_tool.setText("工具 XYZ")
        self.btn_joint.setText("关节 J1–J6")
        if self.btn_joint.isChecked():
            frame = "关节 J1–J6"
        elif self.btn_tool.isChecked():
            frame = "工具 XYZ"
        else:
            frame = "基座 XYZ"
        self._apply_seg_style(self.btn_base, on=self.btn_base.isChecked(), kind="frame")
        self._apply_seg_style(self.btn_tool, on=self.btn_tool.isChecked(), kind="frame")
        self._apply_seg_style(self.btn_joint, on=self.btn_joint.isChecked(), kind="frame")

        hold = self.btn_hold.isChecked()
        mode = "按住连续" if hold else "点按一步"
        self.btn_inch.setText("点按一步")
        self.btn_hold.setText("按住连续")
        self._apply_seg_style(self.btn_inch, on=not hold, kind="inch")
        self._apply_seg_style(self.btn_hold, on=hold, kind="hold")

        vel = int(self.sld_vel.value())
        self.lbl_now.setText(f"当前：{arm}   ·   {frame}   ·   {mode}   ·   {vel}%")
        bg = "#1e8449" if r2 else "#2980b9"
        self.lbl_now.setStyleSheet(
            f"background:{bg};color:#ffffff;font-size:17px;font-weight:bold;"
            "padding:12px 8px;border-radius:6px;border:2px solid #ffffff;"
        )
        win = self.window()
        if isinstance(win, QMainWindow):
            win.setWindowTitle(f"{i18n.tr('nav.jog')}  —  {arm}")

    def _apply_seg_style(self, btn: QPushButton, *, on: bool, kind: str) -> None:
        """直接写控件样式，避免全局 QSS 把选中字色盖成深色看不见。"""
        if not on:
            qss = (
                "QPushButton { background-color:#2c3e50; color:#c5d0dc;"
                "border:2px solid #1c2833; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        elif kind == "r1":
            qss = (
                "QPushButton { background-color:#2980b9; color:#ffffff;"
                "border:3px solid #7ecef4; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        elif kind == "r2":
            qss = (
                "QPushButton { background-color:#1e8449; color:#ffffff;"
                "border:3px solid #7dcea0; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        elif kind == "inch":
            qss = (
                "QPushButton { background-color:#27ae60; color:#ffffff;"
                "border:3px solid #7dcea0; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        elif kind == "hold":
            qss = (
                "QPushButton { background-color:#e67e22; color:#ffffff;"
                "border:3px solid #f5cba7; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        else:
            qss = (
                "QPushButton { background-color:#f4d03f; color:#111111;"
                "border:3px solid #f7dc6f; border-radius:8px; font-weight:bold;"
                "font-size:15px; min-height:48px; padding:8px 6px; }"
            )
        btn.setStyleSheet(qss)

    def _robot(self):
        return self.ctx.robot2 if self.btn_r2.isChecked() else self.ctx.robot1

    def _ref(self) -> int:
        if self.btn_joint.isChecked():
            return _REF_JOINT
        if self.btn_tool.isChecked():
            return _REF_TOOL
        return _REF_BASE

    def _step_dis(self, axis: int) -> float:
        if self._ref() == _REF_JOINT:
            return float(self.sp_deg.value())
        if axis <= 3:
            return float(self.sp_mm.value())
        return float(self.sp_deg.value())

    def _locked(self) -> str:
        m = self.ctx.machine
        if m.state == MachineState.ESTOP:
            return "急停中，禁止点动"
        if m.state == MachineState.ALARM:
            return "报警中，请先复位"
        if m.mode == RunMode.AUTO and m.state == MachineState.RUNNING and not self.ctx.gvl.Main.Paused:
            return "自动连续运行中，请先暂停/停止"
        robot = self._robot()
        if getattr(robot, "estop_active", False):
            return f"{robot.name} 急停"
        return ""

    def _on_frame_changed(self, *_a) -> None:
        self._stop_all()
        self._sync_frame_panels()
        self._refresh_selection_ui()

    def _sync_frame_panels(self) -> None:
        joint = self.btn_joint.isChecked()
        self.box_joint.setVisible(joint)
        self.box_cart.setVisible(not joint)
        if self.btn_tool.isChecked():
            self.box_cart.setTitle("工具坐标系")
            self.lbl_cart_hint.setText(
                "相对当前 TCP：十字盘是工具 XY，蓝键是工具 Z（接近方向）。"
                "红绿蓝与法兰贴纸一致。默认按住才动，松开立刻停。"
            )
        else:
            self.box_cart.setTitle("基座坐标系")
            self.lbl_cart_hint.setText(
                "相对机器人底座：十字盘是水平面（右=红X+，上=绿Y+），"
                "右侧蓝键升高/降低。请对照底座红绿蓝贴纸。"
                "默认按住才动，松开立刻停。"
            )

    def _axis_buttons(self) -> list[QPushButton]:
        return self._cart_btns + self._joint_btns

    def _any_axis_btn_down(self) -> bool:
        return any(btn.isDown() for btn in self._axis_buttons())

    def _on_clicked(self, axis: int, positive: bool) -> None:
        if not self.btn_inch.isChecked():
            return
        if self._held is not None:
            return
        self._fire_jog(axis, positive, hold=False)

    def _on_pressed(self, axis: int, positive: bool) -> None:
        if not self.btn_hold.isChecked():
            return
        if self._align_moving:
            self._halt_align(stopped_by_user=True)
        else:
            self._align_hold_timer.stop()
            self._align_watch.stop()
            self._align_pending = False
        if self._held is not None:
            self._stop_jog_only()
        if not self._fire_jog(axis, positive, hold=True):
            return
        self._held = (axis, positive)
        self._hold_timer.start()

    def _on_released(self) -> None:
        """松开立刻停；仍按着别的轴键则不停（换向时由新按下先停再起）。"""
        if self._held is None:
            return
        if self._any_axis_btn_down():
            return
        self._stop_jog_only()

    def _on_hold_tick(self) -> None:
        """按住看门狗：键已松开则立刻停。Mock 再按步距叠加。"""
        if self._held is None:
            self._hold_timer.stop()
            return
        if not self._any_axis_btn_down():
            self._stop_jog_only()
            return
        robot = self._robot()
        if not robot.use_mock:
            return
        axis, positive = self._held
        try:
            robot.start_jog(
                ref=self._ref(),
                axis=axis,
                positive=positive,
                max_dis=self._step_dis(axis),
                vel_pct=float(self.sld_vel.value()),
            )
        except Exception:
            self._stop_all()

    def _fire_jog(self, axis: int, positive: bool, *, hold: bool) -> bool:
        err = self._locked()
        if err:
            QMessageBox.information(self, i18n.tr("nav.jog"), err)
            return False
        robot = self._robot()
        dis = self._step_dis(axis)
        if hold and not robot.use_mock:
            dis = 80.0 if self._ref() == _REF_JOINT else 250.0
        try:
            robot.start_jog(
                ref=self._ref(),
                axis=axis,
                positive=positive,
                max_dis=dis,
                vel_pct=float(self.sld_vel.value()),
            )
        except Exception as e:
            QMessageBox.warning(self, "点动失败", str(e))
            return False
        return True

    def _on_align_pressed(self) -> None:
        if self._align_moving:
            return
        err = self._locked()
        if err:
            QMessageBox.information(self, "姿态打平", err)
            return
        self._stop_jog_only()
        self._align_pending = True
        self.lbl_align.setStyleSheet("color:#f4d03f;font-weight:bold;min-height:22px;")
        self.lbl_align.setText("按住中… 松开即停")
        self._align_hold_timer.start()
        self._align_watch.start()

    def _on_align_released(self) -> None:
        """松开立刻停：未启动则取消，已发令/运动中则急停。"""
        self._align_hold_timer.stop()
        self._align_watch.stop()
        pending = self._align_pending
        moving = self._align_moving
        self._align_pending = False
        if moving:
            self._halt_align(stopped_by_user=True)
            return
        if pending:
            self.lbl_align.setStyleSheet("color:#f5b041;font-weight:bold;min-height:22px;")
            self.lbl_align.setText("已松开，未启动（请按住）")

    def _on_align_watch(self) -> None:
        """看门狗：松开事件丢失时仍立刻停。"""
        if not (self._align_pending or self._align_moving):
            self._align_watch.stop()
            return
        if not self.btn_align.isDown():
            self._on_align_released()

    def _halt_align(self, *, stopped_by_user: bool) -> None:
        self._align_moving = False
        self._align_pending = False
        self._align_hold_timer.stop()
        self._align_watch.stop()
        robot = self._align_robot or self._robot()
        self._align_robot = None
        try:
            robot.halt_motion(hard=True, rounds=1)
        except Exception:
            try:
                robot.halt_motion(hard=False, rounds=1)
            except Exception:
                pass
        if stopped_by_user:
            self.lbl_align.setStyleSheet("color:#f5b041;font-weight:bold;min-height:22px;")
            self.lbl_align.setText("已松开，运动已停止")

    def _start_align_move(self) -> None:
        """按住达到确认后开始 MoveL；松开立刻停。"""
        if not self._align_pending:
            return
        if not self.btn_align.isDown():
            self._align_pending = False
            return
        err = self._locked()
        if err:
            self._align_pending = False
            self.lbl_align.setText(err)
            return
        robot = self._robot()
        cur = self._pose_cache
        try:
            if cur is None:
                cur = robot.get_actual_tcp_pose()
            target = pose_tcp_parallel_to_base(cur)
        except Exception as e:
            self._align_pending = False
            QMessageBox.warning(self, "读位失败", str(e))
            self.lbl_align.setText(f"读位失败: {e}")
            return
        if not self.btn_align.isDown() or not self._align_pending:
            self._align_pending = False
            return
        already = (
            angle_diff_deg(cur["rx"], target["rx"]) < _ALIGN_EPS_DEG
            and angle_diff_deg(cur["ry"], target["ry"]) < _ALIGN_EPS_DEG
        )
        if already:
            self._align_pending = False
            self._mark_align_done(cur, target, moved=False)
            return
        vel = float(self.sld_vel.value())
        # 先置位，阻塞发令期间排队的 released 才能急停，而不是当成「未启动」
        self._align_moving = True
        self._align_pending = False
        self._align_target = target
        self._align_robot = robot
        try:
            robot.move_l(
                target,
                label="TCP与基座平行",
                from_label="当前位置",
                async_rpc=True,
                vel=vel,
            )
        except Exception as e:
            self._align_moving = False
            QMessageBox.warning(self, "发令失败", str(e))
            self.lbl_align.setStyleSheet("color:#f1948a;font-weight:bold;min-height:22px;")
            self.lbl_align.setText(f"发令失败: {e}")
            return
        if not self._align_moving:
            return
        if not self.btn_align.isDown():
            self._halt_align(stopped_by_user=True)
            return
        self.lbl_align.setStyleSheet("color:#f4d03f;font-weight:bold;min-height:22px;")
        self.lbl_align.setText(
            f"按住运动中，松开即停   Rx→{target['rx']:.1f}°  Ry→{target['ry']:.1f}°"
        )

    def _poll_align_move(self) -> None:
        if not self._align_moving:
            return
        if not self.btn_align.isDown():
            self._halt_align(stopped_by_user=True)
            return
        robot = self._robot()
        try:
            done = robot.poll_move_done()
        except Exception as e:
            self._align_moving = False
            self.lbl_align.setStyleSheet("color:#f1948a;font-weight:bold;min-height:22px;")
            self.lbl_align.setText(f"对齐中断: {e}")
            QMessageBox.warning(self, "对齐中断", str(e))
            return
        if not done:
            return
        self._align_moving = False
        try:
            pose = robot.get_actual_tcp_pose()
        except Exception:
            pose = getattr(self, "_align_target", {})
        self._mark_align_done(pose, getattr(self, "_align_target", pose), moved=True)

    def _mark_align_done(self, pose: dict, target: dict, *, moved: bool) -> None:
        self._align_watch.stop()
        self._align_robot = None
        rx = float(pose.get("rx", target.get("rx", 0)))
        ry = float(pose.get("ry", target.get("ry", 0)))
        rz = float(pose.get("rz", target.get("rz", 0)))
        xyz = (
            f"X {float(pose.get('x', 0)):.1f}  Y {float(pose.get('y', 0)):.1f}  "
            f"Z {float(pose.get('z', 0)):.1f}"
        )
        self.lbl_align.setStyleSheet(
            "color:#1b2631;background:#58d68d;font-weight:bold;"
            "padding:8px;border-radius:6px;min-height:22px;"
        )
        self.lbl_align.setText(f"✓ 移动完成  已与基座平行   {xyz}")
        if moved:
            msg = (
                "TCP 姿态已与基座平行（工具 XY ∥ 基座 XY）。\n\n"
                f"位置未改：{xyz}\n"
                f"Rx={rx:.1f}°  Ry={ry:.1f}°  Rz={rz:.1f}°（Rz 保持）"
            )
        else:
            msg = (
                "当前姿态已与基座平行，无需运动。\n\n"
                f"{xyz}\nRx={rx:.1f}°  Ry={ry:.1f}°  Rz={rz:.1f}°"
            )
        QMessageBox.information(self, "移动完成", msg)

    def stop_all(self) -> None:
        """对外：关窗/切臂时停点动。"""
        self._stop_all()

    def _stop_all(self, *_a) -> None:
        self._stop_motion_now()

    def _stop_jog_only(self) -> None:
        """松开点动键：立刻停 JOG，不动对齐 MoveL。"""
        self._hold_timer.stop()
        self._held = None
        for robot in (self.ctx.robot1, self.ctx.robot2):
            try:
                robot.stop_jog(immediate=True)
            except Exception:
                pass

    def _stop_motion_now(self) -> None:
        """切臂/关窗/锁定：立刻停点动；对齐若已发令则急停。"""
        moving = self._align_moving
        self._stop_jog_only()
        self._align_hold_timer.stop()
        self._align_watch.stop()
        self._align_pending = False
        if moving:
            self._halt_align(stopped_by_user=True)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_all()
        super().hideEvent(event)

    def refresh(self) -> None:
        if not self.isVisible():
            return
        holding = (
            self._held is not None or self._align_pending or self._align_moving
        )
        if holding:
            if self._held is not None and not self._any_axis_btn_down():
                self._stop_jog_only()
            if (self._align_pending or self._align_moving) and not self.btn_align.isDown():
                self._on_align_released()
            if self._align_moving:
                self._poll_align_move()
            return
        err = self._locked()
        if self.lbl_lock.text() != err:
            self.lbl_lock.setText(err)
        enabled = not bool(err)
        if err:
            self._stop_all()
        can_jog = enabled and not self._align_moving
        for btn in self._axis_buttons():
            if btn.isDown():
                continue
            if btn.isEnabled() != can_jog:
                btn.setEnabled(can_jog)
        if not self.btn_align.isDown() and self.btn_align.isEnabled() != enabled:
            self.btn_align.setEnabled(enabled)
        robot = self._robot()
        try:
            pose = robot.get_actual_tcp_pose()
            joints = robot.get_actual_joint_pos()
            self._pose_cache = pose
            self._joint_cache = [float(v) for v in joints]
        except Exception as e:
            mock = "模拟" if robot.use_mock else "真机"
            text = f"{robot.name} [{mock}] 读位失败: {e}"
            if self.lbl_pose.text() != text:
                self.lbl_pose.setText(text)
            return
        tcp = "  ".join(f"{k}={float(pose.get(k, 0)):.1f}" for k in POSE_AXES)
        jtxt = "  ".join(f"J{i + 1}={float(joints[i]):.2f}" for i in range(6))
        mock = "模拟" if robot.use_mock else "真机"
        if self.btn_joint.isChecked():
            frame = "关节"
        elif self.btn_tool.isChecked():
            frame = "工具"
        else:
            frame = "基座"
        text = f"{robot.name} [{mock}]  {frame}\nTCP  {tcp}\n关节 {jtxt}"
        if self.lbl_pose.text() != text:
            self.lbl_pose.setText(text)

    def refresh_fast(self) -> None:
        if self.isVisible():
            self.refresh()


class JogPendantWindow(QMainWindow):
    """独立示教器：关窗只隐藏并停点动；记住大小位置，下次打开不重置。"""

    def __init__(self, coord: Coordinator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("jogPendantWin")
        self._allow_close = False
        self._geom_applied = False
        self.setWindowTitle(i18n.tr("nav.jog"))
        self.setMinimumSize(480, 720)
        self.setStyleSheet(_PENDANT_QSS)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self.panel = JogPendantPanel(coord)
        wrap = QWidget()
        wrap.setObjectName("jogPendantRoot")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.chk_top = QCheckBox("窗口置顶")
        self.chk_top.setChecked(True)
        self.chk_top.setToolTip("勾选后浮在主界面之上，可一边看示教点位一边点动")
        self.chk_top.toggled.connect(self._on_stay_top)
        top.addWidget(self.chk_top)
        top.addStretch(1)
        lay.addLayout(top)
        scroll = QScrollArea()
        scroll.setObjectName("jogPendantScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.panel)
        scroll.viewport().setStyleSheet("background:#1b2631;")
        lay.addWidget(scroll, 1)
        self.setCentralWidget(wrap)

        self._ui_timer = QTimer(self)
        self._ui_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._ui_timer.setInterval(120)
        self._ui_timer.timeout.connect(self.panel.refresh)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_geometry)

    def _on_stay_top(self, on: bool) -> None:
        geom = self.saveGeometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.restoreGeometry(geom)
        if self.isHidden():
            return
        self.show()

    def retranslate_ui(self) -> None:
        self.panel._refresh_selection_ui()

    def show_and_raise(self) -> None:
        if not self._geom_applied:
            self._restore_or_default_geometry()
            self._geom_applied = True
        self.show()
        self.raise_()
        self.activateWindow()
        if not self._ui_timer.isActive():
            self._ui_timer.start()
        self.panel._refresh_selection_ui()
        self.panel.refresh()

    def _restore_or_default_geometry(self) -> None:
        raw = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_GEOM_KEY)
        if raw:
            ok = self.restoreGeometry(raw)
            if ok and self._is_geometry_on_screen():
                return
        self._apply_default_geometry()

    def _apply_default_geometry(self) -> None:
        """首次打开：右侧近全高，避免每次再拖大。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(520, 880)
            return
        geo = screen.availableGeometry()
        w = min(560, max(500, int(geo.width() * 0.34)))
        h = min(geo.height() - 28, max(780, int(geo.height() * 0.92)))
        x = geo.x() + max(8, geo.width() - w - 16)
        y = geo.y() + 12
        self.setGeometry(x, y, w, h)

    def _is_geometry_on_screen(self) -> bool:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return True
        geo = screen.availableGeometry()
        rect = self.frameGeometry()
        inter = geo.intersected(rect)
        if inter.width() < 240 or inter.height() < 240:
            return False
        if rect.width() < 400 or rect.height() < 520:
            return False
        return True

    def _save_geometry(self) -> None:
        if not self._geom_applied:
            return
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_GEOM_KEY, self.saveGeometry())

    def _schedule_save_geometry(self) -> None:
        if self._geom_applied and self.isVisible():
            self._save_timer.start()

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_save_geometry()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_save_geometry()

    def shutdown(self) -> None:
        self._save_geometry()
        self._ui_timer.stop()
        self.panel.stop_all()
        self._allow_close = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_geometry()
        self._ui_timer.stop()
        self.panel.stop_all()
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.hide()
