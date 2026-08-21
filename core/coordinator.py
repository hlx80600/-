# =============================================================================
# OB1 / Main 周期任务（最像 PLC 主循环）
#
# 每约 50ms：
#   1) 把 HMI 按钮状态同步到 GVL.Main（Running/Paused/Stop...）
#   2) 跑初始化 CASE
#   3) 并行跑 Station1～6 的 cycle()（各自内部有 Busy + CASE）
#   4) 刷新三色灯
#
# HMI 的「启动/暂停/停止/急停」也在本文件底部 cmd_* 函数里。
# =============================================================================

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

from core.app_context import AppContext
from core.machine_state import MachineState, RunMode
from stations import init_sequence
from stations import station1_belt_photo as s1
from stations import station2_robot1 as s2
from stations import station3_place_slot_photo as s3
from stations import station4_pick_slot_photo as s4
from stations import station5_robot2 as s5
from stations import station6_press_rotate as s6

log = logging.getLogger(__name__)


class StationView:
    """给 HMI 用的薄封装，看起来仍像旧 Station 对象。"""

    def __init__(self, no: int, cycle_fn: Callable, auto_names: Dict[int, str]):
        self.no = no
        self.name = f"Station{no}"
        self._cycle_fn = cycle_fn
        self.autos = {f"Auto_A{k}": k for k in auto_names}
        self._auto_names = auto_names
        self.ctx: Optional[AppContext] = None
        self.fb = None

    def bind(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.fb = ctx.gvl.Station[self.no]

    @property
    def busy(self) -> bool:
        return self.fb.Busy

    def status_text(self) -> str:
        return self.fb.status_text()

    def current_step_name(self) -> str:
        for k, v in self.fb.Auto_A.items():
            if v != 0:
                return f"Auto_A[{k}] 步{v}"
        return "-"

    def active_auto_step(self) -> Optional[tuple]:
        """返回 (auto_key, step) 或 None。"""
        for k, v in sorted(self.fb.Auto_A.items()):
            if v != 0:
                return int(k), int(v)
        return None

    def arm(self, auto_name: str, force: bool = False) -> bool:
        """单步/调试：直接 Auto_A[x]:=10。"""
        return self.arm_at_step(auto_name, 10, force=force)

    def arm_at_step(self, auto_name: str, step: int, force: bool = False) -> bool:
        """武装 Auto 并从指定步号开始（现场跳步调试）。"""
        key = self.autos.get(auto_name)
        if key is None:
            if auto_name.startswith("Auto_A"):
                key = int(auto_name.replace("Auto_A", ""))
        if key is None or key not in self.fb.Auto_A:
            return False
        if self.fb.Busy and not force:
            return False
        self.fb.reset_all_auto()
        self._clear_station_latches()
        self.fb.Auto_A[key] = int(step)
        if self.ctx.machine.state in (
            MachineState.READY,
            MachineState.STOPPED,
            MachineState.PAUSED,
        ):
            self.ctx.machine.set_state(MachineState.RUNNING)
        return True

    def set_step(self, auto_key: int, step: int) -> bool:
        """不改变其它 Auto，只改当前 Auto 的步号并清本站发令锁存。"""
        if auto_key not in self.fb.Auto_A:
            return False
        self._clear_station_latches()
        for k in self.fb.Auto_A:
            self.fb.Auto_A[k] = 0
        self.fb.Auto_A[auto_key] = int(step)
        return True

    def skip_to_next_step(self) -> bool:
        """强制跳到步表中的下一步（或结束=0）。"""
        from stations.step_catalog import next_step_no

        act = self.active_auto_step()
        if not act:
            return False
        auto_key, step = act
        nxt = next_step_no(self.no, auto_key, step)
        self._clear_station_latches()
        self.fb.Auto_A[auto_key] = int(nxt)
        return True

    def refire_current_step(self) -> bool:
        """清发令锁存，使当前步可再次发 Move/夹爪（不改步号）。"""
        act = self.active_auto_step()
        if not act:
            return False
        self._clear_station_latches()
        self.fb.StepPulse = False
        return True

    def _clear_station_latches(self) -> None:
        from core.plc_util import cmd_reset_prefix
        from stations.step_catalog import LATCH_PREFIX

        prefix = LATCH_PREFIX.get(self.no, f"s{self.no}")
        cmd_reset_prefix(self.ctx.gvl, prefix)

    def abort(self) -> None:
        self.fb.reset_all_auto()
        self._clear_station_latches()

    def operator_run_current(self) -> None:
        self.fb.StepPulse = True
        self.ctx.gvl.Main.InitStepPulse = True

    def operator_next(self) -> None:
        self.fb.StepPulse = True
        self.ctx.gvl.Main.InitStepPulse = True

    def retry_failed_step(self) -> None:
        pass

    def cycle(self) -> None:
        self._cycle_fn(self.ctx)


class Coordinator:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.stations: List[StationView] = [
            StationView(1, s1.cycle, {10: "皮带拍照"}),
            StationView(2, s2.cycle, {10: "皮带取料", 20: "鞋槽放料"}),
            StationView(3, s3.cycle, {10: "放料槽拍照"}),
            StationView(4, s4.cycle, {10: "取料槽拍照"}),
            StationView(5, s5.cycle, {10: "取槽", 20: "下料皮带放料"}),
            StationView(6, s6.cycle, {10: "旋转压鞋"}),
        ]
        for st in self.stations:
            st.bind(ctx)
        ctx.stations = {s.name: s for s in self.stations}

        self.init_seq = self

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def busy(self) -> bool:
        return self.ctx.gvl.Main.Initializing

    def status_text(self) -> str:
        a = self.ctx.gvl.Main.Init_Auto
        if not self.ctx.gvl.Main.Initializing:
            return "未在初始化"
        return f"Init 步{a}"

    def start_thread(self) -> None:
        self._thread = threading.Thread(target=self._run, name="OB1", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        scan = float(self.ctx.cfg.get("system", {}).get("scan_time_s", 0.05))
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self.cycle()
            except Exception as e:
                log.exception("OB1 异常: %s", e)
                # 运行/初始化中未捕获异常 → 当报警停机（否则机器人拒动时程序还在跑）
                try:
                    st = self.ctx.machine.state
                    if st in (
                        MachineState.RUNNING,
                        MachineState.INITIALIZING,
                        MachineState.PAUSED,
                        MachineState.READY,
                    ):
                        self._handle_robot_fault("OB1", f"程序扫描异常停机: {e}")
                except Exception:
                    pass
            time.sleep(max(0.0, scan - (time.perf_counter() - t0)))

    def _sync_main_flags(self) -> None:
        gvl = self.ctx.gvl
        m = self.ctx.machine
        state = m.state
        gvl.Main.Mode = m.mode.name
        gvl.Main.DebugBypass = m.debug_bypass
        gvl.Main.EStopped = state == MachineState.ESTOP or self.ctx.io.read_estop()
        gvl.Main.Alarming = state == MachineState.ALARM or self.ctx.alarms.has_alarm
        gvl.Main.Paused = state == MachineState.PAUSED
        gvl.Main.Stop = state == MachineState.STOPPED
        gvl.Main.Running = state == MachineState.RUNNING
        gvl.Main.InitDone = m.init_ok
        for i in range(1, 11):
            gvl.Memory_BOOL[i] = bool(self.ctx.memory[i])

    def cycle(self) -> None:
        # ★ 急停优先：立刻停臂/压机，本周期不再跑任何 Station/初始化运动
        estop_hw = bool(self.ctx.io.read_estop())
        if estop_hw or self.ctx.machine.state == MachineState.ESTOP:
            if estop_hw or self.ctx.machine.state != MachineState.ESTOP:
                self.cmd_estop()
            else:
                self.ctx.robot1.halt_motion()
                self.ctx.robot2.halt_motion()
                try:
                    self.ctx.press.estop_outputs_off()
                except Exception:
                    pass
            self._sync_main_flags()
            self.ctx.update_lights()
            return

        self._sync_main_flags()
        self.ctx.press.refresh_inputs()

        # ★ 每周期查机器人本体报警：有则停流程并弹 HMI 报警
        self._poll_robot_faults()

        # 真机掉线：运行/初始化等过程中立刻停机报警；其后限频重连
        self._poll_device_link_loss()
        try:
            self.ctx.maintain_device_links()
        except Exception as e:
            log.debug("maintain_device_links: %s", e)
        try:
            self.ctx.raise_link_failures_if_needed()
        except Exception as e:
            log.debug("raise_link_failures: %s", e)

        init_sequence.cycle(self.ctx)
        # 空跑屏蔽：在各 Station 之前维持光电/槽位 Mock，避免进入条件卡住
        try:
            self.ctx.dry_run.tick()
        except Exception as e:
            log.debug("dry_run.tick: %s", e)
        for st in self.stations:
            st.cycle()

        for i in range(1, 11):
            if self.ctx.memory[i] != self.ctx.gvl.Memory_BOOL[i]:
                self.ctx.memory[i] = self.ctx.gvl.Memory_BOOL[i]

        self.ctx.update_lights()

    def _poll_robot_faults(self) -> None:
        """真机/Docker 报警或通信断开 → 停 Station + 程序报警。"""
        m = self.ctx.machine
        if m.state in (MachineState.ESTOP, MachineState.ALARM):
            return
        for robot, code in (
            (self.ctx.robot1, "ROBOT1"),
            (self.ctx.robot2, "ROBOT2"),
        ):
            msg = robot.poll_fault()
            if not msg:
                continue
            self._handle_robot_fault(code, msg)
            return

    def _handle_robot_fault(self, code: str, msg: str) -> None:
        log.error("机器人故障停机: %s %s", code, msg)
        # 停两臂运动
        self.ctx.robot1.halt_motion()
        self.ctx.robot2.halt_motion()
        # 清所有 Station / 初始化 / 发令锁存（否则失败步无法再次发 Move）
        gvl = self.ctx.gvl
        for st in gvl.Station.values():
            st.reset_all_auto()
        gvl.clear_cmd_state()
        gvl.Main.Init_Auto = 0
        gvl.Main.Initializing = False
        gvl.Main.Running = False
        self.ctx.raise_alarm(code, msg, "Robot", 0)

    def _poll_device_link_loss(self) -> None:
        """
        非 Mock 设备掉线：在初始化/就绪/运行/暂停中立刻停机报警。
        已处于 ALARM/ESTOP/IDLE/STOPPED 不再重复弹；后台仍会重连。
        """
        m = self.ctx.machine
        if m.state in (
            MachineState.ESTOP,
            MachineState.ALARM,
            MachineState.IDLE,
            MachineState.STOPPED,
        ):
            return
        if m.state not in (
            MachineState.INITIALIZING,
            MachineState.READY,
            MachineState.RUNNING,
            MachineState.PAUSED,
        ):
            return
        missing = self.ctx.missing_real_devices()
        if not missing:
            return
        names = "、".join(f"{r['name']}({r['endpoint']})" for r in missing)
        msg = (
            f"设备掉线停机：{names}。"
            "已停止运动与程序；后台持续重连。"
            "全部连上后请「报警复位」→「初始化」→「启动」。"
        )
        self._handle_link_loss("LINK", msg)

    def _handle_link_loss(self, code: str, msg: str) -> None:
        log.error("设备掉线停机: %s", msg)
        self.ctx.robot1.halt_motion()
        self.ctx.robot2.halt_motion()
        try:
            self.ctx.press.estop_outputs_off()
        except Exception:
            pass
        gvl = self.ctx.gvl
        for st in gvl.Station.values():
            st.reset_all_auto()
        gvl.clear_cmd_state()
        gvl.Main.Init_Auto = 0
        gvl.Main.Initializing = False
        gvl.Main.Running = False
        gvl.Main.InitDone = False
        self.ctx.machine.init_ok = False
        self.ctx.init_message = "设备掉线，需重连后重新初始化"
        self.ctx.raise_link_failures_if_needed()

    def cmd_init(self) -> Optional[str]:
        """返回拒绝原因（None=已开始初始化）。"""
        if self.ctx.machine.state == MachineState.ESTOP:
            return "急停中，请先急停复位"
        if self.ctx.machine.state == MachineState.ALARM or self.ctx.alarms.has_alarm:
            return "报警中，请先报警复位（需设备已全部连接）"
        err = self.ctx.require_all_linked()
        if err:
            return err
        init_sequence.start_init(self.ctx)
        return None

    def cmd_start(self) -> Optional[str]:
        """返回拒绝原因（None=已启动/继续）。"""
        m = self.ctx.machine
        gvl = self.ctx.gvl
        if m.state == MachineState.ESTOP:
            return "急停中，请先急停复位"
        if m.state == MachineState.ALARM or self.ctx.alarms.has_alarm:
            return "报警中，请先报警复位（需设备已全部连接）"
        err = self.ctx.require_all_linked()
        if err:
            return err
        if not gvl.Main.InitDone and m.state != MachineState.READY:
            return "请先完成初始化"
        # 停止后残留：启动前安静消警（不再二次 StopMotion，避免示教器闪红）
        if m.state in (MachineState.READY, MachineState.STOPPED, MachineState.PAUSED):
            for r in (self.ctx.robot1, self.ctx.robot2):
                if r.use_mock or not r.connected:
                    continue
                need = bool(getattr(r, "_need_premove_clear", False))
                fault = r.snapshot_controller_fault() if hasattr(r, "snapshot_controller_fault") else ""
                if not need and not fault:
                    continue
                ok, tip = r.clear_motion_fault_after_stop(
                    reason="启动前消警", stop_first=False, remember=bool(fault)
                )
                log.info("启动前消警: %s", tip)
                if not ok and fault:
                    return (
                        f"控制器仍有故障，无法启动：{fault}\n"
                        "请先点「报警复位」，示教器消红后再启动。"
                    )
        if m.state == MachineState.PAUSED:
            m.set_state(MachineState.RUNNING)
            return None
        if m.state in (MachineState.READY, MachineState.STOPPED):
            m.set_state(MachineState.RUNNING)
            gvl.Main.Stop = False
            return None
        return f"当前状态 {m.state.name} 不可启动"

    def cmd_pause(self) -> None:
        if self.ctx.machine.state == MachineState.RUNNING:
            self.ctx.machine.set_state(MachineState.PAUSED)

    def cmd_stop(self) -> None:
        """停止：立刻停双臂（StopMotion，不用 ImmStop），清 Auto。"""
        log.warning("!!! 停止触发：立即停止所有运动 !!!")
        # 普通停止不用 ImmStop，减少留下 err=185 无法再动
        self.ctx.robot1.halt_motion(hard=False)
        self.ctx.robot2.halt_motion(hard=False)
        try:
            self.ctx.press.estop_outputs_off()
        except Exception as e:
            log.error("停止时压机输出关闭失败: %s", e)
        gvl = self.ctx.gvl
        gvl.Main.Stop = True
        gvl.Main.Running = False
        gvl.Main.Paused = False
        gvl.Main.Initializing = False
        gvl.Main.Init_Auto = 0
        for st in gvl.Station.values():
            st.reset_all_auto()
        gvl.clear_cmd_state()
        self.ctx.machine.set_state(MachineState.STOPPED)
        # 停止后立刻消掉「故障信号」，下次启动可直接 Move（仅此路径允许 Reset，连拍中不预消警）
        for r in (self.ctx.robot1, self.ctx.robot2):
            try:
                fault = (
                    r.snapshot_controller_fault()
                    if hasattr(r, "snapshot_controller_fault")
                    else ""
                )
                # StopMotion 后常无主子码但仍会 Move=185：停止后必须消一次
                ok, tip = r.clear_motion_fault_after_stop(
                    reason="停止后消警",
                    stop_first=False,
                    remember=bool(fault),
                )
                log.info("停止后消警: %s", tip)
            except Exception as e:
                log.warning("停止后消警失败: %s", e)
        try:
            self.ctx.update_lights()
        except Exception:
            pass

    def cmd_estop(self) -> None:
        """急停：ImmStop+StopMotion，清所有 Auto。再动须急停复位+报警复位。"""
        log.error("!!! 急停触发：立即停止所有运动 !!!")
        self.ctx.robot1.soft_estop()
        self.ctx.robot2.soft_estop()
        try:
            self.ctx.press.estop_outputs_off()
        except Exception as e:
            log.error("压机急停输出关闭失败: %s", e)
        self.ctx.gvl.Main.EStopped = True
        self.ctx.gvl.Main.Running = False
        self.ctx.gvl.Main.Paused = False
        self.ctx.gvl.Main.Initializing = False
        self.ctx.gvl.Main.Init_Auto = 0
        for st in self.ctx.gvl.Station.values():
            st.reset_all_auto()
        self.ctx.gvl.clear_cmd_state()
        self.ctx.machine.set_state(MachineState.ESTOP)
        try:
            self.ctx.update_lights()
        except Exception:
            pass

    def cmd_reset_estop(self) -> None:
        self.ctx.robot1.clear_estop()
        self.ctx.robot2.clear_estop()
        self.ctx.io.set_estop_mock(False)
        self.ctx.gvl.Main.EStopped = False
        for st in self.ctx.gvl.Station.values():
            st.reset_all_auto()
        self.ctx.gvl.clear_cmd_state()
        self.ctx.machine.set_state(MachineState.IDLE)

    def cmd_alarm_reset(self):
        """
        HMI 报警复位：
        1) 真机设备须已全部连接（否则拒绝，后台继续重连）
        2) 真机：StopMotion → Mode(0) → ResetAllError → RobotEnable(1)
        3) 清发令锁存；掉线类报警清除后强制回 IDLE，需重新初始化
        返回说明列表供 HMI 弹窗。
        """
        tips: List[str] = []
        link_err = self.ctx.require_all_linked()
        if link_err:
            tips.append(link_err)
            tips.append("后台持续重连中：全部设备连上后再点「报警复位」→「初始化」→「启动」。")
            return tips

        any_fail = False
        for r in (self.ctx.robot1, self.ctx.robot2):
            ok, tip = r.reset_controller_errors()
            tips.append(tip)
            if not ok:
                any_fail = True
                log.warning("控制器消警未完全成功: %s", tip)

        was_link = bool(
            self.ctx.alarms.active and self.ctx.alarms.active.code == "LINK"
        )
        need_reinit = was_link or (not self.ctx.machine.init_ok) or (
            not self.ctx.gvl.Main.InitDone
        )

        self.ctx.alarms.reset()
        gvl = self.ctx.gvl
        gvl.Main.Alarming = False
        for st in gvl.Station.values():
            st.reset_all_auto()
        gvl.clear_cmd_state()
        # 勿在消警成功后再 halt/StopMotion：会再次留下 err=185，下一拍 MoveL 立刻失败
        self.ctx.robot1.inject_fault_mock(None)
        self.ctx.robot2.inject_fault_mock(None)
        for r in (self.ctx.robot1, self.ctx.robot2):
            r._last_fault_key = None
            r._moving = False
            r._move_cmd_sent = False
        if self.ctx.machine.state == MachineState.ALARM:
            if need_reinit:
                gvl.Main.InitDone = False
                self.ctx.machine.init_ok = False
                self.ctx.machine.set_state(MachineState.IDLE)
            elif self.ctx.machine.init_ok:
                self.ctx.machine.set_state(MachineState.PAUSED)
            else:
                self.ctx.machine.set_state(MachineState.IDLE)
        if tips:
            log.info("报警复位: %s", " | ".join(tips))
        if any_fail:
            tips.append("程序侧报警已清除；示教器若仍报红，请按提示处理急停/外部故障后再点一次报警复位。")
        elif need_reinit:
            tips.append("程序侧报警已清除。请重新「初始化」，完成后再「启动」。")
        else:
            tips.append("程序侧报警已清除，状态为暂停，请再点「启动」。")
        return tips
