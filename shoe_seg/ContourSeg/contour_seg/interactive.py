"""交互式画框工具（基于 OpenCV 窗口）。"""

from __future__ import annotations

import cv2
import numpy as np


def draw_boxes_interactively(
    image_bgr: np.ndarray,
    window_name: str = "Draw Boxes  [drag=box  Enter=add  D=done  ESC=skip]",
) -> list[list[int]] | None:
    """交互式绘制多个矩形框。

    操作方式
    --------
    - **拖拽鼠标**：绘制当前框（红色）
    - **Enter / Space**：确认当前框（变绿），可继续绘制
    - **D / Q**：完成，返回所有框
    - **ESC**：跳过本张图片，返回 ``None``

    Parameters
    ----------
    image_bgr : np.ndarray
        BGR 格式图像。
    window_name : str
        OpenCV 窗口标题。

    Returns
    -------
    list[list[int]] or None
        ``[[x0, y0, x1, y1], ...]`` 格式的框列表，跳过时返回 ``None``。
    """
    confirmed: list[list[int]] = []
    state: dict = {"drawing": False, "start": None, "cur": None}
    canvas = image_bgr.copy()

    def redraw():
        nonlocal canvas
        canvas = image_bgr.copy()
        if state["cur"]:
            overlay = canvas.copy()
            b = state["cur"]
            cv2.rectangle(overlay, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), -1)
            cv2.addWeighted(overlay, 0.18, canvas, 0.82, 0, canvas)
        for i, b in enumerate(confirmed):
            cv2.rectangle(canvas, (b[0], b[1]), (b[2], b[3]), (0, 255, 0), 3)
            cv2.putText(canvas, str(i + 1), (b[0] + 4, b[1] + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        if state["cur"]:
            b = state["cur"]
            cv2.rectangle(canvas, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 4)
            cv2.circle(canvas, (b[0], b[1]), 5, (255, 255, 255), -1)
            cv2.circle(canvas, (b[2], b[3]), 5, (255, 255, 255), -1)
        info = f"Boxes: {len(confirmed)}  |  Drag=draw  Enter=add  D=done  ESC=skip"
        cv2.putText(canvas, info, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(canvas, info, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)

    redraw()

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state.update(drawing=True, start=(x, y), cur=None)
            redraw()
        elif event == cv2.EVENT_MOUSEMOVE and state["drawing"]:
            x0, y0 = state["start"]
            state["cur"] = [min(x0, x), min(y0, y), max(x0, x), max(y0, y)]
            redraw()
        elif event == cv2.EVENT_LBUTTONUP:
            state["drawing"] = False
            x0, y0 = state["start"]
            state["cur"] = [min(x0, x), min(y0, y), max(x0, x), max(y0, y)]
            redraw()

    print("  打开交互窗口，请到桌面弹出的 OpenCV 窗口中拖拽画框。")
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    try:
        cv2.moveWindow(window_name, 80, 80)
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    except cv2.error:
        pass
    cv2.setMouseCallback(window_name, on_mouse)
    while True:
        cv2.imshow(window_name, canvas)
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 32):  # Enter / Space
            if state["cur"]:
                confirmed.append(state["cur"])
                state["cur"] = None
                redraw()
        elif key in (ord("d"), ord("D"), ord("q"), ord("Q")):
            break
        elif key == 27:  # ESC
            cv2.destroyWindow(window_name)
            return None
    cv2.destroyWindow(window_name)
    return confirmed if confirmed else None


def click_quad_points_interactively(
    image_bgr: np.ndarray,
    window_name: str = "Click 4 Points  [left click=add  Z=undo  Enter=confirm  ESC=skip]",
) -> list[list[int]] | None:
    """交互式点击 4 个顶点定义一个四边形（OBB）框。

    操作方式
    --------
    - **左键单击**：依次添加顶点（最多 4 个，点满自动连线）
    - **Z**：撤销最后一个点
    - **Enter / Space**：确认当前四边形，进入下一个（可添加多个四边形）
    - **D / Q**：完成所有标注，返回
    - **ESC**：跳过本张图片，返回 ``None``

    Parameters
    ----------
    image_bgr : np.ndarray
        BGR 格式图像。
    window_name : str
        OpenCV 窗口标题。

    Returns
    -------
    list[list[int]] or None
        ``[[x0,y0, x1,y1, x2,y2, x3,y3], ...]`` 格式的四点框列表，
        跳过时返回 ``None``。
    """
    confirmed: list[list[int]] = []   # 已确认的四点框列表
    current_pts: list[tuple[int, int]] = []   # 当前正在标注的点
    cursor: tuple[int, int] = (0, 0)
    canvas = image_bgr.copy()

    COLORS = [
        (0, 120, 255),   # 橙
        (255, 50, 50),   # 蓝
        (50, 200, 50),   # 绿
        (180, 0, 220),   # 紫
    ]

    def redraw() -> None:
        nonlocal canvas
        canvas = image_bgr.copy()
        # 已确认的四边形
        for i, quad in enumerate(confirmed):
            pts = np.array(quad, dtype=np.int32).reshape(4, 2)
            cv2.polylines(canvas, [pts], isClosed=True, color=(0, 255, 0), thickness=2)
            for j, (px, py) in enumerate(pts):
                cv2.circle(canvas, (px, py), 6, (0, 255, 0), -1)
                cv2.putText(canvas, f"{i+1}-{j+1}", (px + 5, py - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        # 当前正在标注的点与连线
        for j, (px, py) in enumerate(current_pts):
            color = COLORS[j % len(COLORS)]
            cv2.circle(canvas, (px, py), 7, color, -1)
            cv2.putText(canvas, str(j + 1), (px + 5, py - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            if j > 0:
                cv2.line(canvas, current_pts[j - 1], (px, py), (0, 200, 255), 2)
        # 点够 4 个时闭合多边形并用半透明填充
        if len(current_pts) == 4:
            pts = np.array(current_pts, dtype=np.int32)
            overlay = canvas.copy()
            cv2.fillPoly(overlay, [pts], (0, 200, 255))
            cv2.addWeighted(overlay, 0.2, canvas, 0.8, 0, canvas)
            cv2.polylines(canvas, [pts], isClosed=True, color=(0, 200, 255), thickness=2)
        # 鼠标悬停时画引导线
        elif current_pts:
            cv2.line(canvas, current_pts[-1], cursor, (180, 180, 180), 1, cv2.LINE_AA)
        # 状态提示
        n = len(current_pts)
        tip = (
            f"Quad {len(confirmed)+1}: {n}/4 pts  |  "
            "Click=add  Z=undo  Enter=confirm  D=done  ESC=skip"
        )
        cv2.putText(canvas, tip, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.putText(canvas, tip, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)

    redraw()

    def on_mouse(event: int, x: int, y: int, flags: int, param) -> None:
        nonlocal cursor
        cursor = (x, y)
        if event == cv2.EVENT_MOUSEMOVE:
            redraw()
        elif event == cv2.EVENT_LBUTTONDOWN and len(current_pts) < 4:
            current_pts.append((x, y))
            redraw()

    print("  打开交互窗口，请到桌面弹出的 OpenCV 窗口中点击四个点。")
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    try:
        cv2.moveWindow(window_name, 80, 80)
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    except cv2.error:
        pass
    cv2.setMouseCallback(window_name, on_mouse)

    while True:
        cv2.imshow(window_name, canvas)
        key = cv2.waitKey(20) & 0xFF

        if key in (13, 32):  # Enter / Space — 确认当前四边形
            if len(current_pts) == 4:
                flat = [coord for pt in current_pts for coord in pt]
                confirmed.append(flat)
                current_pts.clear()
                redraw()
        elif key in (ord("z"), ord("Z")):  # Z — 撤销最后一个点
            if current_pts:
                current_pts.pop()
                redraw()
        elif key in (ord("d"), ord("D"), ord("q"), ord("Q")):  # D — 完成
            # 未确认但已有 4 点时自动确认
            if len(current_pts) == 4:
                flat = [coord for pt in current_pts for coord in pt]
                confirmed.append(flat)
            break
        elif key == 27:  # ESC — 跳过
            cv2.destroyWindow(window_name)
            return None

    cv2.destroyWindow(window_name)
    return confirmed if confirmed else None
