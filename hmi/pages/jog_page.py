"""兼容旧 import：点动已封装为独立示教器窗口。"""

from hmi.pages.jog_pendant import JogPendantPanel as JogPage
from hmi.pages.jog_pendant import JogPendantWindow, open_jog_pendant_from

__all__ = ["JogPage", "JogPendantWindow", "open_jog_pendant_from"]
