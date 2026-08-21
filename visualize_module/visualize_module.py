"""可视化模块（莆田项目本地，风格对齐机器人自动化框架 visualize_module）。

职责：
  - 图像标注 / ROI 叠加（算法侧可复用）
  - 相机监控后台取流（与视觉调试抢锁避让）
  - Qt 显示控件（FrameView / CamPane）

不主动加载 Qt / cv2：调用 activate_* 后再导入对应子模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .live_compute import LiveComputeLoop
    from .live_grabber import LiveGrabber


class VisualizeModule:
    """可视化门面：惰性激活，未用到的子能力不加载。"""

    def __init__(self) -> None:
        self.live_grabber: Optional["LiveGrabber"] = None
        self.live_compute: Optional["LiveComputeLoop"] = None
        self._qt_ready = False

    def activate_frames(self) -> Any:
        """加载画框/标注工具（不依赖 Qt）。"""
        from . import frames

        return frames

    def activate_qt_views(self) -> Any:
        """加载 Qt 显示控件。"""
        from . import qt_views

        self._qt_ready = True
        return qt_views

    def activate_live_grabber(
        self,
        vision: Any,
        cameras: Any,
        *,
        skip_cam_fn: Optional[Callable[[], str]] = None,
        cam_ids: Optional[tuple] = None,
    ) -> "LiveGrabber":
        """后台四路取流；窗口关闭时应 stop。"""
        from .live_grabber import LiveGrabber

        if self.live_grabber is not None:
            self.live_grabber.stop()
        self.live_grabber = LiveGrabber(
            vision,
            cameras,
            skip_cam_fn=skip_cam_fn,
            cam_ids=cam_ids,
        )
        return self.live_grabber

    def activate_live_compute(
        self,
        vision: Any,
        *,
        cam_ids: Optional[tuple] = None,
        on_done: Optional[Callable[[str, str], None]] = None,
    ) -> "LiveComputeLoop":
        """后台缓存帧推演；与取流并行，不抢 grab。"""
        from .live_compute import LiveComputeLoop

        if self.live_compute is not None:
            self.live_compute.stop()
        self.live_compute = LiveComputeLoop(
            vision,
            cam_ids=cam_ids,
            on_done=on_done,
        )
        return self.live_compute


# 单例：业务侧可 `from visualize_module import viz`
viz = VisualizeModule()
# 兼容旧类名
visualizeModule = VisualizeModule
