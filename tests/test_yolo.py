from pathlib import Path

import pytest

from benchmark_tool.yolo import read_segmentation_boxes, segmentation_label_path


def test_read_segmentation_boxes_converts_and_clips_polygon(tmp_path: Path) -> None:
    label = tmp_path / "label.txt"
    label.write_text("3 -0.1 0.2 0.5 0.2 1.1 0.8\n", encoding="utf-8")

    assert read_segmentation_boxes(label, width=100, height=50) == [
        {"label": "product", "bbox": [0.0, 10.0, 100.0, 30.0]}
    ]


def test_read_segmentation_boxes_rejects_invalid_polygon(tmp_path: Path) -> None:
    label = tmp_path / "label.txt"
    label.write_text("3 0.1 0.2 0.5 0.2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="segmentation polygon"):
        read_segmentation_boxes(label, width=100, height=50)


def test_segmentation_label_path_requires_yolo_layout(tmp_path: Path) -> None:
    image = tmp_path / "train" / "images" / "sample.jpg"
    expected = tmp_path / "train" / "labels" / "sample.txt"

    assert segmentation_label_path(image) == expected
    with pytest.raises(ValueError, match="images directory"):
        segmentation_label_path(tmp_path / "sample.jpg")
