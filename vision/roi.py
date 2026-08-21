"""感兴趣区域 ROI：可在 HMI 修改，存 JSON。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROI_DIR = Path(__file__).resolve().parents[1] / "config" / "roi"
ROI_DIR.mkdir(parents=True, exist_ok=True)


def roi_path(camera_id: str) -> Path:
    return ROI_DIR / f"{camera_id}.json"


def save_roi(camera_id: str, x: int, y: int, w: int, h: int) -> Path:
    path = roi_path(camera_id)
    path.write_text(
        json.dumps({"x": int(x), "y": int(y), "w": int(w), "h": int(h)}, indent=2),
        encoding="utf-8",
    )
    return path


def load_roi(camera_id: str) -> Optional[Dict[str, int]]:
    path = roi_path(camera_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "x": int(data.get("x", 0)),
        "y": int(data.get("y", 0)),
        "w": int(data.get("w", 0)),
        "h": int(data.get("h", 0)),
    }


def crop_roi(image: Any, roi: Optional[Dict[str, int]]) -> Any:
    if not roi or image is None:
        return image
    x, y, w, h = int(roi["x"]), int(roi["y"]), int(roi["w"]), int(roi["h"])
    return image[y : y + h, x : x + w]


def delete_roi(camera_id: str) -> bool:
    path = roi_path(camera_id)
    if not path.exists():
        return False
    path.unlink()
    return True
