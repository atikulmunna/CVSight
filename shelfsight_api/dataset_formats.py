"""Write a prepared, split dataset in the layouts that common training tools read.

COCO, CSV, CreateML, and TFRecord use the Roboflow-style layout of one folder per split;
YOLO uses the Ultralytics images and labels folders with data.yaml.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from shelfsight_api.export_split import SPLITS
from shelfsight_api.tfrecord import (
    bytes_feature,
    example_bytes,
    float_feature,
    int64_feature,
    tfrecord_bytes,
)

DATASET_FORMATS = ("coco", "yolo", "csv", "createml", "tfrecord")
YOLO_FOLDERS = {"train": "train", "valid": "val", "test": "test"}


@dataclass(frozen=True)
class ExportBox:
    class_index: int
    # Pixels, from the image's top-left corner.
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ExportImage:
    file_name: str
    split: str
    width: int
    height: int
    image_format: str
    data: bytes
    boxes: tuple[ExportBox, ...]


Writer = Callable[[Sequence[str], Sequence[ExportImage]], dict[str, bytes]]


def write_dataset(
    dataset_format: str,
    classes: Sequence[str],
    images: Sequence[ExportImage],
) -> dict[str, bytes]:
    writers: dict[str, Writer] = {
        "coco": _coco,
        "yolo": _yolo,
        "csv": _csv,
        "createml": _createml,
        "tfrecord": _tfrecord,
    }
    return writers[dataset_format](classes, images)


def _coco(classes: Sequence[str], images: Sequence[ExportImage]) -> dict[str, bytes]:
    entries = _split_images(images)
    for split, split_images in _by_split(images):
        coco_images: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        for number, image in enumerate(split_images, start=1):
            coco_images.append(
                {"id": number, "file_name": image.file_name,
                 "width": image.width, "height": image.height}
            )
            for box in image.boxes:
                annotations.append(
                    {
                        "id": len(annotations) + 1,
                        "image_id": number,
                        "category_id": box.class_index + 1,
                        "bbox": [
                            _round(box.x), _round(box.y), _round(box.width), _round(box.height)
                        ],
                        "area": _round(box.width * box.height),
                        "iscrowd": 0,
                    }
                )
        entries[f"{split}/_annotations.coco.json"] = _json(
            {
                "categories": [
                    {"id": index + 1, "name": name, "supercategory": "shelf"}
                    for index, name in enumerate(classes)
                ],
                "images": coco_images,
                "annotations": annotations,
            }
        )
    return entries


def _yolo(classes: Sequence[str], images: Sequence[ExportImage]) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    present = set()
    for image in images:
        folder = YOLO_FOLDERS[image.split]
        present.add(folder)
        entries[f"images/{folder}/{image.file_name}"] = image.data
        entries[f"labels/{folder}/{PurePosixPath(image.file_name).stem}.txt"] = "".join(
            f"{box.class_index} {(box.x + box.width / 2) / image.width:.6f} "
            f"{(box.y + box.height / 2) / image.height:.6f} "
            f"{box.width / image.width:.6f} {box.height / image.height:.6f}\n"
            for box in image.boxes
        ).encode("utf-8")
    data = {
        "path": ".",
        **{folder: f"images/{folder}" for folder in ("train", "val", "test") if folder in present},
        "nc": len(classes),
        "names": list(classes),
    }
    entries["data.yaml"] = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()
    return entries


def _csv(classes: Sequence[str], images: Sequence[ExportImage]) -> dict[str, bytes]:
    entries = _split_images(images)
    for split, split_images in _by_split(images):
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["filename", "width", "height", "class", "xmin", "ymin", "xmax", "ymax"])
        for image in split_images:
            for box in image.boxes:
                writer.writerow(
                    [image.file_name, image.width, image.height, classes[box.class_index],
                     round(box.x), round(box.y),
                     round(box.x + box.width), round(box.y + box.height)]
                )
        entries[f"{split}/_annotations.csv"] = output.getvalue().encode("utf-8")
    return entries


def _createml(classes: Sequence[str], images: Sequence[ExportImage]) -> dict[str, bytes]:
    entries = _split_images(images)
    for split, split_images in _by_split(images):
        entries[f"{split}/_annotations.createml.json"] = _json(
            [
                {
                    "image": image.file_name,
                    # CreateML places each box by its center, in pixels.
                    "annotations": [
                        {
                            "label": classes[box.class_index],
                            "coordinates": {
                                "x": _round(box.x + box.width / 2),
                                "y": _round(box.y + box.height / 2),
                                "width": _round(box.width),
                                "height": _round(box.height),
                            },
                        }
                        for box in image.boxes
                    ],
                }
                for image in split_images
            ]
        )
    return entries


def _tfrecord(classes: Sequence[str], images: Sequence[ExportImage]) -> dict[str, bytes]:
    entries = {"label_map.pbtxt": _label_map(classes)}
    for split, split_images in _by_split(images):
        entries[f"{split}/{split}.tfrecord"] = tfrecord_bytes(
            _tf_example(image, classes) for image in split_images
        )
    return entries


def _tf_example(image: ExportImage, classes: Sequence[str]) -> bytes:
    """The feature names the TensorFlow Object Detection API reads."""
    boxes = image.boxes
    return example_bytes(
        {
            "image/height": int64_feature([image.height]),
            "image/width": int64_feature([image.width]),
            "image/filename": bytes_feature([image.file_name.encode("utf-8")]),
            "image/source_id": bytes_feature([image.file_name.encode("utf-8")]),
            "image/encoded": bytes_feature([image.data]),
            "image/format": bytes_feature([image.image_format.encode("utf-8")]),
            "image/object/bbox/xmin": float_feature([b.x / image.width for b in boxes]),
            "image/object/bbox/xmax": float_feature([(b.x + b.width) / image.width for b in boxes]),
            "image/object/bbox/ymin": float_feature([b.y / image.height for b in boxes]),
            "image/object/bbox/ymax": float_feature(
                [(b.y + b.height) / image.height for b in boxes]
            ),
            "image/object/class/text": bytes_feature(
                [classes[b.class_index].encode("utf-8") for b in boxes]
            ),
            "image/object/class/label": int64_feature([b.class_index + 1 for b in boxes]),
        }
    )


def _label_map(classes: Sequence[str]) -> bytes:
    items = []
    for index, name in enumerate(classes, start=1):
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'item {{\n  id: {index}\n  name: "{escaped}"\n}}\n')
    return "".join(items).encode("utf-8")


def _split_images(images: Sequence[ExportImage]) -> dict[str, bytes]:
    return {f"{image.split}/{image.file_name}": image.data for image in images}


def _by_split(images: Sequence[ExportImage]) -> list[tuple[str, list[ExportImage]]]:
    grouped = [(split, [image for image in images if image.split == split]) for split in SPLITS]
    return [(split, split_images) for split, split_images in grouped if split_images]


def _round(value: float) -> float:
    return round(value, 3)


def _json(value: Any) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
