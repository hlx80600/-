# 算法模块（本工程）

所有视觉算法的统一调用口。风格对齐框架 `algorithm_module` + 本工程 `visualize_module`。

```python
from algorithm_module import algo

# 生产
r = algo.detect_belt_pick(cameras, vis_cfg, z, rx, ry)
s = algo.classify_slot_occupied(img, vis_cfg)
t = algo.classify_toe_align(img, vis_cfg)
o = algo.measure_rod_offset_mm(cameras, vis_cfg)

# 投产
algo.write_intrinsics_from_calib(ctx, "cam1")
algo.solve_handeye_and_write(ctx, "cam1")
algo.apply_belt_pick(ctx)

# 采图训练
algo.capture_to_slot(ctx, "shoe_lr", "left")
algo.train_cmd("shoe_lr", epochs=40, device="0")   # GPU；CPU 用 device="cpu"
```

HMI「视觉采图」②：下拉 **训练设备** 可选「CPU（不加GPU）」/「GPU（CUDA:0）」；① 可安装 CPU 或 GPU 版 torch。

## 接口一览

### 生产（工位 1～5）

| 方法 | 用途 | 对应工位 |
|------|------|----------|
| `detect_belt_pick` | 皮带找鞋+左右+楦+手眼 → 取料位姿 | Station1 |
| `detect_belt_shoes_mock` | 屏蔽示教鞋位列表 | Station1 Mock |
| `classify_slot_occupied` | 槽有无鞋 | Station3/4 |
| `classify_toe_align` | 鞋头到位/向前 | Station2 |
| `measure_rod_offset_mm` | 压杆 XY 偏移（入参 cameras+cfg） | Station4→5 |
| `stack_status` / `model_status_text` / `listed_model_paths` | 环境与模型 | HMI |
| `reset_shoe_vision` | 改配置后清缓存 | 投产 |

### 投产（视觉采图 / 视觉调试）

| 方法 | 用途 |
|------|------|
| `write_intrinsics_from_calib` | 棋盘格内参写入 json |
| `write_roi_ratio_from_file` | ROI 写入 json |
| `record_handeye_sample` | 手眼采样点 |
| `solve_handeye_and_write` | 求解手眼 4×4 |
| `apply_belt_pick` / `move_robot1_to_pick` | 写入 PickPose / 试走 |
| `find_chessboard` / `calibrate_intrinsics` / `save_calib` | 内参标定 |
| `save_roi` / `load_roi` | ROI 文件 |

### 采图训练

| 方法 | 用途 |
|------|------|
| `capture_to_slot` / `bind_model` / `link_legacy_models` | 采图与挂模型 |
| `train_cmd` / `prepare_shoe_lr_crop` / `slots` | 训练与左右脚抠图 |

## 与 VisionService 的关系

- **算法**：`algorithm_module`（图像/配置进 → 结果出）
- **业务 I/O**：`VisionService`（取图、Mock、发布监控图、记 PickPose 副作用）
- 工位仍可调 `ctx.vision.photo_*`；内部已改为调用 `algo.*`，行为不变。

## 结构

```
algorithm_module/
  algorithm_module.py   # 门面 algo
  production.py         # 生产算法实现入口
  commission.py         # 投产
  tooling.py            # 采图训练
  results.py            # 结果类型
  readme.md
```
