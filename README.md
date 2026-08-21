# 莆田鞋厂四槽机器控制程序

四槽压鞋机 + 双臂（法奥 FR5）自动上下料控制程序（HMI + 工位流程 + 视觉）。

## ★ 改机器人地址（IP）在哪？

打开：**[config/default.yaml](config/default.yaml)**

```yaml
robots:
  robot1:
    ip: "192.168.1.105"   # 上料机器人 ← 改这里
  robot2:
    ip: "192.168.1.115"   # 下料机器人 ← 改这里
```

也可在 HMI **「通信配置」** 改完点保存。改 IP 后请重启程序。

**分设备 Mock：** 各设备有自己的 `use_mock`。详见 `docs/操作说明.md`。

## 先看这个

1. **视觉/算法入口（推荐最先读）**：[algorithm_module/readme.md](algorithm_module/readme.md) — 接口、输入输出、谁调用谁  
2. 打开软件 → 标签 **「使用说明」**（各 HMI 页：做什么 / 实现文件 / 引用）  
3. 联调摘要：**[docs/操作说明.md](docs/操作说明.md)**  
4. 工位流程示例：**[stations/station2_robot1.py](stations/station2_robot1.py)**

## 运行

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
python3 main.py
```

默认 Mock，无真机：初始化 → 启动 →「模拟光电感应到位」。

## 目录（极简）

| 路径 | 作用 |
|------|------|
| `core/` | 协调器、记忆、报警、灯 |
| `stations/` | Station1～6 自动流程 |
| `devices/` | 机器人 / 夹爪 / 压鞋机 / IO |
| `vision/` + `algorithm_module/` | 相机与视觉算法接口 |
| `visualize_module/` | 相机监控取流与显示 |
| `hmi/` | 触摸屏界面（`help_content.py` = 使用说明正文） |
| `config/default.yaml` | **参数表（IP/点位）← 现场改这里** |

> 维护者若需 **Codesys/PLC ↔ 本程序对照（含 Python 从零）**，见 [`docs/Codesys对照说明.md`](docs/Codesys对照说明.md)（不面向全组入门）。
