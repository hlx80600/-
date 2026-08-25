# press_shoes — 旧压鞋机样例（对照用）

> **新主程序逻辑在 `devices/` + `stations/` + `core/`。**  
> 本目录保留早期 Casbot 压鞋脚本、Modbus 样例、ROS 接口，供迁移对照或单独跑旧 workflow。

---

## 目录说明

| 路径 | 内容 |
|------|------|
| `press_shoes_workflow.py` / `run_workflow.py` | 旧整线 workflow 入口 |
| `robot_arm/` | 法奥臂封装、CAN 夹爪、示教脚本 |
| `manager/` | 取放料 workflow 管理 |
| `press_machine_modbusTCP.py` | 压机 Modbus 样例 |
| `vision/shoe_vision.py` | 旧 ShoeVision 封装 |
| `config/` | 槽位 yaml/json、臂配置 |
| `scripts/` | 示教、ROI、槽位采集工具 |
| `ros/interface/` | ROS srv 定义（可选） |

---

## 何时还用这里

- 对照旧 Casbot_Press_Shoes 行为  
- 单独调试 `gripper_controller_can.py`、Modbus 寄存器  
- 新现场 **不必** 从此目录启动；用根目录 `python3 main.py`

---

## 相关文档

- 新驱动：[devices/readme.md](../devices/readme.md)  
- 旧视觉说明：[shoe_vision_readme.md](../shoe_vision_readme.md)
