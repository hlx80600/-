"""
Station 流程（Codesys CASE 风格）。

每个 stationX.py 提供 cycle(ctx)，结构为：
  1) Busy
  2) Stop/EStop/Alarm 清 Auto_A
  3) IF 进入条件 THEN Auto_A[10]:=10
  4) IF NOT Paused THEN CASE Auto_A[10] OF ...
"""
