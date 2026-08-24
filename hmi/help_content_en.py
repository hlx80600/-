"""English help sections for Help page."""

from __future__ import annotations

from typing import List

from hmi.help_content import Section, _L, _code, _h, _io_block, _ol, _p, _ul
from hmi.tab_titles import T


def build_sections_en() -> List[Section]:
    return [
        (
            "overview",
            "Overview: navigation, lights, memory, flow",
            _h("How to use this page")
            + _p(
                "This Help page is the on-site operator manual. Browse chapters on the left; search above. "
                "Each page documents purpose, UI files, dependencies, and who calls it."
            )
            + _p(
                f"The main window uses a left navigation list (not top tabs). "
                f"Daily production starts on **{_L(T.MONITOR)}**; long explanations live here only."
            )
            + _h("Entry & main scan")
            + _ul(
                [
                    f"Entry: {_code('main.py')} → Coordinator + MainWindow.",
                    f"Main loop: {_code('core/coordinator.py')} ~50 ms station cycles.",
                    f"Global state: {_code('core/gvl.py')} (Main / Station / Memory_BOOL).",
                    f"Devices & vision: {_code('core/app_context.py')}.",
                    f"Parameters: {_code('config/default.yaml')}.",
                    "Repo docs: docs/程序总览.md; vision API: algorithm_module/readme.md.",
                ]
            )
            + _h("One shoe in auto mode")
            + _ul(
                [
                    "Station1 belt photo → pick coordinates",
                    "Station2 load arm pick → (Station3 place slot photo) → place & press heel gripper",
                    "Station6 press → turntable advances slot",
                    "Station4 pick slot photo + rod → Station5 unload to belt",
                    "Step details: Stations chapter or docs/程序总览.md",
                ]
            )
            + _h("New machine commissioning order")
            + _ol(
                [
                    f"<b>{_L(T.SETTINGS)}</b> or Comm tab: IP / CAN / camera serial / Mock → save → <b>restart</b>",
                    f"<b>{_L(T.PAYLOAD)}</b>: tool TCP & payload → save & push",
                    f"<b>{_L(T.POINTS)}</b>: teach entry, slot, offsets → save",
                    f"<b>{_L(T.MOTION)}</b>: step speeds; disable blend on retreat if needed",
                    f"<b>{_L(T.PRESS_IO)}</b>: slot sequence & addresses",
                    f"<b>{_L(T.VISION)}</b>: models; ROI → intrinsics → hand-eye → test → capture/train",
                    f"<b>{_L(T.SHIELD_PICK)}</b>: teach pick when cam1 Mock",
                    f"<b>{_L(T.GRIPPER)}</b>: verify open/close",
                    f"<b>{_L(T.DRY_RUN)}</b> / <b>{_L(T.STEP_DEBUG)}</b>: dry-run or single step",
                    f"<b>{_L(T.MONITOR)}</b>: Auto → Initialize → Start",
                ]
            )
            + _h("Page index")
            + _ul(
                [
                    f"<b>{_L(T.MONITOR)}</b>: start/stop, lights, memory, slots, speed, manual gripper/press",
                    f"<b>{_L(T.CAM_MONITOR)}</b>: separate window, 4 cameras + inference overlay",
                    f"<b>{_L(T.PRODUCTION)}</b>: count / CT / UPH",
                    f"<b>{_L(T.STEP_DEBUG)}</b>: per-station single step",
                    f"<b>{_L(T.MOTION)}</b>: per-step vel / blend",
                    f"<b>{_L(T.VISION)}</b>: hub (ROI / chessboard / hand-eye / test / train)",
                    f"<b>{_L(T.POINTS)}</b>: taught points",
                    f"<b>{_L(T.SHIELD_PICK)}</b>: cam1 Mock pick teach",
                    f"<b>{_L(T.DRY_RUN)}</b>: dry-run shields",
                    f"<b>{_L(T.PAYLOAD)}</b>: payload & TCP",
                    f"<b>{_L(T.PRESS_IO)}</b>: press slots & Modbus",
                    f"<b>{_L(T.GRIPPER)}</b>: gripper debug & GRIP_* reset",
                    f"<b>{_L(T.SETTINGS)}</b>: language, UI, comm",
                    f"<b>{_L(T.ALARM)}</b>: alarms",
                ]
            )
            + _h("Stack lights")
            + _ul(
                [
                    "Green steady = auto running",
                    "Yellow + green = init done (READY, can start)",
                    "Yellow steady = stopped / not initialized",
                    "Yellow blink = initializing / paused / single step",
                    "Red steady = alarm",
                    "Red blink = E-stop",
                ]
            )
            + _h("Memory Mem1–10")
            + _p(
                "Internal BOOL flags ("
                + _code("core/gvl.py")
                + " Memory_BOOL). Locked while auto running; editable when idle/paused/stopped/alarm/step."
            )
            + _ul(
                [
                    "1 belt photo done; 2 load gripper has shoe; 3 place slot has shoe; 4 place photo done",
                    "5 unload gripper has shoe; 6 pick slot has shoe; 7 pick photo done; 8/9 left/right shoe; 10 place mismatch",
                ]
            ),
        ),
        (
            "monitor",
            _L(T.MONITOR),
            _io_block(
                purpose=(
                    "Main operator desk: init, auto/step mode, start/pause/stop/E-stop; stack lights & station busy; "
                    "edit memory & slots; global speed & path blend; manual gripper/press; dry-run shortcuts."
                ),
                impl=[
                    f"{_code('hmi/pages/monitor_page.py')} — UI",
                    f"{_code('hmi/main_window.py')} — nav + refresh",
                ],
                refs=[
                    f"{_code('core/coordinator.py')} — init / mode / start-stop",
                    f"{_code('core/lights.py')} — stack lights",
                    f"{_code('core/memory.py')} — Mem read/write",
                    f"{_code('devices/robot_fr5.py')} — SetSpeed",
                    f"{_code('devices/gripper_can.py')} / {_code('devices/press_modbus.py')} — manual",
                    f"{_code('core/dry_run_shield.py')} — Start dry-run program",
                ],
                used_by=[
                    "Operators use daily",
                    f"Step Next links {_L(T.STEP_DEBUG)}",
                    f"Dry-run program links {_L(T.DRY_RUN)}",
                ],
            )
            + _h("Key steps")
            + _ol(
                [
                    "Mode Auto → Initialize → READY (yellow+green) → Start.",
                    "When paused you can edit memory/slots; after Stop re-init before Start.",
                    "After E-stop: reset E-stop → Initialize again.",
                    "Arm speed ≈ Monitor SetSpeed% × Motion page step vel%.",
                ]
            ),
        ),
        (
            "cam_monitor",
            _L(T.CAM_MONITOR),
            _io_block(
                purpose=(
                    "Separate window: cam1–4 raw frames and inference. "
                    "Refresh raw in background; live inference uses cached frames."
                ),
                impl=[
                    f"{_code('hmi/pages/vision_monitor_page.py')}",
                    f"{_code('hmi/main_window.py')} — show_cam_monitor",
                ],
                refs=[
                    f"{_code('visualize_module/')} — LiveGrabber, LiveComputeLoop",
                    f"{_code('vision/vision_service.py')} — last_raw / compute_monitor",
                    f"{_code('algorithm_module')} — inference via VisionService",
                ],
                used_by=[
                    f"Top bar {_L(T.CAM_MONITOR)} button",
                    f"{_L(T.VISION)} monitor button",
                ],
            ),
        ),
        (
            "production",
            _L(T.PRODUCTION),
            _io_block(
                purpose="Production board: piece count, cycle time CT, UPH instant & rolling average.",
                impl=[f"{_code('hmi/pages/production_page.py')}"],
                refs=[
                    f"{_code('core/production_stats.py')}",
                    "Trigger: Station5 unload complete callback",
                ],
                used_by=["Capacity view; simulate one piece tests UI only"],
            ),
        ),
        (
            "step",
            _L(T.STEP_DEBUG),
            _io_block(
                purpose="Single-step by station: step table, arm step, run next, abort auto, debug bypass.",
                impl=[
                    f"{_code('hmi/pages/step_debug_page.py')}",
                    f"{_code('stations/step_catalog.py')}",
                ],
                refs=[
                    f"{_code('stations/station1_*.py')} … station6",
                    f"{_code('core/step_engine.py')} / {_code('core/plc_util.py')}",
                    f"{_code('core/point_undo.py')}",
                ],
                used_by=[
                    "Commissioning & stuck-step debug",
                    f"Use with {_L(T.POINTS)} and {_L(T.MOTION)}",
                ],
            ),
        ),
        (
            "motion",
            _L(T.MOTION),
            _io_block(
                purpose="Set vel / accel / blend per program step key (not teach point name).",
                impl=[
                    f"{_code('hmi/pages/motion_steps_page.py')}",
                    f"{_code('core/motion_steps.py')}",
                ],
                refs=[
                    f"{_code('config/default.yaml')} motion_steps",
                    f"{_code('core/app_context.py')} step_motion_kwargs",
                    f"{_code('devices/robot_fr5.py')}",
                ],
                used_by=[
                    "Station2/5 moves via step_key",
                    f"Global multiplier on {_L(T.MONITOR)} SetSpeed",
                ],
            ),
        ),
        (
            "vision",
            _L(T.VISION),
            _io_block(
                purpose=(
                    "Vision hub: preview + tabs for ROI, chessboard intrinsics, hand-eye, detection test, capture/train."
                ),
                impl=[
                    f"{_code('hmi/pages/vision_hub_page.py')}",
                    f"{_code('hmi/pages/vision_workspace.py')}",
                ],
                refs=[
                    f"{_code('vision/camera_orbbec.py')}",
                    f"{_code('vision/vision_service.py')}",
                    f"{_code('algorithm_module')}",
                ],
                used_by=["Commission before production; auto flow uses same VisionService"],
            )
            + _h("Four cameras")
            + _ul(
                [
                    "<b>cam1</b>: belt find shoe → pick pose (Station1)",
                    "<b>cam2</b>: toe align classify → Station2 MoveL",
                    "<b>cam3</b>: place slot occupied (Station3)",
                    "<b>cam4</b>: pick slot + rod offset (Station4→5)",
                ]
            ),
        ),
        (
            "points",
            _L(T.POINTS),
            _io_block(
                purpose="Teach & save TCP+joints, transition points, offset trial moves.",
                impl=[f"{_code('hmi/pages/points_page.py')}"],
                refs=[
                    f"{_code('config/default.yaml')} points",
                    f"{_code('devices/robot_fr5.py')}",
                ],
                used_by=["Station2/5 via ctx.pose / move_to_point"],
            ),
        ),
        (
            "shield",
            _L(T.SHIELD_PICK),
            _io_block(
                purpose="When cam1 Mock: teach belt_pick_mock positions instead of YOLO PickPose.",
                impl=[f"{_code('hmi/pages/shield_pick_page.py')}"],
                refs=[
                    f"{_code('config/default.yaml')} vision.belt_pick_mock",
                    f"{_code('vision/vision_service.py')}",
                ],
                used_by=["Station1 cam1 Mock; often with dry-run"],
            ),
        ),
        (
            "dry",
            _L(T.DRY_RUN),
            _io_block(
                purpose="Dry-run Station1–6 without real belt/press: photo mock, press mock, slot timing.",
                impl=[
                    f"{_code('hmi/pages/dry_run_page.py')}",
                    f"{_code('core/dry_run_shield.py')}",
                ],
                refs=[
                    f"{_code('devices/io_manager.py')}",
                    f"{_code('devices/press_modbus.py')}",
                ],
                used_by=[f"{_L(T.MONITOR)} dry-run program button"],
            ),
        ),
        (
            "payload",
            _L(T.PAYLOAD),
            _io_block(
                purpose="Load/unload arm: empty vs gripping payload mass/COM + tool TCP; push to controller.",
                impl=[f"{_code('hmi/pages/payload_page.py')}"],
                refs=[f"{_code('devices/robot_fr5.py')} apply_payload"],
                used_by=["Real robot teach pendant tool coords"],
            ),
        ),
        (
            "press",
            _L(T.PRESS_IO),
            _io_block(
                purpose="Four-slot press: place/pick ports, sequence, auto slot compute, Modbus addresses.",
                impl=[f"{_code('hmi/pages/press_io_page.py')}"],
                refs=[f"{_code('devices/press_modbus.py')}"],
                used_by=["Station6; slot widgets shared with Monitor"],
            ),
        ),
        (
            "config",
            "Comm & devices (Settings tab)",
            _io_block(
                purpose="Robot IP, press, gripper CAN, photo DI, use_mock; save default.yaml & reconnect.",
                impl=[f"{_code('hmi/pages/config_page.py')}"],
                refs=[f"{_code('core/config_loader.py')}"],
                used_by=["First step for real hardware; restart after camera serial change"],
            ),
        ),
        (
            "alarm",
            _L(T.ALARM),
            _io_block(
                purpose="Alarm history, copy text, alarm reset.",
                impl=[f"{_code('hmi/pages/alarm_page.py')}"],
                refs=[f"{_code('core/alarm.py')}"],
                used_by=["Main window popup timer; motion/vision failures"],
            ),
        ),
        (
            "stations",
            "Stations 1–6",
            _h("Purpose")
            + _p("Auto flow split into 6 station modules polled by coordinator.cycle(ctx).")
            + _h("Files")
            + _ul(
                [
                    f"Station1 {_code('station1_belt_photo.py')} — belt photo",
                    f"Station2 {_code('station2_robot1.py')} — load arm",
                    f"Station3 {_code('station3_place_slot_photo.py')}",
                    f"Station4 {_code('station4_pick_slot_photo.py')}",
                    f"Station5 {_code('station5_robot2.py')} — unload",
                    f"Station6 {_code('station6_press_rotate.py')} — press & rotate",
                ]
            ),
        ),
        (
            "algo",
            "algorithm_module",
            _h("Purpose")
            + _p("Unified vision API: images/config in → results out. Stations use VisionService.")
            + _h("Key calls")
            + _ul(
                [
                    "detect_belt_pick → Station1",
                    "classify_toe_align → Station2",
                    "classify_slot_occupied → Station3/4",
                    "measure_rod_offset_mm → Station5",
                ]
            ),
        ),
        (
            "gripper",
            "Gripper (DM-J4310-2EC)",
            _h("Setup")
            + _ul(
                [
                    f"Settings → Comm: motor count, CAN id, load/unload bind → save; restart if count changes",
                    f"{_L(T.GRIPPER)} page: open/close, speed, reconnect, GRIP_* reset",
                    "Wiring: docs/夹爪使用说明.md",
                ]
            ),
        ),
        (
            "mock_auto",
            "Mock flow / real hardware checklist",
            _h("Mock quick path")
            + _ol(
                [
                    f"{_L(T.MONITOR)}: Auto → Initialize → READY",
                    f"{_L(T.DRY_RUN)} enable shields",
                    f"{_L(T.SHIELD_PICK)} confirm left/right teach",
                    "Start; watch S1→S6",
                ]
            )
            + _h("Before real hardware")
            + _ul(
                [
                    f"Settings Comm: use_mock=false, correct IP/CAN/DI",
                    f"{_L(T.VISION)} live + models; hand-eye in json",
                    f"{_L(T.POINTS)} saved; {_L(T.STEP_DEBUG)} per station",
                ]
            ),
        ),
    ]
