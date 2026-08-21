"""HMI 使用说明正文（各操作页的长释义集中在此）。"""

from __future__ import annotations

from typing import List, Tuple

from hmi.tab_titles import T

Section = Tuple[str, str, str]  # id, title, html


def _h(title: str) -> str:
    return f"<h2>{title}</h2>"


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def _ol(items: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>"


def sections() -> List[Section]:
    return [
        (
            "overview",
            "总览：分页、灯语、记忆",
            _h("怎么用本页")
            + _p("各操作页上原来的长段释义已全部收到这里。现场操作页只留按钮、参数和实时状态。")
            + _p(f"左侧点章节；上方可搜索。日常生产从「{T.MONITOR}」开始。")
            + _h("分页做什么")
            + _ul(
                [
                    f"<b>{T.MONITOR}</b>：初始化 / 启动 / 暂停 / 停止 / 急停、三色灯、槽号、记忆、速度、夹爪、压机手动、空跑快控。",
                    f"<b>{T.CAM_MONITOR}</b>：独立窗口（四路原图 + YOLO 结果），可与调试页同时开着。主界面顶部「{T.CAM_MONITOR}窗口」可再打开。",
                    f"<b>{T.PRODUCTION}</b>：下料完成记件、CT、UPH。",
                    f"<b>{T.STEP_DEBUG}</b>：按工位、按步单步跑。",
                    f"<b>{T.MOTION}</b>：按程序步调速度 / 平滑，不是按示教点名。",
                    f"<b>{T.VISION_SETUP}</b>：采图训练（含左右脚重训）、挂/训模型、写内参与手眼、测皮带、写入取料位、试抓。",
                    f"<b>{T.VISION}</b>：相机出图、ROI、棋盘格内参、检测测试、手眼点像素。",
                    f"<b>{T.POINTS}</b>：上料 R1 / 下料 R2 示教点、过渡点、偏移试跑。",
                    f"<b>{T.SHIELD_PICK}</b>：cam1 模拟时用的皮带取料示教点。",
                    f"<b>{T.DRY_RUN}</b>：无真皮带/相机/压机时走通工位 1～6。",
                    f"<b>{T.PAYLOAD}</b>：两臂负载与 TCP。",
                    f"<b>{T.PRESS_IO}</b>：左右口、槽号顺序、Modbus 地址。",
                    f"<b>{T.CONFIG}</b>：IP / CAN / DI / 各设备模拟开关，保存到 yaml。",
                    f"<b>{T.ALARM}</b>：历史与复位。",
                ]
            )
            + _h("灯语")
            + _ul(
                [
                    "绿常亮 = 自动运行",
                    "黄+绿常亮 = 初始化完成（READY，可启动）",
                    "黄常亮 = 停止后 / 未初始化",
                    "黄闪 = 初始化中 / 暂停 / 单步中",
                    "红常亮 = 报警",
                    "红闪 = 急停",
                ]
            )
            + _h("记忆 Mem1～10")
            + _p("仅程序内部 BOOL。自动运行中锁定；暂停 / 停止 / 报警 / 单步 / 空闲时可在运行监控勾选修改。")
            + _p("参数都在 <code>config/default.yaml</code>。通信配置页改完点保存会写回同一文件。"),
        ),
        (
            "monitor",
            T.MONITOR,
            _h("日常操作")
            + _ol(
                [
                    "模式选「自动」→ 初始化 → 状态 READY（黄+绿）→ 启动。",
                    "暂停可改记忆和槽号；停止后重新初始化再启动。",
                    "急停后必须报警复位，再初始化。",
                ]
            )
            + _h("槽号")
            + _p("停止/暂停后可改放料槽、取料槽和顺序，改完点「应用槽号」。改放料则取料按顺序联动，改取料则放料联动。自动运行中槽号与顺序锁定。")
            + _p("压鞋打当前放料槽（左口）；取料看当前取料槽（右口）。手臂在槽口时禁止旋转。")
            + _h("单步快捷 / 空跑")
            + _p("「单步：下一步」切到单步模式，给忙站发 StepPulse（条件满足才跳步）。初始化中则推进 InitStepPulse。细调请用「工位调试」。")
            + _p(f"「启动空跑程序」一键启用空跑屏蔽（光电/压机 Mock、先压后转；不改相机模拟）并切自动模式；仍需初始化 → 启动后才连续空跑。完整选项在「{T.DRY_RUN}」页。")
            + _h("路径平滑")
            + _p("总开关关则所有步都不交融。单步速度 / 是否平滑见「运动参数」。实际速度 ≈ 全局 SetSpeed% × 本步 vel%。")
            + _h("夹爪")
            + _p("停止 / 暂停 / 单步时可点开合，等「张开完成 / 夹紧完成」，不再固定延时。速度写入 CAN 开合命令速度字（默认约 50）；真机生效。Mock 只影响模拟完成时间。")
            + _h("压鞋机手动")
            + _p("真机写 Modbus。自动跑 Station6 时请先停止，不要同时猛点。「置旋转/压鞋完成」：Mock 置到位并清命令；真机请看 PLC 到位信号。"),
        ),
        (
            "cam_monitor",
            T.CAM_MONITOR,
            _h("独立窗口")
            + _p(f"相机监控不再占主界面标签。启动后会单独弹出，可与「{T.VISION} / {T.STEP_DEBUG}」同时摆在屏幕上。")
            + _p(f"关掉监控窗只是隐藏，不会退出程序。主界面顶部或视觉页的「{T.CAM_MONITOR}窗口」可再打开。勾选「窗口置顶」则始终浮在调试页之上。")
            + _p("宽屏会自动左右分栏（左主界面、右监控）。窄屏监控叠在右上角，可自己拖到第二块屏。")
            + _h("画面")
            + _p("四路各显示原图 | 计算结果。窗口可见时刷原图；关掉窗口即停止抢相机。")
            + _p("计算结果：勾选「实时推演」用缓存帧后台算法（不抢相机）；「结果跟原图」让右侧跟拍叠加上次数字，原图不卡顿。也可点「立即推演」。")
            + _p("YOLO 慢时推演会自动跳过忙路，保证原图流畅。未测通的那路请保持 Mock。"),
        ),
        (
            "production",
            T.PRODUCTION,
            _h("统计规则")
            + _p("Station5 下料皮带放料完成记 1 件。CT = 两次下料完成的时间差。UPH 即时 = 3600 / CT；平均 UPH 用最近约 20 次 CT。")
            + _p("「模拟记一件下料」只用于调试看板，不驱动机械。"),
        ),
        (
            "step",
            T.STEP_DEBUG,
            _h("现场步骤")
            + _ol(
                [
                    "模式切「单步」并启动（可先在本页点「切到单步模式」）。",
                    "选 Station / Auto，看步表（黄底=当前步；双击某行=跳到该步）。",
                    "互锁不满足时勾选「调试旁路」。",
                    "武装 Auto（从头步10）或武装到选中步。",
                    "「执行当前步/下一步」：该步跑到条件满足后停住。",
                    "「中止本站 Auto」清空该站步号（记忆不自动清，可在运行监控改）。",
                ]
            )
            + _h("路点")
            + _p("路点不够可「新增过渡点」，再到点位页采关节，用「试跑关联点」验证。过渡点可用中文名；同名覆盖。删除/覆盖后可「撤回路点操作」（与点位页共用撤回栈）。")
            + _p("按钮颜色：绿=执行/新增 · 红=中止/删除/停止 · 蓝=武装/撤回 · 琥珀=跳步 · 青绿=运动。")
            + _p("引导失败（cam2）：停在放料点、夹爪仍夹着。报警后暂停处理 → 报警复位 → 启动 → 从引导步继续。"),
        ),
        (
            "motion",
            T.MOTION,
            _h("按程序步调，不按示教点名")
            + _p("例如 pick_entry 进入（s2a10_30）与退出（s2a10_90）可设不同 vel / 平滑。")
            + _p("实际速度 ≈ 运行监控全局 SetSpeed% × 本步 vel%。加速度：MoveL 用 oacc%；MoveJ 的 acc 法奥暂可能不生效，仍可保存。平滑还需运行监控总开关。")
            + _p("注意：上一段「平滑」、下一段「到位」时，程序会先等手臂停稳再发到位令（否则会从半路对取料上方做 MoveCart，姿态异常易撞机）。进入点与取料上方建议同为平滑，或同为到位。"),
        ),
        (
            "zero_pick",
            T.VISION_SETUP,
            _h("这一页替代改 yaml/json/命令行")
            + _p(f"新机做完皮带抓取投产，不必手改 <code>shoe_vision_config.json</code>。预览点像素仍在「{T.VISION}」。")
            + _ol(
                [
                    "① 挂接旧模型，或自选 .pt 写入配置。可在本页 pip 安装 ultralytics（可选 CPU 或 GPU 版 torch）。",
                    "② 分类（槽/鞋头）：选类别后「采图」，不用圈框。左右脚旧模型不能用时：点「左右脚采图/训练」→ 选左脚/右脚 → 采图（自动抠鞋，把鞋头转到朝上）→ 两类都采够再训练。「训练设备」可选 CPU（不加GPU）或 GPU（CUDA）。找鞋/楦/压杆：每拍一张在下方圈旋转框；用旧模型可跳过。",
                    f"③ 「{T.VISION}」采棋盘格并计算内参 → 回本页「内参写入json」。绿框保存后「ROI写入json」。点像素并对准 TCP，至少 3 个散开点 →「计算手眼4×4并写入json」。",
                    f"④ 取消 cam1 Mock，「测试皮带拍照」→「写入PickPose」→ 确认安全后 MoveL 到取料上方。再去「{T.STEP_DEBUG}」单步工位 1/2，最后自动。",
                ]
            )
            + _p("手眼：base = T × 相机XYZ。磁盘默认矩阵是旧机的，本机必须重标。无深度时用「假定Z」（相机到皮带大约毫米）。")
            + _p(f"Z/Rx/Ry 抓取姿态仍用「{T.SHIELD_PICK}」里示教的 belt_pick_mock；视觉主要给 XY 与 Rz。"),
        ),
        (
            "vision",
            T.VISION,
            _h("原则")
            + _p(f"检测一律旧压鞋机 YOLO。OpenCV 只用于出图和棋盘格内参，没有形状模板备用。缺模型或缺 ultralytics 时，<b>该路保持 Mock</b>；皮带用「{T.SHIELD_PICK}」。")
            + _ul(
                [
                    "<b>cam1 皮带</b>：彩色+深度 → 鞋 OBB → 左右脚 → 楦 OBB → 相机 XYZ → 手眼 4×4 → 机器人毫米 (x,y,z,yaw)；鞋头距写入抓取 TCP 的 +Y。配置：<code>shoe_vision_config.json</code>（roi_ratio、内参、handeye.mat）。",
                    "<b>cam2 鞋头</b>：分类 0=到位，1=向前。Station2 相对 MoveL。模型 <code>models/toe_align/*.pt</code>。槽几何：<code>press_shoes/config/left_slot.yaml</code> / <code>right_slot.yaml</code>。",
                    "<b>cam3 放料槽</b>：0=空槽，1=有鞋。左右槽走流程记忆。",
                    "<b>cam4 取料槽</b>：有无鞋 + 压杆 XY，只叠加到示教 <code>slot_pick</code>。参数：<code>position_config.yaml</code>。",
                ]
            )
            + _h("共同：先出真图")
            + _ol(
                [
                    "USB 插稳；点「枚举设备」抄 serial。",
                    "填 serial（优先）→「写入serial并重开」。空 serial 才用 index；Gemini 彩色不要用 video-index 0/1。",
                    "取消该路 Mock，预览必须是真图再往下测。",
                    "四路 serial 不要填成同一台。cam2 这台一般是手眼 Gemini CV27561000FH。",
                    "把旧工程 models/ 拷到本工程 models/。看本页「YOLO模型」✓/✗。",
                ]
            )
            + _p("模型路径：<code>models/shoe_vision/</code>（鞋OBB、左右脚、楦）、<code>models/toe_align/</code>、<code>models/slot_check/</code>、<code>models/position/rod/obb.pt</code>。")
            + _p(f"旧模型可在「{T.VISION_SETUP}」点「挂接旧模型」，或 <code>bash tools/yolo_train/link_legacy_models.sh</code>。分类可在该页训练；命令行见 <code>tools/yolo_train/README.md</code>。")
            + _h("cam1 皮带")
            + _ol(
                [
                    "皮带上能看清整只鞋。roi_ratio 在 json 里裁皮带区域。",
                    "本机手眼 4×4 写入 json 的 handeye.mat（磁盘默认仍是旧机矩阵，本机必须重标）。本程序不再另开 RSDT，画面来自 cam1。",
                    "内参 fx/fy/cx/cy 与皮带相机一致，可先用旧值再棋盘格写入 json。",
                    "「测试皮带拍照」应打出楦心基座 XYZ、yaw、左右脚、鞋头距 mm。这就是自动 runtime_pick。",
                    "失败则勾回 Mock，用屏蔽示教点。",
                ]
            )
            + _h("cam2 / cam3 / cam4")
            + _p("cam2：「测试鞋头对位」。模拟时第一次给偏移、第二次到位。")
            + _p("cam3：「测试槽有无鞋」或「测试放料槽」。测右槽可勾 cam3 模拟 + 运行监控 Mock。")
            + _p("cam4：「测试取料槽」看有无鞋；「测试压杆偏移」看 dx/dy。测不到则 Station5 用示教点。")
            + _h("ROI 绿框")
            + _p("四路各有一份，文件 <code>config/roi/camN.json</code>。先选相机，再拖框/微调，最后「写入配置」。切相机时未保存的编辑留在本页，不会写到另一路。绿框给槽口/皮带搜索范围；皮带主检测还看 json 的 roi_ratio。")
            + _h("棋盘格内参")
            + _p("列/行填内角点数（默认 11×8），格边长填格子边长毫米（默认 15）。只标内参，不参与 YOLO 检测。保存后不会被检测结果改掉。建议 15 帧以上再「计算并保存内参」。")
            + _h("手眼文件")
            + _ul(
                [
                    "皮带生产用：<code>shoe_vision_config.json</code> 的 handeye.mat。",
                    "本程序采样：<code>config/calib/&lt;相机&gt;_handeye_samples.json</code>。",
                    "矩阵文件：<code>config/calib/&lt;相机&gt;_handeye.json</code>。皮带生产以 json 的 4×4 为准；HMI「计算手眼4×4」会同时写入这两处。",
                ]
            ),
        ),
        (
            "points",
            T.POINTS,
            _h("示教")
            + _p("MoveJ 路点请同时保存 TCP + 关节角 j1..j6，路径才与示教器一致（第二次从放料进入→取料进入也不拧腕）。")
            + _p("方法：示教器走到目标点 →「读入当前TCP+关节」→「保存」。仅有 TCP 无关节时 MoveJ 会回退 MoveCart（路径可能怪）。关节滚轮已禁用，请点箭头或键盘输入。")
            + _p(f"「到此点平滑」仅用于本页点位试跑。自动流程的速度/平滑请到「{T.MOTION}」按工位步设置（同一示教点进入/退出可不同）。实际速度 ≈ 全局 SetSpeed% × 本步 vel%。取放终点流程里仍可强制到位。")
            + _h("过渡点")
            + _p("示教器走到期望位置 →「新增过渡点」输入名称（可用中文）。同名则覆盖该点 TCP+关节；不同名则新建。删除/覆盖后可用「撤回路点操作」恢复（与程序调试页共用撤回栈）。")
            + _p("按钮颜色：绿=新增/保存 · 红=删除/停止 · 蓝=撤回/读入 · 琥珀=清除 · 青绿=运动试跑。")
            + _h("偏移试跑")
            + _p("基点 + 偏移 = 实际目标；偏移不是绝对坐标。例如：基点=鞋槽放料点 + 偏移=place_above_offset → 走到放料上方。皮带取料上方请选基点「当前PickPose」。")
            + _h("常用点")
            + _p("机器人1：home, pick_entry, pick_above_offset, place_entry, place_slot, place_above_offset。")
            + _p("机器人2：home, slot_pick_entry, slot_pick, slot_pick_above_offset, belt_place_entry, belt_place, belt_place_above_offset。")
            + _p("取料点 XYR 由视觉写入 runtime_pick；Z/Rx/Ry 用配置默认值。*_above_offset 是相对偏移，不需要关节。"),
        ),
        (
            "shield",
            T.SHIELD_PICK,
            _h("何时用")
            + _p(f"相机1 为 Mock 时，皮带取料目标走本页：<code>vision.belt_pick_mock</code>。空跑联调请先到「{T.DRY_RUN}」一键启用。")
            + _p("默认「左右鞋交替」给出 PickPose（一次左、一次右）。关闭交替则按 X 最小只取一只。")
            + _h("示教")
            + _p("示教器走到取料位 →「读入当前TCP」→「保存」→ 可用 Move 试跑。每只鞋务必勾对「左鞋 is_left_shoe」，放料槽方向才会与 Mem8/9 正确比对。")
            + _p("鞋头 Y 偏移仅 Mock 用。真机由 cam1 按鞋长测量，不要把所有鞋写成同一个值。公共 Z/Rx/Ry 所有屏蔽鞋共用。"),
        ),
        (
            "dry",
            T.DRY_RUN,
            _h("用途")
            + _p("没有真皮带 / 真相机 / 真压机时，验证 Station1～6 自动握手与两臂轨迹。")
            + _p("「一键启用空跑」会：光电模拟并保持有料、压机强制 Mock、放料/取料槽按流程自动给有无料。相机模拟仍以通信配置为准，不会被空跑改掉。")
            + _p("时序：先压鞋完成延时再旋转完成延时（对齐 Station6）；放料槽自动跟手且空槽；取料槽在待转时无料 / 转完后有料（避免 Mem6 卡 S6）。")
            + _p(f"屏蔽取料示教点仍在「{T.SHIELD_PICK}」；本页只管运行屏蔽信号。手动信号在空跑自动项开启时会被周期改写。")
            + _h("推荐步骤")
            + _ol(
                [
                    f"本页「一键启用空跑」（或{T.MONITOR}「启动空跑程序」）。",
                    f"「{T.SHIELD_PICK}」确认左右鞋示教点。",
                    f"{T.CONFIG}：机械臂/夹爪若无真机请用 Mock；真机臂可只开光电模拟。",
                    f"{T.MONITOR}：初始化 → 启动（自动连续空跑）。",
                    f"单步验证：模式切「单步」→ 启动 →「{T.STEP_DEBUG}」武装/下一步，或{T.MONITOR}「下一步」。",
                    "观察 Mem1～10 与工位步号（S6：压鞋完成 → 再旋转）。",
                ]
            ),
        ),
        (
            "payload",
            T.PAYLOAD,
            _h("两臂独立")
            + _p("上料臂 / 下料臂参数分开，互不影响。未抓鞋 → 负载1 + 工具坐标1；抓鞋 → 负载2 + 工具坐标2。TCP = 工具中心相对法兰（mm / °）。")
            + _p("连接真机后：yaml 里 TCP 非全 0 会自动下发到示教器；也可勾选后点「保存并下发」（会覆盖示教器该工具坐标）。「读回控制器TCP」把示教器当前工具1/2 读到本页。"),
        ),
        (
            "press",
            T.PRESS_IO,
            _h("开口与顺序")
            + _p("左口=放料口，右口=取料口。自动运行按所选顺序自行计算槽号（旋转到位后推进）。")
            + _p("正序 12341：取1放2…；反序 43214：取1放4…。压杆打放料槽，取料看取料槽完成。")
            + _p("公共口四槽共用；槽1～4 各自独立地址（0=未接）。实机改完点保存。")
            + _p("「锁定手动槽号」：勾选后旋转到位仍可按顺序推进；周期刷新不再改你设的槽号。「取料→推算放料」用取料槽号顺时针推算放料槽号。")
            + _p("改当前槽号用「应用槽号」。顺序/自算改完请点「保存地址到 yaml」。"),
        ),
        (
            "config",
            T.CONFIG,
            _h("改地址")
            + _p("全部接口参数可在本页修改并保存到 <code>config/default.yaml</code>（不要去改 <code>devices/robot_fr5.py</code>）。")
            + _p("上实机：取消对应设备「模拟」→ 填 IP/CAN/DI/Modbus 地址 → 保存。夹爪/压机/机器人会尽量立刻重连；相机改序号/序列号后建议重启程序。")
            + _p("只有一台真机时，不要只改 system.use_mock，应改各设备自己的 use_mock。改完建议重启。")
            + _ul(
                [
                    "上料机器人 robots.robot1.ip",
                    "下料机器人 robots.robot2.ip",
                    "压鞋机 press.ip / press.port",
                    "夹爪 grippers.gripper1/2.can_id（两夹爪必须不同）",
                ]
            ),
        ),
        (
            "alarm",
            T.ALARM,
            _h("处理")
            + _p("红灯后先暂停处理现场，再「报警复位」。急停需硬件恢复后再复位、初始化。报警全文可选中复制。"),
        ),
        (
            "mock_auto",
            "Mock 走通自动 / 接真机",
            _h("Mock 快速走通")
            + _ol(
                [
                    "打开软件，模式选自动。",
                    "初始化（Mock 会很快完成）→ READY。",
                    "空跑页一键启用；或运行监控勾光电、Mock取料槽有料、放料空槽且左右匹配。",
                    "启动。观察 S1 拍照 → S2 取料 →（Mem4）S3 拍放料槽 → S2 放料 → …",
                    "S6 先压鞋完成再旋转完成。卡住可手动点「模拟压鞋机旋转完成」。",
                ]
            )
            + _h("接真机前")
            + _ul(
                [
                    "要接的设备各自 use_mock=false，未到场的保持 true。",
                    "对应机器人 IP 能 ping 通。",
                    "皮带光电 DI 号填对。",
                    "两夹爪 can_id 不同。",
                    "压鞋机 Modbus IP/端口/addr_*。",
                    "工控机急停 DI。",
                    "四相机出图 + YOLO 模型 + cam1 手眼 OK。",
                    "单站单步验证无干涉 → 自动空跑 → 带鞋试产。",
                ]
            ),
        ),
    ]


def all_text_for_search() -> str:
    return "\n".join(f"{t}\n{html}" for _i, t, html in sections())
