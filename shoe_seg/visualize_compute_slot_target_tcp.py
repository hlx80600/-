from __future__ import annotations
import threading
import numpy as np
from scipy.spatial.transform import Rotation as Rot

import cv2

def start_cv2_vis_thread(vis_frame: np.ndarray | None, vis_stop_event: threading.Event) -> threading.Thread | None:
    """在独立线程中显示 shoe_detection OpenCV 窗口。"""
    if vis_frame is None:
        return None

    def _show_vis_frame() -> None:
        while not vis_stop_event.is_set():
            cv2.imshow("shoe_detection", vis_frame)
            key = cv2.waitKey(100) & 0xFF
            if key == ord("q"):
                break

    t = threading.Thread(target=_show_vis_frame, daemon=True)
    t.start()
    return t

def run_visualization(
    slot_xy: np.ndarray | None = None,
    z_heights: list[float] | None = None,
    center_line_dir: np.ndarray | None = None,
    toe_arc_base: np.ndarray | None = None,
    target_tcp: np.ndarray | None = None,
    flange_vec_in_tcp: np.ndarray | None = None,
    grab_tcp_xyz: np.ndarray | None = None,
    toe_arc_orig: np.ndarray | None = None,
    toe_lower_base: np.ndarray | None = None,
    toe_lower_orig: np.ndarray | None = None,
    move_buttons: list[tuple[str, object]] | None = None,
    transition_tcp: np.ndarray | None = None,
    transition_toe_base: np.ndarray | None = None,
    transition_toe_lower_base: np.ndarray | None = None,
    trajectory: list[np.ndarray] | None = None,
    traj_toe_arc_in_tcp: np.ndarray | None = None,
    traj_toe_lower_in_tcp: np.ndarray | None = None,
) -> bool:
    """在单独线程中运行 matplotlib 3D 可视化。

    图1: 目标TCP + 目标鞋头帘面（可选叠加鞋槽）
    图3 (可选): 轨迹动画 — 鞋头帘面与TCP沿轨迹运动
    """
    if toe_arc_base is None or target_tcp is None:
        raise ValueError("toe_arc_base and target_tcp are required")

    import matplotlib

    plt = None
    selected_backend = None
    backend_errors: list[tuple[str, str]] = []
    for candidate in ("TkAgg", "QtAgg"):
        try:
            matplotlib.use(candidate, force=True)
            import matplotlib.pyplot as _plt

            plt = _plt
            selected_backend = str(_plt.get_backend())
            break
        except Exception as exc:
            backend_errors.append((candidate, str(exc)))

    if plt is None:
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as _plt

        plt = _plt
        selected_backend = str(_plt.get_backend())
        print("[WARN] No interactive matplotlib backend available; button window will not be shown.")
        for name, msg in backend_errors:
            print(f"  backend {name} unavailable: {msg}")
        return False

    print(f"[VIS] matplotlib backend: {selected_backend}")
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    toe_arc_base = np.asarray(toe_arc_base, dtype=float)
    target_tcp = np.asarray(target_tcp, dtype=float)
    toe_lower_base = np.asarray(toe_lower_base, dtype=float) if toe_lower_base is not None else None
    transition_toe_base = np.asarray(transition_toe_base, dtype=float) if transition_toe_base is not None else None
    transition_toe_lower_base = (
        np.asarray(transition_toe_lower_base, dtype=float)
        if transition_toe_lower_base is not None
        else None
    )
    flange_vec = np.asarray(flange_vec_in_tcp, dtype=float) if flange_vec_in_tcp is not None else None

    slot_xy_arr = None
    has_slot_surface = slot_xy is not None and z_heights is not None and len(z_heights) > 0
    if has_slot_surface:
        slot_xy_arr = np.asarray(slot_xy, dtype=float)
        has_slot_surface = slot_xy_arr.ndim == 2 and slot_xy_arr.shape[0] > 0

    if has_slot_surface:
        n = len(slot_xy_arr)
        z_lo, z_hi = float(min(z_heights)), float(max(z_heights))
    else:
        z_samples = [toe_arc_base[:, 2], np.array([float(target_tcp[2])], dtype=float)]
        if toe_lower_base is not None and len(toe_lower_base) > 0:
            z_samples.append(toe_lower_base[:, 2])
        if transition_toe_base is not None and len(transition_toe_base) > 0:
            z_samples.append(transition_toe_base[:, 2])
        if transition_toe_lower_base is not None and len(transition_toe_lower_base) > 0:
            z_samples.append(transition_toe_lower_base[:, 2])
        z_all = np.concatenate(z_samples)
        z_lo, z_hi = float(np.min(z_all)), float(np.max(z_all))
        n = 0

    tcp_xyz = target_tcp[:3]
    R_target = Rot.from_euler("xyz", target_tcp[3:], degrees=True).as_matrix()

    def _draw_slot(ax: object) -> None:
        """绘制鞋槽帘面、拟合圆、中线。"""
        if not has_slot_surface or slot_xy_arr is None:
            return
        # 竖线
        for i in range(n):
            ax.plot(
                [slot_xy_arr[i, 0], slot_xy_arr[i, 0]],
                [slot_xy_arr[i, 1], slot_xy_arr[i, 1]],
                [z_lo, z_hi],
                "b-", alpha=0.4,
            )
        # 上下弧线
        for z in z_heights:
            ax.plot(slot_xy_arr[:, 0], slot_xy_arr[:, 1], [z] * n, "b-", linewidth=2)
        # 帘面填充
        for i in range(n - 1):
            verts = [
                [slot_xy_arr[i, 0], slot_xy_arr[i, 1], z_lo],
                [slot_xy_arr[i + 1, 0], slot_xy_arr[i + 1, 1], z_lo],
                [slot_xy_arr[i + 1, 0], slot_xy_arr[i + 1, 1], z_hi],
                [slot_xy_arr[i, 0], slot_xy_arr[i, 1], z_hi],
            ]
            poly = Poly3DCollection(
                [verts], alpha=0.12, facecolor="cyan", edgecolor="blue"
            )
            ax.add_collection3d(poly)
        # 鞋槽测量点
        for z in z_heights:
            ax.scatter(
                slot_xy_arr[:, 0], slot_xy_arr[:, 1], [z] * n,
                c="blue", s=40, marker="o", zorder=5,
            )

    def _draw_toe_arc_target(ax: object) -> None:
        """绘制鞋头帘面目标位置（上弧线 + 下线 + 帘面填充）。"""
        # 上弧线
        ax.scatter(
            toe_arc_base[:, 0], toe_arc_base[:, 1], toe_arc_base[:, 2],
            c="red", s=30, label="toe arc upper (target)",
        )
        ax.plot(
            toe_arc_base[:, 0], toe_arc_base[:, 1], toe_arc_base[:, 2],
            "r-", linewidth=1.5,
        )
        # 下线 + 帘面
        if toe_lower_base is not None and len(toe_lower_base) > 0:
            ax.scatter(
                toe_lower_base[:, 0], toe_lower_base[:, 1], toe_lower_base[:, 2],
                c="darkred", s=20, label="toe lower (target)",
            )
            ax.plot(
                toe_lower_base[:, 0], toe_lower_base[:, 1], toe_lower_base[:, 2],
                color="darkred", linewidth=1.5,
            )
            # 竖线连接上下
            for i in range(len(toe_arc_base)):
                ax.plot(
                    [toe_arc_base[i, 0], toe_lower_base[i, 0]],
                    [toe_arc_base[i, 1], toe_lower_base[i, 1]],
                    [toe_arc_base[i, 2], toe_lower_base[i, 2]],
                    "r-", alpha=0.3, linewidth=0.8,
                )
            # 帘面填充
            for i in range(len(toe_arc_base) - 1):
                verts = [
                    [toe_arc_base[i, 0], toe_arc_base[i, 1], toe_arc_base[i, 2]],
                    [toe_arc_base[i + 1, 0], toe_arc_base[i + 1, 1], toe_arc_base[i + 1, 2]],
                    [toe_lower_base[i + 1, 0], toe_lower_base[i + 1, 1], toe_lower_base[i + 1, 2]],
                    [toe_lower_base[i, 0], toe_lower_base[i, 1], toe_lower_base[i, 2]],
                ]
                poly = Poly3DCollection(
                    [verts], alpha=0.15, facecolor="salmon", edgecolor="red"
                )
                ax.add_collection3d(poly)
        else:
            # 无下线时仅画投影虚线
            ax.plot(
                toe_arc_base[:, 0], toe_arc_base[:, 1], [z_lo] * len(toe_arc_base),
                "r--", linewidth=1, alpha=0.3,
            )

    def _draw_tcp_target(ax: object) -> None:
        """绘制 TCP 目标位置。"""
        ax.scatter(
            [tcp_xyz[0]], [tcp_xyz[1]], [tcp_xyz[2]],
            c="magenta", s=120, marker="*", label="TCP target",
        )
        # TCP 到鞋头弧线质心的连线
        arc_centroid = np.mean(toe_arc_base, axis=0)
        ax.plot(
            [tcp_xyz[0], arc_centroid[0]],
            [tcp_xyz[1], arc_centroid[1]],
            [tcp_xyz[2], arc_centroid[2]],
            "m--", linewidth=1, alpha=0.5,
        )

    def _set_labels(ax: object) -> None:
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_zlabel("Z (mm)")
        ax.legend(loc="upper left")

    # ── 图 1: 目标位置特写与控制界面 ──
    fig2 = plt.figure(figsize=(14, 10))
    ax2 = fig2.add_subplot(111, projection="3d")
    ax2.set_title("Target: Slot + Toe Arc + TCP" if has_slot_surface else "Target: Toe Arc + TCP", fontsize=14)
    if has_slot_surface:
        _draw_slot(ax2)
    _draw_toe_arc_target(ax2)
    _draw_tcp_target(ax2)
    # ── 过渡位姿鞋头帘面 ──
    if transition_toe_base is not None:
        ax2.scatter(
            transition_toe_base[:, 0], transition_toe_base[:, 1], transition_toe_base[:, 2],
            c="limegreen", s=25, label="toe arc (transition)",
        )
        ax2.plot(
            transition_toe_base[:, 0], transition_toe_base[:, 1], transition_toe_base[:, 2],
            color="limegreen", linewidth=1.5,
        )
        if transition_toe_lower_base is not None and len(transition_toe_lower_base) > 0:
            ax2.plot(
                transition_toe_lower_base[:, 0], transition_toe_lower_base[:, 1],
                transition_toe_lower_base[:, 2],
                color="darkgreen", linewidth=1.5,
            )
            for i in range(len(transition_toe_base)):
                ax2.plot(
                    [transition_toe_base[i, 0], transition_toe_lower_base[i, 0]],
                    [transition_toe_base[i, 1], transition_toe_lower_base[i, 1]],
                    [transition_toe_base[i, 2], transition_toe_lower_base[i, 2]],
                    color="limegreen", alpha=0.3, linewidth=0.8,
                )
    if transition_tcp is not None:
        t_xyz = transition_tcp[:3]
        ax2.scatter(
            [t_xyz[0]], [t_xyz[1]], [t_xyz[2]],
            c="lime", s=100, marker="D", label="TCP transition",
        )
    # 自动缩放到目标区域附近
    margin = 30.0
    pts_list = [toe_arc_base, [tcp_xyz]]
    if toe_lower_base is not None and len(toe_lower_base) > 0:
        pts_list.append(toe_lower_base)
    if transition_toe_base is not None:
        pts_list.append(transition_toe_base)
    if transition_tcp is not None:
        pts_list.append([transition_tcp[:3]])
    all_pts = np.vstack(pts_list)
    x_min, x_max = float(all_pts[:, 0].min()) - margin, float(all_pts[:, 0].max()) + margin
    y_min, y_max = float(all_pts[:, 1].min()) - margin, float(all_pts[:, 1].max()) + margin
    z_min, z_max = min(z_lo, float(all_pts[:, 2].min())) - margin, float(all_pts[:, 2].max()) + margin
    ax2.set_xlim(x_min, x_max)
    ax2.set_ylim(y_min, y_max)
    ax2.set_zlim(z_min, z_max)
    _set_labels(ax2)

    # ── 按钮: 点击触发机械臂运动 ──
    _btns_keep = []  # prevent GC
    if move_buttons:
        from matplotlib.widgets import Button as MplButton
        n_btns = len(move_buttons)
        btn_w = min(0.28, 0.88 / n_btns)
        spacing = 0.90 / n_btns

        def _make_handler(btn_obj, callback, clicked_flag, repeatable=False):
            def handler(event):
                if repeatable:
                    callback()
                elif not clicked_flag[0]:
                    clicked_flag[0] = True
                    btn_obj.label.set_text("Moving ...")
                    fig2.canvas.draw_idle()
                    callback()
            return handler

        for i, (label, cb) in enumerate(move_buttons):
            x = 0.05 + i * spacing
            ax_btn = fig2.add_axes([x, 0.01, btn_w, 0.05])
            btn = MplButton(ax_btn, label)
            repeatable = ("Traj" in label)
            btn.on_clicked(_make_handler(btn, cb, [False], repeatable=repeatable))
            _btns_keep.append(btn)

    # ── 图 3: 轨迹动画 ──
    _anim_keep = None  # prevent GC
    if trajectory is not None and traj_toe_arc_in_tcp is not None:
        from matplotlib.animation import FuncAnimation

        fig3 = plt.figure(figsize=(12, 9))
        ax3 = fig3.add_subplot(111, projection="3d")

        # 静态: 鞋槽
        if has_slot_surface and slot_xy_arr is not None:
            for i_s in range(n):
                ax3.plot(
                    [slot_xy_arr[i_s, 0]] * 2, [slot_xy_arr[i_s, 1]] * 2, [z_lo, z_hi],
                    "b-", alpha=0.4,
                )
            for z in z_heights:
                ax3.plot(slot_xy_arr[:, 0], slot_xy_arr[:, 1], [z] * n, "b-", linewidth=2)
            for i_s in range(n - 1):
                verts = [
                    [slot_xy_arr[i_s, 0], slot_xy_arr[i_s, 1], z_lo],
                    [slot_xy_arr[i_s + 1, 0], slot_xy_arr[i_s + 1, 1], z_lo],
                    [slot_xy_arr[i_s + 1, 0], slot_xy_arr[i_s + 1, 1], z_hi],
                    [slot_xy_arr[i_s, 0], slot_xy_arr[i_s, 1], z_hi],
                ]
                ax3.add_collection3d(
                    Poly3DCollection([verts], alpha=0.12, facecolor="cyan", edgecolor="blue")
                )

        # 轴范围: 覆盖所有轨迹路点
        _all_pts = []
        for _wp in trajectory:
            _R = Rot.from_euler("xyz", _wp[3:], degrees=True).as_matrix()
            _tb = (_R @ traj_toe_arc_in_tcp.T).T + _wp[:3]
            _all_pts.append(_tb)
            _all_pts.append([_wp[:3]])
            if flange_vec is not None:
                _all_pts.append([_wp[:3] + _R @ flange_vec])
        _all_pts = np.vstack(_all_pts)
        _m = 30.0
        ax3.set_xlim(float(_all_pts[:, 0].min()) - _m, float(_all_pts[:, 0].max()) + _m)
        ax3.set_ylim(float(_all_pts[:, 1].min()) - _m, float(_all_pts[:, 1].max()) + _m)
        ax3.set_zlim(
            min(z_lo, float(_all_pts[:, 2].min())) - _m,
            float(_all_pts[:, 2].max()) + _m,
        )
        ax3.set_xlabel("X (mm)")
        ax3.set_ylabel("Y (mm)")
        ax3.set_zlabel("Z (mm)")

        _dyn: list = []

        def _anim_update(frame_idx):
            for _a in _dyn:
                _a.remove()
            _dyn.clear()

            wp = trajectory[frame_idx]
            R_f = Rot.from_euler("xyz", wp[3:], degrees=True).as_matrix()
            toe_b = (R_f @ traj_toe_arc_in_tcp.T).T + wp[:3]
            tcp_p = wp[:3]
            fl_p = tcp_p + R_f @ flange_vec if flange_vec is not None else None

            # 上弧线
            _dyn.append(ax3.scatter(
                toe_b[:, 0], toe_b[:, 1], toe_b[:, 2], c="red", s=30,
            ))
            _l, = ax3.plot(toe_b[:, 0], toe_b[:, 1], toe_b[:, 2], "r-", linewidth=1.5)
            _dyn.append(_l)

            # 下线 + 帘面
            if traj_toe_lower_in_tcp is not None:
                tl_b = (R_f @ traj_toe_lower_in_tcp.T).T + wp[:3]
                _dyn.append(ax3.scatter(
                    tl_b[:, 0], tl_b[:, 1], tl_b[:, 2], c="darkred", s=20,
                ))
                _ll, = ax3.plot(
                    tl_b[:, 0], tl_b[:, 1], tl_b[:, 2],
                    color="darkred", linewidth=1.5,
                )
                _dyn.append(_ll)
                for i_v in range(len(toe_b)):
                    _vl, = ax3.plot(
                        [toe_b[i_v, 0], tl_b[i_v, 0]],
                        [toe_b[i_v, 1], tl_b[i_v, 1]],
                        [toe_b[i_v, 2], tl_b[i_v, 2]],
                        "r-", alpha=0.3, linewidth=0.8,
                    )
                    _dyn.append(_vl)
                for i_v in range(len(toe_b) - 1):
                    _verts = [
                        [toe_b[i_v, 0], toe_b[i_v, 1], toe_b[i_v, 2]],
                        [toe_b[i_v + 1, 0], toe_b[i_v + 1, 1], toe_b[i_v + 1, 2]],
                        [tl_b[i_v + 1, 0], tl_b[i_v + 1, 1], tl_b[i_v + 1, 2]],
                        [tl_b[i_v, 0], tl_b[i_v, 1], tl_b[i_v, 2]],
                    ]
                    _poly = Poly3DCollection(
                        [_verts], alpha=0.15, facecolor="salmon", edgecolor="red",
                    )
                    ax3.add_collection3d(_poly)
                    _dyn.append(_poly)

            # TCP
            _dyn.append(ax3.scatter(
                [tcp_p[0]], [tcp_p[1]], [tcp_p[2]],
                c="magenta", s=120, marker="*",
            ))
            if fl_p is not None:
                _fl, = ax3.plot(
                    [tcp_p[0], fl_p[0]], [tcp_p[1], fl_p[1]], [tcp_p[2], fl_p[2]],
                    "m-", linewidth=2,
                )
                _dyn.append(_fl)

            ax3.set_title(
                f"Trajectory [{frame_idx}/{len(trajectory) - 1}]  "
                f"rx={wp[3]:.1f} ry={wp[4]:.1f} rz={wp[5]:.1f}",
                fontsize=13,
            )
            return _dyn

        _anim_keep = FuncAnimation(
            fig3, _anim_update, frames=len(trajectory),
            interval=300, repeat=True, blit=False,
        )

    plt.tight_layout()
    plt.show()
    return True


def start_visualization_thread(**kwargs) -> threading.Thread:
    """启动可视化线程。"""
    t = threading.Thread(target=run_visualization, kwargs=kwargs, daemon=True)
    t.start()
    return t
