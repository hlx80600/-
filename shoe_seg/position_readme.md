# `position.py` 使用说明

`position.py` 负责左右相机初始化、压杆/夹抓对位测距、卡鞋检测，以及检测失败样本归档。

当前参数统一从同目录的 `position_config.yaml` 读取，不再在 `position.py` 顶部直接修改类属性。

## 当前能力

`Position` 类目前提供：

- 初始化左/右 Orbbec 相机
- 加载压杆 OBB 检测模型
- 计算鞋与压杆的 X 向距离
- 计算鞋与夹抓示教点的 X 向距离
- 将夹抓对位距离转换为机器人基坐标系 XYZ 偏移
- 使用卡鞋分类模型判断当前槽位是否 OK
- 只在检测 NG 时保存原图和深度图

已移除的旧能力：

- 鞋槽 OBB 对位
- 鞋槽参考点对位
- `slot_obb_model_path` 相关配置
- OK 样本存图
- `vis` 和 `meta` 归档文件

## 配置文件

默认配置文件：

```text
position_config.yaml
```

也可以在初始化时传入其他配置路径：

```python
pos = Position(machine, config_path="path/to/position_config.yaml")
```

### 相机配置

`cam_left` 表示左相机，`cam_right` 表示右相机。

```yaml
cam_left_sn: CPC4B41000G0
cam_right_sn: CPCLA5300066

cam_left_K: [610.7285, 610.8050, 632.7072, 360.3536]
cam_right_K: [610.1464, 609.8653, 644.0151, 361.8214]

cam_left_color_exposure: 150
cam_left_color_gain: 14
cam_right_color_exposure: 150
cam_right_color_gain: 14
```

`cam_left_K` / `cam_right_K` 格式为：

```text
[fx, fy, cx, cy]
```

### 相机到机器人基坐标系旋转

```yaml
cam_left_robot_base_rotation_degrees: [0, 0, 140.27]
cam_right_robot_base_rotation_degrees: [0.0, 0.0, -39.73]
```

格式为：

```text
[x_degree, y_degree, z_degree]
```

用于把相机 X 方向测得的距离分解成机器人基坐标系下的 `[dx, dy, dz]`。

### 压杆模型配置

```yaml
rod_obb_model_path: /home/casbot/robot/Casbot_Press_Shoes/models/position/rod/obb.pt
rod_obb_img_size: 640
rod_obb_detection_conf: 0.7
rod_default_shift:
  - [0, 0]
  - [0, 0]
  - [0, 0]
  - [0, 0]
  - [0, 0]
  - [0, 0]
```

`rod_default_shift` 会透传给 `OBBOnlyDetector.detect()`，用于按类别对 OBB 中心点做比例偏移。当前全为 `[0, 0]`，等价于直接使用 OBB 中心点取深度。

### 预设坐标

压杆预设坐标：

```yaml
cam_left_rod_preset_xyz: [0.1057, 0.0282, 0.3620]
cam_right_rod_preset_xyz: [-0.0772, 0.0273, 0.3250]
```

夹抓示教预设坐标：

```yaml
cam_left_gripper_preset_xyz: [0.0887, 0.0221, 0.3490]
cam_right_gripper_preset_xyz: [-0.0729, 0.0355, 0.3370]
```

当前测距只使用预设坐标的 X 分量。

### ROI 配置

```yaml
roi_width: 600
roi_height: 300
cam_left_rod_roi: [[400, 280]]
cam_right_rod_roi: [[300, 320]]
```

ROI 只配置左上角 `[[x1, y1]]`，右下角由程序自动计算：

```text
[x1 + roi_width, y1 + roi_height]
```

`null` 表示不做 ROI 过滤。

当前 ROI 不是先裁图再检测，而是：

1. 整图送入模型检测
2. 根据 OBB 中心点过滤检测结果
3. 中心点落在 ROI 外的目标不参与后续测距

### 卡鞋检测配置

```yaml
slot_check_model_path: /home/casbot/robot/Casbot_Press_Shoes/models/position/slot/slot_check.pt
slot_check_conf: 0.7
slot_check_img_size: 640

slot_check_depth_alpha: 0.5
slot_check_depth_beta: 0.5
slot_check_color_start_depth_mm: 250
slot_check_color_range_mm: 300
slot_check_min_depth_mm: 100
slot_check_max_depth_mm: 1000

slot_check_roi_start: [160, 210]
slot_check_roi_size: [1000, 500]
```

`is_slot_ok()` 会先生成卡鞋检测输入图：

1. 深度图按配置范围生成伪彩色图
2. RGB 图转灰度，再转回 3 通道
3. 灰度 RGB 与深度伪彩按 `alpha/beta` 融合
4. 对融合图裁 `slot_check_roi`
5. 送入 `slot_check_model_path` 分类模型

分类结果中 `good` 概率最高且超过阈值时返回 `True`，否则返回 `False`。

### 失败样本归档

```yaml
enable_detection_artifact_save: true
detection_artifact_root: position_artifacts
```

当前只保存 NG 样本，不保存 OK 样本。

保存内容：

- `raw/`：原始 RGB 图
- `depth/`：深度 `.npy`

不再保存：

- `vis/`
- `meta/`

示例目录：

```text
position_artifacts/
  rod_distance/
    cam_left/
      raw/
      depth/
  gripper_distance/
    cam_right/
      raw/
      depth/
```

NG 样本不是每次重试失败都保存，而是一次测量连续失败后保存最后一次失败快照。

## 最小使用示例

```python
from RSDT_Simple_Automation.automation_machine import automationMachine
from position import Position

machine = automationMachine()
pos = Position(machine)
```

关闭失败样本保存：

```python
pos = Position(machine, artifact_save=False)
```

使用 YAML 中的 `enable_detection_artifact_save`：

```python
pos = Position(machine, artifact_save=None)
```

## 相机 ID 约定

外部接口仍使用数字 ID：

- `id=1`：左相机，对应 `cam_left_*` 配置
- `id=2`：右相机，对应 `cam_right_*` 配置

内部注册到 `automationMachine` 时仍使用：

- 左相机：`cam1`
- 右相机：`cam2`

## 接口 1：压杆对位

调用：

```python
find_flag, distance, detect_result_image = pos.get_rod_distance(1)
```

返回值：

- `find_flag`：是否成功检测到鞋并获取深度
- `distance`：鞋 X 坐标减去压杆预设 X 坐标，单位米
- `detect_result_image`：检测结果图

计算方式：

```python
distance = shoe_x_3d - rod_preset_xyz[0]
```

## 接口 2：夹抓对位距离

调用：

```python
find_flag, distance, detect_result_image = pos.get_gripper_distance(1)
```

返回值：

- `find_flag`：是否成功检测到鞋并获取深度
- `distance`：鞋 X 坐标减去夹抓示教预设 X 坐标，单位米
- `detect_result_image`：检测结果图

计算方式：

```python
distance = shoe_x_3d - gripper_preset_xyz[0]
```

## 接口 3：夹抓对位机器人偏移

调用：

```python
find_flag, robot_xyz_offset, detect_result_image = pos.get_rod_robot_offset(1)
```

说明：

- 内部调用 `get_gripper_distance()`
- 将相机 X 向距离转换成机器人基坐标系 `[dx, dy, dz]`
- 单位为米

示例：

```python
find_flag, robot_xyz_offset, _ = pos.get_rod_robot_offset(1)
if find_flag:
    print(robot_xyz_offset)
```

## 接口 4：卡鞋检测

调用：

```python
ok = pos.is_slot_ok(1)
```

返回值：

- `True`：分类结果为 `good`
- `False`：分类结果为 `bad`、未知，或未达到阈值

## 调试脚本

`position_utils/run_position.py` 可用于测试压杆/夹抓对位接口。

`position_utils/run_colormap.py` 可用于在线查看卡鞋检测输入图：

- 按 `t`：触发一次卡鞋分类
- 按 `g`：保存当前图到 `data_color/good`
- 按 `b`：保存当前图到 `data_color/bad`
- 按 `q`：退出

`run_colormap.py` 中的 `crop_project_data_to_roi()` 可把 `data_color` 下图片按 `slot_check_roi_*` 裁切到 `data_roi`。

## 注意事项

- 两个终端可以分别连接不同物理相机 SN；不要同时连接同一个 SN。
- `Position` 初始化时会同时连接左右相机。
- `cam_left_*` / `cam_right_*` 的内参、曝光、增益和旋转角度需要按现场标定值配置。
- ROI 过滤按 OBB 中心点判断，不按目标面积重叠比例判断。
- 当前 `position.py` 只负责视觉测量和判断，不执行机械臂运动。
