"""Train DiscreteMultiActionHead model: RGB -> per-axis {-1, 0, 1} classes."""
import os
os.environ["ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS"] = "1"

import sys
sys.path.insert(0, "/home/casbotskill/ultralytics_all")

from pathlib import Path

from ImgAct.models.casbot.action.discrete_multiaction_train import DiscreteMultiActionHeadTrainer

DATASET_ROOT = Path("/home/casbotskill/ultralytics_all/shoe_align_dataset_split")

if not DATASET_ROOT.exists():
    raise SystemExit(
        "Expected a discrete multi-head dataset with train/val/rgb and labels.csv at "
        f"{DATASET_ROOT}."
    )

args = {
    "model": "/home/casbotskill/ultralytics_all/ImgAct/cfg/models/26/discrete-multiaction-head.yaml",
    "pretrained": False,
    "data": str(DATASET_ROOT),
    "epochs": 300,
    "batch": 16,
    "workers": 0,
    "imgsz": 640,
    "num_heads": 3,
    "classes_per_head": 3,
    "head_names": ["X", "Y", "Z"],
    "lr0": 0.001,
    "amp": False,
    "patience": 300,
    "project": "/home/casbotskill/ultralytics_all/runs/discrete_cls",
    "name": "shoe_align_discrete_dataset_300e",
}

trainer = DiscreteMultiActionHeadTrainer(overrides=args)
trainer.train()