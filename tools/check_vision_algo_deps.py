#!/usr/bin/env python3
"""检查四槽是否具备双槽产线那三套视觉算法源码（不加载权重、尽量不 import torch）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.algo_deps import algo_dep_status, ensure_algo_paths, imgact_dir


def main() -> int:
    ensure_algo_paths()
    st = algo_dep_status()
    print(f"point4d : {'OK' if st['point4d'] else 'MISSING'}  {st['point4d_dir']}")
    print(f"obb360  : {'OK' if st['obb360'] else 'MISSING'}  {st['obb360_dir']}")
    print(f"ImgAct  : {'OK' if st['imgact'] else 'MISSING'}  {st['imgact_dir']}")
    print(st["message"])

    if st["imgact"]:
        try:
            from ImgAct.nn.modules.head import DiscreteMultiActionHead
            from ImgAct.models.casbot.action.model import DiscreteMultiActionModel

            print(
                "ImgAct import OK:",
                DiscreteMultiActionHead.__name__,
                DiscreteMultiActionModel.__name__,
                "from",
                imgact_dir(),
            )
        except Exception as exc:
            print(f"ImgAct 源码在，但 import 失败（缺 torch/cv2 也正常）: {exc}")

    if st["point4d"]:
        try:
            from casbot_yolo_point4d.casbot_yolo_point4d_utils import get_center_pose

            print("point4d_utils import OK:", get_center_pose.__name__)
        except Exception as exc:
            print(f"point4d_utils 源码在，但 import 失败（缺 numpy 也正常）: {exc}")
        try:
            from casbot_yolo_point4d.casbot_yolo_point4d import CasbotYoloP3D

            print("CasbotYoloP3D import OK:", CasbotYoloP3D.__name__)
        except Exception as exc:
            print(f"CasbotYoloP3D 源码在，但 import 失败（缺 torch 也正常）: {exc}")

    if st["obb360"]:
        try:
            from ultralytics_obb360 import YOLO as OBB360YOLO

            print("obb360 import OK:", OBB360YOLO.__name__)
        except Exception as exc:
            print(f"obb360 源码在，但 import 失败（缺 torch 也正常）: {exc}")

    ok = bool(st["point4d"] and st["obb360"] and st["imgact"])
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
