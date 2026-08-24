from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark_tool.rfdetr import (
    convert_output,
    non_maximum_suppression,
    slice_regions,
    validate_options,
)


def test_convert_output_clips_boxes_and_preserves_classes() -> None:
    detections = SimpleNamespace(
        xyxy=[[-2, 4, 12, 14], [8, 3, 8, 7]],
        confidence=[0.75, 0.5],
        data={"class_name": ["bottle", "cup"]},
    )

    assert convert_output(detections, width=10, height=10) == [
        {"label": "bottle", "bbox": [0.0, 4.0, 10.0, 6.0], "score": 0.75}
    ]


def test_validate_options_rejects_invalid_inputs(tmp_path: Path) -> None:
    checkpoint = tmp_path / "rf-detr-nano.pth"
    checkpoint.write_bytes(b"checkpoint")

    with pytest.raises(ValueError, match="threshold"):
        validate_options(checkpoint, -0.1)
    with pytest.raises(ValueError, match="NMS IoU"):
        validate_options(checkpoint, 0.5, 0)
    with pytest.raises(ValueError, match="checkpoint"):
        validate_options(tmp_path / "missing.pth", 0.5)


def test_slice_regions_create_two_by_two_grid_with_overlap() -> None:
    assert slice_regions(100, 80) == [
        (0, 0, 55, 44),
        (45, 0, 100, 44),
        (0, 36, 55, 80),
        (45, 36, 100, 80),
    ]


def test_non_maximum_suppression_is_class_aware() -> None:
    predictions = [
        {"label": "bottle", "bbox": [0, 0, 10, 10], "score": 0.9},
        {"label": "bottle", "bbox": [1, 1, 9, 9], "score": 0.8},
        {"label": "cup", "bbox": [1, 1, 9, 9], "score": 0.7},
    ]

    assert non_maximum_suppression(predictions, 0.5) == [
        predictions[0],
        predictions[2],
    ]
