# vision — 视觉业务层

> 相机取流、标定、手眼、VisionService；**算法实现**在 `algorithm_module/`（门面 `algo`）。

---

## 主要文件

| 文件 | 作用 |
|------|------|
| `vision_service.py` | 业务入口：取图、Mock、调 `algo`、发监控帧 |
| `vision_journal.py` | 生产快照落盘、运送回写、HMI 历史列表（`logs/vision_snaps/`） |
| `camera_orbbec.py` | Orbbec 四路相机（需 `pyorbbecsdk`） |
| `commission_actions.py` | HMI 标定动作：内参、手眼采样、PickPose 写入 |
| `handeye_solve.py` | 手眼矩阵求解 |
| `calib.py` | 棋盘格内参 |
| `roi.py` | ROI 读写 `config/roi/` |
| `pixel_to_robot.py` | 像素 → 机器人坐标 |
| `legacy_pipeline.py` | YOLO / 旧管线实现 |
| `monitor_frames.py` | 监控帧发布 |
| `template_match.py` / `shape_match.py` | 模板 / 形状匹配 |
| `belt_toe.py` / `cls_crop.py` | 皮带鞋头、分类裁切 |
| `model_store.py` | 模型路径解析 |

---

## 配置与数据

| 路径 | 内容 |
|------|------|
| `config/default.yaml` → `cameras` / `vision` | serial、手眼参数、`save_runtime_snaps` |
| `logs/vision_snaps/` | 生产拍照原图/叠图/`meta.json`；当日索引 `index_YYYY-MM-DD.jsonl` |
| `config/roi/camN.json` | 各相机 ROI |
| `config/calib/` | 内参、手眼采样备份 |
| `shoe_vision_config.json` | 生产用 cam1 皮带配置 |
| `models/` | YOLO 权重（见 [models/readme.md](../models/readme.md)） |

---

## 运行快照

生产 `photo_belt_pick` / `photo_place_slot` / `photo_pick_slot`（默认 `persist=True`）把当时图和检测写入 `logs/vision_snaps/`。Station2 放料完成写 `transport.place`，Station3 判定写 `slot_check`，Station5 下料完成写 `unload`。监控 `compute_monitor(..., persist=False)` 不存。

JPEG 文件名含相机与时间，例如 `cam1_20260828_140455_635_belt_pick_raw.jpg`。当日索引为 `index_YYYY-MM-DD.jsonl`。

HMI：`hmi/pages/vision_snap_page.py` 挂在 **报警记录** 第四个页签「运行快照」；可打开文件夹、翻历史图、看运送回写。配置键 `vision.save_runtime_snaps`、`vision.snap_keep_days`。

现场说明：[docs/界面操作手册.md](../docs/界面操作手册.md) §14.4；软件内「使用说明」专章「报警记录 · 运行快照」。

---

## 阅读顺序

1. [algorithm_module/readme.md](../algorithm_module/readme.md) — API 与调用图  
2. `vision_service.py`  
3. HMI 视觉工作区：`hmi/pages/vision_workspace.py`  
4. 现场操作：[docs/界面操作手册.md](../docs/界面操作手册.md) § 视觉 / 手眼
