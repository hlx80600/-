# stations — 六工位流程

> 一条鞋从皮带拍照到压合出料的**步骤编排**；每站继承 `BaseStation`，由 `core/coordinator.py` 调度。

---

## 工位一览

| 文件 | 工位 | 职责 |
|------|------|------|
| `init_sequence.py` | 初始化 | 上电复位、回零、夹爪/压机就绪 |
| `station1_belt_photo.py` | Station1 | 皮带 YOLO 取料位 |
| `station2_robot1.py` | Station2 | 上料臂取鞋 → 放槽 |
| `station3_place_slot_photo.py` | Station3 | 放槽后拍照（可选） |
| `station4_pick_slot_photo.py` | Station4 | 取槽前拍照（可选） |
| `station5_robot2.py` | Station5 | 下料臂从槽取鞋 → 出料 |
| `station6_press_rotate.py` | Station6 | 压机转台 / 四槽节拍 |
| `toe_place_assist.py` | 辅助 | 鞋头对位辅助逻辑 |
| `step_catalog.py` | 目录 | 运动步名称与 HMI「运动步调试」映射 |

---

## 与视觉 / 设备的关系

- 拍照、手眼、PickPose：调 `ctx.vision.*`（见 [vision/readme.md](../vision/readme.md)）  
- 机械臂 / 夹爪 / 压机：调 `ctx.robots` / `ctx.grippers` / `ctx.press`（见 [devices/readme.md](../devices/readme.md)）  
- 算法门面：`algorithm_module`（见 [algorithm_module/readme.md](../algorithm_module/readme.md)）

---

## 阅读顺序

1. [docs/程序总览.md](../docs/程序总览.md) § 一条鞋流程  
2. `base_station.py`  
3. `station1` → `station2` → `station6` → `station5`  
4. HMI「运动步调试」对应 `step_catalog.py`
