"""Quick test to verify DiscreteMultiActionHead classification model from YAML."""
import sys
sys.path.insert(0, "/home/casbotskill/ultralytics_all")

import torch
from ImgAct.nn.modules.head import DiscreteMultiActionHead

# Test 1: Model instantiation with 3 channels
print("=" * 60)
print("Test 1: Model instantiation from YAML (3ch input, 3 heads x 3 classes)")
model = DiscreteMultiActionHead(
    "/home/casbotskill/ultralytics_all/ImgAct/cfg/models/26/discrete-multiaction-head.yaml"
)
print(f"Model created successfully!")

# Check last module
last = model.model[-1]
print(f"Last module type: {type(last).__name__}")
print(f"  num_heads: {last.num_heads}")
print(f"  classes_per_head: {last.classes_per_head}")
print(f"  linears count: {len(last.linears)}")
assert last.num_heads == 3
assert last.classes_per_head == 3

# Test 2: Forward pass with 3-channel input
print("\n" + "=" * 60)
print("Test 2: Forward pass (3ch input)")
model.train()
x = torch.randn(4, 3, 640, 640)  # batch=4, RGB input
out_train = model(x)
print(f"Training output shape: {out_train.shape}")
assert out_train.shape == (4, 9), f"Expected (4, 9), got {out_train.shape}"

model.eval()
with torch.no_grad():
    out_eval = model(x)
    print(f"Eval output shape: {out_eval.shape}")
    assert out_eval.shape == (4, 9), f"Expected (4, 9), got {out_eval.shape}"
    print(f"Sample logits: {out_eval[0].tolist()}")

# Test 3: CrossEntropy classification loss computation
print("\n" + "=" * 60)
print("Test 3: CrossEntropy classification loss computation")
model.train()
criterion = model.init_criterion()
print(f"Criterion type: {type(criterion).__name__}")
out = model(x)
# Integer targets simulating class indices for each of the 3 heads
batch = {"cls": torch.randint(0, 3, (4, 3))}
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
print("All tests passed! Model is ready for discrete multi-action classification.")