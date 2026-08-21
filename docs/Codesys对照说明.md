# Codesys 写法对照（方便电气工程师阅读）

流程已改成接近你 Codesys 的结构，优先看：

| Codesys | 本项目 |
|---------|--------|
| `GVL_Memory.Memory_BOOL[i]` | `ctx.gvl.Memory_BOOL[i]` |
| `GVL_Station.Station[2].Auto_A[10]` | `ctx.gvl.Station[2].Auto_A[10]` |
| `Busy` | `Station[n].Busy`（各 `cycle` 开头 `update_busy()`） |
| `Main.Running / Paused / Stop` | `ctx.gvl.Main.*` |
| `IF 条件 THEN Auto_A[10]:=10` | 各 station 文件前半段进入条件 |
| `CASE Auto_A[10] OF 10: 20:` | `if A[10]==10: ... elif A[10]==20:` |
| `Ton` 延时 | `delay_start` / `delay_done`（更简单） |
| OB1 周期扫描 | `core/coordinator.py` 的 `cycle()` |

## 推荐阅读顺序

1. `core/gvl.py` —— 全局变量表  
2. `stations/station2_robot1.py` —— 最完整的 Busy + 进入 + CASE 示例  
3. 其它 `stations/station*.py`  
4. `core/coordinator.py` —— 主扫描 / 启停急停  

## 单步

单步模式下一步号到了条件满足后，要等 HMI `StepPulse`（点「执行当前步/下一步」）才 `Auto_A:=下一档`。  
自动模式则条件满足立即跳步。
