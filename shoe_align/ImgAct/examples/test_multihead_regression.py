"""Quick test to verify MultiActionHead regression model (RGB + pseudo-color depth → XYZ)."""
import sys
sys.path.insert(0, "/home/casbotskill/ultralytics_all")

import torch
from ImgAct.nn.modules.head import MultiActionHead

# Test 1: Model instantiation with 6 channels
print("=" * 60)
print("Test 1: Model instantiation from YAML (6ch input, 3 regression outputs)")
model = MultiActionHead(
    "/home/casbotskill/ultralytics_all/ImgAct/cfg/models/26/multiaction-head.yaml"
)
print(f"Model created successfully!")

# Check last module
last = model.model[-1]
print(f"Last module type: {type(last).__name__}")
print(f"  num_heads: {last.num_heads}")
print(f"  classes_per_head: {last.classes_per_head}")
print(f"  linears count: {len(last.linears)}")
assert last.num_heads == 3
assert last.classes_per_head == 1

# Test 2: Forward pass with 6-channel input
print("\n" + "=" * 60)
print("Test 2: Forward pass (6ch input)")
model.train()
x = torch.randn(4, 6, 640, 640)  # batch=4, 6 channels (RGB + pseudo-color depth)
out_train = model(x)
print(f"Training output shape: {out_train.shape}")
assert out_train.shape == (4, 3), f"Expected (4, 3), got {out_train.shape}"

model.eval()
with torch.no_grad():
    out_eval = model(x)
    print(f"Eval output shape: {out_eval.shape}")
    assert out_eval.shape == (4, 3), f"Expected (4, 3), got {out_eval.shape}"
    print(f"Sample predictions (dx, dy, dz): {out_eval[0].tolist()}")

# Test 3: SmoothL1 regression loss computation
print("\n" + "=" * 60)
print("Test 3: SmoothL1 regression loss computation")
model.train()
criterion = model.init_criterion()
print(f"Criterion type: {type(criterion).__name__}")
out = model(x)
# Float targets simulating annotated dx, dy, dz values
batch = {"cls": torch.randn(4, 3) * 0.5}  # random targets in [-0.5, 0.5] range
loss, loss_detach = criterion(out, batch)
print(f"Loss: {loss.item():.6f}")
print(f"Loss detach: {loss_detach.item():.6f}")
assert loss.isfinite(), "Loss is not finite!"
assert loss.requires_grad, "Loss should require grad for backprop!"

# Test 4: Backward pass (gradient flow)
print("\n" + "=" * 60)
print("Test 4: Backward pass (gradient flow)")
loss.backward()
grad_norms = []
for name, param in model.named_parameters():
    if param.grad is not None:
        grad_norms.append((name, param.grad.norm().item()))
print(f"Parameters with gradients: {len(grad_norms)}")
assert len(grad_norms) > 0, "No gradients computed!"
print(f"Sample grad norms: {grad_norms[-3:]}")

print("\n" + "=" * 60)
print("All tests passed! Model is ready for RGB+pseudo-color → XYZ regression.")
