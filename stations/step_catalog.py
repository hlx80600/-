"""
各 Station / Auto_A 步表（给 HMI 单步调试用）。

字段：
  step     步号
  title    短标题
  detail   说明
  kind     move_j / move_l / grip / delay / vision / mem / io / wait / other
  robot    robot1 / robot2 / ""
  points   关联配置点名列表（便于现场加过渡点）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# station_no -> auto_key -> [step dict, ...]
STEP_CATALOG: Dict[int, Dict[int, List[Dict[str, Any]]]] = {
    1: {
        10: [
            {"step": 10, "title": "皮带拍照", "detail": "相机1/屏蔽示教 → 候选鞋", "kind": "vision", "points": []},
            {"step": 20, "title": "解析/重试", "detail": "失败则延时重拍", "kind": "wait", "points": []},
            {"step": 30, "title": "写PickPose+Mem1", "detail": "XYRz写入取料位，Mem[1]=True", "kind": "mem", "points": []},
        ],
    },
    2: {
        10: [
            {"step": 10, "title": "张爪", "detail": "上料夹爪张开，等张开完成", "kind": "grip", "robot": "robot1", "points": []},
            {"step": 20, "title": "(跳过)", "detail": "原固定延时已取消，直接进下一步", "kind": "other", "points": []},
            {
                "step": 30,
                "title": "MoveJ→取料进入点",
                "detail": "到 pick_entry（建议有示教关节）",
                "kind": "move_j",
                "robot": "robot1",
                "points": ["pick_entry"],
            },
            {
                "step": 40,
                "title": "MoveL→取料上方",
                "detail": "PickPose + pick_above_offset（直线，勿用无关节MoveJ/MoveCart）",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["pick_above_offset"],
            },
            {
                "step": 50,
                "title": "MoveL→取料点",
                "detail": "下压到 PickPose",
                "kind": "move_l",
                "robot": "robot1",
                "points": [],
            },
            {"step": 60, "title": "夹爪", "detail": "夹紧，等夹紧完成+抓鞋负载(仍抓取TCP)", "kind": "grip", "robot": "robot1", "points": []},
            {"step": 70, "title": "(跳过)", "detail": "原固定延时已取消", "kind": "other", "points": []},
            {
                "step": 80,
                "title": "MoveL→取料上方",
                "detail": "抬起(抓取TCP，与下降同系)",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["pick_above_offset"],
            },
            {
                "step": 90,
                "title": "MoveJ→取料进入点",
                "detail": "退出工作区(抓取TCP)",
                "kind": "move_j",
                "robot": "robot1",
                "points": ["pick_entry"],
            },
            {"step": 100, "title": "写记忆", "detail": "已回进入点后：Mem2=1；过渡仍工具1", "kind": "mem", "points": []},
        ],
        20: [
            {
                "step": 10,
                "title": "MoveJ→放料进入点",
                "detail": "到 place_entry",
                "kind": "move_j",
                "robot": "robot1",
                "points": ["place_entry"],
            },
            {
                "step": 20,
                "title": "MoveL→放料上方",
                "detail": "place_slot + place_above_offset（直线）",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["place_slot", "place_above_offset"],
            },
            {
                "step": 30,
                "title": "MoveL→放料接近",
                "detail": "工具1到 place_slot；记录鞋头TCP",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["place_slot"],
            },
            {
                "step": 35,
                "title": "换工具关节同步",
                "detail": "仅 toe_tcp_switch_motion_tool=true",
                "kind": "move_j",
                "robot": "robot1",
                "points": [],
            },
            {
                "step": 40,
                "title": "鞋头对位推进",
                "detail": "相对推进+改姿态(默认仍工具1)",
                "kind": "vision",
                "robot": "robot1",
                "points": ["place_slot"],
            },
            {
                "step": 45,
                "title": "绕鞋头压跟",
                "detail": "旋转使鞋底水平",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["place_slot"],
            },
            {"step": 50, "title": "张爪", "detail": "放料，等张开完成并恢复手爪TCP", "kind": "grip", "robot": "robot1", "points": []},
            {"step": 60, "title": "(跳过)", "detail": "原固定延时已取消", "kind": "other", "points": []},
            {
                "step": 70,
                "title": "MoveL→放料上方",
                "detail": "抬起",
                "kind": "move_l",
                "robot": "robot1",
                "points": ["place_slot", "place_above_offset"],
            },
            {
                "step": 80,
                "title": "MoveJ→放料进入点",
                "detail": "退出",
                "kind": "move_j",
                "robot": "robot1",
                "points": ["place_entry"],
            },
            {"step": 90, "title": "写记忆", "detail": "已回进入点后：Mem2=0 Mem3=1 清Mem8/9", "kind": "mem", "points": []},
        ],
    },
    3: {
        10: [
            {"step": 10, "title": "放料槽拍照", "detail": "相机3", "kind": "vision", "points": []},
            {"step": 20, "title": "写Mem3/4/10", "detail": "有料/方向互锁", "kind": "mem", "points": []},
        ],
    },
    4: {
        10: [
            {"step": 10, "title": "取料槽拍照", "detail": "相机4", "kind": "vision", "points": []},
            {"step": 20, "title": "写Mem6/7", "detail": "有料标志", "kind": "mem", "points": []},
        ],
    },
    5: {
        10: [
            {"step": 10, "title": "张爪", "detail": "下料夹爪张开，等张开完成", "kind": "grip", "robot": "robot2", "points": []},
            {"step": 20, "title": "(跳过)", "detail": "原固定延时已取消", "kind": "other", "points": []},
            {
                "step": 30,
                "title": "MoveJ→取槽进入点",
                "detail": "slot_pick_entry",
                "kind": "move_j",
                "robot": "robot2",
                "points": ["slot_pick_entry"],
            },
            {
                "step": 40,
                "title": "MoveL→取槽上方",
                "detail": "slot_pick + offset（直线）",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["slot_pick", "slot_pick_above_offset"],
            },
            {
                "step": 50,
                "title": "MoveL→取槽点",
                "detail": "固定示教 slot_pick",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["slot_pick"],
            },
            {"step": 60, "title": "夹爪", "detail": "夹紧，等夹紧完成", "kind": "grip", "robot": "robot2", "points": []},
            {"step": 70, "title": "(跳过)", "detail": "原固定延时已取消", "kind": "other", "points": []},
            {
                "step": 80,
                "title": "MoveL→取槽上方",
                "detail": "抬起",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["slot_pick", "slot_pick_above_offset"],
            },
            {
                "step": 90,
                "title": "MoveJ→取槽进入点",
                "detail": "退出",
                "kind": "move_j",
                "robot": "robot2",
                "points": ["slot_pick_entry"],
            },
            {"step": 100, "title": "写记忆", "detail": "已回进入点后：Mem5=1 Mem6=0", "kind": "mem", "points": []},
        ],
        20: [
            {
                "step": 10,
                "title": "MoveJ→皮带放料进入点",
                "detail": "belt_place_entry",
                "kind": "move_j",
                "robot": "robot2",
                "points": ["belt_place_entry"],
            },
            {
                "step": 20,
                "title": "MoveL→皮带放料上方",
                "detail": "belt_place + offset（直线）",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["belt_place", "belt_place_above_offset"],
            },
            {
                "step": 30,
                "title": "MoveL→皮带放料点",
                "detail": "belt_place",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["belt_place"],
            },
            {"step": 40, "title": "张爪", "detail": "等张开完成", "kind": "grip", "robot": "robot2", "points": []},
            {"step": 50, "title": "(跳过)", "detail": "原固定延时已取消", "kind": "other", "points": []},
            {
                "step": 60,
                "title": "MoveL→放料上方",
                "detail": "抬起",
                "kind": "move_l",
                "robot": "robot2",
                "points": ["belt_place", "belt_place_above_offset"],
            },
            {
                "step": 70,
                "title": "MoveJ→放料进入点",
                "detail": "退出",
                "kind": "move_j",
                "robot": "robot2",
                "points": ["belt_place_entry"],
            },
            {"step": 80, "title": "写记忆+产量", "detail": "已回进入点后：Mem5=0", "kind": "mem", "points": []},
        ],
    },
    6: {
        10: [
            {"step": 10, "title": "启压鞋(或跳过)", "detail": "清Mem7/4；按左口放料槽号对slots[N]发压杆/底座/压合；Mem10=1跳过", "kind": "io", "points": []},
            {
                "step": 20,
                "title": "等压鞋完成",
                "detail": "放料口 press_done / 侧状态就绪",
                "kind": "wait",
                "points": [],
            },
            {
                "step": 30,
                "title": "启旋转",
                "detail": "关压鞋令后 set_rotate",
                "kind": "io",
                "points": [],
            },
            {
                "step": 40,
                "title": "等旋转完成",
                "detail": "rotate_done",
                "kind": "wait",
                "points": [],
            },
            {
                "step": 50,
                "title": "清输出+推进槽号",
                "detail": "关令；按12341/43214推进取放料槽号；Mem10/3/7=0",
                "kind": "mem",
                "points": [],
            },
        ],
    },
}

# 发令锁存前缀（停机/跳步时清）
LATCH_PREFIX: Dict[int, str] = {
    1: "s1",
    2: "s2",
    3: "s3",
    4: "s4",
    5: "s5",
    6: "s6",
}

AUTO_TITLES: Dict[int, Dict[int, str]] = {
    1: {10: "皮带拍照"},
    2: {10: "皮带取料", 20: "鞋槽放料(鞋头对位)"},
    3: {10: "放料槽拍照"},
    4: {10: "取料槽拍照"},
    5: {10: "鞋槽取料", 20: "下料皮带放料"},
    6: {10: "旋转压鞋"},
}


def steps_for(station_no: int, auto_key: int) -> List[Dict[str, Any]]:
    return list(STEP_CATALOG.get(station_no, {}).get(auto_key, []))


def find_step(station_no: int, auto_key: int, step: int) -> Optional[Dict[str, Any]]:
    for s in steps_for(station_no, auto_key):
        if int(s["step"]) == int(step):
            return s
    return None


def next_step_no(station_no: int, auto_key: int, step: int) -> int:
    """返回下一步号；没有则 0（结束）。"""
    steps = steps_for(station_no, auto_key)
    nums = [int(s["step"]) for s in steps]
    if step not in nums:
        return 0
    i = nums.index(step)
    if i + 1 < len(nums):
        return nums[i + 1]
    return 0


def auto_title(station_no: int, auto_key: int) -> str:
    return AUTO_TITLES.get(station_no, {}).get(auto_key, f"Auto_A[{auto_key}]")
