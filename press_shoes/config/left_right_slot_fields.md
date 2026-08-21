# left_slot.yaml / right_slot.yaml 参数说明

[left_slot.yaml](left_slot.yaml) 和 [right_slot.yaml](right_slot.yaml) 用于分别保存左槽、右槽的放鞋几何配置。两份文件字段结构相同，只是数值会因左右槽实测标定结果不同而不同。

当前推荐通过 [shoe_seg/slot_config.py](../../shoe_seg/slot_config.py) 中的 SlotConfig 读取这两份 YAML。需要注意的是，`arm_ip` 属于额外保留字段，不是 SlotConfig 的正式 dataclass 字段，但会被保留在原始 YAML 中，不会影响解析。

当前 left/right 槽配置已经不再维护 `slot_xy_points` 和 `slot_line`。当前主流程直接依赖 `target_point` 和 `gripper_refer_pose`，因此左右槽 YAML 里只保留当前解算真正需要的字段。

## 字段分组

这两份配置里的字段可以分成三类：

- 手工维护字段：示教、测量或标定后写入，例如 `arm_ip`、`target_point`、`z_heights`、`shoe_origin_z_min`、`toe_z_offset`、`use_sam`、`gripper_refer_pose`
- 运行时写回字段：由 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py) 计算后回写，例如 `toe_arc_in_tcp`、`toe_lower_in_tcp`
- 历史兼容说明：例如 `slot_line`、`flange_vec_in_tcp` 这类字段已不再写入当前 YAML，如需兼容旧数据流程请单独保留历史配置

## 参数说明

# left_slot.yaml / right_slot.yaml 参数说明

[left_slot.yaml](left_slot.yaml) 和 [right_slot.yaml](right_slot.yaml) 用于分别保存左槽、右槽的放鞋几何配置。两份文件字段结构相同，只是数值会因左右槽实测标定结果不同而不同。

当前推荐通过 [shoe_seg/slot_config.py](../../shoe_seg/slot_config.py) 中的 SlotConfig 读取这两份 YAML。需要注意的是，`arm_ip` 属于额外保留字段，不是 SlotConfig 的正式 dataclass 字段，但会被保留在原始 YAML 中，不会影响解析。

当前 left/right 槽配置已经不再维护 `slot_xy_points` 和 `slot_line`。当前主流程直接依赖 `target_point` 和 `gripper_refer_pose`，并且 `SlotConfig` 已不再兼容读取旧 `slot_line`。

## 字段分组

- 手工维护字段：例如 `arm_ip`、`target_point`、`z_heights`、`shoe_origin_z_min`、`toe_z_offset`、`use_sam`、`gripper_refer_pose`
- 运行时写回字段：例如 `toe_arc_in_tcp`、`toe_lower_in_tcp`
- 历史兼容字段：例如 `slot_line`、`flange_vec_in_tcp`。这些字段不再出现在当前 YAML 中，如需兼容旧流程请单独维护历史配置。

## 参数说明

### arm_ip
- 类型：字符串
- 示例：`192.168.57.2` 或 `fake`
- 作用：记录当前配置对应的机械臂 IP，便于示教脚本或运维流程复用同一份配置。
- 备注：这个字段不是 SlotConfig dataclass 的正式字段，而是作为额外字段保留。

### target_point
- 类型：`[x, y]`
- 单位：mm，机器人基坐标系
- 作用：表示当前求解里鞋头弧线希望逼近的目标点，复用了历史 `slot_line.point1` 的语义。
- 当前流程中的作用：当前 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py) 会直接把它作为 `target_point_xy`。
- 调参建议：如果要调插入深浅，优先修改这个点。

### z_heights
- 类型：`[z_min, z_max]`
- 单位：mm，机器人基坐标系
- 作用：描述鞋槽垂直范围。
- 当前流程中的作用：当前主流程实际只使用其中的最小值 `z_min` 参与高度约束求解。

### shoe_origin_z_min
- 类型：浮点数
- 单位：mm，机器人基坐标系
- 作用：表示鞋子在传送带或抓取工位上的鞋底最低点 z 值。
- 当前流程中的作用：用于构造 `toe_lower_in_tcp` 对应的下边界点列。

### toe_z_offset
- 类型：浮点数
- 单位：mm
- 作用：表示鞋头最低点相对鞋槽最低 z 的目标偏移量。
- 当前流程中的作用：直接参与 C4 高度约束，决定最终求解出的 TCP 高度。

### use_sam
- 类型：布尔值
- 作用：控制鞋头弧线提取时是否启用 SAM 分割。

### gripper_refer_pose
- 类型：`list[[x, y, z, rx, ry, rz]]`
- 单位：mm / deg
- 作用：保存夹爪在鞋槽附近的参考位姿。
- 当前流程中的作用：当前至少使用前 2 个位姿生成 XY 方向线段约束和 RZ 姿态范围约束。
- 当前方向语义：当前主流程会直接用前 2 个位姿的 XY 方向作为引导线方向。
- 极重要约束：前 2 个位姿必须按“外侧 -> 内侧”的顺序示教，否则引导线方向会反。

### toe_arc_in_tcp
- 类型：`list[[x, y, z]]`
- 单位：mm，TCP 局部坐标系
- 作用：保存鞋头弧线在 TCP 坐标系下的刚性关系。
- 生成方式：由 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py) 运行时自动计算并回写。

### toe_lower_in_tcp
- 类型：`list[[x, y, z]]`
- 单位：mm，TCP 局部坐标系
- 作用：保存鞋头下边界点列在 TCP 坐标系下的刚性关系。
- 生成方式：依赖 `shoe_origin_z_min`，由 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py) 运行时自动计算并回写。

## 使用建议

- 修改左槽时只改 [left_slot.yaml](left_slot.yaml)，修改右槽时只改 [right_slot.yaml](right_slot.yaml)。
- 如果只是调插入位置，优先改 `target_point`、`toe_z_offset`、`gripper_refer_pose`。
- 如果后续仍要兼容旧 `slot_line` 脚本，请单独维护历史配置，不要再写回当前 left/right 槽 YAML。

## 相关参考

- 通用槽配置字段说明见 [slot_json_fields.md](slot_json_fields.md)
- 配置解析定义见 [shoe_seg/slot_config.py](../../shoe_seg/slot_config.py)
- 运行时写回逻辑见 [shoe_seg/compute_shoe_target_position.py](../../shoe_seg/compute_shoe_target_position.py)