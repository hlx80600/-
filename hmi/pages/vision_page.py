"""兼容旧 import：VisionPage 现为 VisionWorkspace 别名；导航请用 VisionHubPage。"""

from __future__ import annotations

from hmi.pages.vision_workspace import PreviewLabel, VisionWorkspace

VisionPage = VisionWorkspace

__all__ = ["PreviewLabel", "VisionPage", "VisionWorkspace"]
