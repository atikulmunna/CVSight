from __future__ import annotations

import math


def pixel_crop_bounds(
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[int, int, int, int]:
    return (
        math.floor(x),
        math.floor(y),
        math.ceil(x + width),
        math.ceil(y + height),
    )
