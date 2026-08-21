# ContourSeg

基于 [SAM (Segment Anything Model)](https://github.com/facebookresearch/segment-anything) 的目标外轮廓提取工具，支持**交互式画框**、**固定坐标框**和**全自动分割**三种工作模式，输出轮廓坐标 JSON 文件与可视化结果图。

---

## 目录结构

```
ContourSeg/
├── contour_seg/          # 核心包
│   ├── __init__.py       # 公开 API
│   ├── model.py          # SAM 模型加载
│   ├── core.py           # 分割、轮廓提取、主流程
│   ├── interactive.py    # 交互式画框（OpenCV 窗口）
│   ├── visualize.py      # 可视化工具
│   └── __main__.py       # CLI 入口
├── tests/
│   └── test_segment.py   # 单元 & 集成测试
├── shoes/                # 输入图片（不纳入 git）
├── weights/              # 模型权重（不纳入 git）
├── output/               # 运行输出（不纳入 git）
├── requirements.txt
└── pyproject.toml
```

---

## 安装

### 1. 安装依赖

```bash
# 建议先按官网选择适合 CUDA 版本的 PyTorch
# https://pytorch.org/get-started/locally/
pip install torch torchvision

# 安装 SAM
pip install git+https://github.com/facebookresearch/segment-anything.git

# 安装其余依赖
pip install -r requirements.txt
```

### 2. 下载模型权重

将权重文件放入 `weights/` 目录：

```bash
mkdir -p weights

# vit_b（推荐入门，375 MB）
wget -P weights/ https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth

# vit_h（最高质量，2.4 GB）
wget -P weights/ https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth
```

### 3. 安装本包（可选）

```bash
pip install -e .
```

---

## 使用方式

### 命令行

```bash
# 交互式拖拽矩形框（鼠标拖拽 → Enter 确认 → D 完成）
python -m contour_seg --input shoes/ --output output/ --draw-box

# 交互式点击四点 OBB 框（左键依次点 4 个顶点 → Enter 确认 → D 完成）
python -m contour_seg --input shoes/ --output output/ --click-quad

# 单张图片
python -m contour_seg --input shoes/img.jpg --output output/ --draw-box

# 固定框坐标（可多次 --box 指定多个框）
python -m contour_seg --input shoes/ --output output/ \
    --box 100 200 800 900

# 使用 vit_h 权重 + 分割后预览
python -m contour_seg --input shoes/ --output output/ --draw-box \
    --checkpoint weights/sam_vit_h_4b8939.pth --model-type vit_h --preview

# 全自动分割（不指定 --box / --draw-box）
python -m contour_seg --input shoes/ --output output/
```

### Python API

```python
from contour_seg import load_sam, pick_mask_by_box, extract_outer_contour
from segment_anything import SamPredictor
import cv2

sam = load_sam("weights/sam_vit_b_01ec64.pth", "vit_b")
predictor = SamPredictor(sam)

image_bgr = cv2.imread("shoes/sample.jpg")
image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

mask = pick_mask_by_box(image_rgb, predictor, [100, 200, 800, 900])
contour = extract_outer_contour(mask)   # shape (N, 2), int32
```

### 与 YOLO 联动（推荐）

可以用 YOLO 检测/OBB 模型先自动输出目标框，再将框坐标传给 `pick_mask_by_box` 实现全自动的"检测 → 分割 → 轮廓"流水线，无需手动画框。

**支持的 YOLO 输出格式**

| YOLO 任务 | 输出 | 说明 |
|-----------|------|------|
| `detect`（目标检测） | `xyxy` 水平框（4 值） | 直接传入 |
| `obb`（旋转框检测） | `xyxyxyxy` 四点框（8 值） | 直接传入，内部自动转换 |

**示例代码**

```python
from ultralytics import YOLO
from contour_seg import load_sam, pick_mask_by_box, extract_outer_contour
from contour_seg.visualize import visualize
from segment_anything import SamPredictor
import cv2
from pathlib import Path

# ── 加载模型 ─────────────────────────────────────────
yolo = YOLO("yolo11n.pt")          # 或 OBB 模型：yolo11n-obb.pt
sam  = load_sam("weights/sam_vit_b_01ec64.pth", "vit_b")
predictor = SamPredictor(sam)

# ── 推理单张图 ────────────────────────────────────────
img_path  = Path("shoes/sample.jpg")
image_bgr = cv2.imread(str(img_path))
image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

results = yolo(image_bgr, verbose=False)[0]

# ── 普通检测框（xyxy，4 值） ──────────────────────────
boxes = results.boxes.xyxy.cpu().numpy().tolist()   # [[x0,y0,x1,y1], ...]

# ── OBB 旋转框（xyxyxyxy，8 值） ─────────────────────
# OBB 四点框可直接传入，pick_mask_by_box 内部会自动转为外接矩形框
# boxes = results.obb.xyxyxyxy.cpu().numpy().reshape(-1, 8).tolist()

for i, box in enumerate(boxes):
    mask    = pick_mask_by_box(image_rgb, predictor, box)  # 支持 4 值或 8 值
    contour = extract_outer_contour(mask)
    if contour is not None:
        visualize(image_bgr, mask, contour, f"output/result_box{i+1}.jpg")
```

> **提示**：OBB 模型在目标有旋转角度时（）检测精度更高，框更贴合目标，
> 传入 SAM 后分割结果也更准确。

---

## 输出说明

每张图片（每个框）会生成两个文件：

| 文件 | 说明 |
|------|------|
| `{stem}_result.jpg` | 叠加 mask 和轮廓的可视化图 |
| `{stem}_contour.json` | 轮廓坐标及元数据 |

JSON 示例：

```json
{
  "image": "sample.jpg",
  "box_index": 1,
  "box_xyxy": [100, 200, 800, 900],
  "contour_points": [[x1, y1], [x2, y2], ...],
  "point_count": 312
}
```

---

## 测试

```bash
python -m pytest tests/ -v
# 或
python tests/test_segment.py
```

---

## 效果演示

以下示例使用**交互式四点 OBB 框**模式，对同一张图中的 5 个目标分别标注，一次推理输出合并可视化结果。

| 原图 | 分割结果（5 目标，不同颜色） |
|:----:|:----:|
| ![原图](docs/demo_input.jpg) | ![结果](docs/demo_result.jpg) |

> 运行命令：
> ```bash
> python -m contour_seg --input shoes/20260514-162329.jpg --output output/ \
>     --click-quad --checkpoint weights/sam_vit_b_01ec64.pth --model-type vit_b
> ```

---

## 一些特别的点

1. **三模式统一接口**  
   拖拽矩形框（`--draw-box`）、鼠标点击四点 OBB 框（`--click-quad`）、全自动分割三种模式共享同一套分割和输出逻辑，可按场景灵活切换，无需修改代码。

2. **四点 OBB 框直接输入**  
   `pick_mask_by_box` 自动识别 4 值水平框和 8 值四点框，内部完成外接矩形转换，对接 YOLO OBB 模型时零额外处理。

3. **多目标彩色合并输出**  
   多框分割结果用调色板循环着色，全部目标叠加在同一张图中，同时为每个目标单独保存 JSON 轮廓坐标，兼顾可视化直观性与数据可用性。

4. **YOLO 全流水线集成**  
   支持将 YOLO detect / OBB 模型的检测框坐标直接传入 SAM，实现"检测 → 分割 → 轮廓提取"全自动流水线，无需人工介入。

---

## 一些特别的解决办法

1. **白色目标与白色背景的分离**  
   目标与背景颜色相近时，自动分割模式容易选中整片背景。通过 `_mask_border_touch_count` 统计 mask 贴边数量、结合面积比过滤，将误选背景的概率降到最低。

2. **OBB 旋转框与 SAM box prompt 的适配**  
   SAM 只接受水平矩形框作为 prompt，OBB 四点框需转换为外接水平框。转换本身会引入冗余区域，可能导致 SAM 将框内无关区域也分割进来，需要配合形态学后处理修正。

3. **多目标不重叠着色**  
   多框 mask 在同一画布上叠加时，后绘制的颜色会覆盖先绘制的，导致重叠区域颜色混乱。采用逐层 `addWeighted` 半透明叠加方案，保留各目标边界的可见性。

4. **交互式四点框的实时引导反馈**  
   OpenCV 鼠标回调中需要在每次移动时重绘整帧画面，在高分辨率图像上频繁 `imshow` 会造成卡顿。通过限制重绘触发条件（仅在有标注点时才画引导线）降低 CPU 开销。

---

## 依赖

- Python ≥ 3.10
- PyTorch ≥ 1.13
- torchvision ≥ 0.14
- [segment-anything](https://github.com/facebookresearch/segment-anything)
- opencv-python ≥ 4.7
- numpy ≥ 1.23
