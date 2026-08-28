# core — 主控核心层

> 程序「大脑」：配置加载、全局状态、协调器主循环、报警与空跑屏蔽。  
> 整机流程见 [docs/程序总览.md](../docs/程序总览.md)；工位逻辑在 `stations/`。

---

## 一句话

`main.py` 启动后创建 `AppContext`，由 `Coordinator` 驱动各 Station 与设备；`GVL` / `Memory` 保存与旧 Codesys 对应的运行时记忆。

---

## 主要文件

| 文件 | 作用 |
|------|------|
| `app_context.py` | 全局上下文：配置、设备、视觉、HMI 回调 |
| `coordinator.py` | 主循环：扫描工位、模式切换、与 HMI 联动 |
| `config_loader.py` | 加载 `config/default.yaml` 及合并 |
| `camera_config.py` | 相机 serial / 启用项读写 |
| `gvl.py` / `memory.py` | 全局变量与工位间共享记忆 |
| `machine_state.py` | 运行/初始化/报警等状态机 |
| `alarm.py` | 报警登记与 HMI 弹窗 |
| `blackbox.py` | 错误日志落盘 + 黑匣子（`logs/`，退出后可查） |
| `dry_run_shield.py` | 空跑时屏蔽真机 I/O |
| `motion_steps.py` | 运动步序配置辅助 |
| `point_undo.py` | 示教点撤销栈 |
| `lights.py` | 三色灯 |
| `production_stats.py` | 产量统计 |
| `plc_util.py` | PLC 相关工具（若启用） |

视觉运行快照（图 + 运送结果）在 `vision/vision_journal.py` → `logs/vision_snaps/`（jpg 文件名含相机与时间），HMI「报警记录 → 运行快照」查阅。

---

## 阅读顺序

1. [docs/程序总览.md](../docs/程序总览.md)  
2. `app_context.py` → `coordinator.py`  
3. `stations/base_station.py` 与各 `station*.py`  
4. [docs/Codesys对照说明.md](../docs/Codesys对照说明.md)（迁移对照）

---

## 相关文档

- 参数总表：[config/readme.md](../config/readme.md)  
- 设备驱动：[devices/readme.md](../devices/readme.md)
