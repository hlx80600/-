# Codesys / PLC 对照说明（维护者自用）

> **写给熟悉 Codesys、还不熟 Python 的人（例如你自己）。**  
> 组里其他人请优先看 [`algorithm_module/readme.md`](../algorithm_module/readme.md) 与 HMI「使用说明」；  
> **凡是「像不像 PLC、CASE 怎么对应」的说明，只放在本文件，其它文档不再展开。**

本工程把自动流程**故意做成接近 Codesys 的骨架**（Busy、进入条件、`CASE Auto_A`、记忆位），方便你用旧习惯改步序。底层运动是法奥 Python SDK（`MoveJ`/`MoveL`），视觉是 YOLO，界面是 PySide6。

---

## 0. 你需要建立的第一张图

```
Codesys 世界                     本工程 Python
─────────────────               ────────────────────────────
OB1 / Main 周期任务      ←→     core/coordinator.py  约每 50ms 扫一遍
GVL（全局变量）          ←→     core/gvl.py          ctx.gvl
功能块 Station           ←→     stations/stationX_*.py  里的 cycle(ctx)
CASE Auto_A OF           ←→     match A[10]: / case 10:
Ton 定时器               ←→     delay_start / delay_done
置位发运动、到位再清     ←→     pulse_cmd / poll_move_done
HMI 触摸屏               ←→     hmi/  （PySide6）
硬件轴 / 夹爪 / 压机     ←→     devices/
视觉                     ←→     vision/ + algorithm_module/
参数表                   ←→     config/default.yaml
```

**读流程改步序：几乎只改 `stations/`。**  
**改 IP/点位：几乎只改 `config/default.yaml`。**

---

## 1. Python 从零（只够读本工程）

Python 不是梯形图，是**文本脚本**。本工程几乎全是 `.py` 文件。

### 1.1 怎么运行

在工程目录打开终端：

```bash
cd 莆田鞋厂四槽机器控制程序
python3 main.py
```

- `python3` = 解释器（像 PLC 运行时）  
- `main.py` = 入口（像启动 Main）  
- 默认大量 `use_mock: true`：没有真机也能点界面看步号、记忆变化  

### 1.2 语法对照（ST → Python）

| Codesys ST | Python | 说明 |
|------------|--------|------|
| `:=` | `=` | 赋值 |
| `AND` / `OR` / `NOT` | `and` / `or` / `not` | 逻辑 |
| `TRUE` / `FALSE` | `True` / `False` | 注意大小写 |
| `(* 注释 *)` | `# 注释` | 一行注释 |
| `IF a THEN … END_IF` | `if a:` 下面缩进一块 | **缩进就是程序块**，空格不能乱 |
| `ELSIF` | `elif` | |
| `CASE x OF 10: … END_CASE` | `match x:` + `case 10:` | 本工程工位都用这个 |
| `FOR i:=1 TO 10 DO` | `for i in range(1, 11):` | |
| 功能块调用 | `函数(参数)` 或 `对象.方法()` | 见下 |

**缩进错误 = 语法错误。** 一般用 4 个空格。编辑器里不要混用 Tab。

### 1.3 什么是「函数」「类」「对象」（对应 FB）

- **函数** = 一段可调用的逻辑，像没有内部状态的小程序：

```python
def sync_mem(ctx, idx: int, val: bool) -> None:
    ctx.gvl.Memory_BOOL[idx] = val
```

- **类（class）** ≈ 功能块类型定义；**对象（实例）** ≈ 功能块实例。  
  例如 `StationFB` 定义「一个工位长什么样」，`ctx.gvl.Station[2]` 是 Station2 那一份。  
- `ctx` = 上下文，里面挂了 `gvl`、机器人、夹爪、视觉……（像一堆全局指针打包）。

### 1.4 import（引用别的文件）

```python
from core.plc_util import pulse_cmd, delay_start
```

意思：从 `core/plc_util.py` 拿这两个名字来用。  
你改 Station 时，文件顶部那些 `from … import …` 就是在「声明用到哪些库」。

### 1.5 字典与列表（你会在点位、记忆里见到）

```python
Memory_BOOL[1] = True          # 像数组下标，但是 dict
pose = {"x": 1.0, "y": 2.0}   # 键值对，像结构体字段
Auto_A[10] = 20                # Auto 号 10 的当前步 = 20
```

### 1.6 类型提示（hint）

```python
def grab_raw(cam_id: str) -> object:
    ...
```

`: str`、`-> object` 是给人（和检查工具）看的类型说明，**不改变运行**。组内新代码要求尽量写 hint。

---

## 2. Codesys ↔ 本工程对照表（核心）

| 你熟悉的 Codesys | 本项目里在哪 | 怎么写 |
|------------------|--------------|--------|
| `GVL_Memory.Memory_BOOL[1]` | `ctx.gvl.Memory_BOOL[1]` | `= True` / `= False` |
| `GVL_Station.Station[2].Auto_A[10]` | `ctx.gvl.Station[2].Auto_A[10]` | 步号 `0/10/20/…` |
| `Busy` | `ctx.gvl.Station[2].Busy` | `update_busy()` 自动算 |
| `Main.Running / Paused / Stop` | `ctx.gvl.Main.Running` 等 | 运行监控按钮改 |
| `IF 条件 THEN Auto_A[10]:=10` | Station 文件前半「进入条件」 | |
| `CASE Auto_A[10] OF` | `match A[10]:` | |
| `Ton` | `delay_start` + `delay_done` | `core/plc_util.py` |
| 步内发一次轴指令 | `pulse_cmd(gvl, "名字")` | 防 50ms 重复发 Move |
| 周期任务 OB1 | `Coordinator.cycle()` | `core/coordinator.py` |
| 硬件 | `devices/` | 机器人/夹爪/压机/IO |
| 可视化 HMI | `hmi/` | |
| 视觉算法统一口 | `algorithm_module` 的 `algo` | 详见其 readme |

文件：`core/gvl.py`、`core/plc_util.py`。

---

## 3. 目录地图（按 PLC 习惯理解）

```
莆田鞋厂四槽机器控制程序/
├── main.py                 ← 启动入口（开 HMI + 起协调器）
├── config/default.yaml     ← ★ 参数表：IP、点位、Mock、运动步速
├── core/                   ← 「PLC 内核」
│   ├── gvl.py              ← GVL：记忆、Station、Main
│   ├── plc_util.py         ← Ton / 发令一次 / 写记忆
│   ├── coordinator.py      ← OB1：扫描 Init + Station1～6
│   ├── app_context.py      ← 根据 yaml 创建机器人等对象
│   ├── memory / alarm / lights / machine_state …
├── stations/               ← ★ 流程 CASE（你最该改这里）
│   ├── station1_belt_photo.py
│   ├── station2_robot1.py  ← 建议第一个精读
│   ├── station3 ～ station6
│   ├── init_sequence.py
│   └── step_catalog.py     ← 给 HMI 步表用的标题
├── devices/                ← 轴与 IO 驱动（Mock 或真机）
├── vision/ + algorithm_module/  ← 相机与算法（改检测再动）
├── visualize_module/       ← 相机监控窗
├── hmi/                    ← 界面
└── docs/
    ├── Codesys对照说明.md  ← 本文件（PLC 对照只在这里）
    └── 操作说明.md         ← 现场联调摘要（不含 PLC 教程）
```

---

## 4. 一个 Station 固定四段（请背下来）

每个 `stations/stationX_*.py` 的 `cycle(ctx)` 结构与你 Codesys 一致：

```text
① Busy
   IF 所有 Auto_A=0 THEN Busy:=FALSE ELSE Busy:=TRUE

② 停机清零
   IF Stop OR EStop OR Alarm THEN 所有 Auto_A:=0

③ 进入条件
   IF 互锁 AND NOT Busy AND Running AND NOT Paused THEN
       Auto_A[10]:=10

④ CASE（Paused 时直接 return，不跑）
   CASE Auto_A[10] OF
     10: 发令 / 等完成 ; Auto_A[10]:=20;
     20: ... ; Auto_A[10]:=0;
   END_CASE
```

Python 里长这样（示意）：

```python
def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[2]
    A = st.Auto_A
    M = gvl.Memory_BOOL

    st.update_busy()                          # ① Busy
    if gvl.Main.Stop or gvl.Main.EStopped:  # ②
        A[10] = 0
        return

    if (条件) and (not st.Busy) and gvl.Main.Running and (not gvl.Main.Paused):
        A[10] = 10                            # ③ 进入

    if gvl.Main.Paused:
        return

    match A[10]:                              # ④ CASE
        case 10:
            if pulse_cmd(gvl, "s2a10_10"):
                ctx.robot1.move_j(...)        # 只发一次
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_10")
                A[10] = 20
        case 20:
            ...
        case _:
            pass
```

### 4.1 `pulse_cmd` 为什么必须有？

PLC 周期一直扫。若每拍都 `MoveL`，会把同一条指令发几百次。  

- `pulse_cmd(gvl, "步名")` 第一次返回 `True`（可以发运动）  
- 之后返回 `False`（只等待到位）  
- 跳步前 `cmd_reset(gvl, "步名")`  

对应你以前「步 10 置位 Go，到位后清位」。

### 4.2 `delay_start` / `delay_done`

```python
delay_start(gvl, "s6_press", 2.0)   # 开始 2 秒
if delay_done(gvl, "s6_press"):
    ...                             # 时间到
```

≈ `Ton` 的 IN + Q（更简化，没有独立 FB 实例表）。

### 4.3 `advance_step`（自动 vs 单步）

- 自动模式：条件满足就允许 `A[x]=下一步`  
- 单步模式：还要 HMI 点「下一步」（`StepPulse`）才跳  

---

## 5. 记忆 Mem1～10

| Mem | 含义（与现场约定一致，以 HMI 勾选旁文字为准） |
|-----|-----------------------------------------------|
| 1 | 皮带上料拍照完成 |
| 2 | 机器人1 手爪有料 |
| 3 | 放料侧逻辑有料 / 待转相关 |
| 4 | 放料鞋槽拍照完成 |
| 5 | 机器人2 手爪有料 |
| 6 | 取料鞋槽有料 |
| 7 | 取料鞋槽拍照完成 |
| 8 | 取到左鞋 |
| 9 | 取到右鞋 |
| 10 | 放料槽不匹配等（旋转时可能不压鞋） |

写记忆请用：

```python
sync_mem(ctx, 2, True)   # 同时写 gvl 与 HMI 显示表
```

不要只改一处导致界面和逻辑不一致。

---

## 6. 六个 Station 各干什么（流程视角）

| Station | 文件 | 像哪段工艺 |
|---------|------|------------|
| 1 | `station1_belt_photo.py` | 皮带拍照 → 出 PickPose |
| 2 | `station2_robot1.py` | 上料臂：取料 Auto_A[10] + 放料 Auto_A[20] |
| 3 | `station3_place_slot_photo.py` | 放料槽拍照 |
| 4 | `station4_pick_slot_photo.py` | 取料槽拍照 + 压杆 |
| 5 | `station5_robot2.py` | 下料臂：取槽 → 皮带放料 |
| 6 | `station6_press_rotate.py` | 压合 → 旋转 → 推槽号 |
| Init | `init_sequence.py` | 上电初始化 CASE |

Station2 放料路径（Auto_A[20]）：

```text
进入点 → 上方 → 接近放料点(工具1) → 切鞋头TCP → 对位 → 压跟 → 张爪 → 上方 → 退出
```

运动不是梯形图里的轴指令名，而是：

```python
ctx.robot1.move_j(...)   # → 法奥 MoveJ
ctx.robot1.move_l(...)   # → 法奥 MoveL
ctx.move_to_point("robot1", "place_entry", step_key="s2a20_10")
```

速度/平滑按**程序步键**（如 `s2a20_10`）在 HMI「运动参数」里设，存在 yaml 的 `motion_steps`。

---

## 7. 视觉在流程里的位置（不必先会 Python 深度学习）

工位**不要直接**抄 YOLO。标准路径：

```text
Station  →  ctx.vision.photo_* / guide_*  →  algorithm_module.algo.*  →  vision/legacy_pipeline
```

| 相机 | 工位 | 接口（概念） |
|------|------|----------------|
| cam1 | S1 | 皮带找鞋 → 基座坐标 |
| cam2 | S2 | 鞋头到位？ → 相对 MoveL |
| cam3 | S3 | 放料槽有没有鞋 |
| cam4 | S4/S5 | 取料槽 + 压杆偏移 |

详细输入输出见：`algorithm_module/readme.md`（给全组看的算法说明）。

---

## 8. 建议学习顺序（按你的背景）

**第 1 天（对照）**  
1. 精读本文件到第 4 节。  
2. 打开 `stations/station2_robot1.py`，只看 `match A[10]` / `match A[20]`，把步号画成你以前的 CASE 表。  
3. 打开 `core/gvl.py`，对照 Mem / Station / Main。

**第 2 天（工具函数）**  
4. 读 `core/plc_util.py`：`pulse_cmd`、`delay_*`、`sync_mem`、`advance_step`。  
5. 读 `core/coordinator.py` 的 `cycle()`：谁在什么顺序被调用。

**第 3 天（跑起来）**  
6. `python3 main.py` → 初始化 → 启动 →「模拟光电」或「空跑联调」一键启用。  
7. 看运行监控里 Mem、各 Station 步号是否按你预期跳。

**第 4 天（改一点流程）**  
8. 只改某个 `case` 里的点名或互锁条件，Mock 下验证。  
9. 再碰 `config/default.yaml` 的点位 / IP。

**以后再学**  
10. `devices/robot_fr5.py`（真机运动）  
11. `vision/` + `algorithm_module`（真视觉）  
12. `hmi/`（按钮从哪触发 Running / StepPulse）

---

## 9. 改流程时改哪里？

| 目的 | 改哪里 |
|------|--------|
| 步序、互锁、发令顺序 | `stations/stationX_*.py` |
| IP、点位、Mock、步速度 | `config/default.yaml`（或 HMI 通信配置/运动参数保存） |
| 界面按钮文案 | `hmi/pages/` |
| 算法接口说明 | `algorithm_module/readme.md` |
| **PLC/Codesys 对照与 Python 入门** | **仅本文件** |

一般不要先改 `devices/`、`vision/` 核心，等流程 Mock 跑通再接真机。

---

## 10. 和「别人看的文档」怎么分工

| 文档 | 给谁 | 内容 |
|------|------|------|
| **本文件** `docs/Codesys对照说明.md` | 你会 PLC、要对照改流程 | Codesys↔Python、CASE、Mem、学习路径 |
| `algorithm_module/readme.md` | 全组（含不会 PLC） | 视觉算法接口、谁调用谁 |
| HMI「使用说明」 | 现场操作 | 每页做什么、实现文件 |
| `docs/操作说明.md` | 现场联调 | 安装、检查清单（无 PLC 教程） |
| 根 `README.md` | 全组快速入口 | 运行与目录（不写 Codesys 教程） |

---

## 11. 运行与 Mock

```bash
cd 莆田鞋厂四槽机器控制程序
python3 main.py
```

- 无真机：初始化 → 启动 → 空跑或模拟光电，观察 Station/Mem。  
- 有真机：在「通信配置」把对应设备 `use_mock` 取消，填 IP，保存后重启。  

急停、报警、单步旁路等操作见 HMI「使用说明」→ 运行监控 / 工位调试。

---

## 12. 常见卡壳（PLC 思维）

| 现象 | 常见原因 |
|------|----------|
| 步号停在 30 不动 | `pulse_cmd` 已锁存但 Move 失败；看 `recover_stuck_move_cmd`、报警 |
| 自动不进站 | 互锁 Mem 不满足；或 `Busy`；或未 Running |
| 单步不跳 | 没点「下一步」；`advance_step` 在等 StepPulse |
| 每拍都在抖 | 某步没走 `pulse_cmd`，每周期重复发令 |
| 改了点位不生效 | 改错机器人或没保存 yaml；或程序用的是 `runtime_pick` 视觉点 |

---

**维护约定：** 若你补充「和 Codesys 怎么对应」的内容，请只追加到本文件，不要写进 `algorithm_module/readme.md` 或面向全组的 README / 操作说明正文。
