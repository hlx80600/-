"""End-to-end test: model build, forward, loss, dataset, trainer/validator/predictor imports."""
import os
import tempfile
import shutil
import numpy as np
from PIL import Image
import torch

print("=" * 60)
print("Test 1: Model build + forward + loss")

from ImgAct.nn.tasks import DetectionModel
from ImgAct.cfg import get_cfg

model = DetectionModel("target-multiaction-head.yaml", ch=3, nc=80, verbose=False)
model.args = get_cfg()
model.train()

# Forward
x = torch.randn(2, 3, 640, 640)
out = model(x)
print(f"  Forward OK: one2many actions shape = {out['one2many']['actions'].shape}")

# Loss
batch = {
    "img": x,
    "batch_idx": torch.tensor([0, 0, 1]),
    "cls": torch.tensor([[0], [1], [2]], dtype=torch.float),
    "bboxes": torch.tensor([[0.5, 0.5, 0.1, 0.1], [0.3, 0.3, 0.2, 0.2], [0.7, 0.7, 0.15, 0.15]]),
    "actions": torch.tensor([[0, 1, 2], [1, 0, 1], [2, 2, 0]], dtype=torch.long),
}
loss, loss_items = model.loss(batch, out)
print(f"  Loss OK: {loss_items.tolist()}")
assert loss_items.shape[0] == 4
print("  PASSED")

print("=" * 60)
print("Test 2: Dataset")

# Create a temp dataset structure
tmpdir = tempfile.mkdtemp()
img_dir = os.path.join(tmpdir, "images", "train")
lbl_dir = os.path.join(tmpdir, "labels", "train")
os.makedirs(img_dir)
os.makedirs(lbl_dir)

# Create fake images and labels
for i in range(4):
    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    img.save(os.path.join(img_dir, f"img_{i:04d}.jpg"))
    # Label: class x y w h a0 a1 a2
    with open(os.path.join(lbl_dir, f"img_{i:04d}.txt"), "w") as f:
        f.write(f"0 0.5 0.5 0.3 0.3 {i%3} {(i+1)%3} {(i+2)%3}\n")
        if i > 0:
            f.write(f"1 0.2 0.2 0.1 0.1 1 1 1\n")

from ImgAct.data.dataset import TargetMultiActionDataset

data_cfg = {
    "names": {0: "cat", 1: "dog"},
    "nc": 2,
    "action_shape": [3, 3],
}
ds = TargetMultiActionDataset(
    img_path=img_dir,
    imgsz=640,
    batch_size=2,
    augment=False,
    hyp=get_cfg(),
    rect=False,
    cache=False,
    single_cls=False,
    stride=32,
    pad=0.5,
    prefix="test: ",
    task="detect",
    classes=None,
    data=data_cfg,
    fraction=1.0,
)
print(f"  Dataset created: {len(ds)} samples")

sample = ds[0]
print(f"  Sample keys: {sorted(sample.keys())}")
assert "actions" in sample, "Missing 'actions' in sample"
print(f"  Actions shape: {sample['actions'].shape}")
print(f"  Bboxes shape: {sample['bboxes'].shape}")
print("  PASSED")

# Test collate
batch = TargetMultiActionDataset.collate_fn([ds[0], ds[1]])
print(f"  Collated batch keys: {sorted(batch.keys())}")
assert "actions" in batch
print(f"  Collated actions shape: {batch['actions'].shape}")
print("  PASSED")

print("=" * 60)
print("Test 3: Trainer/Validator/Predictor imports")

from ImgAct.models.yolo.target_multiaction import (
    TargetMultiActionTrainer,
    TargetMultiActionValidator,
    TargetMultiActionPredictor,
)
print("  Imports OK")
print("  PASSED")

# Cleanup
shutil.rmtree(tmpdir)

print("=" * 60)
print("ALL TESTS PASSED!")
