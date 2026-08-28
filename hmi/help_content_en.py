"""English help sections for Help page."""

from __future__ import annotations

from typing import List

from hmi.help_content import Section, _L, _code, _h, _io_block, _logo_html, _ol, _p, _ul
from hmi.tab_titles import T


def build_sections_en() -> List[Section]:
    return [
        (
            "overview",
            "Overview: navigation, lights, memory, flow",
            _logo_html()
            + _h("How to use this page")
            + _p(
                "This Help page is the on-site operator manual. Browse chapters on the left; search above. "
                "Each page documents purpose, UI files, dependencies, and who calls it."
            )
            + _p(
                f"The circular RSDT badge sits at the top of the left nav "
                f"(also on the splash, camera window, and teach pendant). "
                f"Below it is the navigation list (not top tabs). "
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
                    f"<b>{_L(T.VISION)}</b>: models; ROI → intrinsics → hand-eye → test → run snaps → capture/train",
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
                    f"<b>{_L(T.JOG)}</b>: separate teach-pendant window (top bar), jog while teach-points stay open",
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
                    f"<b>{_L(T.ALARM)}</b>: this-run / saved errors / black box / run snaps (JPEG names include camera and time)",
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
                    "Live inference does not write run snaps; browse history on Alarms → Run snaps",
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
                    "Vision hub: preview + tabs for ROI, chessboard, hand-eye, detection test, capture/train."
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
                used_by=[
                    "Commission before production; auto flow uses same VisionService",
                    "Production photos: browse Alarms → Run snaps (manual §14.4)",
                ],
            )
            + _h("Four cameras")
            + _ul(
                [
                    "<b>cam1</b>: belt find shoe → pick pose (Station1)",
                    "<b>cam2</b>: toe align classify → Station2 MoveL",
                    "<b>cam3</b>: place slot occupied (Station3)",
                    "<b>cam4</b>: pick slot + rod offset (Station4→5)",
                ]
            )
            + _h("Run snaps (on Alarms page)")
            + _p(
                "Auto-flow photos and this page's detection tests save raw, overlay, and detect results under "
                + _code("logs/vision_snaps/")
                + ". JPEG names include camera and time, e.g. "
                + _code("cam1_20260828_140455_635_belt_pick_raw.jpg")
                + ". After place/slot-check/unload the <b>same</b> record is updated. "
                "Browse on <b>Alarms → Run snaps</b>. "
                "Camera-monitor live inference does <b>not</b> save. "
                "Full detail is in the next chapter and paper manual "
                + _code("docs/界面操作手册.md")
                + " §14.4."
            ),
        ),
        (
            "vision_snaps",
            "Alarms · run snaps (history / transport / open log)",
            _h("Purpose")
            + _p(
                "Bind “what the camera saw” to “where it was carried and whether that succeeded”, "
                "so you can debug from the HMI without hunting folders on the IPC."
            )
            + _p(
                "At photo time: JPEGs named with camera + time + kind, plus detect fields. "
                "After the robot finishes place-into-slot or unload-to-belt: write back onto that same record. "
                "Browse under Alarms → Run snaps, or open the vision log folder."
            )
            + _h("Implementation")
            + _ul(
                [
                    f"{_code('hmi/pages/vision_snap_page.py')} — list, images, detail, open folder",
                    f"{_code('hmi/pages/alarm_page.py')} — fourth tab Run snaps",
                    f"{_code('vision/vision_journal.py')} — save, transport write-back, listing",
                    f"{_code('vision/vision_service.py')} — photo_* persist=True by default; monitor persist=False",
                    f"{_code('stations/station1_belt_photo.py')} — stores vision_snap_id on confirm",
                    f"{_code('stations/station2_robot1.py')} — place write-back",
                    f"{_code('stations/station3_place_slot_photo.py')} — slot_check write-back",
                    f"{_code('stations/station4_pick_slot_photo.py')} — unload snap id",
                    f"{_code('stations/station5_robot2.py')} — unload write-back",
                ]
            )
            + _h("When a snap is saved")
            + _ul(
                [
                    "<b>Station1</b> belt photo (cam1): every photo_belt_pick, success or fail. "
                    "After the pick pose is confirmed, snap_id is stored on BeltPickSnapshot for later write-back.",
                    "<b>Station3</b> place-slot photo (cam3): empty/occupied and left/right slot.",
                    "<b>Station4</b> pick-slot photo (cam4): occupied + rod offset.",
                    "Vision → Detection test: test belt / place slot / pick slot (same APIs as production).",
                    "Write PickPose from vision: keeps snap_id so a later auto place can still write transport.",
                ]
            )
            + _h("When it is intentionally not saved")
            + _ul(
                [
                    "Camera monitor live inference and periodic monitor refresh (too frequent; no one-shoe identity).",
                    "cam2 toe-align / edge guide (repeated during motion).",
                    "Top-bar Save screenshot on the vision page: that is calibration shots in "
                    + _code("config/vision_snaps/")
                    + ", not run snaps.",
                ]
            )
            + _h("On disk")
            + _p(
                "Root "
                + _code("logs/vision_snaps/")
                + " (gitignored). Per day:"
            )
            + _ul(
                [
                    _code("cam1_20260828_140455_635_belt_pick_raw.jpg")
                    + " — raw (name = camera_time_kind_raw)",
                    _code("…_vis.jpg") + " — overlay",
                    _code("meta.json") + " — detect fields + transport",
                    _code("index_YYYY-MM-DD.jsonl") + " — daily index (one line per photo, plus a line per transport write-back)",
                ]
            )
            + _p("Id looks like <code>20260828_111343_635_cam1_belt_pick</code> (time, camera, kind).")
            + _h("Transport write-back")
            + _p(
                "Stored in <code>meta.json</code> → <code>transport</code>. "
                "HMI detail pane and list summaries (placed slot #2 / not yet / can place / unloaded) read this."
            )
            + _ul(
                [
                    "<b>place</b> — Station2 after place, back at place_entry. Slot number and left/right. Tied to the cam1 belt shot.",
                    "<b>slot_check</b> — Station3 decision: empty+matching side → can place; "
                    "side mismatch → no place, rotate only; occupied → no place. "
                    "Written on the cam3 shot and also on the in-flight cam1 shot.",
                    "<b>unload</b> — Station5 after belt place and production count. Pick-slot number. Tied to the cam4 shot.",
                ]
            )
            + _p(
                "If detail says not written yet: the shoe is still in motion, or you only ran Detection test. Click Reload."
            )
            + _h("How to open and browse on HMI")
            + _ol(
                [
                    "Nav → Alarms → tab Run snaps (next to Black box).",
                    "Open vision log folder opens "
                    + _code("logs/vision_snaps/")
                    + ".",
                    "List is newest first; filter by camera/kind; 20 per page.",
                    "Select a row: raw + overlay on the right; detect data and transport below.",
                    "After place or unload, click Reload to see “placed slot #n” / “unloaded”.",
                    "Open this item’s folder to copy the camera-and-time-named JPEGs and meta.json.",
                    "Copy detail copies the text pane.",
                ]
            )
            + _h("Not the same as screenshot / calib folders")
            + _ul(
                [
                    "<b>Open vision log / Run snaps</b> → "
                    + _code("logs/vision_snaps/")
                    + " production history with transport.",
                    "<b>Screenshot folder</b> → "
                    + _code("config/vision_snaps/")
                    + " manual Save screenshot, no transport.",
                    "<b>Calib / YOLO model folders</b> → files and weights, not run history.",
                    "Alarms → Open log folder → whole "
                    + _code("logs/")
                    + " (app_YYYY-MM-DD.log, black box). Run snaps has its own Open vision log folder for vision_snaps only.",
                ]
            )
            + _h("Config")
            + _p(
                _code("config/default.yaml")
                + " → <code>vision.save_runtime_snaps</code> (default true) "
                "and <code>vision.snap_keep_days</code> (default 7, old day folders pruned). Restart after edit."
            )
            + _h("Field debug tips")
            + _ul(
                [
                    "Missed pick: filter cam1 / belt, compare overlay vs X/Y/Rz, side, shoe length.",
                    "Wrong slot / rotate-only: cam1 slot_check/place and cam3 decision text.",
                    "Empty unload: cam4 occupied + rod offset, unload slot number.",
                    "List still “not transported” after the cycle: Reload; if still empty check Alarms / black box for a stop.",
                    "Grey “no image yet”: JPEG is written on a background thread; wait a second and Reload.",
                ]
            )
            + _h("Used by")
            + _ul(
                [
                    "Operators browsing on Alarms → Run snaps",
                    "Debug: detection test and auto flow share the same journal",
                    f"Paper steps: {_code('docs/界面操作手册.md')} §14.4",
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
            "jog",
            _L(T.JOG),
            _io_block(
                purpose=(
                    "Separate teach-pendant window wrapping arm jog "
                    "(base/tool Cartesian or joints). Stay on the current HMI page "
                    "instead of switching to the Fairino pendant. "
                    "Default is hold-to-run: motion only while pressed, ImmStopJOG on release. "
                    "Inch mode moves the set mm/deg once."
                ),
                impl=[
                    f"{_code('hmi/pages/jog_pendant.py')} JogPendantWindow / JogPendantPanel",
                    f"{_code('hmi/main_window.py')} top-bar show_jog_pendant",
                ],
                refs=[
                    f"{_code('devices/robot_fr5.py')} start_jog / stop_jog → StartJOG / StopJOG",
                    "Locked during auto run, e-stop, and alarm",
                ],
                used_by=[
                    "Top-bar Pendant button; Teach Points page Open pendant",
                    f"Saving taught points still uses {_L(T.POINTS)} (read current TCP/joints)",
                ],
            )
            + _h("Notes")
            + _ul(
                [
                    "Speed cap 25%; start around 8%. Keep people clear; e-stop ready.",
                    "Tool frame follows the active TCP. Gripper open/close stays on the gripper page.",
                    "Closing the pendant or the main window stops jogging.",
                ]
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
                purpose=(
                    "This-run alarms (paged), copy, reset. Saved errors / black box / run snaps live in logs/, survive exit. "
                    "Run snaps: production photos (JPEG names include camera and time) plus transport write-back. "
                    "Open log folder opens whole logs/; the snaps tab also opens vision_snaps."
                ),
                impl=[
                    f"{_code('hmi/pages/alarm_page.py')}",
                    f"{_code('hmi/pages/vision_snap_page.py')}",
                    f"{_code('core/blackbox.py')}",
                ],
                refs=[
                    f"{_code('core/alarm.py')}",
                    "logs/app_YYYY-MM-DD.log, error_YYYY-MM-DD.log, errors_YYYY-MM-DD.jsonl, blackbox_YYYY-MM-DD.jsonl, dumps/, vision_snaps/",
                ],
                used_by=["Main window popup timer; motion/vision failures"],
            )
            + _h("Four tabs")
            + _ul(
                [
                    "<b>This run</b>: alarms since start (paged). After exit, use saved errors / black box.",
                    "<b>Saved errors</b>: WARNING/ERROR/alarms in logs/errors_YYYY-MM-DD.jsonl, survive restart.",
                    "<b>Black box</b>: trajectory around faults (blackbox_YYYY-MM-DD.jsonl); crash dumps in logs/dumps/. Not camera photos.",
                    "<b>Run snaps</b>: production raw/overlay JPEGs (camera_time_kind_raw/vis.jpg) plus transport write-back. "
                    "See previous chapter and manual §14.4.",
                ]
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
                    f"Station1 {_code('station1_belt_photo.py')} — belt photo; stores run-snap id",
                    f"Station2 {_code('station2_robot1.py')} — load arm; place write-back",
                    f"Station3 {_code('station3_place_slot_photo.py')} — slot_check write-back",
                    f"Station4 {_code('station4_pick_slot_photo.py')} — unload snap id",
                    f"Station5 {_code('station5_robot2.py')} — unload + production count + unload write-back",
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
