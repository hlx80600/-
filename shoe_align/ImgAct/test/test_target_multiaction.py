"""Test TargetMultiActionDetect model build, forward pass, and loss."""
import torch
from ImgAct.nn.tasks import DetectionModel

print("=" * 60)
print("Test: TargetMultiActionDetect model build")
model = DetectionModel("target-multiaction-head.yaml", ch=3, nc=80, verbose=True)

# Check last module
last = model.model[-1]
print(f"\nLast module type: {type(last).__name__}")
print(f"  action_shape: {last.action_shape}")
print(f"  na (total action outputs): {last.na}")
print(f"  nc (object classes): {last.nc}")
print(f"  cv4 (action branches): {len(last.cv4)} scales")

# Forward pass
print("\n" + "=" * 60)
print("Test: Forward pass")
model.train()
x = torch.randn(2, 3, 640, 640)
out = model(x)
print(f"Training output type: {type(out)}")
if isinstance(out, dict):
    for k, v in out.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, torch.Tensor):
                    print(f"  {k}/{k2}: {v2.shape}")
                elif isinstance(v2, list):
                    print(f"  {k}/{k2}: list of {len(v2)} tensors, first shape={v2[0].shape}")
                else:
                    print(f"  {k}/{k2}: {type(v2)}")
        elif isinstance(v, torch.Tensor):
            print(f"  {k}: {v.shape}")
        else:
            print(f"  {k}: {type(v)}")

model.eval()
with torch.no_grad():
    out = model(x)
    if isinstance(out, tuple):
        y, preds = out
        print(f"\nInference output shape: {y.shape}")
        # y should be (batch, 4+nc+na, anchors) → (2, 4+80+9, anchors)
        print(f"Expected channels: 4(box) + 80(cls) + 9(action) = 93")
    else:
        print(f"\nInference output shape: {out.shape}")

print("\n" + "=" * 60)
print("All forward tests passed!")

# Test loss computation
print("\n" + "=" * 60)
print("Test: Loss computation")

# Set model args (normally done by trainer)
from ImgAct.cfg import get_cfg
model.args = get_cfg()

model.train()

# Create fake batch with detection + action labels
batch = {
    "img": torch.randn(2, 3, 640, 640),
    "batch_idx": torch.tensor([0, 0, 1]),  # 2 objects in img0, 1 in img1
    "cls": torch.tensor([[0], [1], [2]], dtype=torch.float),  # class labels
    "bboxes": torch.tensor([
        [0.5, 0.5, 0.1, 0.1],  # xywh normalized
        [0.3, 0.3, 0.2, 0.2],
        [0.7, 0.7, 0.15, 0.15],
    ]),
    "actions": torch.tensor([
        [0, 1, 2],  # object 0: X=-1, Y=0, Z=1
        [1, 0, 1],  # object 1: X=0, Y=-1, Z=0
        [2, 2, 0],  # object 2: X=1, Y=1, Z=-1
    ], dtype=torch.long),
}
preds = model(batch["img"])
loss, loss_items = model.loss(batch, preds)
print(f"Total loss: {loss.sum().item():.4f}")
print(f"Loss items (box, cls, dfl, action): {loss_items.tolist()}")
assert loss_items.shape[0] == 4, f"Expected 4 loss items, got {loss_items.shape[0]}"
assert loss.sum() > 0, "Loss should be > 0"
print("Loss test passed!")

print("\n" + "=" * 60)
print("All tests passed!")
