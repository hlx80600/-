# 莆田鞋厂四槽机器控制程序

给**电气 / Codesys** 转机器人的同事：流程按 PLC 习惯写（Busy + 进入条件 + CASE Auto_A）。

## ★ 改机器人地址（IP）在哪？

打开：**[config/default.yaml](config/default.yaml)**

```yaml
robots:
  robot1:
    ip: "192.168.1.105"   # 上料机器人 ← 改这里
  robot2:
    ip: "192.168.1.115"   # 下料机器人 ← 改这里
```

也可在 HMI 页 **「通信配置」** 改完点保存（同样写入上述 yaml）。改 IP 后请重启程序。

**分设备 Mock：** 各设备有自己的 `use_mock`（不要只关全局）。例：只有上料真机时  
`robots.robot1.use_mock: false`，其余保持 `true`。详见 `docs/操作说明.md`。

压鞋机 IP：同文件 `press.ip`；夹爪：`grippers.*.can_id`；点位：`points`。

## 先看这个（最重要）

打开：**[docs/从零看懂本程序.md](docs/从零看懂本程序.md)**

然后打开：**[stations/station2_robot1.py](stations/station2_robot1.py)**（注释里一步步对照 Codesys）

## 运行

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
python3 main.py
```

默认 Mock，无真机：初始化 → 启动 →「模拟光电感应到位」。

## 目录（极简）

| 路径 | 相当于 Codesys |
|------|----------------|
| `core/gvl.py` | GVL 全局变量 |
| `core/coordinator.py` | OB1 主扫描 |
| `stations/*.py` | 各 Station 程序 |
| `config/default.yaml` | **参数表（IP/点位）← 现场改这里** |
| `devices/` | 驱动（机器人/夹爪/压鞋机） |
| `hmi/` | 触摸屏/上位界面 |

更多：`docs/操作说明.md`、`docs/Codesys对照说明.md`
