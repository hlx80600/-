# devices — 设备驱动层

> 封装法奥 FR5、达妙 CAN 夹爪、压机 Modbus、IO；**不含**工位流程（在 `stations/`）。

现场改 IP / CAN / Mock： [docs/操作说明.md](../docs/操作说明.md) + [config/default.yaml](../config/default.yaml)。

---

## 主要文件

| 文件 | 设备 | 说明 |
|------|------|------|
| `robot_fr5.py` | 法奥 FR5 ×2 | MoveL、TCP、Mock；依赖 `fairino` SDK |
| `gripper_can.py` | 单路夹爪 CAN | SocketCAN + DM 电机协议 |
| `gripper_bank.py` | 多夹爪管理 | 上/下料臂各爪实例 |
| `press_modbus.py` | 压鞋机 | Modbus TCP 槽位、压合信号 |
| `toe_tcp.py` | 鞋头 TCP 辅助 | 与 toe 对位相关 |
| `io_manager.py` | 数字 IO | 光电、使能等 |
| `pose_utils.py` | 位姿工具 | 欧拉角、示教点换算 |

---

## Mock 与真机

`config/default.yaml` 中各设备可独立 `use_mock: true/false`。  
空跑屏蔽见 `core/dry_run_shield.py`。

---

## 相关文档

- 夹爪接线与试夹：[docs/夹爪使用说明.md](../docs/夹爪使用说明.md)  
- 旧样例脚本（对照用）：[press_shoes/readme.md](../press_shoes/readme.md)
