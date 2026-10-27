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


def padded_crop_bounds(
    x: float,
    y: float,
    width: float,
    height: float,
    padding: float,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Pixel bounds of a box grown by a share of its own size on every side, kept inside
    the image."""
    pad_x = width * padding
    pad_y = height * padding
    left, top, right, bottom = pixel_crop_bounds(
        x - pad_x,
        y - pad_y,
        width + 2 * pad_x,
        height + 2 * pad_y,
    )
    image_width, image_height = image_size
    return (max(left, 0), max(top, 0), min(right, image_width), min(bottom, image_height))
