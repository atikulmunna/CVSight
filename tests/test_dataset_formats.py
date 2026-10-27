from __future__ import annotations

import csv
import io
import json
import struct

import pytest
import yaml

from shelfsight_api.dataset_formats import ExportBox, ExportImage, write_dataset

CLASSES = ["Dove Pink", 'Lux "Rose"', "unknown"]
TRAIN = ExportImage(
    file_name="a.jpg",
    split="train",
    width=200,
    height=100,
    image_format="jpeg",
    data=b"jpeg-bytes-a",
    boxes=(ExportBox(0, 10, 20, 40, 30), ExportBox(1, 100, 50, 50, 50)),
)
VALID = ExportImage(
    file_name="b.png",
    split="valid",
    width=100,
    height=100,
    image_format="png",
    data=b"png-bytes-b",
    boxes=(ExportBox(2, 0, 0, 100, 100),),
)
EMPTY_TRAIN = ExportImage("c.jpg", "train", 50, 50, "jpeg", b"jpeg-bytes-c", ())
IMAGES = [TRAIN, VALID, EMPTY_TRAIN]
# A triangle outline with its bounds as the box, next to a box without an outline.
OUTLINED = ExportImage(
    file_name="d.jpg",
    split="train",
    width=200,
    height=100,
    image_format="jpeg",
    data=b"jpeg-bytes-d",
    boxes=(
        ExportBox(0, 10, 10, 30, 40, polygon=((10, 10), (40, 10), (25, 50))),
        ExportBox(2, 100, 20, 50, 30),
    ),
)


def test_coco_writes_one_annotation_file_per_split_with_sequential_ids() -> None:
    entries = write_dataset("coco", CLASSES, IMAGES)

    train = json.loads(entries["train/_annotations.coco.json"])
    assert [category["name"] for category in train["categories"]] == CLASSES
    assert [image["file_name"] for image in train["images"]] == ["a.jpg", "c.jpg"]
    assert [a["id"] for a in train["annotations"]] == [1, 2]
    assert train["annotations"][1] == {
        "id": 2, "image_id": 1, "category_id": 2,
        "bbox": [100, 50, 50, 50], "segmentation": [], "area": 2500, "iscrowd": 0,
    }
    assert entries["train/a.jpg"] == b"jpeg-bytes-a"
    assert entries["valid/b.png"] == b"png-bytes-b"
    assert "test/_annotations.coco.json" not in entries


def test_yolo_writes_normalized_centers_and_lists_only_present_splits() -> None:
    entries = write_dataset("yolo", CLASSES, IMAGES)

    assert entries["labels/train/a.txt"] == (
        b"0 0.150000 0.350000 0.200000 0.300000\n1 0.625000 0.750000 0.250000 0.500000\n"
    )
    assert entries["labels/train/c.txt"] == b""
    assert entries["images/val/b.png"] == b"png-bytes-b"
    data = yaml.safe_load(entries["data.yaml"])
    assert data == {
        "path": ".", "train": "images/train", "val": "images/val", "nc": 3, "names": CLASSES,
    }


def test_coco_carries_an_outline_as_the_segmentation_and_its_area() -> None:
    entries = write_dataset("coco", CLASSES, [OUTLINED])

    outlined, boxed = json.loads(entries["train/_annotations.coco.json"])["annotations"]
    assert outlined["segmentation"] == [[10, 10, 40, 10, 25, 50]]
    assert outlined["bbox"] == [10, 10, 30, 40]
    assert outlined["area"] == 600
    assert boxed["segmentation"] == []
    assert boxed["area"] == 1500


def test_yolo_segmentation_writes_outlines_and_boxes_as_polygons() -> None:
    entries = write_dataset("yolo-seg", CLASSES, [OUTLINED, VALID])

    assert entries["labels/train/d.txt"] == (
        b"0 0.050000 0.100000 0.200000 0.100000 0.125000 0.500000\n"
        b"2 0.500000 0.200000 0.750000 0.200000 0.750000 0.500000 0.500000 0.500000\n"
    )
    assert entries["images/train/d.jpg"] == b"jpeg-bytes-d"
    assert yaml.safe_load(entries["data.yaml"]) == {
        "path": ".", "train": "images/train", "val": "images/val", "nc": 3, "names": CLASSES,
    }


def test_csv_lists_one_row_per_box_in_pixel_corners() -> None:
    entries = write_dataset("csv", CLASSES, IMAGES)

    rows = list(csv.reader(io.StringIO(entries["train/_annotations.csv"].decode())))
    assert rows == [
        ["filename", "width", "height", "class", "xmin", "ymin", "xmax", "ymax"],
        ["a.jpg", "200", "100", "Dove Pink", "10", "20", "50", "50"],
        ["a.jpg", "200", "100", 'Lux "Rose"', "100", "50", "150", "100"],
    ]


def test_createml_places_boxes_by_their_center() -> None:
    entries = write_dataset("createml", CLASSES, IMAGES)

    train = json.loads(entries["train/_annotations.createml.json"])
    assert train[0]["annotations"][0] == {
        "label": "Dove Pink",
        "coordinates": {"x": 30, "y": 35, "width": 40, "height": 30},
    }
    assert train[1] == {"image": "c.jpg", "annotations": []}


def test_tfrecord_writes_framed_examples_and_an_escaped_label_map() -> None:
    entries = write_dataset("tfrecord", CLASSES, IMAGES)

    label_map = entries["label_map.pbtxt"].decode()
    assert 'item {\n  id: 2\n  name: "Lux \\"Rose\\""\n}' in label_map
    records = _records(entries["train/train.tfrecord"])
    assert len(records) == 2
    assert b"jpeg-bytes-a" in records[0]
    assert b"image/object/bbox/xmin" in records[0]
    assert len(_records(entries["valid/valid.tfrecord"])) == 1


def test_unknown_formats_are_not_written() -> None:
    with pytest.raises(KeyError):
        write_dataset("voc", CLASSES, IMAGES)


def _records(data: bytes) -> list[bytes]:
    records, offset = [], 0
    while offset < len(data):
        (length,) = struct.unpack_from("<Q", data, offset)
        records.append(data[offset + 12 : offset + 12 + length])
        offset += 12 + length + 4
    return records
