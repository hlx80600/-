"""Train MultiActionHead regression model: RGB + pseudo-color depth → XYZ."""
import os
os.environ["ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS"] = "1"

import sys
sys.path.insert(0, "/home/casbotskill/ultralytics_all")

from ImgAct.models.casbot.action.multiaction_train import MultiActionHeadTrainer

args = {
    "model": "/home/casbotskill/ultralytics_all/ImgAct/cfg/models/26/multiaction-head.yaml",
    "pretrained": False,
    "data": "/home/casbotskill/ultralytics_all/dataset_h5",
    "epochs": 200,
    "batch": 16,      # 降低 batch 以防显存/内存溢出
    "workers": 4,    # 限制数据加载进程数
    "imgsz": 640,
    "num_heads": 3,
    "classes_per_head": 1,
    "channels": 6,
    "lr0": 0.001,
    "amp": False,
    "patience": 20,
    "project": "runs/reg",
    "name": "xyz_regression",
}

trainer = MultiActionHeadTrainer(overrides=args)
trainer.train()
