"""运行监控页文案（按语言分表，由 i18n.tr 合并查找）。"""

from __future__ import annotations

MONITOR_BY_LANG: dict[str, dict[str, str]] = {
    "zh-CN": {
        "monitor.btn.init": "初始化",
        "monitor.btn.start": "启动",
        "monitor.btn.start_ok": "启动 ✓",
        "monitor.btn.start_resume": "启动(继续)",
        "monitor.btn.pause": "暂停",
        "monitor.btn.stop": "停止",
        "monitor.btn.estop": "急停",
        "monitor.btn.reset_estop": "急停复位",
        "monitor.btn.alarm_reset": "报警复位",
        "monitor.btn.copy_alarm": "复制报警",
        "monitor.btn.copy_none": "无报警",
        "monitor.btn.copy_done": "已复制",
        "monitor.init_flag.tooltip": "报警全文可选中复制，或点「复制报警」（不弹窗）",
        "monitor.mode.auto": "模式:自动",
        "monitor.mode.step": "模式:单步",
        "monitor.mode.manual": "模式:手动",
        "monitor.mode.label": "模式: {mode}",
        "monitor.step.next": "单步：下一步",
        "monitor.step.next_tip": (
            "切到单步模式；给忙站发 StepPulse（条件满足才跳步）。"
            "初始化中则推进 InitStepPulse。细调请用「工位调试」页。"
        ),
        "monitor.dry.start": "启动空跑程序",
        "monitor.dry.start_tip": (
            "一键启用空跑屏蔽（光电/压机Mock、先压后转时序；不改相机模拟）并切自动模式；"
            "仍需「初始化」→「启动」后连续空跑。"
        ),
        "monitor.light.red": "红",
        "monitor.light.yellow": "黄",
        "monitor.light.green": "绿",
        "monitor.state.label": "状态: {state}  |  {init}  |  {msg}",
        "monitor.state.init_ok": "已初始化",
        "monitor.state.init_no": "未初始化",
        "monitor.mem.title_locked": "当前槽号 / 记忆（自动运行中记忆锁定）",
        "monitor.mem.title_edit": "当前槽号 / 记忆（可改槽号、顺序、记忆）",
        "monitor.hero.place": "放料槽（左口）\n#{slot}",
        "monitor.hero.pick": "取料槽（右口）\n#{slot}",
        "monitor.hero.place_short": "放料槽\n#—",
        "monitor.hero.pick_short": "取料槽\n#—",
        "monitor.hero.meta": (
            "顺序 {seq} ｜ {lock} ｜ 旋转到位={rotate}  压合={press}  取料槽{ready}"
        ),
        "monitor.slot.auto": "自算槽号",
        "monitor.slot.plc": "读PLC槽号",
        "monitor.slot.manual_lock": "手动锁定",
        "monitor.slot.ready": "可取",
        "monitor.slot.not_ready": "未完成",
        "monitor.slot.seq_order": "槽号顺序",
        "monitor.slot.seq_fwd": "12341 正序",
        "monitor.slot.seq_rev": "43214 反序",
        "monitor.slot.place": "放料槽",
        "monitor.slot.pick": "取料槽",
        "monitor.slot.lock_manual": "锁定手动槽号",
        "monitor.slot.apply": "应用槽号",
        "monitor.slot.tip_edit": "停止/暂停后可改槽号和顺序，改完点「应用槽号」。",
        "monitor.slot.tip_locked": "自动运行中槽号与顺序锁定，暂停或停止后可改。",
        "monitor.slot.tip_default": (
            "停止/暂停后可改：改放料槽则取料槽按顺序联动，改取料槽则放料槽联动。"
        ),
        "monitor.link.title": "设备连接（Mock=模拟就绪；真机断线将自动重连）",
        "monitor.link.mock": "模拟",
        "monitor.link.opening": "正在连接…",
        "monitor.link.ok": "已连接",
        "monitor.link.warn": (
            "⚠ 有设备未连接：{names}\n"
            "非 Mock 设备将按间隔自动重连；请检查网线/IP/电源，或在「设置」通信页改为 Mock。"
        ),
        "monitor.prod.ct": "CT: {val}",
        "monitor.prod.ct_empty": "CT: -- s",
        "monitor.prod.uph": "UPH: {val}",
        "monitor.prod.uph_empty": "UPH: --",
        "monitor.prod.uph_avg": "UPH均: {val}",
        "monitor.prod.uph_avg_empty": "UPH均: --",
        "monitor.prod.hour": "本小时: {n}",
        "monitor.prod.total": "总产量: {n}",
        "monitor.vel.title": "机器人速度（%）",
        "monitor.vel.robot1": "上料机器人 {pct}%",
        "monitor.vel.robot2": "下料机器人 {pct}%",
        "monitor.vel.both": "两臂同步 {pct}%",
        "monitor.vel.saved": "{label}（已保存）",
        "monitor.blend.title": "路径平滑（全局总开关 + 默认 blendT/blendR）",
        "monitor.blend.enable": "启用路径平滑",
        "monitor.blend.t": "MoveJ平滑时间 blendT(ms)",
        "monitor.blend.r": "MoveL平滑半径 blendR(mm)",
        "monitor.blend.delay": "衔接提前量(s)",
        "monitor.blend.apply": "应用并保存",
        "monitor.grip.title": (
            "夹爪手动（停止/暂停/单步时可操作；等张开完成/夹紧完成，不再固定延时）"
        ),
        "monitor.grip.feed1": "上料夹爪: {status} | spd开={open:.0f}/关={close:.0f}{err}",
        "monitor.grip.feed2": "下料夹爪: {status} | spd开={open:.0f}/关={close:.0f}{err}",
        "monitor.grip.err_suffix": " | 错:{err}",
        "monitor.grip.busy": "动作中…",
        "monitor.grip.open": "张开",
        "monitor.grip.closed": "夹紧",
        "monitor.grip.lamp_open": "张开完成",
        "monitor.grip.lamp_close": "夹紧完成",
        "monitor.grip.btn1_open": "上料张开",
        "monitor.grip.btn1_close": "上料夹紧",
        "monitor.grip.btn2_open": "下料张开",
        "monitor.grip.btn2_close": "下料夹紧",
        "monitor.grip.spd1_open": "上料张开速度",
        "monitor.grip.spd1_close": "上料夹紧速度",
        "monitor.grip.spd2_open": "下料张开速度",
        "monitor.grip.spd2_close": "下料夹紧速度",
        "monitor.grip.save_spd": "保存夹爪速度到 yaml",
        "monitor.press.title": "压鞋机/转盘手动（真机写 Modbus；自动跑 Station6 时请先停止）",
        "monitor.press.manual": "压鞋机: -",
        "monitor.press.rot_on": "启动旋转鞋槽",
        "monitor.press.rot_off": "停止旋转",
        "monitor.press.start": "启动压鞋",
        "monitor.press.stop": "停止压鞋",
        "monitor.press.done_sim": "置旋转/压鞋完成",
        "monitor.press.done_sim_tip": (
            "Mock：置 rotate_done/press_done=True 并清命令；真机仅作调试提示，请看 PLC 到位信号"
        ),
        "monitor.station.title": "Station 状态",
        "monitor.mock.title": "屏蔽信号快控（完整空跑请用「空跑联调」页）",
        "monitor.mock.dry_on": "一键启用空跑屏蔽",
        "monitor.mock.dry_off": "关闭空跑",
        "monitor.mock.dry_prog": "启动空跑程序（屏蔽+自动模式）",
        "monitor.mock.belt_force": "光电用模拟（真机臂时勾选才能点下面按钮）",
        "monitor.mock.belt_di": "上料皮带光电 DI（电平保持）",
        "monitor.mock.belt_status": "光电 DI[{id}]: {state}",
        "monitor.mock.belt_on": "模拟光电感应到位",
        "monitor.mock.belt_on_tip": "置光电 DI=True，触发 Station1 皮带拍照（需 Mem[1]=False）",
        "monitor.mock.belt_off": "模拟光电离开(无鞋)",
        "monitor.mock.belt_off_tip": "置光电 DI=False，模拟两只鞋都取走后电平变低",
        "monitor.mock.estop_di": "物理急停 DI",
        "monitor.mock.rotate_done": "Mock旋转到位",
        "monitor.mock.press_done": "Mock压鞋完成",
        "monitor.mock.rot_sim": "模拟压鞋机旋转完成",
        "monitor.mock.rot_sim_tip": "空跑已开自动旋转时一般不用点；卡住时手动置到位",
        "monitor.mock.place_mat": "Mock放料槽有料（仅cam3模拟）",
        "monitor.mock.place_mat_tip": (
            "空跑开启且「放料自动跟手」时会被周期改成空槽。\n仅当相机3为 Mock 时生效。"
        ),
        "monitor.mock.place_left": "Mock放料槽=左鞋槽（仅cam3模拟）",
        "monitor.mock.place_left_tip": (
            "空跑开启时会自动跟手中鞋左右。\n仅当相机3为 Mock 时生效。"
        ),
        "monitor.mock.pick_mat": "Mock取料槽有料（仅cam4模拟）",
        "monitor.mock.pick_mat_tip": (
            "空跑时：待转(Mem3)强制无料；转完后自动有料。\n"
            "不要一直勾着有料，否则 Mem6 会挡住 Station6。"
        ),
        "monitor.mock.fault_r1": "模拟上料臂报警（测停机）",
        "monitor.mock.fault_r2": "模拟下料臂报警（测停机）",
        "monitor.mock.shoe_match": "方向联锁: {detail}",
        "monitor.mock.belt_label": "光电: -",
        "monitor.mock.belt_status": "光电 DI[{id}]: {state}",
        "monitor.mock.belt_true": "到位 True",
        "monitor.mock.belt_false": "无鞋 False",
        "monitor.mock.dry_label_on": "空跑屏蔽：开 — ",
        "monitor.mock.dry_label_off": "空跑屏蔽：关 — ",
        "monitor.mobile.enabled": (
            "手机监控: {url}{token} （手机与工控机同一局域网；关闭改 yaml system.mobile_web.enabled=false）"
        ),
        "monitor.mobile.token": "  口令={token}",
        "monitor.mobile.disabled": (
            "手机监控未启用。在 config/default.yaml 设 system.mobile_web.enabled: true 后重启。"
        ),
        "monitor.auto_clear": "示教器瞬态报警已自动消除（详情也在「报警」页标[瞬态]）\n{lines}",
        "monitor.init.estop": "急停中 — 请先「急停复位」，再初始化",
        "monitor.init.alarm": "报警中 — 设备连齐后点「报警复位」，再重新初始化",
        "monitor.init.progress": "初始化进行中…（步 {step}）— 完成后才能启动",
        "monitor.init.running": "初始化完成 · 运行中",
        "monitor.init.paused": "初始化完成 · 已暂停 — 可再点「启动」继续",
        "monitor.init.stopped": "初始化完成 · 已停止 — 可再点「启动」",
        "monitor.init.ready": "✓ 初始化完成 — 可以点「启动」",
        "monitor.init.idle": "未初始化 — 请先点「初始化」，完成后再「启动」",
        "monitor.start_tip.estop": "急停中，无法启动",
        "monitor.start_tip.alarm": "报警中，无法启动",
        "monitor.start_tip.init_wait": "初始化尚未完成，请等待",
        "monitor.start_tip.can_start": "可以启动",
        "monitor.start_tip.running": "已在运行",
        "monitor.start_tip.need_init": "请先完成初始化",
        "monitor.msg.init_fail": "无法初始化",
        "monitor.msg.start_fail": "无法启动",
        "monitor.msg.alarm_reset": "报警复位",
        "monitor.msg.reset_ok": "已复位",
        "monitor.msg.slot_title": "槽号",
        "monitor.msg.slot_locked": "自动运行中不可改槽号，请先暂停或停止。",
        "monitor.msg.dry_ready_title": "空跑程序已就绪",
        "monitor.msg.dry_ready_body": (
            "已启用空跑屏蔽（光电/压机Mock；Station6 先压后转自动完成；相机模拟仍按通信配置），"
            "并切到「自动」模式。\n\n"
            "请按：初始化 → 启动。\n"
            "若要逐步验证：模式切「单步」后点「单步：下一步」，"
            "或到「工位调试」页武装后推进。"
        ),
        "monitor.msg.dry_on_title": "空跑已启用",
        "monitor.msg.dry_on_body": (
            "已启用空跑屏蔽（光电/相机/放料跟手/取料时序/压机先压后转）。\n"
            "细节可到「空跑联调」页调整。\n"
            "请「初始化」→「启动」验证。"
        ),
        "monitor.press.status": (
            "压鞋机: 顺序{seq} 放料#{place} 取料#{pick}({lock}) "
            "旋转到位={rotate} 压合={press} 可取={ready} "
            "旋令={cmd_rot} 压令={cmd_press}"
        ),
        "monitor.press.lock": "锁定",
        "monitor.press.auto": "自动",
        "monitor.mem.1": "皮带上料拍照完成",
        "monitor.mem.2": "机器人1手爪有料",
        "monitor.mem.3": "放料鞋槽有料",
        "monitor.mem.4": "放料鞋槽拍照完成",
        "monitor.mem.5": "机器人2手爪有料",
        "monitor.mem.6": "取料鞋槽有料",
        "monitor.mem.7": "取料鞋槽拍照完成",
        "monitor.mem.8": "机器人1取到左鞋",
        "monitor.mem.9": "机器人1取到右鞋",
        "monitor.mem.10": "放料鞋槽不匹配",
    },
    "en-US": {
        "monitor.btn.init": "Initialize",
        "monitor.btn.start": "Start",
        "monitor.btn.start_ok": "Start ✓",
        "monitor.btn.start_resume": "Start (Resume)",
        "monitor.btn.pause": "Pause",
        "monitor.btn.stop": "Stop",
        "monitor.btn.estop": "E-Stop",
        "monitor.btn.reset_estop": "E-Stop Reset",
        "monitor.btn.alarm_reset": "Alarm Reset",
        "monitor.btn.copy_alarm": "Copy Alarm",
        "monitor.btn.copy_none": "No Alarm",
        "monitor.btn.copy_done": "Copied",
        "monitor.init_flag.tooltip": "Select alarm text here, or click Copy Alarm (no dialog).",
        "monitor.mode.auto": "Mode: Auto",
        "monitor.mode.step": "Mode: Single Step",
        "monitor.mode.manual": "Mode: Manual",
        "monitor.mode.label": "Mode: {mode}",
        "monitor.step.next": "Step: Next",
        "monitor.step.next_tip": (
            "Switch to single-step mode; pulse busy stations (StepPulse). "
            "During init, advances InitStepPulse. Fine control: Station Debug page."
        ),
        "monitor.dry.start": "Start Dry-Run Program",
        "monitor.dry.start_tip": (
            "Enable dry-run shields (photo eye / press mock, press-then-rotate timing; "
            "camera mock unchanged) and switch to Auto. Still requires Initialize → Start."
        ),
        "monitor.light.red": "Red",
        "monitor.light.yellow": "Yellow",
        "monitor.light.green": "Green",
        "monitor.state.label": "State: {state}  |  {init}  |  {msg}",
        "monitor.state.init_ok": "Initialized",
        "monitor.state.init_no": "Not initialized",
        "monitor.mem.title_locked": "Slots / Memory (locked while running)",
        "monitor.mem.title_edit": "Slots / Memory (editable when stopped/paused)",
        "monitor.hero.place": "Place slot (left)\n#{slot}",
        "monitor.hero.pick": "Pick slot (right)\n#{slot}",
        "monitor.hero.place_short": "Place\n#—",
        "monitor.hero.pick_short": "Pick\n#—",
        "monitor.hero.meta": (
            "Seq {seq} | {lock} | Rotate={rotate}  Press={press}  Pick {ready}"
        ),
        "monitor.slot.auto": "Auto slots",
        "monitor.slot.plc": "From PLC",
        "monitor.slot.manual_lock": "Manual lock",
        "monitor.slot.ready": "Ready",
        "monitor.slot.not_ready": "Not ready",
        "monitor.slot.seq_order": "Slot sequence",
        "monitor.slot.seq_fwd": "12341 Forward",
        "monitor.slot.seq_rev": "43214 Reverse",
        "monitor.slot.place": "Place slot",
        "monitor.slot.pick": "Pick slot",
        "monitor.slot.lock_manual": "Lock manual slots",
        "monitor.slot.apply": "Apply Slots",
        "monitor.slot.tip_edit": "When stopped/paused: edit slots/sequence, then Apply Slots.",
        "monitor.slot.tip_locked": "Slots locked while running; pause or stop to edit.",
        "monitor.slot.tip_default": (
            "When stopped/paused: editing place slot updates pick slot (and vice versa) by sequence."
        ),
        "monitor.link.title": "Device links (Mock=sim ready; real devices auto-reconnect)",
        "monitor.link.mock": "Mock",
        "monitor.link.opening": "Connecting…",
        "monitor.link.ok": "Connected",
        "monitor.link.warn": (
            "⚠ Not connected: {names}\n"
            "Non-mock devices retry automatically; check cable/IP/power or set Mock in Settings → Comm."
        ),
        "monitor.prod.ct": "CT: {val}",
        "monitor.prod.ct_empty": "CT: -- s",
        "monitor.prod.uph": "UPH: {val}",
        "monitor.prod.uph_empty": "UPH: --",
        "monitor.prod.uph_avg": "UPH avg: {val}",
        "monitor.prod.uph_avg_empty": "UPH avg: --",
        "monitor.prod.hour": "This hour: {n}",
        "monitor.prod.total": "Total: {n}",
        "monitor.vel.title": "Robot speed (%)",
        "monitor.vel.robot1": "Load robot {pct}%",
        "monitor.vel.robot2": "Unload robot {pct}%",
        "monitor.vel.both": "Both arms {pct}%",
        "monitor.vel.saved": "{label} (saved)",
        "monitor.blend.title": "Path blend (global switch + default blendT/blendR)",
        "monitor.blend.enable": "Enable path blend",
        "monitor.blend.t": "MoveJ blend time blendT (ms)",
        "monitor.blend.r": "MoveL blend radius blendR (mm)",
        "monitor.blend.delay": "Queue lead time (s)",
        "monitor.blend.apply": "Apply & Save",
        "monitor.grip.title": (
            "Gripper manual (when stopped/paused/step; wait for open/close done, no fixed delay)"
        ),
        "monitor.grip.feed1": "Load gripper: {status} | open={open:.0f}/close={close:.0f}{err}",
        "monitor.grip.feed2": "Unload gripper: {status} | open={open:.0f}/close={close:.0f}{err}",
        "monitor.grip.err_suffix": " | err:{err}",
        "monitor.grip.busy": "Moving…",
        "monitor.grip.open": "Open",
        "monitor.grip.closed": "Closed",
        "monitor.grip.lamp_open": "Open done",
        "monitor.grip.lamp_close": "Close done",
        "monitor.grip.btn1_open": "Load open",
        "monitor.grip.btn1_close": "Load close",
        "monitor.grip.btn2_open": "Unload open",
        "monitor.grip.btn2_close": "Unload close",
        "monitor.grip.spd1_open": "Load open speed",
        "monitor.grip.spd1_close": "Load close speed",
        "monitor.grip.spd2_open": "Unload open speed",
        "monitor.grip.spd2_close": "Unload close speed",
        "monitor.grip.save_spd": "Save gripper speeds to yaml",
        "monitor.press.title": "Press / turntable manual (Modbus on real machine; stop before Station6 auto)",
        "monitor.press.manual": "Press: -",
        "monitor.press.rot_on": "Start rotate slots",
        "monitor.press.rot_off": "Stop rotate",
        "monitor.press.start": "Start press",
        "monitor.press.stop": "Stop press",
        "monitor.press.done_sim": "Set rotate/press done",
        "monitor.press.done_sim_tip": (
            "Mock: set rotate_done/press_done=True and clear cmds; on real machine check PLC signals"
        ),
        "monitor.station.title": "Station status",
        "monitor.mock.title": "Quick signal shields (full dry-run: Dry Run page)",
        "monitor.mock.dry_on": "Enable dry-run shields",
        "monitor.mock.dry_off": "Disable dry-run",
        "monitor.mock.dry_prog": "Dry-run program (shields + auto mode)",
        "monitor.mock.belt_force": "Simulate photo eye (required on real arm to use buttons below)",
        "monitor.mock.belt_di": "Load belt photo DI (latched)",
        "monitor.mock.belt_status": "Photo DI[{id}]: {state}",
        "monitor.mock.belt_on": "Simulate shoe present",
        "monitor.mock.belt_on_tip": "Set photo DI=True → Station1 belt photo (needs Mem[1]=False)",
        "monitor.mock.belt_off": "Simulate no shoe",
        "monitor.mock.belt_off_tip": "Set photo DI=False after both shoes picked",
        "monitor.mock.estop_di": "Physical E-stop DI",
        "monitor.mock.rotate_done": "Mock rotate done",
        "monitor.mock.press_done": "Mock press done",
        "monitor.mock.rot_sim": "Simulate rotate complete",
        "monitor.mock.rot_sim_tip": "Usually not needed when auto-rotate in dry-run; use if stuck",
        "monitor.mock.place_mat": "Mock place slot has shoe (cam3 mock only)",
        "monitor.mock.place_mat_tip": (
            "Dry-run with auto-follow hand may clear slot periodically.\nCam3 must be Mock."
        ),
        "monitor.mock.place_left": "Mock place = left slot (cam3 mock only)",
        "monitor.mock.place_left_tip": (
            "Dry-run auto-follows left/right from hand.\nCam3 must be Mock."
        ),
        "monitor.mock.pick_mat": "Mock pick slot has shoe (cam4 mock only)",
        "monitor.mock.pick_mat_tip": (
            "Dry-run: pending transfer forces empty; after rotate auto-fills.\n"
            "Do not leave checked or Mem6 blocks Station6."
        ),
        "monitor.mock.fault_r1": "Simulate load-arm fault (stop test)",
        "monitor.mock.fault_r2": "Simulate unload-arm fault (stop test)",
        "monitor.mock.shoe_match": "Direction interlock: {detail}",
        "monitor.mock.belt_label": "Photo: -",
        "monitor.mock.belt_status": "Photo DI[{id}]: {state}",
        "monitor.mock.belt_true": "Present True",
        "monitor.mock.belt_false": "Empty False",
        "monitor.mock.dry_label_on": "Dry-run shields: ON — ",
        "monitor.mock.dry_label_off": "Dry-run shields: OFF — ",
        "monitor.mobile.enabled": (
            "Mobile monitor: {url}{token} (same LAN as PC; disable: yaml system.mobile_web.enabled=false)"
        ),
        "monitor.mobile.token": "  token={token}",
        "monitor.mobile.disabled": (
            "Mobile monitor disabled. Set system.mobile_web.enabled: true in config/default.yaml and restart."
        ),
        "monitor.auto_clear": "Teach pendant transient alarms auto-cleared (see Alarms page [transient])\n{lines}",
        "monitor.init.estop": "E-stop active — reset E-stop, then Initialize",
        "monitor.init.alarm": "Alarm active — connect devices, Alarm Reset, then Initialize again",
        "monitor.init.progress": "Initializing… (step {step}) — wait before Start",
        "monitor.init.running": "Init done · Running",
        "monitor.init.paused": "Init done · Paused — click Start to resume",
        "monitor.init.stopped": "Init done · Stopped — click Start",
        "monitor.init.ready": "✓ Init done — click Start",
        "monitor.init.idle": "Not initialized — click Initialize, then Start",
        "monitor.start_tip.estop": "E-stop — cannot start",
        "monitor.start_tip.alarm": "Alarm — cannot start",
        "monitor.start_tip.init_wait": "Init not finished — please wait",
        "monitor.start_tip.can_start": "Ready to start",
        "monitor.start_tip.running": "Already running",
        "monitor.start_tip.need_init": "Complete initialization first",
        "monitor.msg.init_fail": "Cannot initialize",
        "monitor.msg.start_fail": "Cannot start",
        "monitor.msg.alarm_reset": "Alarm reset",
        "monitor.msg.reset_ok": "Reset done",
        "monitor.msg.slot_title": "Slots",
        "monitor.msg.slot_locked": "Cannot change slots while running; pause or stop first.",
        "monitor.msg.dry_ready_title": "Dry-run program ready",
        "monitor.msg.dry_ready_body": (
            "Dry-run shields enabled (photo/press mock; Station6 press-then-rotate; "
            "camera mock per comm config), Auto mode set.\n\n"
            "Then: Initialize → Start.\n"
            "For step verify: Single Step mode → Step: Next, or use Station Debug."
        ),
        "monitor.msg.dry_on_title": "Dry-run enabled",
        "monitor.msg.dry_on_body": (
            "Dry-run shields on (photo/camera/place follow/pick timing/press order).\n"
            "Adjust on Dry Run page.\nInitialize → Start to verify."
        ),
        "monitor.press.status": (
            "Press: seq {seq} place #{place} pick #{pick} ({lock}) "
            "rotate={rotate} press={press} pick_ready={ready} "
            "rot_cmd={cmd_rot} press_cmd={cmd_press}"
        ),
        "monitor.press.lock": "locked",
        "monitor.press.auto": "auto",
        "monitor.mem.1": "Belt load photo done",
        "monitor.mem.2": "Robot1 gripper has shoe",
        "monitor.mem.3": "Place slot has shoe",
        "monitor.mem.4": "Place slot photo done",
        "monitor.mem.5": "Robot2 gripper has shoe",
        "monitor.mem.6": "Pick slot has shoe",
        "monitor.mem.7": "Pick slot photo done",
        "monitor.mem.8": "Robot1 picked left shoe",
        "monitor.mem.9": "Robot1 picked right shoe",
        "monitor.mem.10": "Place slot mismatch",
    },
}
