from __future__ import annotations

import logging
import math
from typing import Any

import cv2

from vision.algo_deps import ensure_algo_paths, load_official_yolo
from vision.draw_overlay import draw_hud_lines, draw_obb_poly


def _p4d_utils() -> tuple[Any, Any, Any]:
    """延迟导入 point4d 几何工具，避免未 clone 时拖垮整个 HMI。"""
    ensure_algo_paths()
    from casbot_yolo_point4d.casbot_yolo_point4d_utils import (
        angle_to_vector,
        get_center_pose,
        shift_center_by_obb_scale,
    )

    return angle_to_vector, get_center_pose, shift_center_by_obb_scale


def _class_name(names: Any, class_id: int) -> str:
    """从 YOLO names 取类别名。"""
    if isinstance(names, dict):
        return str(names.get(class_id, names.get(str(class_id), class_id)))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


class OBBOnlyDetector:
    """仅通过单个 OBB 模型完成检测的替换组件"""

    def __init__(self, obb_model_path):
        logging.getLogger("ultralytics").setLevel(logging.WARNING)  # 减少不必要的日志输出
        # 皮带 ShoeVision 会把 ultralytics 别名到 obb360；压杆必须用官方 YOLO。
        self.model = load_official_yolo(obb_model_path)
        self.imgsz = 640
        self.conf = 0.5
        self.iou = 0.7

    def set_parameters(
        self, obb_img_size=640, obb_detection_conf=0.5, obb_iou_thres=0.5, **kwargs
    ):
        self.imgsz = obb_img_size
        self.conf = obb_detection_conf
        self.iou = obb_iou_thres

    def detect(self, image, depth, fx, fy, cx, cy, shift=[[0, 0]], draw_result=True):
        """检测 OBB 并可选叠图。

        叠图不用 ``results[0].plot()``：YOLO 默认实心底标签会挡住鞋面。
        框只描边，坐标写在左下角半透明条。
        """
        angle_to_vector, get_center_pose, shift_center_by_obb_scale = _p4d_utils()
        results = self.model.predict(
            image, conf=self.conf, imgsz=self.imgsz, iou=self.iou, verbose=False
        )

        yolo_p3d_results = []
        if not results or len(results) == 0:
            return yolo_p3d_results, image.copy()

        img_obb_show = image.copy()
        hud: list[str] = []
        names = getattr(results[0], "names", None) or getattr(self.model, "names", {}) or {}

        for result in results:
            if result.obb is None or len(result.obb) == 0:
                continue

            xyxyxyxy = result.obb.xyxyxyxy.cpu().numpy()
            xywhr = result.obb.xywhr.cpu().numpy()
            confs = result.obb.conf.cpu().numpy()
            classes = result.obb.cls.cpu().numpy()

            for i in range(len(xywhr)):
                class_id = int(classes[i])
                conf_val = float(confs[i])

                pts = xyxyxyxy[i]
                x1, y1 = pts[0]
                x2, y2 = pts[1]
                x3, y3 = pts[2]
                x4, y4 = pts[3]
                pts8 = [x1, y1, x2, y2, x3, y3, x4, y4]

                angle_rad = float(xywhr[i][4])
                degree = math.degrees(angle_rad)
                vector_x, vector_y = angle_to_vector(degree)

                center = ((x1 + x2 + x3 + x4) / 4, (y1 + y2 + y3 + y4) / 4)

                curr_shift = shift[class_id] if class_id < len(shift) else [0, 0]

                shift_point = shift_center_by_obb_scale(
                    center,
                    pts8,
                    curr_shift,
                    (vector_x, vector_y),
                )

                flag, x_3d, y_3d, z_3d = get_center_pose(
                    shift_point[0], shift_point[1], depth, 20, fx, fy, cx, cy
                )

                cls_name = _class_name(names, class_id)
                if draw_result:
                    draw_obb_poly(img_obb_show, pts8, (255, 180, 40), thickness=1)
                    cv2.circle(
                        img_obb_show,
                        (int(shift_point[0]), int(shift_point[1])),
                        3,
                        (0, 0, 255),
                        -1,
                        lineType=cv2.LINE_AA,
                    )

                if not flag:
                    if draw_result:
                        hud.append(f"{cls_name} {conf_val:.2f} no depth")
                    yolo_p3d_results.append(
                        [
                            False,
                            class_id,
                            conf_val,
                            shift_point[0],
                            shift_point[1],
                            -1,
                            -1,
                            -1,
                            pts8,
                            degree,
                            vector_x,
                            vector_y,
                        ]
                    )
                else:
                    if draw_result:
                        hud.append(
                            f"{cls_name} {conf_val:.2f} "
                            f"X:{x_3d:.2f} Y:{y_3d:.2f} Z:{z_3d:.2f}"
                        )
                    yolo_p3d_results.append(
                        [
                            True,
                            class_id,
                            conf_val,
                            shift_point[0],
                            shift_point[1],
                            x_3d,
                            y_3d,
                            z_3d,
                            pts8,
                            degree,
                            vector_x,
                            vector_y,
                        ]
                    )

        if draw_result and hud:
            draw_hud_lines(img_obb_show, hud, ok=True, anchor="bl")
        return yolo_p3d_results, img_obb_show
