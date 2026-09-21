"""Quick test to verify MultiActionHead model can be instantiated and forward pass works."""
import torch
from ImgAct.nn.tasks import ClassificationModel

# Test 1: Model instantiation
print("=" * 60)
print("Test 1: Model instantiation from YAML")
model = ClassificationModel("yolo26n-multiaction-cls.yaml", ch=3, nc=9, verbose=True)
print(f"Model created successfully!")

# Check last module
last = model.model[-1]
print(f"Last module type: {type(last).__name__}")
print(f"  num_heads: {last.num_heads}")
print(f"  classes_per_head: {last.classes_per_head}")
print(f"  linears count: {len(last.linears)}")

# Test 2: Forward pass
print("\n" + "=" * 60)
print("Test 2: Forward pass")
model.train()
x = torch.randn(4, 3, 224, 224)
out_train = model(x)
print(f"Training output shape: {out_train.shape}")
assert out_train.shape == (4, 9), f"Expected (4, 9), got {out_train.shape}"

model.eval()
with torch.no_grad():
    out_eval = model(x)
    probs, logits = out_eval
    print(f"Eval probs shape: {probs.shape}")
    print(f"Eval logits shape: {logits.shape}")

    # Verify per-head softmax sums to 1
    reshaped = probs.view(4, 3, 3)
    sums = reshaped.sum(dim=2)
    print(f"Per-head probability sums (should be ~1.0): {sums[0].tolist()}")

# Test 3: Loss computation
print("\n" + "=" * 60)
print("Test 3: Loss computation")
model.train()
criterion = model.init_criterion()
print(f"Criterion type: {type(criterion).__name__}")
out = model(x)
batch = {"cls": torch.randint(0, 3, (4, 3))}  # 4 samples, 3 heads, 3 classes
loss, loss_detach = criterion(out, batch)
print(f"Loss: {loss.item():.4f}")
print(f"Loss detach: {loss_detach.item():.4f}")

print("\n" + "=" * 60)
print("All tests passed!")
