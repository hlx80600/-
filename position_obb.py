import cv2
from ultralytics import YOLO
import math
from casbot_yolo_point4d.casbot_yolo_point4d_utils import shift_center_by_obb_scale, angle_to_vector, get_center_pose
import logging



class OBBOnlyDetector:
    """仅通过单个 OBB 模型完成检测的替换组件"""

    def __init__(self, obb_model_path):
        logging.getLogger("ultralytics").setLevel(logging.WARNING)  # 减少不必要的日志输出
        self.model = YOLO(obb_model_path)
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
        results = self.model.predict(
            image, conf=self.conf, imgsz=self.imgsz, iou=self.iou, verbose=False
        )

        yolo_p3d_results = []
        if not results or len(results) == 0:
            return yolo_p3d_results, image.copy()

        img_obb_show = results[0].plot()

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

                angle_rad = float(xywhr[i][4])
                degree = math.degrees(angle_rad)
                vector_x, vector_y = angle_to_vector(degree)

                center = ((x1 + x2 + x3 + x4) / 4, (y1 + y2 + y3 + y4) / 4)

                curr_shift = shift[class_id] if class_id < len(shift) else [0, 0]

                shift_point = shift_center_by_obb_scale(
                    center,
                    [x1, y1, x2, y2, x3, y3, x4, y4],
                    curr_shift,
                    (vector_x, vector_y),
                )

                flag, x_3d, y_3d, z_3d = get_center_pose(
                    shift_point[0], shift_point[1], depth, 20, fx, fy, cx, cy
                )


                if draw_result:
                    cv2.circle(
                        img_obb_show,
                        (int(shift_point[0]), int(shift_point[1])),
                        3,
                        (0, 0, 255),
                        -1,
                    )

                if not flag:
                    if draw_result:
                        cv2.putText(
                            img_obb_show,
                            "no depth",
                            (int(shift_point[0]), int(shift_point[1])),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 0, 255),
                            2,
                        )
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
                            [x1, y1, x2, y2, x3, y3, x4, y4],
                            degree,
                            vector_x,
                            vector_y,
                        ]
                    )
                else:
                    if draw_result:
                        text = f"ID:{class_id} X:{x_3d:.2f} Y:{y_3d:.2f} Z:{z_3d:.2f}"
                        cv2.putText(
                            img_obb_show,
                            text,
                            (int(shift_point[0]), int(shift_point[1]) - 15),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 255, 0),
                            2,
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
                            [x1, y1, x2, y2, x3, y3, x4, y4],
                            degree,
                            vector_x,
                            vector_y,
                        ]
                    )

        return yolo_p3d_results, img_obb_show
