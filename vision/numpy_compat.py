"""无 numpy 时的极简替代（仅 Mock 冒烟用）。"""

from __future__ import annotations

try:
    import numpy as np  # type: ignore
except ImportError:
    class _NP:
        @staticmethod
        def zeros(shape, dtype=None):
            h, w = shape[0], shape[1]
            c = shape[2] if len(shape) > 2 else 1
            if c == 1:
                return [[0 for _ in range(w)] for _ in range(h)]
            return [[[0, 0, 0] for _ in range(w)] for _ in range(h)]

        @staticmethod
        def mean(arr):
            return 40.0

        @staticmethod
        def frombuffer(data, dtype=None):
            return data

        @staticmethod
        def mgrid(*args):
            return [[0]]

        uint8 = int
        float32 = float

    np = _NP()  # type: ignore

__all__ = ["np"]
