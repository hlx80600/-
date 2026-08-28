"""HMI 使用说明正文：每页做什么、实现在哪、引用什么、被谁调用。"""

from __future__ import annotations

from typing import List, Tuple

from hmi import i18n
from hmi.tab_titles import T, nav_title


def _L(nav_id: str) -> str:
    """帮助文档中的当前语言导航名。"""
    return nav_title(nav_id)

Section = Tuple[str, str, str]  # id, title, html


def _h(title: str) -> str:
    return f"<h2>{title}</h2>"


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def _ol(items: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>"


def _code(path: str) -> str:
    return f"<code>{path}</code>"


def _io_block(*, purpose: str, impl: list[str], refs: list[str], used_by: list[str]) -> str:
    """统一块：职责 / 实现 / 引用 / 被引用。"""
    return (
        _h(i18n.tr("help.section.what"))
        + _p(purpose)
        + _h(i18n.tr("help.section.impl"))
        + _ul(impl)
        + _h(i18n.tr("help.section.refs"))
        + _ul(refs)
        + _h(i18n.tr("help.section.used_by"))
        + _ul(used_by)
    )


def sections() -> List[Section]:
    """按当前界面语言返回帮助章节。"""
    lang = i18n.language()
    if lang == "zh-CN":
        return _sections_zh_cn()
    if lang == "zh-TW":
        return _sections_zh_tw()
    return _sections_en_us()


def _sections_zh_cn() -> List[Section]:
    return [
        (
            "overview",
            "总览：怎么查、灯语、记忆、流程",
            _h("怎么用本页")
            + _p(
                "本「使用说明」是现场操作的完整手册。左侧按章节浏览，上方可搜索。"
                "每个单页都写清：做什么、实现文件、引用了什么、被谁调用。"
            )
            + _p(
                f"主界面左侧为「功能导航」列表（不再用顶部标签左右翻页）；"
                f"日常生产从「{_L(T.MONITOR)}」开始；长释义只在本页，操作页只留按钮和状态。"
            )
            + _h("程序入口与主扫描")
            + _ul(
                [
                    f"入口：{_code('main.py')} → 建 Coordinator + MainWindow。",
                    f"主循环：{_code('core/coordinator.py')} 约 50ms 调各 Station 的 cycle。",
                    f"全局状态：{_code('core/gvl.py')}（Main / Station / Memory_BOOL）。",
                    f"设备与视觉：{_code('core/app_context.py')}（robot1/2、夹爪、压机、cameras、vision）。",
                    f"参数总表：{_code('config/default.yaml')}（IP、点位、Mock、运动步参数）。",
                    "纸质/仓库详解：docs/程序总览.md（主流程）；视觉接口见 algorithm_module/readme.md。",
                ]
            )
            + _h("一条鞋怎么走（自动）")
            + _ul(
                [
                    "Station1 皮带拍照 → 得到取料坐标",
                    "Station2 上料臂取料 →（需要时 Station3 拍放料槽）→ 放料对位压跟张爪",
                    "Station6 压合 → 转台推进槽号",
                    "Station4 拍取料槽 ± 压杆 → Station5 下料放到皮带",
                    "详细步序与文件：见本说明「工位程序」章，或 docs/程序总览.md",
                ]
            )
            + _h("新机投产总顺序（必按序）")
            + _ol(
                [
                    f"<b>{_L(T.CONFIG)}</b>：IP / CAN / 相机 serial / Mock → 保存 → <b>重启程序</b>",
                    f"<b>{_L(T.PAYLOAD)}</b>：手爪 TCP 与抓鞋负载 → 保存并下发",
                    f"<b>{_L(T.POINTS)}</b>：示教进入点、槽点、偏移 → 保存",
                    f"<b>{_L(T.MOTION)}</b>：各步速度；退回进入点建议关闭平滑",
                    f"<b>{_L(T.PRESS_IO)}</b>：槽号顺序与地址",
                    f"<b>{_L(T.VISION)}</b>：挂/训模型；页内 ROI → 棋盘格内参 → 手眼 → 检测测试 → 采图训练",
                    f"<b>{_L(T.SHIELD_PICK)}</b>：仅 cam1 Mock 时示教取料点",
                    f"<b>{_L(T.GRIPPER)}</b>：单机开合确认",
                    f"<b>{_L(T.DRY_RUN)}</b> / <b>{_L(T.STEP_DEBUG)}</b>：空跑或单步",
                    f"<b>{_L(T.MONITOR)}</b>：自动 → 初始化 → 启动",
                ]
            )
            + _p(
                "纸质详解（含手眼逐步点击顺序）："
                + _code("docs/界面操作手册.md")
                + "；参数改址："
                + _code("docs/操作说明.md")
                + "。"
            )
            + _h("分页一览")
            + _ul(
                [
                    f"<b>{_L(T.MONITOR)}</b>：启停、灯、记忆、槽号、速度、夹爪/压机手动",
                    f"<b>{_L(T.CAM_MONITOR)}</b>：独立窗，四路原图+推演（含中心→鞋头/抓鞋前后TCP）",
                    f"<b>{_L(T.JOG)}</b>：独立示教器窗口（顶栏打开），点动机械臂，可与示教点位同时开",
                    f"<b>{_L(T.PRODUCTION)}</b>：记件 / CT / UPH",
                    f"<b>{_L(T.STEP_DEBUG)}</b>：按工位单步",
                    f"<b>{_L(T.MOTION)}</b>：按程序步 vel/平滑",
                    f"<b>{_L(T.VISION)}</b>：总页（相机与ROI / 棋盘格 / 手眼 / 检测 / 采图训练）",
                    f"<b>{_L(T.POINTS)}</b>：示教点 / 过渡点",
                    f"<b>{_L(T.SHIELD_PICK)}</b>：cam1 Mock 取料示教",
                    f"<b>{_L(T.DRY_RUN)}</b>：空跑屏蔽信号",
                    f"<b>{_L(T.PAYLOAD)}</b>：负载与 TCP",
                    f"<b>{_L(T.PRESS_IO)}</b>：压机槽号与地址",
                    f"<b>{_L(T.GRIPPER)}</b>：夹爪单独调试与 GRIP_* 报警复位",
                    f"<b>{_L(T.CONFIG)}</b>：通信与 Mock",
                    f"<b>{_L(T.ALARM)}</b>：本次运行 / 落盘错误 / 黑匣子 / 运行快照（生产图，文件名含相机与时间）",
                    f"<b>算法接口 / 工位程序</b>：见本说明后几章",
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
            + _p(
                "程序内部 BOOL（"
                + _code("core/gvl.py")
                + " 的 Memory_BOOL）。自动运行中锁定；暂停/停止/报警/单步/空闲时可在运行监控勾选修改。"
            )
            + _ul(
                [
                    "1 皮带拍照完成；2 上料爪有料；3 放料槽有料待压转；4 放料槽拍照完成",
                    "5 下料爪有料；6 取料槽有料；7 取料槽拍照完成；8/9 左/右鞋；10 放料方向不匹配",
                ]
            ),
        ),
        (
            "monitor",
            _L(T.MONITOR),
            _io_block(
                purpose=(
                    "产线主操作台：初始化、自动/单步、启动暂停停止急停；看三色灯与工位忙闲；"
                    "改记忆与槽号；调全局速度与路径平滑总开关；手动夹爪/压机；空跑与单步快捷入口。"
                ),
                impl=[
                    f"{_code('hmi/pages/monitor_page.py')} — UI",
                    f"{_code('hmi/main_window.py')} — 挂标签、100ms refresh",
                ],
                refs=[
                    f"{_code('core/coordinator.py')} — 初始化/运行模式/启停",
                    f"{_code('core/lights.py')} — 三色灯",
                    f"{_code('core/memory.py')} — Mem 读写",
                    f"{_code('devices/robot_fr5.py')} — SetSpeed",
                    f"{_code('devices/gripper_can.py')} / {_code('devices/press_modbus.py')} — 手动",
                    f"{_code('core/dry_run_shield.py')} — 「启动空跑程序」",
                ],
                used_by=[
                    "操作员每日必开页",
                    f"「单步下一步」联动「{_L(T.STEP_DEBUG)}」逻辑",
                    f"「启动空跑」联动「{_L(T.DRY_RUN)}」屏蔽策略",
                ],
            )
            + _h("操作要点")
            + _ol(
                [
                    "模式选「自动」→ 初始化 → READY（黄+绿）→ 启动。",
                    "暂停可改记忆和槽号；停止后需重新初始化再启动。",
                    "急停后报警复位 → 再初始化。",
                    "实际臂速 ≈ 本页全局 SetSpeed% ×「运动参数」里该步 vel%。",
                ]
            ),
        ),
        (
            "cam_monitor",
            _L(T.CAM_MONITOR),
            _io_block(
                purpose=(
                    "独立窗口显示 cam1～4 原图与推演结果。"
                    "「刷新原图」后台取流；「实时推演」用缓存帧跑算法不抢相机；"
                    "「结果跟原图」右侧跟拍叠上次结果文字，避免卡顿掉帧。"
                ),
                impl=[
                    f"{_code('hmi/pages/vision_monitor_page.py')} — VisionMonitorPage / Window",
                    f"{_code('hmi/main_window.py')} — show_cam_monitor、启动自动弹窗",
                ],
                refs=[
                    f"{_code('visualize_module/')} — LiveGrabber、LiveComputeLoop、CamPane",
                    f"{_code('vision/vision_service.py')} — last_raw / last_vis / compute_monitor(from_cache)",
                    f"{_code('algorithm_module')} — 推演内部算法（经 VisionService）",
                ],
                used_by=[
                    f"主界面顶部「{_L(T.CAM_MONITOR)}窗口」按钮",
                    f"「{_L(T.VISION)}」打开监控按钮",
                    "与「视觉」总页同开时避让当前调试相机，减少抢锁闪烁",
                    "推演只刷新画面，不写入运行快照（避免刷盘）；查历史请到「报警记录 → 运行快照」",
                ],
            ),
        ),
        (
            "production",
            _L(T.PRODUCTION),
            _io_block(
                purpose="产量看板：下料完成记件数、节拍 CT、换算 UPH（即时与滚动平均）。",
                impl=[f"{_code('hmi/pages/production_page.py')}"],
                refs=[
                    f"{_code('core/production_stats.py')} — 记件与 CT 窗口",
                    "触发点：Station5 下料皮带放料完成回调",
                ],
                used_by=["现场看产能；「模拟记一件」仅测看板不驱动机械"],
            ),
        ),
        (
            "step",
            _L(T.STEP_DEBUG),
            _io_block(
                purpose=(
                    "按 Station / Auto 程序单步：看步表、武装某步、执行下一步、中止本站 Auto、调试旁路。"
                    "可新增过渡点并与点位页共用撤回栈。"
                ),
                impl=[
                    f"{_code('hmi/pages/step_debug_page.py')}",
                    f"{_code('stations/step_catalog.py')} — 步标题表",
                ],
                refs=[
                    f"{_code('stations/station1_*.py')} … {_code('station6_*.py')} — cycle 真实逻辑",
                    f"{_code('core/step_engine.py')} / {_code('core/plc_util.py')} — 步脉冲与延时",
                    f"{_code('core/point_undo.py')} — 路点撤回",
                    f"{_code('core/app_context.py')} — move_to_point / step_motion_kwargs",
                ],
                used_by=[
                    "新机调轨迹、排查卡步必用",
                    f"与「{_L(T.POINTS)}」「{_L(T.MOTION)}」配合：先示教点 → 再设步速 → 再单步验证",
                ],
            ),
        ),
        (
            "motion",
            _L(T.MOTION),
            _io_block(
                purpose=(
                    "按「程序步键」（如 s2a10_30）设置 vel / 加速度 / 是否平滑 / blend，"
                    "不是按示教点名。同一点进入与退出可不同速度。"
                ),
                impl=[
                    f"{_code('hmi/pages/motion_steps_page.py')}",
                    f"{_code('core/motion_steps.py')} — 读写 yaml motion_steps",
                ],
                refs=[
                    f"{_code('config/default.yaml')} → motion_steps",
                    f"{_code('core/app_context.py')} step_motion_kwargs — 工位发 Move 时读取",
                    f"{_code('devices/robot_fr5.py')} move_j / move_l — 真正发法奥 MoveJ/MoveL",
                ],
                used_by=[
                    "Station2/5 等所有 ctx.move_to_point(..., step_key=...) / step_motion_kwargs",
                    f"全局倍率仍在「{_L(T.MONITOR)}」SetSpeed",
                ],
            ),
        ),
        (
            "zero_pick",
            f"{_L(T.VISION)} · 采图训练",
            _io_block(
                purpose=(
                    "「视觉」总页「采图训练」子页签：挂/训模型、采图、将棋盘格内参与手眼写入 shoe_vision_config.json、"
                    "测皮带取料、写入 PickPose、MoveL 试抓。左右脚可本页重训。"
                ),
                impl=[f"{_code('hmi/pages/zero_to_pick_page.py')}"],
                refs=[
                    f"{_code('algorithm_module')} / {_code('vision/commission_actions.py')} — 写内参手眼、试抓",
                    f"{_code('vision/model_store.py')} — 数据集槽位、train_cmd、CUDA 检测",
                    f"{_code('vision/obb_label.py')} / {_code('hmi/pages/obb_label_widget.py')} — OBB 圈框",
                    f"{_code('hmi/pages/cls_preview_widget.py')} — 分类预览旋转裁剪",
                    f"{_code('shoe_vision_config.json')} — 生产手眼与内参",
                ],
                used_by=[
                    "投产前必做；训完的 .pt 被 VisionService / Station1～4 加载",
                    f"点像素预览在「{_L(T.VISION)}」上方预览区 /「手眼标定」页签",
                ],
            )
            + _h("推荐流程")
            + _ol(
                [
                    "① 挂接旧模型或自选 .pt；可 pip 装 ultralytics（CPU/GPU 版）。",
                    "② 采图/训练；「训练设备」选 CPU 或 GPU。",
                    f"③ 切到「{_L(T.VISION)}」其它页签：ROI → 棋盘格内参 → 手眼采样 → 回「采图训练」写入 json。",
                    f"④ 取消 cam1 Mock → 测皮带 → 写 PickPose → MoveL 试上方 →「{_L(T.STEP_DEBUG)}」单步。",
                ]
            )
            + _p("完整检查清单：" + _code("docs/界面操作手册.md") + " §C。"),
        ),
        (
            "vision",
            _L(T.VISION),
            _io_block(
                purpose=(
                    "视觉总页：上方常驻预览；子页签含相机与ROI、棋盘格内参、手眼标定、检测测试、采图训练。"
                    "缺模型时该路保持 Mock。"
                ),
                impl=[
                    f"{_code('hmi/pages/vision_hub_page.py')} — VisionHubPage",
                    f"{_code('hmi/pages/vision_workspace.py')} — 共享预览与标定状态",
                    f"{_code('hmi/pages/vision_commission.py')} — 本路检查清单文案",
                ],
                refs=[
                    f"{_code('vision/camera_orbbec.py')} — 取流",
                    f"{_code('vision/calib.py')} / {_code('vision/roi.py')} / {_code('vision/handeye_solve.py')}",
                    f"{_code('vision/vision_service.py')} — photo_* / guide_place_edge / 测试接口",
                    f"{_code('algorithm_module')} — detect / classify / measure",
                    f"配置：{_code('config/roi/camN.json')}、{_code('config/calib/')}、{_code('shoe_vision_config.json')}",
                ],
                used_by=[
                    "「采图训练」写入 json 依赖本页采到的内参/采样",
                    "自动流程不直接开本页，但用同一套 VisionService；生产拍照落盘后到「报警记录 → 运行快照」查",
                ],
            )
            + _h("四路职责")
            + _ul(
                [
                    "<b>cam1</b>：皮带找鞋 → 基座取料位姿（Station1）",
                    "<b>cam2</b>：鞋头对位分类 → Station2 相对 MoveL",
                    "<b>cam3</b>：放料槽有无鞋（Station3）",
                    "<b>cam4</b>：取料槽有无鞋 + 压杆偏移（Station4→5）",
                ]
            )
            + _h("运行快照（到报警记录页查）")
            + _p(
                "自动流程拍照以及本页「检测测试」，会把当时原图、叠图、检测结果存到 "
                + _code("logs/vision_snaps/")
                + "。图片文件名带相机和时间，例如 "
                + _code("cam1_20260828_140455_635_belt_pick_raw.jpg")
                + "。放入鞋槽、槽判定、下料放到皮带完成后，会把结果写回<b>同一条</b>记录。"
                "到左侧「报警记录 → 运行快照」翻历史。"
                "相机监控窗的实时推演<b>不存</b>。"
                "完整说明见后章「报警记录 · 运行快照」，纸质手册 "
                + _code("docs/界面操作手册.md")
                + " §14.4。"
            )
            + _h("本页推荐操作顺序（每路相机）")
            + _ol(
                [
                    "通信配置里该路 Mock=关、填 serial，保存并重启；「相机与ROI」枚举 → 绑定并重开。",
                    "调 ROI 绿框 →「写入配置（保存 ROI）」；cam1 再可「ROI 写入皮带 json」（手眼页签）。",
                    "棋盘格：检测 → 多角度「采集有效帧」→「计算并保存内参」；cam1「内参写入皮带 json」。",
                    "手眼（cam1 必做）：预览上点针尖像素 →「记录手眼采样点」"
                    "→ 换位采满 ≥8～12 点 →「保存手眼采样」→「计算手眼4×4写入 json」。",
                    "「检测测试」测皮带 →「写入 PickPose」→「MoveL 到取料上方」核对。",
                    "其它路：用对应「测试…」按钮验证模型；再切「采图训练」写生产 json / 单步。",
                ]
            )
            + _h("手眼注意")
            + _ul(
                [
                    "必须先有内参再求解手眼；点像素与读 TCP 时臂必须停稳。",
                    "采样点铺满皮带工作区，略变高度；算错用「清除手眼采样/矩阵」重来。",
                    "改完手眼建议重启程序再跑自动。详图步骤见 docs/界面操作手册.md §6。",
                ]
            ),
        ),
        (
            "vision_snaps",
            "报警记录 · 运行快照（历史图 / 运送结果 / 打开log）",
            _h("做什么")
            + _p(
                "把「当时看见什么」和「后来运到哪里、运得对不对」绑在同一条记录上，方便现场翻图排故，"
                "不必停机去工控机翻文件夹。"
            )
            + _p(
                "拍照瞬间写入原图、叠图、检测结果；机器人完成放入鞋槽或下料到皮带后，再把结果写回这一条。"
                "HMI「报警记录 → 运行快照」可浏览；也可打开视觉 log 文件夹用资源管理器看原始文件。"
                "每张 jpg 文件名含相机和时间，拷出来也能分清是哪路、何时拍的。"
            )
            + _h("实现文件")
            + _ul(
                [
                    f"{_code('hmi/pages/vision_snap_page.py')} — 历史列表、原图/叠图、详情、打开目录",
                    f"{_code('hmi/pages/alarm_page.py')} — 「报警记录」第四个页签「运行快照」",
                    f"{_code('vision/vision_journal.py')} — 落盘、运送回写、列表扫描",
                    f"{_code('vision/vision_service.py')} — photo_* 默认 persist=True；监控推演 persist=False",
                    f"{_code('stations/station1_belt_photo.py')} — 确认取料时记下 vision_snap_id",
                    f"{_code('stations/station2_robot1.py')} — 放料完成回写 place",
                    f"{_code('stations/station3_place_slot_photo.py')} — 槽判定回写 slot_check",
                    f"{_code('stations/station4_pick_slot_photo.py')} — 记下下料本拍 id",
                    f"{_code('stations/station5_robot2.py')} — 下料放皮带完成回写 unload",
                ]
            )
            + _h("什么时候会存一张")
            + _ul(
                [
                    "<b>Station1</b> 皮带拍照（cam1）：自动流程每次 photo_belt_pick，成功或失败都落盘。"
                    "操作员确认本拍取料位后，把 snap_id 写入 BeltPickSnapshot，供后续运送回写。",
                    "<b>Station3</b> 放料槽拍照（cam3）：判空槽、左右槽。",
                    "<b>Station4</b> 取料槽拍照（cam4）：有无料、压杆偏移。",
                    "视觉页「检测测试」：测试皮带拍照 / 测试放料槽 / 测试取料槽（与生产同一套接口，便于对模型）。",
                    "「视觉结果写入 PickPose」：会带上本次 snap_id，之后若走自动放料，仍可回写运送结果。",
                ]
            )
            + _h("什么时候故意不存")
            + _ul(
                [
                    "「相机监控」窗口的实时推演、监视页定时刷新：频率高，存了会把磁盘刷满，也没有「这一拍对应哪只鞋」。",
                    "cam2 鞋头对位、贴边引导：运动中反复拍，不作为运行快照。",
                    "视觉页上方「截图保存」：那是标定用截图，目录是 "
                    + _code("config/vision_snaps/")
                    + "，和运行快照不是一回事。",
                ]
            )
            + _h("磁盘里长什么样")
            + _p(
                "根目录："
                + _code("logs/vision_snaps/")
                + "（已 gitignore，不会进版本库）。按日期分子目录："
            )
            + _ul(
                [
                    _code("cam1_20260828_140455_635_belt_pick_raw.jpg") + " — 原图（文件名=相机_时间_类型_raw）",
                    _code("…_vis.jpg") + " — 叠了检测框/文字的图",
                    _code("meta.json") + " — 检测字段 + transport（运送回写）",
                    _code("logs/vision_snaps/index.jsonl") + " — 总索引（拍照一行、每次运送回写再追加一行）",
                ]
            )
            + _p(
                "快照 id 形如 <code>20260828_111343_635_cam1_belt_pick</code>，含时间、相机、类型，便于对文件名。"
            )
            + _h("运送之后写回什么")
            + _p(
                "写在该条 <code>meta.json</code> 的 <code>transport</code> 里，HMI 详情区「运送结果」就是读这里。"
                "列表右侧摘要（已放槽#2 / 未运送 / 可放料 / 已下料）也来自这里。"
            )
            + _ul(
                [
                    "<b>place</b>（放入鞋槽）— Station2 放料完成、回到 place_entry 之后。"
                    "含是否成功、放料槽号、左右脚。对应 cam1 皮带那一拍。",
                    "<b>slot_check</b>（槽判定）— Station3 判定结束：空槽且左右对应→可放料；"
                    "左右不配→禁止放料只转不压；槽内有料→禁止放料。会写到 cam3 本拍，并同步写到正在运送的 cam1 那一拍。",
                    "<b>unload</b>（下料到皮带）— Station5 放到皮带并记产量之后。含取料槽号。对应 cam4 那一拍。",
                ]
            )
            + _p(
                "若详情写「尚未回写」：可能还在途中（刚拍完、手臂还没放到位），"
                "或只点了「检测测试」没有跑自动工位。点「刷新」再看一次。"
            )
            + _h("HMI 怎么打开、怎么查")
            + _ol(
                [
                    "左侧导航打开「报警记录」，点页签「运行快照」（在「黑匣子」旁边）。",
                    "点「打开视觉log文件夹」：用系统文件管理器打开 "
                    + _code("logs/vision_snaps/")
                    + "。",
                    "左侧列表新的在前；可用「相机」「类型」筛选；每页 20 条，用首页/上一页/下一页/末页翻。",
                    "点一条：右侧看原图、叠图；下方看检测数据（坐标、左右脚、鞋长、有无料等）和运送回写。",
                    "刚放完料或刚下料：点「刷新」，同一条上会出现「已放槽#n」或「已下料」。",
                    "「打开本条目录」打开这一拍的文件夹（可拷带相机和时间的 jpg 与 meta.json 发给别人）。",
                    "「复制详情」把当前文字说明拷到剪贴板。",
                ]
            )
            + _h("和「截图目录 / 标定目录」的区别")
            + _ul(
                [
                    "<b>打开视觉log文件夹 / 运行快照</b> → "
                    + _code("logs/vision_snaps/")
                    + " 生产运行记录，带运送结果。",
                    "<b>截图目录</b> → "
                    + _code("config/vision_snaps/")
                    + " 视觉页「截图保存」的标定截图，没有运送回写。",
                    "<b>标定目录 / YOLO模型目录</b> → 内参手眼文件、权重，不是运行历史。",
                    "「报警记录」里另有「打开日志目录」→ 整个 "
                    + _code("logs/")
                    + "（app.log、黑匣子等）；运行快照页的「打开视觉log文件夹」只打开 vision_snaps。",
                ]
            )
            + _h("开关与保留天数")
            + _p(
                _code("config/default.yaml")
                + " → <code>vision.save_runtime_snaps</code>（默认 true，false 则完全不存）"
                "；<code>vision.snap_keep_days</code>（默认 7，过期日期目录会自动删）。"
                "改 yaml 后重启程序生效。"
            )
            + _h("现场怎么用它排故")
            + _ul(
                [
                    "取偏、抓空：筛 cam1 / 皮带取料，对照叠图与 X/Y/Rz、左右脚、鞋长。",
                    "放错槽、只转不压：看 cam1 那条的 slot_check / place，以及 cam3 放料槽判定原文。",
                    "下料取空或皮带放偏：筛 cam4，看有无料、压杆偏移，以及 unload 的槽号。",
                    "列表写「未运送」但流程已走完：点刷新；仍没有则看报警记录/黑匣子是否中途停过。",
                    "图还是灰的「暂无」：JPEG 在后台线程写，等一两秒再刷新。",
                ]
            )
            + _h("被谁使用")
            + _ul(
                [
                    "现场操作员：报警记录页翻图，不必上工控机开资源管理器",
                    "调试：检测测试与自动流程用同一落盘，便于对比 Mock / 真机",
                    f"纸质步骤：{_code('docs/界面操作手册.md')} §14.4",
                ]
            ),
        ),
        (
            "points",
            _L(T.POINTS),
            _io_block(
                purpose="示教并保存机器人1/2 的 TCP+关节角、过渡点、偏移试跑（基点+偏移）。",
                impl=[f"{_code('hmi/pages/points_page.py')}"],
                refs=[
                    f"{_code('config/default.yaml')} → points.robot1 / robot2",
                    f"{_code('devices/robot_fr5.py')} — 读当前位姿、MoveJ/MoveL 试跑",
                    f"{_code('core/point_undo.py')} — 与工位调试共用撤回",
                    f"{_code('devices/pose_utils.py')} — 位姿/关节解析",
                ],
                used_by=[
                    "Station2/5 经 ctx.pose / move_to_point 读这些点",
                    f"自动流程步速度在「{_L(T.MOTION)}」；本页试跑用上方速度条（1%～25%）",
                ],
            )
            + _h("常用点名")
            + _p("R1：home, pick_entry, pick_above_offset, place_entry, place_slot, place_above_offset。")
            + _p("R2：home, slot_pick_entry, slot_pick, slot_pick_above_offset, belt_place_entry, belt_place, …")
            + _p("皮带取料 XYR 常由视觉写入 runtime_pick；*_above_offset 为相对偏移。")
            + _h("单点调试")
            + _p(
                "「按住 MoveJ/MoveL 到此点」：按住才动，松开立刻停；到位弹窗。"
                "点按不会动。偏移点请先选好基点+偏移再按住。"
                "试跑速度用本页速度条（默认 8%，上限 25%），不改自动运行。"
            ),
        ),
        (
            "jog",
            _L(T.JOG),
            _io_block(
                purpose=(
                    "独立示教器窗口封装机械臂点动（基座/工具笛卡尔或关节），"
                    "不必切到法奥示教器，也不必离开当前主界面页。"
                    "默认「按住连续」：按住才动，松开立刻 ImmStopJOG；"
                    "「点按一步」按设定 mm/° 走一次。"
                ),
                impl=[
                    f"{_code('hmi/pages/jog_pendant.py')} JogPendantWindow / JogPendantPanel",
                    f"{_code('hmi/main_window.py')} 顶栏「示教器」show_jog_pendant",
                ],
                refs=[
                    f"{_code('devices/robot_fr5.py')} start_jog / stop_jog → 法奥 StartJOG / StopJOG",
                    "自动运行、急停、报警时锁定",
                ],
                used_by=[
                    "主界面顶栏「示教器」；「示教点位」页「打开示教器」",
                    f"示教点保存仍在「{_L(T.POINTS)}」读当前 TCP/关节",
                ],
            )
            + _h("注意")
            + _ul(
                [
                    "速度上限 25%，建议先 8%。周围清人，急停随时可拍。",
                    "工具坐标系随当前激活 TCP 变化；夹爪开合仍用夹爪页/运行监控。",
                    "关示教器窗口或关主程序会停止点动。",
                ]
            ),
        ),
        (
            "shield",
            _L(T.SHIELD_PICK),
            _io_block(
                purpose=(
                    "cam1 为 Mock 时，用本页示教的多只鞋位（belt_pick_mock）代替 YOLO 出 PickPose；"
                    "可左右交替。"
                ),
                impl=[f"{_code('hmi/pages/shield_pick_page.py')}"],
                refs=[
                    f"{_code('config/default.yaml')} → vision.belt_pick_mock",
                    f"{_code('vision/template_match.py')} detect_belt_shoes_mock / algorithm_module",
                    f"{_code('vision/vision_service.py')} photo_belt_pick Mock 分支",
                ],
                used_by=[
                    "Station1 在 cam1 Mock 时读本页结果",
                    f"「{_L(T.DRY_RUN)}」空跑通常配合本页示教点",
                ],
            ),
        ),
        (
            "dry",
            _L(T.DRY_RUN),
            _io_block(
                purpose=(
                    "无真皮带/压机时联调 Station1～6：一键光电模拟、压机 Mock、槽有无料时序，"
                    "不强制改相机 Mock。"
                ),
                impl=[
                    f"{_code('hmi/pages/dry_run_page.py')}",
                    f"{_code('core/dry_run_shield.py')}",
                ],
                refs=[
                    f"{_code('devices/io_manager.py')} — 光电模拟",
                    f"{_code('devices/press_modbus.py')} — Mock 压合/旋转延时",
                    f"{_code('config/default.yaml')} press.mock_auto_*",
                ],
                used_by=[
                    f"「{_L(T.MONITOR)}」启动空跑程序按钮",
                    "软件验收 / 轨迹空跑",
                ],
            ),
        ),
        (
            "payload",
            _L(T.PAYLOAD),
            _io_block(
                purpose="分别设置上料/下料臂：未抓鞋与抓鞋两套负载质量质心 + 工具 TCP，并可下发到法奥控制器。",
                impl=[f"{_code('hmi/pages/payload_page.py')}"],
                refs=[
                    f"{_code('config/default.yaml')} → robots.*.payloads",
                    f"{_code('devices/robot_fr5.py')} apply_payload / sync_payloads_to_controller",
                    f"{_code('devices/toe_tcp.py')} — 抓取后鞋头 TCP 与工具号切换（流程侧）",
                ],
                used_by=["真机连接后示教器显示的工具坐标；Station2 切鞋头 TCP 依赖工具体系"],
            ),
        ),
        (
            "press",
            _L(T.PRESS_IO),
            _io_block(
                purpose=(
                    "四槽压机：放料口/取料口约定、正序/反序、槽号自算、各槽 Modbus 地址、"
                    "手动压杆/压合/模拟完成。"
                ),
                impl=[f"{_code('hmi/pages/press_io_page.py')}"],
                refs=[
                    f"{_code('devices/press_modbus.py')} — 槽号推进、发令、完成位",
                    f"{_code('press_shoes/press_machine_modbusTCP.py')} — 底层寄存器（可选）",
                    f"{_code('config/default.yaml')} → press / four_slot / slots",
                ],
                used_by=[
                    "Station6 压合+旋转；Station2/5 看槽号与完成条件",
                    f"运行监控槽号控件与本页同源 ctx.press",
                ],
            ),
        ),
        (
            "config",
            _L(T.CONFIG),
            _io_block(
                purpose="改机器人 IP、压机、夹爪 CAN、光电 DI、各设备 use_mock，保存回 default.yaml 并尽量重连。",
                impl=[f"{_code('hmi/pages/config_page.py')}"],
                refs=[
                    f"{_code('core/config_loader.py')} save_config",
                    f"{_code('config/default.yaml')}",
                    "重连：app_context / 各 device 的 reconnect",
                ],
                used_by=["所有真机联调第一步；改完建议重启程序（尤其相机 serial）"],
            ),
        ),
        (
            "alarm",
            _L(T.ALARM),
            _io_block(
                purpose=(
                    "查看本次报警（分页）、复制全文、报警复位；「落盘错误 / 黑匣子 / 运行快照」写入项目 logs/，退出后再开也能查。"
                    "运行快照：生产拍照原图/叠图（文件名含相机与时间）及运送回写；本页第四个页签。"
                    "「打开日志目录」打开整个 logs/；运行快照页另有「打开视觉log文件夹」。"
                ),
                impl=[
                    f"{_code('hmi/pages/alarm_page.py')}",
                    f"{_code('hmi/pages/vision_snap_page.py')} — 运行快照页签",
                    f"{_code('hmi/alarm_dialog.py')} — 弹窗",
                    f"{_code('core/blackbox.py')} — 落盘与黑匣子",
                ],
                refs=[
                    f"{_code('core/alarm.py')} — 队列与弹窗",
                    "logs/app.log、error.log、errors.jsonl、blackbox.jsonl、dumps/、vision_snaps/",
                ],
                used_by=["主窗口定时 pop_popup；运动失败 / 视觉失败写报警"],
            )
            + _h("四个页签")
            + _ul(
                [
                    "<b>本次运行</b>：当前启动后的报警，分页，可复制、报警复位。退出后请看落盘错误 / 黑匣子。",
                    "<b>落盘错误</b>：WARNING/ERROR/报警写入 logs/errors.jsonl，关机后再开也能查。",
                    "<b>黑匣子</b>：故障前后程序轨迹（blackbox.jsonl）；崩溃另有 logs/dumps/。这是轨迹，不是相机照片。",
                    "<b>运行快照</b>：生产拍照原图/叠图（文件名=相机_时间_类型_raw/vis.jpg）及运送回写。"
                    "详见上一章「报警记录 · 运行快照」与手册 §14.4。",
                ]
            ),
        ),
        (
            "stations",
            "工位程序 Station1～6",
            _h("做什么")
            + _p(
                "自动流程拆成 6 个工位文件，由 coordinator 轮询 cycle(ctx)。"
                "每站用 Auto_A 步号推进；发运动用 pulse_cmd 保证只发一次。"
            )
            + _h("实现文件与职责")
            + _ul(
                [
                    f"<b>Station1</b> {_code('stations/station1_belt_photo.py')} — 皮带拍照 → PickPose（调 vision.photo_belt_pick / algo）；记下运行快照 id",
                    f"<b>Station2</b> {_code('stations/station2_robot1.py')} — 上料臂取料+放料（MoveJ/MoveL、夹爪、鞋头对位 {_code('stations/toe_place_assist.py')}）；放料完成回写快照 place",
                    f"<b>Station3</b> {_code('stations/station3_place_slot_photo.py')} — 放料槽拍照（photo_place_slot）；判定结果回写 slot_check",
                    f"<b>Station4</b> {_code('stations/station4_pick_slot_photo.py')} — 取料槽拍照+压杆（photo_pick_slot）；记下下料快照 id",
                    f"<b>Station5</b> {_code('stations/station5_robot2.py')} — 下料臂取槽→皮带放料；记产量；完成回写 unload",
                    f"<b>Station6</b> {_code('stations/station6_press_rotate.py')} — 压合→旋转→推进槽号",
                    f"初始化：{_code('stations/init_sequence.py')}",
                    f"步目录：{_code('stations/step_catalog.py')}（给 HMI 步表用）",
                ]
            )
            + _h("引用")
            + _ul(
                [
                    f"{_code('core/app_context.py')} — robot/gripper/press/vision/pose",
                    f"{_code('core/plc_util.py')} — pulse_cmd / delay / advance_step",
                    f"{_code('devices/robot_fr5.py')} — MoveJ / MoveL",
                ]
            )
            + _h("被谁使用")
            + _ul(
                [
                    f"{_code('core/coordinator.py')} 主扫描",
                    f"「{_L(T.STEP_DEBUG)}」武装/单步同一套 cycle",
                ]
            )
            + _h("Station2 放料路径（Auto_A[20]）")
            + _p("进入点 → 上方 → 接近放料点(工具1) → 切鞋头TCP → 对位 → 压跟 → 张爪 → 上方 → 退出"),
        ),
        (
            "algo",
            "算法接口 algorithm_module",
            _h("做什么")
            + _p(
                "统一视觉算法调用口：图像/配置进 → 结果出。"
                "工位通常经 VisionService 取图后再调；也可 "
                + _code("from algorithm_module import algo")
                + "。"
            )
            + _h("实现文件")
            + _ul(
                [
                    f"{_code('algorithm_module/algorithm_module.py')} — 门面 AlgorithmModule / algo",
                    f"{_code('algorithm_module/production.py')} — 生产检测",
                    f"{_code('algorithm_module/commission.py')} — 投产写配置",
                    f"{_code('algorithm_module/tooling.py')} — 采图训练",
                    f"{_code('algorithm_module/results.py')} — 结果类型",
                    f"细节表：{_code('algorithm_module/readme.md')}",
                ]
            )
            + _h("主要接口 → 谁调用")
            + _ul(
                [
                    f"<b>detect_belt_pick</b> — {_code('vision/vision_service.py')} photo_belt_pick → <b>Station1</b>",
                    f"<b>detect_belt_shoes_mock</b> — Mock 皮带 → Station1 / 「{_L(T.SHIELD_PICK)}」",
                    f"<b>classify_toe_align</b> — guide_place_edge / toe_place_assist → <b>Station2</b>",
                    f"<b>classify_slot_occupied</b> — photo_place_slot / photo_pick_slot → <b>Station3/4</b>",
                    f"<b>measure_rod_offset_mm</b> — photo_pick_slot → <b>Station5</b> 叠加偏移",
                    f"<b>write_intrinsics_from_calib / solve_handeye_and_write / apply_belt_pick</b> — 「{_L(T.VISION)}·采图训练」",
                    f"<b>capture_to_slot / train_cmd / cuda_train_status</b> — 「{_L(T.VISION)}·采图训练」",
                    f"<b>compute_monitor(from_cache)</b> 在 VisionService — 「{_L(T.CAM_MONITOR)}」实时推演（不落运行快照）",
                ]
            )
            + _h("底层实现引用")
            + _ul(
                [
                    f"{_code('vision/legacy_pipeline.py')} — YOLO/手眼旧链路",
                    f"{_code('vision/model_store.py')} — 训练命令与模型挂接",
                    f"{_code('vision/commission_actions.py')} — 写 json",
                ]
            ),
        ),
        (
            "viz_mod",
            "可视化接口 visualize_module",
            _h("做什么")
            + _p("监控窗专用：画 ROI/标注、后台四路取流、缓存帧推演循环、Qt 画面控件。不替代算法。")
            + _h("实现与引用")
            + _ul(
                [
                    f"{_code('visualize_module/visualize_module.py')} — VisualizeModule / viz",
                    f"{_code('visualize_module/frames.py')} — annotate_bgr、draw_roi（{_code('vision/monitor_frames.py')} 可转调）",
                    f"{_code('visualize_module/live_grabber.py')} — 原图取流",
                    f"{_code('visualize_module/live_compute.py')} — 实时推演",
                    f"{_code('visualize_module/qt_views.py')} — CamPane",
                ]
            )
            + _h("被谁使用")
            + _ul([f"仅「{_L(T.CAM_MONITOR)}」窗口（vision_monitor_page）主用；关窗 stop 取流与推演"]),
        ),
        (
            "gripper",
            "夹爪（达妙 DM-J4310-2EC）",
            _h("型号与对应")
            + _ul(
                [
                    "达妙 DM-J4310-2EC（48V），CAN 1 Mbps，位置速度模式，gripper_type=2",
                    "最多 99 路：grippers.motor_count + motors；HMI 通信配置选数量后填地址",
                    "上料绑定 load_index → ctx.gripper1；下料绑定 unload_index → ctx.gripper2",
                    "其余启用电机：ctx.grippers[序号]",
                ]
            )
            + _h("现场改什么")
            + _ul(
                [
                    f"「{_L(T.CONFIG)}」：启用数量、每路 CAN/can_id、上料/下料绑定 → 保存；改数量后重启",
                    f"「{_L(T.GRIPPER)}」：单独调试开合、速度、重连、掉落检测、报警复位",
                    f"开合速度也可在「{_L(T.MONITOR)}」保存到 yaml",
                    "接线、试夹：docs/夹爪使用说明.md",
                ]
            )
            + _h("报警代码 GRIP_*")
            + _ul(
                [
                    "GRIP_LINK — CAN 连接/使能失败",
                    "GRIP_OPEN / GRIP_CLOSE — 张开/夹紧反馈失败",
                    "GRIP_DRV — 驱动/掉落等",
                    f"「{_L(T.MONITOR)}」或「{_L(T.GRIPPER)}」点「报警复位」会清 GRIP_* 并尝试重连夹爪",
                ]
            )
            + _h("自动流程")
            + _ul(
                [
                    f"Station2 上料：张爪取料 → 夹紧 → 放料对位后张爪（{_code('stations/station2_robot1.py')}）",
                    f"Station5 下料：张爪取槽 → 夹紧 → 皮带放料张爪（{_code('stations/station5_robot2.py')}）",
                    "到位用 poll_done() 等反馈，不再固定延时",
                ]
            )
            + _h("程序接口（单文件 devices/gripper_can.py）")
            + _ul(
                [
                    f"{_code('from devices.gripper_can import GripperCAN, create_gripper')} — 统一入口",
                    "工位：open()/close() + poll_done()；HMI：open_claw()/close_claw()",
                    "电机模式：位置速度模式（目标位置 + 速度）；gripper_type=2",
                    "单独测试：python3 -m devices.gripper_can --side 1",
                    "详见 docs/夹爪使用说明.md",
                ]
            )
            + _h("实现文件")
            + _ul(
                [
                    f"{_code('devices/gripper_can.py')} — CAN 驱动 + GripperCAN 接口（全部逻辑在此文件）",
                ]
            ),
        ),
        (
            "devices",
            "设备层（无单独标签，供接口索引）",
            _h("做什么")
            + _p("封装法奥臂、夹爪 CAN、压机 Modbus、光电 IO。HMI 与工位都经 app_context 使用。")
            + _ul(
                [
                    f"{_code('devices/robot_fr5.py')} — MoveJ/MoveL、SetSpeed、StopMotion；被 Station2/5、点位页、视觉试抓调用",
                    f"{_code('devices/gripper_can.py')} — 达妙夹爪（单文件 CAN+接口）；Station2/5、运行监控",
                    f"{_code('devices/press_modbus.py')} — 压合旋转槽号；Station6、压机信号页、运行监控",
                    f"{_code('devices/io_manager.py')} — 光电/急停 DI；Station1、空跑",
                    f"{_code('devices/toe_tcp.py')} — 鞋头 TCP；Station2 放料段",
                    f"{_code('devices/pose_utils.py')} — 位姿工具；多处",
                ]
            ),
        ),
        (
            "mock_auto",
            "Mock 走通 / 接真机清单",
            _h("Mock 快速走通")
            + _ol(
                [
                    f"打开软件 →「{_L(T.MONITOR)}」模式自动 → 初始化 → READY。",
                    f"「{_L(T.DRY_RUN)}」一键启用（或监控页启动空跑）。",
                    f"「{_L(T.SHIELD_PICK)}」确认左右鞋示教。",
                    "启动；观察 S1→S2→S3→…→S6（先压合再旋转）。",
                ]
            )
            + _h("接真机前")
            + _ul(
                [
                    f"「{_L(T.CONFIG)}」：要接的设备 use_mock=false，IP/CAN/DI 正确。",
                    f"「{_L(T.VISION)}」四路出图 + 模型；采图训练页签本机手眼已写。",
                    f"「{_L(T.POINTS)}」关节已存；「{_L(T.STEP_DEBUG)}」单站验证无干涉。",
                    "再自动空跑 → 带鞋试产。",
                ]
            ),
        ),
    ]


def _sections_zh_tw() -> List[Section]:
    """繁中暂与简体同源（导航名仍随语言）。"""
    return _sections_zh_cn()


def _sections_en_us() -> List[Section]:
    from hmi.help_content_en import build_sections_en

    return build_sections_en()


def all_text_for_search() -> str:
    return "\n".join(f"{t}\n{html}" for _i, t, html in sections())
