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

1. **整机怎么跑**：[docs/程序总览.md](docs/程序总览.md) — 启动、主循环、一条鞋的流程  
2. **各界面怎么点、投产顺序、手眼标定**：[docs/界面操作手册.md](docs/界面操作手册.md)（与软件内「使用说明」一致）  
3. **视觉/算法接口**：[algorithm_module/readme.md](algorithm_module/readme.md)  
4. 打开软件 → **「使用说明」**（总览含新机投产顺序；视觉调试含 ROI→内参→手眼逐步）  
5. 联调 / 改地址：[docs/操作说明.md](docs/操作说明.md)  
6. 夹爪（达妙 DM-J4310-2EC）：[docs/夹爪使用说明.md](docs/夹爪使用说明.md)  

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
| `docs/程序总览.md` | 主流程与架构 |
| `docs/界面操作手册.md` | 全界面操作与投产/手眼顺序 |
| `docs/夹爪使用说明.md` | 达妙 DM-J4310-2EC 接线与试夹 |