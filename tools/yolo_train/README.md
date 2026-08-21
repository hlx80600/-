# YOLO 模型：旧模型 + 自训

本机**已经有旧压鞋机 `.pt`**（在 `压鞋机_旧/.../models`）。本工程 `models/` 已用软链接挂上当前配置要用的那几份；自训完成后装成 `custom_*.pt`，改路径即可切换，旧模型不会丢。

## 需要哪几个模型

| 用途 | 类型 | 默认路径 | 类约定 |
|------|------|----------|--------|
| 皮带找鞋 | OBB | `models/shoe_vision/7.23鞋obb.pt` | shoe |
| 左右脚 | 分类 | `models/shoe_vision/7.1鞋头朝上左右脚分类.pt` | left / right |
| 鞋楦中心 | OBB | `models/shoe_vision/7.24鞋楦obb.pt` | last |
| 鞋头对位 | 分类 | `models/toe_align/0722best.pt` | 0=到位, 1=向前 |
| 槽有无鞋 | 分类 | `models/slot_check/7.10slot_check.pt` | 0=空, 1=有鞋 |
| 压杆 | OBB | `models/position/rod/obb.pt` | rod |

建议**先用旧模型跑通**，再按相机场景自训/微调。分类（槽、对位、左右脚）最好训；OBB 标注量大，可先旧模型，再采本机图微调。

现场不必用命令行：HMI「视觉采图」点「左右脚采图/训练」可重采左右脚（自动抠鞋、鞋头朝上）并训练；也可挂旧模型、训其它分类、写 json 手眼、测皮带。本 README 仍是命令行备查。

## 0. 环境

本机当前没有 `ultralytics` / GPU。CPU 也能训分类，慢一些；OBB 建议有 NVIDIA 再训。

```bash
cd 莆田鞋厂四槽机器控制程序
python3 -m pip install --user --break-system-packages ultralytics torch torchvision
# 有 GPU 时再装对应 CUDA 版 torch（见 pytorch.org）
```

皮带 ShoeVision 完整链路还要旧栈（`casbot_yolo_point4d` / `ultralytics_obb360`），与旧工程同一 conda 最省事。槽分类、鞋头对位用标准 ultralytics 即可。

## 1. 挂上旧模型（已做过可跳过）

```bash
bash tools/yolo_train/link_legacy_models.sh
# 或指定旧 models 目录：
# bash tools/yolo_train/link_legacy_models.sh /path/to/Casbot_Press_Shoes-main/models
```

HMI「视觉调试」页应出现 ✓。然后装 ultralytics，取消该路 Mock，点测试。

## 2. 自训：推荐顺序

1. **slot_check**（最容易，立刻有用）  
2. **toe_align**  
3. **shoe_lr**（左右脚）  
4. **rod_obb / shoe_obb / last_obb**（要标旋转框）

### 2.1 采集

```bash
# 例：cam3 采槽图 100 张（真机取消 Mock 更好）
python3 tools/yolo_train/capture_dataset.py --task slot_check --cam cam3 --n 100

# 已有照片：
python3 tools/yolo_train/capture_dataset.py --task toe_align --from-dir ~/照片/鞋头
```

图在 `datasets/<task>/raw/`。

### 2.2 分类标注（文件夹即类别）

```bash
# 槽有无鞋
mkdir -p datasets/slot_check/train/{empty,has_shoe} datasets/slot_check/val/{empty,has_shoe}
# 把 raw/ 图按「空槽 / 有鞋」拷进对应文件夹；约 20% 放 val/

# 鞋头对位
mkdir -p datasets/toe_align/train/{aligned,forward} datasets/toe_align/val/{aligned,forward}

# 左右脚
mkdir -p datasets/shoe_lr/train/{left,right} datasets/shoe_lr/val/{left,right}
```

Ultralytics 按**文件夹名字母序**给类号。要保证：

- slot：`empty`→0、`has_shoe`→1（empty 排在 has 前）  
- toe：`aligned`→0、`forward`→1  
- 左右：类名含「左/右」或 `left`/`right`（程序按名字推断）

每类建议 ≥80～150 张，灯光/角度多变。

### 2.3 训分类

```bash
python3 tools/yolo_train/train_classify.py --task slot_check --epochs 80
# CPU 可加： --device cpu --batch 8
```

训完会拷到例如 `models/slot_check/custom_slot_check.pt`，并打印要改的配置项。

### 2.4 OBB 标注与训练

1. 采集：`capture_dataset.py --task shoe_obb`  
2. 用 [Roboflow](https://roboflow.com)、CVAT 或支持 YOLO-OBB 的标注工具标旋转框  
3. 导出到：

```text
datasets/shoe_obb/
  images/train|val/
  labels/train|val/   # 每行: cls x1 y1 x2 y2 x3 y3 x4 y4（归一化）
```

4. 训练：

```bash
python3 tools/yolo_train/train_obb.py --task shoe_obb --epochs 100
```

标准 `yolov8*-obb` 权重在 Casbot OBB 栈上需实测；不通时用旧 `.pt` 作基座微调，或继续用旧模型。

### 2.5 手动安装权重

```bash
python3 tools/yolo_train/install_model.py runs/classify/slot_check/weights/best.pt \
  --to models/slot_check/custom_slot_check.pt --task slot_check
```

## 3. 切换旧模型 ↔ 自训模型

**槽：** `config/default.yaml`

```yaml
vision:
  slot_check:
    model_path: models/slot_check/custom_slot_check.pt   # 或 7.10slot_check.pt
  toe_align:
    model_path: models/toe_align/custom_toe_align.pt     # 或 0722best.pt
  position:
    rod_model_path: models/position/rod/custom_obb.pt    # 或 obb.pt
```

**皮带：** `shoe_vision_config.json`

```json
"shoe_model_path": "models/shoe_vision/custom_鞋obb.pt",
"shoe_cls_model_path": "models/shoe_vision/custom_鞋头朝上左右脚分类.pt",
"shoe_tree_model_path": "models/shoe_vision/custom_鞋楦obb.pt"
```

改完重启程序。视觉页 ✓/✗ 会刷新。旧软链仍在，随时改回旧文件名。

## 4. 本机无 GPU 时

- 分类：`--device cpu`，先小图 `imgsz=224`、少 epoch 试通  
- OBB：标好数据，拷到有 GPU 的机器训，再把 `best.pt` 拷回来 `install_model.py`  
- 未训通前：该路 **Mock**；皮带用「屏蔽取料」

## 5. 目录速查

```text
models/                 运行时权重（旧软链 + custom_*.pt）
models/legacy/          指向旧工程整包 models
datasets/<task>/        采集与标注
runs/classify|obb/      训练输出
tools/yolo_train/       本工具
```
