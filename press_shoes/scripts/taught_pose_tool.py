#!/usr/bin/env python3
"""示教位姿交互工具：在同一界面中切换「移动到示教位」与「更新示教位」。

快捷键:
    Tab         切换模式（移动 / 更新），保持当前机械臂
    Shift+Tab   切换机械臂（投鞋 / 取鞋），保持当前模式
    ↑ / ↓       选择位姿或路由
    Enter       执行当前模式操作
    c / j       [移动模式] 切换笛卡尔 MoveCart / 关节 MoveJ
    q           退出

用法:
    python press_shoes/scripts/taught_pose_tool.py
"""

from __future__ import annotations

import curses
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from press_shoes.robot_arm.fairino_robot_arm_inherit import FairinoRobotArm
import move_to_taught_pose as move_mod
import update_taught_pose as update_mod

ARM_LIST = [update_mod.ARM_CONFIGS["1"], update_mod.ARM_CONFIGS["2"]]
MODES = ("move", "update")
MODE_LABELS = {"move": "移动到示教位", "update": "更新示教位"}


class TaughtPoseApp:
    def __init__(self, stdscr: Any) -> None:
        self.stdscr = stdscr
        self.arm_idx = 0
        self.mode = "move"
        self.motion_mode = "cart"
        self.selected = 0
        self.msg = ""
        self.arm: Optional[FairinoRobotArm] = None
        self.cfg: Dict = {}
        self.tool = 0
        self.spline_speed = 100.0
        self.move_items: List[Dict[str, str]] = []
        self.update_items: List[Tuple[str, str, str]] = []

        curses.curs_set(0)
        self.stdscr.timeout(200)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)

    def _arm_cfg(self) -> Dict[str, str]:
        return ARM_LIST[self.arm_idx]

    def _reload_arm(self) -> None:
        arm_cfg = self._arm_cfg()
        filename = arm_cfg["filename"]
        self.cfg = update_mod.load_config(filename)
        arm_ip = self.cfg.get("arm_ip", "fake")
        if arm_ip == "fake":
            raise RuntimeError(f"{filename} 的 arm_ip 为 'fake'，请先配置真实 IP。")

        self.tool = int(self.cfg.get("tool", 0))
        self.spline_speed = float(self.cfg.get("spline_speed", 100.0) or 100.0)

        pose_options = update_mod.build_pose_options(self.cfg)
        self.update_items = list(pose_options.values())
        self.move_items = move_mod.build_options(self.cfg)

        if self.arm is not None:
            self.arm = None

        self.arm = FairinoRobotArm(arm_cfg["arm_name"], robot_ip=arm_ip)
        if not self.arm.ConnectRobotArm():
            raise RuntimeError(f"连接 {arm_cfg['arm_name']} 失败 ({arm_ip})")

        self.selected = 0
        self.msg = f"已连接 {arm_cfg['desc']}"

    def _item_count(self) -> int:
        return len(self.move_items) if self.mode == "move" else len(self.update_items)

    def _clamp_selected(self) -> None:
        count = self._item_count()
        if count <= 0:
            self.selected = 0
        elif self.selected >= count:
            self.selected = count - 1

    def _toggle_mode(self) -> None:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self._clamp_selected()
        self.msg = f"已切换到 {MODE_LABELS[self.mode]}"

    def _toggle_arm(self) -> None:
        self.arm_idx = (self.arm_idx + 1) % len(ARM_LIST)
        try:
            self._reload_arm()
        except RuntimeError as exc:
            self.msg = str(exc)

    def _toggle_motion(self) -> None:
        self.motion_mode = "joint" if self.motion_mode == "cart" else "cart"
        label = "MoveCart" if self.motion_mode == "cart" else "MoveJ"
        self.msg = f"运动模式: {label}"

    def _pause_curses(self) -> None:
        curses.endwin()

    def _resume_curses(self) -> None:
        self.stdscr.clear()
        self.stdscr.refresh()

    def _prompt_confirm(self, text: str) -> bool:
        self._pause_curses()
        try:
            answer = input(f"\n{text} (y/N): ").strip().lower()
            return answer == "y"
        finally:
            self._resume_curses()

    def _prompt_enter(self, text: str) -> bool:
        self._pause_curses()
        try:
            input(f"\n{text}，按回车继续，Ctrl+C 取消...")
            return True
        except KeyboardInterrupt:
            return False
        finally:
            self._resume_curses()

    def _execute_update(self) -> None:
        if not self.update_items:
            self.msg = "没有可更新的位姿对"
            return
        pose_key, joint_key, desc = self.update_items[self.selected]
        if not self._prompt_enter(f"请将机械臂移动到 [{desc}]，然后按回车采集"):
            self.msg = "已取消采集"
            return

        tcp_pose, joint = update_mod.get_current_pose_and_joint(self.arm)
        self._pause_curses()
        print(f"\n采集 {desc}:")
        print(f"  TCP: {[round(v, 4) for v in tcp_pose]}")
        print(f"  关节: {[round(v, 4) for v in joint]}")
        self._resume_curses()

        if not self._prompt_confirm(f"确认写入 {pose_key} / {joint_key}"):
            self.msg = "已取消写入"
            return

        self.cfg[pose_key] = tcp_pose
        self.cfg[joint_key] = joint
        update_mod.save_config(self._arm_cfg()["filename"], self.cfg)
        self.msg = f"已更新 {pose_key}"

    def _execute_move_pose(self, opt: Dict[str, str]) -> None:
        pose_key = opt["key"]
        joint_key = opt["joint_key"]
        pose = self.cfg.get(pose_key, [])
        if not move_mod._is_6d_pose(pose):
            self.msg = f"{pose_key} 无效"
            return
        target_pose = [float(v) for v in pose[:6]]

        if self.motion_mode == "cart":
            if not self._prompt_enter(f"MoveCart -> {pose_key}"):
                self.msg = "已取消"
                return
            ret = self.arm.MoveCart(
                desc_pos=target_pose, tool=self.tool, user=0, vel=50, ovl=100.0, blendT=-1.0
            )
        else:
            joint = self.cfg.get(joint_key, [])
            if not move_mod._is_6d_pose(joint):
                self.msg = f"{joint_key} 无效"
                return
            target_joint = [float(v) for v in joint[:6]]
            if not self._prompt_enter(f"MoveJ -> {pose_key}"):
                self.msg = "已取消"
                return
            ret = self.arm.MoveJ(
                joint_pos=target_joint,
                desc_pos=target_pose,
                tool=self.tool,
                user=0,
                vel=100,
                ovl=100.0,
                blendT=-1.0,
            )

        self.msg = "移动成功" if ret == 0 else f"移动失败，返回值 {ret}"

    def _execute_move_route(self, opt: Dict[str, str]) -> None:
        move_key = opt["key"]
        points = move_mod.resolve_move_points(self.cfg, move_key)
        if len(points) < 2:
            self.msg = f"{move_key} 有效点不足 2 个"
            return

        self._pause_curses()
        print(f"\n路由 {move_key}，共 {len(points)} 点，速度 {self.spline_speed} mm/s")
        for i, p in enumerate(points):
            print(f"  [{i}] {[round(v, 3) for v in p]}")
        self._resume_curses()

        if not self._prompt_enter(f"RobotServoSpline -> {move_key}"):
            self.msg = "已取消"
            return

        ret = self.arm.RobotServoSpline(
            points=points,
            speed_mm_s=self.spline_speed,
            max_ori_step_deg=0.4,
            tool=self.tool,
            user=0,
        )
        self.msg = "样条运动成功" if ret == 0 else f"样条运动失败，返回值 {ret}"

    def _execute(self) -> None:
        if self.arm is None:
            self.msg = "机械臂未连接"
            return
        if self.mode == "update":
            self._execute_update()
            return

        if not self.move_items:
            self.msg = "没有可执行的位姿"
            return
        opt = self.move_items[self.selected]
        if opt["kind"] == "pose":
            self._execute_move_pose(opt)
        else:
            self._execute_move_route(opt)

    def _draw_header(self, width: int) -> int:
        arm_cfg = self._arm_cfg()
        mode_label = MODE_LABELS[self.mode]
        motion = ""
        if self.mode == "move":
            motion = f" | 运动: {'MoveCart' if self.motion_mode == 'cart' else 'MoveJ'} (c/j)"

        line1 = f" 机械臂: {arm_cfg['desc']} ({arm_cfg['filename']}) "
        line2 = f" 模式: {mode_label}{motion} "
        help_line = " Tab=切换模式  Shift+Tab/s=切换机械臂  ↑↓=选择  Enter=执行  c/j=运动模式  q=退出 "

        if curses.has_colors():
            self.stdscr.attron(curses.color_pair(1))
        self.stdscr.addstr(0, 0, line1[: max(0, width - 1)])
        self.stdscr.addstr(1, 0, line2[: max(0, width - 1)])
        self.stdscr.attroff(curses.color_pair(1) if curses.has_colors() else 0)
        self.stdscr.addstr(2, 0, help_line[: max(0, width - 1)])
        return 4

    def _draw_list(self, start_y: int, height: int, width: int) -> None:
        if self.mode == "move":
            items = self.move_items
            for i, opt in enumerate(items):
                if i >= height:
                    break
                y = start_y + i
                tag = "[样条] " if opt["kind"] == "move" else "[位姿] "
                label = f"{tag}{opt['desc']}"
                prefix = "> " if i == self.selected else "  "
                line = prefix + label
                if i == self.selected and curses.has_colors():
                    self.stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                self.stdscr.addstr(y, 0, line[: max(0, width - 1)])
                if i == self.selected and curses.has_colors():
                    self.stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        else:
            for i, (_, _, desc) in enumerate(self.update_items):
                if i >= height:
                    break
                y = start_y + i
                prefix = "> " if i == self.selected else "  "
                line = prefix + desc
                if i == self.selected and curses.has_colors():
                    self.stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                self.stdscr.addstr(y, 0, line[: max(0, width - 1)])
                if i == self.selected and curses.has_colors():
                    self.stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

        if self._item_count() == 0:
            self.stdscr.addstr(start_y, 0, "  (无可用项)")

    def _draw_status(self, y: int, width: int) -> None:
        color = curses.color_pair(3) if curses.has_colors() else 0
        if self.msg.startswith("失败") or "无效" in self.msg or "不足" in self.msg:
            color = curses.color_pair(4) if curses.has_colors() else 0
        if curses.has_colors():
            self.stdscr.attron(color)
        self.stdscr.addstr(y, 0, f" {self.msg}"[: max(0, width - 1)])
        if curses.has_colors():
            self.stdscr.attroff(color)

    def _draw(self) -> None:
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        header_rows = self._draw_header(width)
        list_height = max(1, height - header_rows - 2)
        self._draw_list(header_rows, list_height, width)
        self._draw_status(height - 1, width)
        self.stdscr.refresh()

    def run(self) -> None:
        try:
            self._reload_arm()
        except RuntimeError as exc:
            self.msg = str(exc)

        while True:
            self._draw()
            key = self.stdscr.getch()
            if key == -1:
                continue
            if key in (ord("q"), ord("Q")):
                break
            if key == 9:  # Tab
                self._toggle_mode()
                continue
            if key == curses.KEY_BTAB:  # Shift+Tab
                self._toggle_arm()
                continue
            if key in (ord("s"), ord("S")):
                self._toggle_arm()
                continue
            if key == curses.KEY_UP:
                if self.selected > 0:
                    self.selected -= 1
                continue
            if key == curses.KEY_DOWN:
                if self.selected < self._item_count() - 1:
                    self.selected += 1
                continue
            if key in (10, 13, curses.KEY_ENTER):
                self._execute()
                continue
            if self.mode == "move" and key in (ord("c"), ord("C")):
                self.motion_mode = "cart"
                self.msg = "运动模式: MoveCart"
                continue
            if self.mode == "move" and key in (ord("j"), ord("J")):
                self.motion_mode = "joint"
                self.msg = "运动模式: MoveJ"
                continue


def main() -> None:
    def _wrapper(stdscr: Any) -> None:
        app = TaughtPoseApp(stdscr)
        app.run()

    try:
        curses.wrapper(_wrapper)
    except KeyboardInterrupt:
        print("\n已中断退出。")


if __name__ == "__main__":
    main()
