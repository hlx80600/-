# 可视化模块（本工程）

风格对齐「机器人自动化框架」的 `visualize_module`：惰性加载、业务页只调门面。

## 结构

| 文件 | 作用 |
|------|------|
| `visualize_module.py` | 门面 `visualizeModule` / 单例 `viz` |
| `frames.py` | 画 ROI、ASCII 标注、`CAM_IDS` |
| `live_grabber.py` | 监控窗后台四路取流（避让视觉调试） |
| `live_compute.py` | 缓存帧实时推演（不抢 grab，忙则跳过） |
| `qt_views.py` | `FrameView` / `CamPane` |

## 用法

```python
from visualize_module import viz

frames = viz.activate_frames()
qt = viz.activate_qt_views()
grabber = viz.activate_live_grabber(ctx.vision, ctx.cameras, skip_cam_fn=...)
compute = viz.activate_live_compute(ctx.vision, on_done=...)
grabber.start()
compute.start()
# 关窗时
grabber.stop()
compute.stop()
```

算法侧若只需标注，可继续：

```python
from visualize_module.frames import annotate_bgr, copy_bgr
# 或兼容旧路径：
from vision.monitor_frames import annotate_bgr
```

## 需求不变

- 四路原图 + 计算结果
- 「刷新原图」才后台取流；「实时推演」用缓存帧算；关窗停止
- 「结果跟原图」：右侧跟拍叠加上次结果文字，不因 YOLO 慢而卡原图
- 与「视觉调试」同开时不闪「未出图」（避让当前调试相机）
