from typing import List, Dict, Tuple, Optional

def get_all_shoe_points(camera: Optional[object] = None):
    """Return detected shoe points in the current view.

    This is a placeholder that should be replaced with real vision logic.
    - `camera` can be a frame from a camera; when None, a simulated result is returned.
    Returns a list of dicts with keys: shoe ("left"|"right"), point (x, y), confidence (0-1).
    """
    # TODO: integrate actual detection model and parsing logic.
    detections: List[Dict[str, object]] = []
    left_shoe_points = []
    right_shoe_points = []
    return left_shoe_points, right_shoe_points
