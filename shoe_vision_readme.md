# ShoeVision（压鞋机）视觉封装说明

本模块封装了“鞋子/鞋楦检测 + Orbbec 相机取流 + 手眼标定(cam->base)坐标换算”的一条完整链路，核心入口为 [shoe_vision.py](shoe_vision.py)。

你通常会用它来：

- 实时取流推理并可视化左右脚、鞋楦中心点与方向
- 输出基座坐标系下的抓取点位 `(x, y, z, yaw_deg)`

## 目录与关键文件

- [shoe_vision.py](shoe_vision.py)：主封装（`ShoeVision` 类 + 工具函数）
- [shoe_vision_config.json](shoe_vision_config.json)：默认配置（模型路径/相机参数/handeye 等）
- [init.sh](init.sh)：拉取依赖仓库（私有仓库需权限）

## 环境与依赖

### 必备

- Python 3.8+（建议 conda 环境）
- `numpy`、`opencv-python`
- `ultralytics`（YOLO 推理）
- Orbbec 相机与其运行依赖（由 [RSDT_Simple_Automation](RSDT_Simple_Automation/readme.md) 中的相机驱动调用）

### 代码依赖仓库

本目录通过 [init.sh](init.sh) 拉取以下依赖（默认使用 GitHub SSH 地址，需要你有仓库权限并配置 SSH key）：

- `RSDT_Simple_Automation`
- `casbot_yolo_point4d`（内部会再 clone `casbot_yolo_obb360`）

## 初始化与安装

在本目录执行：

```bash

# 1) 拉取依赖仓库（私有仓库需权限）
bash init.sh

# 2) 安装 Python 依赖（按需补齐）
pip install -r RSDT_Simple_Automation/requirements.txt
pip install ultralytics
```

说明：`ultralytics` 会依赖 PyTorch；如果你的环境里还没有 torch，请按你的 CUDA/CPU 场景安装对应版本。

## 配置说明（shoe_vision_config.json）

默认会读取同目录下的 [shoe_vision_config.json](shoe_vision_config.json)。你也可以通过环境变量 `SHOE_VISION_CONFIG` 指向其它配置文件。

### 配置字段

- `shoe_model_path`：鞋子 OBB 检测模型（Ultralytics YOLO，OBB）
- `shoe_cls_model_path`：可选，鞋子分类模型（Ultralytics YOLO，`task=classify`），用于判断左右脚
- `shoe_tree_model_path`：鞋楦 OBB/点位模型（供 `CasbotYoloP3D` 使用）
- `shoe_tree_cls_model_path`：鞋楦分类模型（供 `CasbotYoloP3D` 使用）
- `conf` / `iou`：鞋子 OBB 检测阈值
- `shift`：鞋楦模型原始 OBB 局部坐标系下的偏移比例（在 `CasbotYoloP3D` 内生效）
- `shoe_tree_center_offset`：基于“修正后的鞋楦正方向”的二次中心点偏移，支持 `[x, y]`
	或按类别配置 `[[x0, y0], [x1, y1], ...]`

- `camera.{fx,fy,cx,cy}`：彩色相机内参（用于像素点反投影成相机坐标）

- `orbbec.cam_id` / `orbbec.cam_serial`：相机标识与序列号
- `orbbec.color_res`：彩色分辨率 `[w, h]`
- `orbbec.depth_res`：深度分辨率 `[w, h]`
- `orbbec.fps`：帧率

- `handeye.mat`：4x4 手眼矩阵（cam->base）
- `handeye.path`：或用文件路径加载 4x4 矩阵（与 `mat` 二选一）

### 单位与坐标系

- 深度 `z` 的单位由相机 SDK 决定（本项目常见为 mm）
- `base_center`/返回的 `(x,y,z)` 与 `handeye` 平移量单位应一致（通常也是 mm）
- `yaw_deg` 为角度（度），归一化到 $[0, 360)$
- `shoe_tree_center_offset[0]` 表示沿修正后鞋楦正方向的 X 偏移，`shoe_tree_center_offset[1]`
	表示其垂直方向 Y 偏移；当前单位为图像像素

## 快速运行


### 1) 直接运行脚本（实时取流，返回一次结果）
运行前需更改配置文件的相关参数

```bash
cd /home/zs/workspace/Casbot_Press_Shoes
python shoe_vision.py
```

脚本默认：

- `vision = ShoeVision.from_config_file()`
- 调用 `get_all_shoe_points()` 获取一帧左右脚基座位姿列表并打印

### 2) 实时可视化（按 q 退出）

在 [shoe_vision.py](shoe_vision.py) 的 `__main__` 中取消注释 `vision.run_live_visualization()`，或在交互脚本中调用：

```python
from shoe_vision import ShoeVision

vision = ShoeVision.from_config_file()
vision.run_live_visualization(window_name="ShoeVision")
```

如果运行环境没有 GUI（例如纯 SSH 无转发），`imshow` 会失败并打印提示。

## 作为模块使用（API）

> 注意：示例里的 `from shoe_vision import ShoeVision` 依赖当前工作目录在本文件同级，或你已把该目录加入 `PYTHONPATH`。

### `ShoeVision.from_config_file(path=None)`

从配置文件构建 `ShoeVision`：加载 YOLO 模型、初始化相机（单例复用）、加载 handeye。

### `run_obb_demo(image, depth=None) -> (left_list, right_list)`

对单帧图像执行完整推理链路（鞋子 OBB -> 抠图 -> 可选分类 -> 鞋楦点位/方向 -> 回投原图）。

- `image`：图片路径或 `numpy` BGR
- 返回 `left_list/right_list`：元素为 dict，常用字段：
	- `side`：`left`/`right`
	- `shoe_tree_center`：`(x, y)` 原图像素中心
	- `shoe_tree_degree`：原图坐标系角度（度）
	- `shoe_tree_conf`：鞋楦检测置信度
	- 以及 `det_conf/cls_name/cls_conf` 等调试信息

### `get_all_shoe_points() -> (left_base_poses, right_base_poses)`

实时取流并推理，计算基座坐标系结果后返回：

- `left_base_poses/right_base_poses`：`List[Tuple[x, y, z, yaw_deg]]`

该函数要求：

- 已成功连接相机
- 配置中存在 `handeye`（否则会 `RuntimeError`）

## 常见问题（排错）

- 报错 `相机连接失败/相机对象未找到`：检查 `orbbec.cam_id`、`orbbec.cam_serial` 是否正确；确认 Orbbec SDK 与驱动环境就绪。
- 没有检测结果：确认模型路径存在且与任务匹配（OBB/分类）；适当调低 `conf` 或检查光照/遮挡。
- `缺少 handeye 矩阵`：在配置里提供 `handeye.mat` 或 `handeye.path`；且注意单位一致。
- 运行在无显示环境：不要调用 `run_live_visualization`，或在带 GUI 的环境运行。

## 开发备注

- 相机对象按 `(cam_id, cam_serial)` 在进程内做单例复用，避免重复连接。
- `automationMachine()` 也会做单例缓存；如果你在同一进程里创建多个 `ShoeVision`，会复用同一套硬件模块。

