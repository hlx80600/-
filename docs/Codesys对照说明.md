# Codesys / PLC 对照说明（维护者自用 · 详细版）

> **写给你（维护者自用）。**  
> 组里其他人只看 [`程序总览.md`](程序总览.md) 与 [`../algorithm_module/readme.md`](../algorithm_module/readme.md)——那些文档**不会**出现本文件中的对照用语。  
> 对照用语**只写在本文件**。

两份内容对齐：别人在「程序总览」里看到的主流程，你在这里用对照表读同构代码。

---

## 0. 总对照图（先建立直觉）

```
Codesys 世界                         本工程 Python
─────────────────────               ────────────────────────────────
OB1 / Main 周期任务          ←→     core/coordinator.py   约 50ms 一拍
GVL                          ←→     core/gvl.py           ctx.gvl
功能块 Station               ←→     stations/stationX_*.py  的 cycle(ctx)
CASE Auto_A OF               ←→     match A[10]: / case 10:
Ton                          ←→     delay_start / delay_done
步内置位发轴、到位清位       ←→     pulse_cmd + poll_move_done + cmd_reset
HMI                          ←→     hmi/（PySide6）
轴 / 夹爪 / 压机             ←→     devices/
视觉                         ←→     vision/ + algorithm_module/
参数表                       ←→     config/default.yaml
```

**改工艺步序 → 几乎只改 `stations/`。**  
**改 IP / 点位 / Mock → 几乎只改 `config/default.yaml`。**

---

## 1. Python 从零（读本工程够用）

Python 是文本脚本，不是梯形图。本工程主要是 `.py` 文件。

### 1.1 怎么运行

```bash
cd 莆田鞋厂四槽机器控制程序
python3 main.py
```

| 概念 | 理解 |
|------|------|
| `python3` | 解释器（≈ PLC 运行时） |
| `main.py` | 入口（≈ 启动 Main） |
| `use_mock: true` | 无硬件也能跑界面与步号 |

### 1.2 ST → Python 语法

| Codesys ST | Python | 注意 |
|------------|--------|------|
| `:=` | `=` | |
| `AND` / `OR` / `NOT` | `and` / `or` / `not` | |
| `TRUE` / `FALSE` | `True` / `False` | **大小写** |
| `(* … *)` | `# …` | |
| `IF a THEN … END_IF` | `if a:` + **缩进**一块 | 缩进错误=语法错误 |
| `ELSIF` | `elif` | |
| `CASE x OF 10: …` | `match x:` / `case 10:` | 工位全用这个 |
| `FOR i:=1 TO 10` | `for i in range(1, 11):` | |

一般 **4 空格**缩进，不要混 Tab。

### 1.3 函数 / 类 / 对象（≈ FB）

```python
def sync_mem(ctx, idx: int, val: bool) -> None:
    """写记忆：像调用一个小功能块。"""
    ctx.gvl.Memory_BOOL[idx] = val
```

- **函数** = 无实例状态的一小段逻辑  
- **类 class** ≈ FB 类型；**对象** ≈ FB 实例（如 `Station[2]`）  
- **`ctx`** = 打包好的全局：gvl、robot、vision…（≈ 一堆指针）

### 1.4 import

```python
from core.plc_util import pulse_cmd, delay_start
```

= 从别的文件引入名字。Station 文件顶部那些就是「声明依赖」。

### 1.5 字典（像结构体 / 带名字的数组）

```python
Memory_BOOL[1] = True
Auto_A[10] = 20
pose = {"x": 1.0, "y": 2.0, "z": 100.0}
```

### 1.6 类型 hint

```python
def f(cam_id: str) -> bool:
    ...
```

`: str`、`-> bool` 给人看，**不改变运行**。组内新代码要求尽量写。

---

## 2. 启动之后发生什么（对照程序总览 §2）

别人文档里的流程与这里相同，你用 PLC 词汇理解：

```
python3 main.py
    │
    ├─ 读 yaml（参数表）
    ├─ 创建 AppContext ≈ 绑定所有硬件 FB + GVL
    ├─ 创建 Coordinator ≈ 启动 OB1 周期任务（约 50ms）
    └─ 打开 HMI
```

每一拍 `Coordinator`（≈ OB1）：

1. 同步 HMI 按钮 → `Main.Running / Paused / Stop / EStop…`  
2. 初始化 CASE（`init_sequence`）  
3. **并行**扫 Station1～6 的 `cycle`（各站内部 Busy + CASE）  
4. 刷三色灯等  

HMI 另有约 100ms 刷新显示，**不等于** OB1，只是画面。

---

## 3. GVL / Main / Station / Mem（对照表）

| Codesys | 本工程 | 读写例子 |
|---------|--------|----------|
| `GVL_Memory.Memory_BOOL[1]` | `ctx.gvl.Memory_BOOL[1]` | `sync_mem(ctx, 1, True)` |
| `Station[2].Auto_A[10]` | `ctx.gvl.Station[2].Auto_A[10]` | 步号 `0/10/20/…` |
| `Station[2].Busy` | 同上 `.Busy` | `update_busy()` 自动算 |
| `Main.Running` | `ctx.gvl.Main.Running` | 运行监控「启动」 |
| `Main.Paused / Stop` | 同上 | 暂停 / 停止 |
| `DebugBypass` | `Main.DebugBypass` | 工位调试旁路互锁 |

文件：`core/gvl.py`。

### 3.1 记忆 Mem1～10

| Mem | 含义 |
|-----|------|
| 1 | 皮带上料拍照完成 |
| 2 | 机器人1 手爪有料 |
| 3 | 放料侧 / 待转相关 |
| 4 | 放料鞋槽拍照完成 |
| 5 | 机器人2 手爪有料 |
| 6 | 取料鞋槽有料 |
| 7 | 取料鞋槽拍照完成 |
| 8 | 左鞋 |
| 9 | 右鞋 |
| 10 | 特殊（如不匹配则旋转不压） |

写记忆务必：

```python
sync_mem(ctx, 2, True)   # Codesys: Memory_BOOL[2]:=TRUE; 且刷新 HMI
```

---

## 4. 一个 Station 的四段骨架（背下来）

与你 Codesys 完全同构（也是「程序总览」里工位结构的 PLC 说法）：

```text
① Busy
   IF 所有 Auto_A=0 THEN Busy:=FALSE ELSE Busy:=TRUE

② 停机清零
   IF Stop OR EStop OR Alarm THEN 所有 Auto_A:=0

③ 进入条件
   IF 互锁 AND NOT Busy AND Running AND NOT Paused THEN
       Auto_A[10]:=10

④ CASE（Paused 时不跑）
   CASE Auto_A[10] OF
     10: … ; Auto_A[10]:=20;
     20: … ; Auto_A[10]:=0;
   END_CASE
```

Python：

```python
def cycle(ctx) -> None:
    gvl = ctx.gvl
    st = gvl.Station[2]
    A = st.Auto_A
    M = gvl.Memory_BOOL
    single = gvl.Main.Mode == "SINGLE_STEP"

    st.update_busy()                                    # ①
    if gvl.Main.Stop or gvl.Main.EStopped or gvl.Main.Alarming:
        A[10] = 0                                       # ②
        return

    if (互锁) and (not st.Busy) and gvl.Main.Running and (not gvl.Main.Paused):
        A[10] = 10                                      # ③

    if gvl.Main.Paused:
        return

    match A[10]:                                        # ④
        case 10:
            if pulse_cmd(gvl, "s2a10_10"):
                ctx.move_to_point("robot1", "pick_entry", step_key="s2a10_10")
            if ctx.robot1.poll_move_done() and advance_step(st, single):
                cmd_reset(gvl, "s2a10_10")
                A[10] = 20
        case _:
            pass
```

精读文件：`stations/station2_robot1.py`。

---

## 5. Ton / 发令一次 / 单步（`plc_util.py`）

### 5.1 Ton → delay

```text
Codesys:  Ton[1](IN:=TRUE, PT:=T#2s);  IF Ton[1].Q THEN …
本工程:   delay_start(gvl, "s6_press", 2.0)
          if delay_done(gvl, "s6_press"): …
```

### 5.2 为什么必须 `pulse_cmd`

OB1 一直扫。若 CASE 10 每拍都 `MoveL`，会狂发指令。

| 返回值 | 含义 |
|--------|------|
| `pulse_cmd(...)==True` | 本步第一次：可以发 Move / 开夹爪 |
| `False` | 已发过：只等待到位 |
| `cmd_reset` | 跳步前清锁存 |

运动失败卡死：看 `recover_stuck_move_cmd`（锁存还在但臂已不在运动 → 清锁存以便重发）。

### 5.3 自动 vs 单步 → `advance_step`

- 自动：条件满足就允许改 `Auto_A` 下一步  
- 单步：还要 HMI「下一步」产生 `StepPulse`  

---

## 6. 一条鞋的主流程（与「程序总览 §4」同一条，带 PLC 说法）

```
① Station1  皮带拍照 → PickPose，置 Mem（拍照完成等）
② Station2  Auto 取料：进入/上方/取料/夹紧/抬起/退出 → Mem 有料
③ Station3  （需要时）放料槽拍照 → Mem
④ Station2  Auto 放料：进入→上方→接近→对位→压跟→张爪→上方→退出
⑤ Station6  压合 → 旋转 → 推槽号（先压后转）
⑥ Station4  取料槽拍照 ± 压杆偏移
⑦ Station5  下料取槽 → 皮带放料 → 产量+1
```

### 6.1 六站文件

| Station | 文件 | 工艺 |
|---------|------|------|
| 1 | `station1_belt_photo.py` | 皮带拍照 |
| 2 | `station2_robot1.py` | 上料取+放（`Auto_A[10]` 取，`Auto_A[20]` 放） |
| 3 | `station3_place_slot_photo.py` | 放料槽视觉 |
| 4 | `station4_pick_slot_photo.py` | 取料槽视觉 |
| 5 | `station5_robot2.py` | 下料 |
| 6 | `station6_press_rotate.py` | 压合+旋转 |
| Init | `init_sequence.py` | 初始化 CASE |

### 6.2 Station2 放料 `Auto_A[20]` 路径

```
进入点 → 上方 → 接近放料点(工具1) → 切鞋头TCP → 对位 → 压跟 → 张爪 → 上方 → 退出
```

对位：`stations/toe_place_assist.py` → `algo.classify_toe_align`。

### 6.3 运动（不是 PLC 轴名，是法奥）

```python
ctx.robot1.move_j(...)   # → SDK MoveJ
ctx.robot1.move_l(...)   # → SDK MoveL
ctx.move_to_point("robot1", "place_entry", step_key="s2a20_10")
```

步速在 HMI「运动参数」按 `step_key` 配置（yaml `motion_steps`）。  
全局倍率：运行监控 SetSpeed%。

---

## 7. 视觉接到流程上（对照程序总览 §5）

```
Station
  → ctx.vision.photo_* / guide_*     ≈ 「视觉 FB」：取图、Mock、监控图
      → algo.*                         ≈ 「算法」：只算结果
          → legacy_pipeline / YOLO
```

| 相机 | Station | 接口概念 |
|------|---------|----------|
| cam1 | 1 | 皮带找鞋 → 基座坐标 |
| cam2 | 2 | 鞋头到位？→ 相对 MoveL |
| cam3 | 3 | 放料槽有无鞋 |
| cam4 | 4/5 | 取料槽 + 压杆偏移 |

**输入输出细节**（给全组也适用）：`algorithm_module/readme.md`。  
本文件不重复全部 API 表，避免两处打架；你改检测时以算法 readme 为准，改步序以 Station 为准。

---

## 8. 目录地图

```
main.py                 入口
config/default.yaml     参数表
core/gvl.py             GVL
core/plc_util.py        Ton / pulse_cmd
core/coordinator.py     OB1
core/app_context.py     创建硬件对象
stations/               ★ CASE 流程
devices/                轴与 IO
vision/ + algorithm_module/
hmi/
docs/程序总览.md         全组版（无 Codesys）
docs/Codesys对照说明.md  本文件
```

---

## 9. 学习路径（按你的背景）

**第 1 天**  
1. 读完本文件 §0～§5。  
2. 打开 `station2_robot1.py`，把 `match A[10]` / `A[20]` 画成你以前的 CASE 表。  
3. 对照 `gvl.py` 的 Mem / Station / Main。

**第 2 天**  
4. `plc_util.py` 全部函数。  
5. `coordinator.py` 的 `cycle()` 调用顺序。

**第 3 天**  
6. `python3 main.py` → 初始化 → 启动 → 空跑或模拟光电。  
7. 看 Mem、各站步号是否按你的 CASE 表跳。

**第 4 天**  
8. 只改一个 `case` 的点名或互锁，Mock 验证。  
9. 再改 yaml 点位 / IP。

**以后**  
10. `devices/robot_fr5.py`、`vision/`、`algorithm_module/readme.md`、`hmi/`。

---

## 10. 改哪里 / 常见卡壳

| 目的 | 改哪里 |
|------|--------|
| 步序、互锁 | `stations/station*.py` |
| IP、点位、Mock、步速 | `config/default.yaml` |
| 按钮 | `hmi/pages/` |
| 视觉 API 说明 | `algorithm_module/readme.md` |
| **Codesys 对照 / Python 从零** | **仅本文件** |

| 现象 | 常见原因 |
|------|----------|
| 停在某步不动 | Move 失败但 pulse 已锁存；看报警与 `recover_stuck_move_cmd` |
| 自动不进站 | Mem 互锁、Busy、未 Running |
| 单步不跳 | 没点「下一步」 |
| 动作抖 / 重复 | 漏了 `pulse_cmd`，每周期重复发令 |
| 改点无效 | 改错机器人；或实际用视觉 `runtime_pick` |

---

## 11. 文档分工（再强调）

| 文档 | 读者 | 内容 |
|------|------|------|
| **本文件** | 你（PLC） | Python 从零 + Codesys 对照 + **同一条主流程** |
| [`程序总览.md`](程序总览.md) | 全组 | **同一条主流程** + 架构（无 Codesys） |
| `algorithm_module/readme.md` | 全组 | 视觉接口详表 |
| HMI「使用说明」 | 现场 | 各页做什么 |
| `操作说明.md` | 现场 | 安装与检查清单 |

补充「像不像 PLC」的内容时，**只追加到本文件**。
