# slot 配置字段说明

当前推荐通过 `shoe_seg/slot_config.py` 中的 `SlotConfig` dataclass 读取该文件。当前脚本默认优先读取 `press_shoes/config/slot.yaml`，并兼容旧路径 `press_shoes/config/slot.json`；文件内容统一按 YAML 格式维护和解析。

当前主流程使用 `target_point` 作为目标点字段；`slot_line` 主要保留给旧流程或旧采集脚本兼容使用。

## 字段说明

### `slot_xy_points`（可选 / 历史轮廓字段）
- **类型**: `list[[x, y]]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽弧形边缘的 XY 坐标点列表，至少 5 个点。采集顺序应沿弧形从一端到另一端连续排列。
- **当前用途**: 主要用于鞋槽轮廓显示、3D 可视化和几何参考；当前 `shoe_seg/compute_shoe_target_position.py` 的 C2 已不再对该字段做样条拟合，当前 `left_slot.yaml` / `right_slot.yaml` 也可以不再维护该字段。
- **采集方式**: `python press_shoes/scripts/collect_slot_xy.py --ip <arm_ip>`
- **备注**: 如果仍需兼容老脚本，例如依赖 `slot_xy_points` 的旧几何流程，可在历史配置中单独保留；当前主流程优先使用 `target_point`。

### `target_point`
- **类型**: `[x, y]`
- **单位**: mm（机器人基坐标系）
- **说明**: 当前主流程中鞋头弧线希望逼近的目标点，语义上复用了历史 `slot_line.point1`。
- **当前用途**: 当前 `shoe_seg/compute_shoe_target_position.py` 会直接把它作为 `target_point_xy`，用于 C2 目标点约束。
- **配置建议**: 如果想调节插入深浅，优先修改这个点。

### `z_heights`
- **类型**: `[z_min, z_max]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽壁的底部和顶部 z 坐标。用于定义鞋槽帘面的垂直范围。
- **示例**: `[-32.8, 24]`
- **采集方式**: 将机械臂 TCP 分别移到槽底和槽顶位置，读取 z 值。

### `slot_line`（旧流程兼容字段）
- **类型**: `object`
- **说明**: 历史上用于描述鞋槽中线，既提供方向，也提供目标点。当前主流程已将目标点拆分为独立的 `target_point` 字段。
- **子字段**:
  - `direction`: `[dx, dy]` — 中线方向单位向量，建议与 `point2 - point1` 同向，并指向鞋槽更深处
  - `normal`: `[nx, ny]` — 中线法向量
  - `point1`: `[x, y]` — 历史中线目标点，当前可迁移到 `target_point`
  - `point2`: `[x, y]` — 中线上的另一点，用于和 `point1` 一起定义中线方向
  - `c`: `float` — 直线方程常数项
  - `angle_deg`: `float` — 中线角度（度）
- **配置建议**:
  - 如果继续维护旧流程，`point1` 应放在你希望鞋头弧线与鞋槽中心线相交的位置。
  - `point2` 只需要与 `point1` 共线且能稳定定义方向，通常放在更深处。
  - `direction` 最好直接由 `point1 -> point2` 归一化得到，减少方向歧义。

### `shoe_origin_z_min`
- **类型**: `float`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋子放在传送带上时鞋底最低点的 z 坐标。用于构造鞋头帘面下线（与 `toe_arc` 相同 XY，z 固定为该值），并参与鞋头帘面上下边界确定。
- **要求**: 该字段为必填。缺失会导致流程无法完整构造鞋头帘面（无法生成 `toe_lower_in_tcp` 与帘面填充）。
- **采集方式**: 将机械臂 TCP 缓慢移动到鞋底最低点正上方并贴近测量位置，读取当前 TCP 的 z 值；建议同一只鞋重复采集 3 次取最小值或中位值以抑制抖动误差。
# slot 配置字段说明

当前推荐通过 `shoe_seg/slot_config.py` 中的 `SlotConfig` dataclass 读取该文件。当前脚本默认优先读取 `press_shoes/config/slot.yaml`，并兼容旧路径 `press_shoes/config/slot.json`；文件内容统一按 YAML 格式维护和解析。

当前主流程使用 `target_point` 作为目标点字段；`slot_line` 主要保留给旧流程或旧采集脚本兼容使用。

## 字段说明

### `slot_xy_points`（可选 / 历史轮廓字段）
- **类型**: `list[[x, y]]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽弧形边缘的 XY 坐标点列表，至少 5 个点。采集顺序应沿弧形从一端到另一端连续排列。
- **当前用途**: 主要用于鞋槽轮廓显示、3D 可视化和几何参考；当前 `shoe_seg/compute_shoe_target_position.py` 的 C2 已不再对该字段做样条拟合，当前 `left_slot.yaml` / `right_slot.yaml` 也可以不再维护该字段。
- **采集方式**: `python press_shoes/scripts/collect_slot_xy.py --ip <arm_ip>`
- **备注**: 如果仍需兼容老脚本，例如依赖 `slot_xy_points` 的旧几何流程，可在历史配置中单独保留；当前主流程优先使用 `target_point`。

### `target_point`
- **类型**: `[x, y]`
- **单位**: mm（机器人基坐标系）
- **说明**: 当前主流程中鞋头弧线希望逼近的目标点，语义上复用了历史 `slot_line.point1`。
- **当前用途**: 当前 `shoe_seg/compute_shoe_target_position.py` 会直接把它作为 `target_point_xy`，用于 C2 目标点约束。
- **配置建议**: 如果想调节插入深浅，优先修改这个点。

### `z_heights`
- **类型**: `[z_min, z_max]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽壁的底部和顶部 z 坐标。用于定义鞋槽帘面的垂直范围。
- **示例**: `[-32.8, 24]`
- **采集方式**: 将机械臂 TCP 分别移到槽底和槽顶位置，读取 z 值。

### `slot_line`（旧流程兼容字段）
- **类型**: `object`
- **说明**: 历史上用于描述鞋槽中线，既提供方向，也提供目标点。当前主流程已将目标点拆分为独立的 `target_point` 字段。
- **子字段**:
  - `direction`: `[dx, dy]` — 中线方向单位向量，建议与 `point2 - point1` 同向，并指向鞋槽更深处
  - `normal`: `[nx, ny]` — 中线法向量
  - `point1`: `[x, y]` — 历史中线目标点，当前可迁移到 `target_point`
  - `point2`: `[x, y]` — 中线上的另一点，用于和 `point1` 一起定义中线方向
  - `c`: `float` — 直线方程常数项
  - `angle_deg`: `float` — 中线角度（度）
- **配置建议**:
  - 如果继续维护旧流程，`point1` 应放在你希望鞋头弧线与鞋槽中心线相交的位置。
  - `point2` 只需要与 `point1` 共线且能稳定定义方向，通常放在更深处。
  - `direction` 最好直接由 `point1 -> point2` 归一化得到，减少方向歧义。
- **相关脚本**: [press_shoes/scripts/collect_slot_line.py](../scripts/collect_slot_line.py) 仍会采集并写回这个历史字段，默认目标仍是旧配置路径。

### `shoe_origin_z_min`
- **类型**: `float`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋子放在传送带上时鞋底最低点的 z 坐标。用于构造鞋头帘面下线（与 `toe_arc` 相同 XY，z 固定为该值），并参与鞋头帘面上下边界确定。
- **要求**: 该字段为必填。缺失会导致流程无法完整构造鞋头帘面（无法生成 `toe_lower_in_tcp` 与帘面填充）。
- **采集方式**: 将机械臂 TCP 缓慢移动到鞋底最低点正上方并贴近测量位置，读取当前 TCP 的 z 值；建议同一只鞋重复采集 3 次取最小值或中位值以抑制抖动误差。
- **采集判定建议**:
  - 采集点应位于鞋底真实最低区域，而非鞋边翘起或纹路凸起位置。
  - 采集姿态尽量与标定流程一致（同 TCP、同基坐标系、同工装状态）。
  - 若更换鞋型、治具高度或传送带基准后，应重新采集该值。
- **数据校验建议**:
  - 与历史值相比若突变超过 5-10 mm，应复测确认。
  - 建议保留 1 位到 2 位小数（例如 `16.5` 或 `16.52`）。
- **示例**: `16.5`

### `toe_z_offset`
- **类型**: `float`
- **单位**: mm
- **说明**: 鞋头最低点高于鞋槽最低 z 的目标偏移量。当前 `shoe_seg/compute_shoe_target_position.py` 会直接从 `SlotConfig` 读取该值参与 C4 高度约束求解。
- **默认值**: `20.0`

### `use_sam`
- **类型**: `bool`
- **说明**: 是否启用 SAM 分割以提取鞋头弧线点。当前 `shoe_seg/compute_shoe_target_position.py` 会直接从 `SlotConfig` 读取该值决定是否加载 SAM。
- **默认值**: `false`

### `gripper_refer_pose`
- **类型**: `list[[x, y, z, rx, ry, rz]]`
- **单位**: mm / deg（机器人基坐标系下位置 + 欧拉角）
- **说明**: 夹爪在鞋槽的参考位姿列表。当前流程至少使用前 2 个位姿来提取:
  - XY 方向线段约束（限制抓取目标落在线段附近）
  - RZ 角度范围约束（由两点 `rz` 推导）
- **要求**: 该字段为必填，且至少包含 2 个 6D 位姿。缺失会导致当前 `transition_tcp` / `target_tcp` 求解流程直接报错。
- **采集方式**: `python3 press_shoes/scripts/teach_gripper_refer_pose.py --ip <arm_ip>`
- **采集顺序要求（极度重要）**:
  - 前 2 个位姿必须严格按照 **“先在外侧，再深插到内侧（鞋槽底部）”** 的顺序进行示教。
  - 当前算法强依赖 P0 -> P1 的向量作为引导线的“前进方向”。当前主流程会直接复用这个方向作为中心线参考方向；如果顺序颠倒，会导致 C0 约束和 C2 目标方向假设相互冲突，解算结果可能明显偏离预期。
- **数据校验建议**:
  - 前 2 个位姿的 XY 不能重合（否则无法定义方向线段）。
  - 每个位姿应包含 6 个数值 `[x, y, z, rx, ry, rz]`。
  - 建议保持与实际抓取工位一致的工装和 TCP 配置后再采集。
- **示例**:
  - `[[420.1, 610.2, 120.5, 179.8, 0.2, -30.0], [305.4, 392.8, 119.9, 179.7, 0.1, -108.0]]`

## 运行时写回字段

# slot 配置字段说明

当前推荐通过 [shoe_seg/slot_config.py](../../shoe_seg/slot_config.py) 中的 SlotConfig 读取当前 YAML 配置。当前脚本默认优先读取 [press_shoes/config/slot.yaml](slot.yaml)，并兼容旧路径 [press_shoes/config/slot.json](slot.json)；文件内容统一按 YAML 格式维护和解析。

当前主流程使用 `target_point` 作为目标点字段。`slot_line` 已不再是 `SlotConfig` 的兼容输入，它只属于旧 JSON/旧脚本流程。

## 字段说明

### `slot_xy_points`（可选 / 历史轮廓字段）
- **类型**: `list[[x, y]]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽弧形边缘的 XY 坐标点列表，至少 5 个点。
- **当前用途**: 主要用于鞋槽轮廓显示、3D 可视化和几何参考；当前主流程不再依赖该字段。
- **采集方式**: `python press_shoes/scripts/collect_slot_xy.py --ip <arm_ip>`

### `target_point`
- **类型**: `[x, y]`
- **单位**: mm（机器人基坐标系）
- **说明**: 当前主流程中鞋头弧线希望逼近的目标点，语义上复用了历史 `slot_line.point1`。
- **当前用途**: 当前 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py) 会直接把它作为 `target_point_xy`，用于 C2 目标点约束。
- **配置建议**: 如果想调节插入深浅，优先修改这个点。

### `z_heights`
- **类型**: `[z_min, z_max]`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋槽壁的底部和顶部 z 坐标。

### `shoe_origin_z_min`
- **类型**: `float`
- **单位**: mm（机器人基坐标系）
- **说明**: 鞋子放在传送带上时鞋底最低点的 z 坐标。

### `toe_z_offset`
- **类型**: `float`
- **单位**: mm
- **说明**: 鞋头最低点高于鞋槽最低 z 的目标偏移量。

### `use_sam`
- **类型**: `bool`
- **说明**: 是否启用 SAM 分割以提取鞋头弧线点。

### `gripper_refer_pose`
- **类型**: `list[[x, y, z, rx, ry, rz]]`
- **单位**: mm / deg（机器人基坐标系下位置 + 欧拉角）
- **说明**: 夹爪在鞋槽的参考位姿列表。当前流程至少使用前 2 个位姿来提取 XY 方向线段约束和 RZ 角度范围约束。
- **采集顺序要求**:
  - 前 2 个位姿必须严格按照“先在外侧，再深插到内侧”的顺序进行示教。
  - 当前主流程直接复用这两个点的 XY 方向作为引导线方向。

## 运行时写回字段

### `toe_arc_in_tcp`
- **类型**: `list[[x, y, z]]`
- **单位**: mm（TCP 局部坐标系）
- **说明**: 鞋头弧线各点在 TCP 坐标系下的坐标。

### `toe_lower_in_tcp`
- **类型**: `list[[x, y, z]]`
- **单位**: mm（TCP 局部坐标系）
- **说明**: 鞋头下边界点列在 TCP 坐标系下的坐标。

## 旧流程字段

### `slot_line`
- **类型**: `object`
- **说明**: 历史上用于描述鞋槽中线，既提供方向，也提供目标点。
- **现状**: 当前 `SlotConfig` 已不再读取这个字段。它只对旧流程或直接操作原始 YAML/JSON 的旧脚本有意义。
- **相关脚本**:
  - [press_shoes/scripts/collect_slot_line.py](../scripts/collect_slot_line.py)
  - [press_shoes/scripts/shift_slot_line_x.py](../scripts/shift_slot_line_x.py)

### `flange_vec_in_tcp`
- **类型**: `[x, y, z]`
- **说明**: 历史兼容字段。当前各 YAML 配置文件已不再维护该字段。

## 完整示例（当前流程）

```yaml
z_heights:
  - -32.8
  - 24
target_point: [407.409, 650.2037]
shoe_origin_z_min: 16.5
toe_z_offset: 20.0
use_sam: true
gripper_refer_pose:
  - [420.1, 610.2, 120.5, 179.8, 0.2, -30.0]
  - [305.4, 392.8, 119.9, 179.7, 0.1, -108.0]
toe_arc_in_tcp:
  - [46.16, -189.17, 16.51]
  - ["..."]
toe_lower_in_tcp:
  - [46.16, -189.17, -20.5]
  - ["..."]
```
