import math
from pathlib import Path
from typing import Any


def segmentation_label_path(image_path: Path) -> Path:
    if image_path.parent.name != "images":
        raise ValueError("YOLO image must be inside an images directory")
    return (image_path.parent.parent / "labels" / image_path.name).with_suffix(".txt")


def read_segmentation_boxes(
    path: Path, width: int, height: int, label: str = "product"
) -> list[dict[str, Any]]:
    boxes = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split()
        try:
            coordinates = [float(value) for value in fields[1:]]
        except ValueError as error:
            raise ValueError(f"line {line_number} has invalid coordinates") from error
        if len(coordinates) < 6 or len(coordinates) % 2:
            raise ValueError(f"line {line_number} is not a segmentation polygon")
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError(f"line {line_number} has non-finite coordinates")

        x_values = coordinates[0::2]
        y_values = coordinates[1::2]
        left = min(max(min(x_values) * width, 0.0), float(width))
        top = min(max(min(y_values) * height, 0.0), float(height))
        right = min(max(max(x_values) * width, 0.0), float(width))
        bottom = min(max(max(y_values) * height, 0.0), float(height))
        if right <= left or bottom <= top:
            raise ValueError(f"line {line_number} has an empty polygon")
        boxes.append(
            {
                "label": label,
                "bbox": [left, top, right - left, bottom - top],
            }
        )
    return boxes
