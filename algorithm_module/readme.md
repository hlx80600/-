# algorithm_module — 视觉算法统一接口（新人请先读本文）

> **本文件是读本工程视觉相关代码的入口说明。**  
> 先建立「谁调谁、输入输出是什么」，再下钻到 `vision/`、`stations/`、`hmi/`。

---

## 1. 一句话是什么

`algorithm_module` 把本工程所有**视觉算法**收成一个门面对象：

```python
from algorithm_module import algo
```

约定：

| 层 | 职责 | 典型代码 |
|----|------|----------|
| **algorithm_module** | 算法：图/配置 → 结果（尽量无副作用） | `algo.detect_belt_pick(...)` |
| **VisionService** | 业务 I/O：取相机、Mock、发监控图、写 PickPose | `ctx.vision.photo_belt_pick(...)` |
| **stations/** | 工位流程：何时拍照、何时 MoveL | `stations/station1_*.py` |
| **visualize_module** | 监控窗取流/显示（不算算法） | 相机监控窗口 |

工位**优先**调 `ctx.vision.photo_*`（已在内部调 `algo`）；脚本/新代码可直接调 `algo`。

---

## 2. 建议阅读顺序（第一次打开工程）

1. **本文**（接口与调用图）  
2. `algorithm_module/results.py` — 结果结构字段含义  
3. `vision/vision_service.py` — 取图 + 调 `algo` + `publish_vis`  
4. `stations/station1_belt_photo.py` → `station2_robot1.py` → `station3/4/5` — 何时调用视觉  
5. `vision/legacy_pipeline.py` — YOLO/手眼真正实现（重、可后读）  
6. HMI「使用说明」标签 — 各页引用关系  
7. `docs/操作说明.md` — 安装与联调摘要  

入口程序：`main.py` → `core/coordinator.py` 扫描各 Station。

---

## 3. 整机视觉在流程里干什么

```
皮带 cam1 ──detect_belt_pick──► Station1 得到 PickPose（基座 mm）
                                      │
                                      ▼
上料臂 Station2 取料 → 放到鞋槽 → cam2 classify_toe_align 对位 → 压跟 → 张爪
                                      │
                              Station3 cam3 classify_slot_occupied（放料槽有无鞋）
                                      │
                              Station6 压合+旋转
                                      │
                              Station4 cam4 有无鞋 + measure_rod_offset_mm
                                      │
                              Station5 下料臂取鞋 → 皮带放料（记产量）
```

相机职责：

| 相机 | 算法接口 | 工位 |
|------|----------|------|
| cam1 皮带 | `detect_belt_pick` / Mock `detect_belt_shoes_mock` | Station1 |
| cam2 鞋头 | `classify_toe_align` | Station2（`toe_place_assist`） |
| cam3 放料槽 | `classify_slot_occupied` | Station3 |
| cam4 取料槽 | `classify_slot_occupied` + `measure_rod_offset_mm` | Station4→5 |

---

## 4. 目录结构

```
algorithm_module/
  __init__.py           # 对外导出 AlgorithmModule / algo / 结果类型
  algorithm_module.py   # 门面类 AlgorithmModule，单例 algo
  production.py         # 生产：找鞋 / 槽 / 鞋头 / 压杆
  commission.py         # 投产：内参 ROI 手眼 PickPose 试走
  tooling.py            # 采图训练 / 挂模型 / CUDA 检测
  results.py            # BeltPickResult / SlotResult / …
  readme.md             # 本文
```

底层实现不在本目录，而在：

| 本模块方法大致落到 | 真实实现文件 |
|--------------------|--------------|
| 生产检测 | `vision/legacy_pipeline.py`、`vision/template_match.py` |
| 投产写配置 | `vision/commission_actions.py`、`vision/calib.py`、`vision/roi.py`、`vision/handeye_solve.py` |
| 采图训练 | `vision/model_store.py`、`vision/cls_crop.py`、`tools/yolo_train/*.py` |

---

## 5. 怎么调用（最小示例）

```python
from algorithm_module import algo, BeltPickResult, SlotResult, ToeAlignResult, RodOffsetResult

# —— 生产（通常已有 cameras 与 vis_cfg=ctx.cfg["vision"]）——
r: BeltPickResult = algo.detect_belt_pick(cameras, vis_cfg, z=120.0, default_rx=180.0, default_ry=0.0)
if r.ok:
    print(r.x, r.y, r.rz, r.is_left_shoe, r.toe_offset_in_grasp_tcp)

s: SlotResult = algo.classify_slot_occupied(image_bgr, vis_cfg)   # image: np.ndarray HxWx3 BGR
t: ToeAlignResult = algo.classify_toe_align(image_bgr, vis_cfg)
o: RodOffsetResult = algo.measure_rod_offset_mm(cameras, vis_cfg)  # 或传 image_bgr=缓存帧

# —— 投产（需要 AppContext ctx）——
algo.write_intrinsics_from_calib(ctx, "cam1")
algo.solve_handeye_and_write(ctx, "cam1", assumed_z_mm=400.0)
algo.apply_belt_pick(ctx)                      # 写入 runtime PickPose
algo.move_robot1_to_pick(ctx, above=True)      # MoveL 试走（真机注意安全）

# —— 采图训练 ——
algo.capture_to_slot(ctx, "shoe_lr", "left")
cmd = algo.train_cmd("shoe_lr", epochs=40, device="cpu")  # 或 device="0" 用 GPU
# cmd 是 argv 列表，由 HMI QProcess 启动；不要在算法线程里直接阻塞训很久
```

兼容旧类名：`algorithmModule` 等于 `AlgorithmModule`。

---

## 6. 结果类型（`results.py`）

### `BeltPickResult` — 皮带取料

| 字段 | 类型 | 含义 |
|------|------|------|
| `ok` | `bool` | 是否成功 |
| `x,y,z,rx,ry,rz` | `float` | 机器人基座毫米 / 度（与示教器一致） |
| `is_left_shoe` | `bool` | 左脚？ |
| `message` | `str` | 人读说明 / 失败原因 |
| `source` | `str` | 如 `legacy_yolo_handeye` / `shield_mock` |
| `toe_offset_in_grasp_tcp` | `list[float] \| None` | 鞋头相对抓取 TCP，常 `[0, Y, 0]` mm |
| `shoe_length_mm` | `float` | 鞋长估计 |
| `vis_bgr` | `np.ndarray \| None` | 可视化图 BGR，供监控叠加 |

### `SlotResult` — 槽有无鞋

| 字段 | 类型 | 含义 |
|------|------|------|
| `ok` | `bool` | 分类是否跑通 |
| `has_material` | `bool` | 有鞋？ |
| `is_left_slot` | `bool \| None` | 左右槽（多数路径不填，流程用记忆） |
| `confidence` | `float` | 置信度 |
| `message` / `vis_bgr` | | 同上 |

### `ToeAlignResult` — 鞋头对位

| 字段 | 类型 | 含义 |
|------|------|------|
| `ok` | `bool` | |
| `aligned` | `bool` | True=到位可停；False=需相对 MoveL 推进 |
| `label` | `str` | 原始类名（如 `0`/`1`） |
| `message` / `vis_bgr` | | |

### `RodOffsetResult` — 压杆偏移

| 字段 | 类型 | 含义 |
|------|------|------|
| `ok` | `bool` | |
| `dx,dy,dz` | `float` | 基座毫米偏移，叠到示教 `slot_pick` |
| `message` / `vis_bgr` | | |

---

## 7. 生产接口详表

### `algo.detect_belt_pick(cameras, vis_cfg, default_z, default_rx, default_ry) -> BeltPickResult`

- **输入**  
  - `cameras`：`dict`，至少含 `cam1`（`OrbbecCamera`），内部会 `grab`/读深度。  
  - `vis_cfg`：`dict | None`，一般是 `ctx.cfg["vision"]` + `shoe_vision_config.json` 相关路径。  
  - `default_z/rx/ry`：`float`，深度/姿态缺省（常与 `belt_pick_mock` 一致）。  
- **输出**：基座取料位姿 + 左右脚 + 鞋头偏移。  
- **实现**：`production.detect_belt_pick` → `vision.legacy_pipeline.detect_belt_legacy`。  
- **谁调用**：`VisionService.photo_belt_pick` / `_compute_monitor_cached(cam1)` → **Station1**。  
- **注意**：会碰相机；监控实时推演应走 VisionService 的 `from_cache` 路径（`prefer_last`），避免抢帧。

### `algo.detect_belt_shoes_mock(vis_cfg) -> list`

- **输入**：`vis_cfg`，读 `belt_pick_mock` 示教鞋列表。  
- **输出**：鞋位对象列表（含 x/y/角度/左右）。  
- **实现**：`vision.template_match.detect_belt_shoes_mock`。  
- **谁调用**：`photo_belt_pick` 在 cam1 Mock 时；HMI「屏蔽取料」。

### `algo.classify_slot_occupied(image_bgr, vis_cfg=None) -> SlotResult`

- **输入**：`image_bgr: np.ndarray` shape `(H,W,3)` BGR；`vis_cfg` 含 slot 模型路径。  
- **输出**：有无鞋。  
- **谁调用**：`photo_place_slot`（cam3）/ `photo_pick_slot`（cam4）→ Station3/4；监控推演。

### `algo.classify_toe_align(image_bgr, vis_cfg=None) -> ToeAlignResult`

- **输入**：cam2 BGR 图。  
- **输出**：`aligned` / `label`。  
- **谁调用**：`VisionService.guide_place_edge`；**`stations/toe_place_assist.py`**（Station2 放料对位）。

### `algo.measure_rod_offset_mm(cameras, vis_cfg=None, image_bgr=None) -> RodOffsetResult`

- **输入**：默认从 cam4 `grab`；若传 `image_bgr` 则用该帧（监控缓存推演）。  
- **输出**：`dx,dy,dz` mm。  
- **谁调用**：`photo_pick_slot` 有鞋时；Station5 叠加取料点。  
- **配置**：`position_config.yaml` + `vision.position`。

### `algo.measure_rod_offset_tuple(...) -> (ok, dx, dy, dz, vis, msg)`

旧元组返回，兼容 `VisionService.test_rod_offset`。

### `algo.stack_status() / model_status_text(vis_cfg) / listed_model_paths(vis_cfg)`

环境与模型是否齐套；HMI「检查 YOLO」「视觉调试」状态栏。

### `algo.reset_shoe_vision() -> None`

清旧 ShoeVision 单例缓存；改模型/手眼后调用。

---

## 8. 投产接口详表

均需 **`ctx: AppContext`**（有 `cameras`、`cfg`、`robot1`、`vision`）。

| 方法 | 输入要点 | 输出 | 写哪里 / 副作用 | 谁调用 |
|------|----------|------|-----------------|--------|
| `write_intrinsics_from_calib(ctx, camera_id)` | 已有棋盘格标定文件 | `str` 说明 | `shoe_vision_config.json` 内参 | 视觉采图 |
| `write_roi_ratio_from_file(ctx, camera_id)` | `config/roi/camN.json` | `str` | json `roi_ratio` | 视觉采图 |
| `record_handeye_sample(ctx, camera_id, use_center=)` | 当前图+TCP | `str` | `config/calib/*_handeye_samples.json` | 视觉采图/调试 |
| `solve_handeye_and_write(ctx, camera_id, assumed_z_mm=)` | ≥3 采样点 | `str` | samples + `handeye.mat` + calib json | 视觉采图 |
| `apply_belt_pick(ctx)` | 先测皮带成功 | `(result, msg)` | 写入 runtime PickPose | 视觉采图 |
| `move_robot1_to_pick(ctx, above=)` | 已有 PickPose | `str` | **真机 MoveL** | 视觉采图试抓 |
| `checklist_lines(ctx)` | | `list[str]` | 无 | 视觉采图检查清单 |
| `find_chessboard(image_bgr, cols, rows)` | BGR 图 | 角点结果 | 无 | 视觉调试 |
| `calibrate_intrinsics(...)` / `save_calib` / `load_calib` | | 内参 dict / 路径 | `config/calib/*_intrinsics.json` | 视觉调试 |
| `save_roi` / `load_roi` | | | `config/roi/camN.json` | 视觉调试 |

封装文件：`commission.py` → `vision/commission_actions.py` 等。

---

## 9. 采图训练接口详表

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `slots()` | | `dict` | 任务槽元数据（与 `model_store.SLOTS` 一致） |
| `capture_to_slot(ctx, slot_id, cls_name="", to_val=False)` | 槽位 id、类名 | `Path` | 拍 1 张写入 datasets |
| `prepare_shoe_lr_crop(ctx, img_bgr)` | BGR | `(crop\|None, note)` | 左右脚鞋头朝上抠图 |
| `bind_model(ctx, slot_id, src, copy_to_default=True)` | `.pt` 路径 | `str` 相对路径 | 写入 yaml / 拷到 models |
| `link_legacy_models(old_dir="")` | 旧 models 目录 | `str` 日志 | 挂接旧工程权重 |
| `train_cmd(slot_id, epochs=80, device="cpu", batch=None)` | `device`: `cpu` / `0` / `0,1` | `list[str]` argv | 不直接训练；返回命令行 |
| `cuda_train_status()` | | `dict` | 是否 CUDA、卡名、提示文案 |
| `pip_ultralytics_cmd(with_cuda=False, cuda_tag="cu124")` | | `list[str]` | pip 安装命令（HMI 进程跑） |

常见 `slot_id`：

| slot_id | 类型 | 用途 |
|---------|------|------|
| `shoe_obb` | OBB | 皮带找鞋 |
| `last_obb` / 楦相关 | OBB | 楦心 |
| `rod` / position | OBB | 压杆 |
| `shoe_lr` | 分类 | 左右脚（鞋头朝上） |
| `toe_align` | 分类 | 鞋头对位 |
| `slot_check` | 分类 | 槽有无鞋 |

训练脚本：`tools/yolo_train/train_obb.py`、`train_classify.py`。  
HMI：「视觉采图」页选 **训练设备** CPU/GPU，再「开始训练」。

---

## 10. 调用关系图（谁引用 algorithm_module）

```
stations/station1_belt_photo.py
    └─ ctx.vision.photo_belt_pick()
         └─ algo.detect_belt_pick / detect_belt_shoes_mock

stations/toe_place_assist.py  (Station2)
    └─ algo.classify_toe_align(img, vis)

stations/station3_place_slot_photo.py
    └─ ctx.vision.photo_place_slot()
         └─ algo.classify_slot_occupied

stations/station4_pick_slot_photo.py
    └─ ctx.vision.photo_pick_slot()
         └─ algo.classify_slot_occupied
         └─ algo.measure_rod_offset_mm

vision/vision_service.py
    └─ 上述 photo_* / guide_* / compute_monitor / test_*
    └─ 几乎所有生产 algo 调用的汇聚点

hmi/pages/zero_to_pick_page.py（视觉采图）
    └─ vision.model_store / commission_actions
    └─ 与 algo.train_cmd / capture / 投产方法等价能力

hmi/pages/vision_monitor_page.py
    └─ vision.compute_monitor(from_cache=True)
         └─ algo.*（缓存帧）
```

**注意**：多数 HMI 仍直接调 `vision.commission_actions` / `model_store`；`algo` 的投产/训练方法是同一能力的门面，脚本与新代码请优先走 `algo`。

---

## 11. 相关配置文件

| 文件 | 用途 |
|------|------|
| `config/default.yaml` → `vision` | 模型路径、Mock、toe_align、cameras |
| `shoe_vision_config.json` | cam1 ROI 比例、内参、手眼 4×4（生产） |
| `config/roi/camN.json` | 绿框 ROI |
| `config/calib/*` | 棋盘格内参、手眼采样与矩阵备份 |
| `position_config.yaml` | 压杆测量 |
| `models/**` | `.pt` 权重 |
| `datasets/**` | 本机采图训练数据 |

---

## 12. 与 visualize_module 的边界

| | algorithm_module | visualize_module |
|--|------------------|------------------|
| 目的 | 算出位姿/类别/偏移 | 取流、画框、Qt 显示 |
| 单例 | `algo` | `viz` |
| 被谁用 | VisionService、工位、采图 | 主要是相机监控窗 |

监控「实时推演」= LiveComputeLoop 调 `VisionService.compute_monitor(from_cache=True)` → 内部再调 `algo`。

---

## 13. 依赖（跑真算法）

- 基础：见根目录 `requirements.txt`（PySide6、opencv-headless、numpy…）  
- 真检测/训练：`ultralytics`、`torch`（GPU 用 CUDA wheel）、旧栈若皮带完整链路还需 `casbot_yolo_point4d` / `ultralytics_obb360` 等（与旧工程环境一致）  
- 相机：`pyorbbecsdk`；机器人：`fairino`  

未装齐时：`stack_status` / `photo_*` 会失败或走 Mock，接口仍可 import。

---

## 14. 编码约定（本组）

- 默认 Python；非必要不用 C++。  
- 类名 `PascalCase`（`AlgorithmModule`）；函数/变量 `snake_case`。  
- 新代码参数与返回值写 type hint；图像优先标明 `np.ndarray` / `NDArray[np.uint8]`。  
- 详见 `.cursor/rules/python-coding-standards.mdc`。

---

## 15. 下一步去哪改代码

| 你想改… | 去哪 |
|---------|------|
| 检测逻辑 / YOLO 后处理 | `vision/legacy_pipeline.py`、`shoe_vision_seg.py` |
| 工位何时拍照、失败怎么办 | `stations/station*.py` |
| 取图、Mock、监控图 | `vision/vision_service.py` |
| 只加一个可调用算法 API | 在 `production.py`（或 commission/tooling）实现，再在 `AlgorithmModule` 挂一层 |
| HMI 按钮 | `hmi/pages/*`；说明文案 `hmi/help_content.py` |

---

**导入入口：**

```python
from algorithm_module import (
    algo,
    AlgorithmModule,
    BeltPickResult,
    SlotResult,
    ToeAlignResult,
    RodOffsetResult,
)
```
