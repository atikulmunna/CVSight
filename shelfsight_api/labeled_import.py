"""Read labeled datasets (YOLO folders and COCO JSON) from the operator import root.

Boxes are kept normalized to the source image (0 to 1) until the image is ingested, so
both formats share one representation. YOLO polygons become their bounding boxes. The
source dataset's train, validation, or test folder is reported as a source split only;
it never becomes CVSight's split, because random source splits can leak near-duplicates.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from shelfsight_api.media import resolve_import_location

LabeledFormat = Literal["yolo", "coco"]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SPLIT_NAMES = {
    "train": "train",
    "valid": "validation",
    "val": "validation",
    "validation": "validation",
    "test": "test",
}
MAX_LABELED_IMAGES = 50_000


class LabeledImportError(ValueError):
    """Raised when a labeled dataset cannot be read; code is stable for API clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LabeledBox:
    class_name: str
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class LabeledImage:
    path: str
    source_split: str | None
    boxes: tuple[LabeledBox, ...]


@dataclass(frozen=True)
class LabeledDataset:
    format: LabeledFormat
    class_names: tuple[str, ...]
    images: tuple[LabeledImage, ...]
    skipped_labels: int


def read_labeled_dataset(
    import_root: Path, dataset_format: LabeledFormat, path: str
) -> LabeledDataset:
    root = import_root.resolve()
    source = resolve_import_location(root, path)
    if dataset_format == "yolo":
        if not source.is_dir():
            raise LabeledImportError("dataset_not_found", "a YOLO dataset path must be a folder")
        return _read_yolo(root, source)
    if not source.is_file() or source.suffix.lower() != ".json":
        raise LabeledImportError("dataset_not_found", "a COCO dataset path must be a .json file")
    return _read_coco(root, source)


def _read_yolo(root: Path, folder: Path) -> LabeledDataset:
    config = folder / "data.yaml"
    if not config.is_file():
        raise LabeledImportError("missing_data_yaml", "a YOLO dataset needs data.yaml at its root")
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise LabeledImportError("invalid_data_yaml", "data.yaml could not be parsed") from error
    names = _class_names(data.get("names") if isinstance(data, dict) else None)

    images: list[LabeledImage] = []
    skipped = 0
    for image_path in sorted(folder.rglob("*")):
        if image_path.suffix.lower() not in IMAGE_SUFFIXES or not image_path.is_file():
            continue
        if not image_path.resolve().is_relative_to(root):
            continue
        directories = image_path.relative_to(folder).parts[:-1]
        if "images" not in directories:
            continue
        # Roboflow writes split/images/x.jpg and Ultralytics images/split/x.jpg; in both,
        # the label sits where the last "images" folder is swapped for "labels".
        swap = len(directories) - 1 - directories[::-1].index("images")
        label_parts = [*directories[:swap], "labels", *directories[swap + 1 :], image_path.name]
        label_path = folder.joinpath(*label_parts).with_suffix(".txt")
        boxes, bad = _yolo_boxes(label_path, names) if label_path.is_file() else ((), 0)
        skipped += bad
        images.append(
            LabeledImage(
                path=image_path.relative_to(root).as_posix(),
                source_split=_split_of(directories),
                boxes=boxes,
            )
        )
        _require_within_limit(len(images))
    return LabeledDataset("yolo", names, tuple(images), skipped)


def _class_names(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict) and all(isinstance(key, int) for key in value):
        if sorted(value) != list(range(len(value))):
            raise LabeledImportError(
                "invalid_class_names", "class indexes must run from 0 without gaps"
            )
        value = [value[index] for index in range(len(value))]
    if not isinstance(value, list) or not value:
        raise LabeledImportError(
            "invalid_class_names",
            "data.yaml names must be a list or a mapping from class index to name",
        )
    names = tuple(str(name).strip() for name in value)
    if any(not name for name in names) or len(set(names)) != len(names):
        raise LabeledImportError("invalid_class_names", "class names must be unique and non-empty")
    return names


def _yolo_boxes(label_path: Path, names: tuple[str, ...]) -> tuple[tuple[LabeledBox, ...], int]:
    boxes: list[LabeledBox] = []
    skipped = 0
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            class_index = int(parts[0])
            coordinates = [float(value) for value in parts[1:]]
        except ValueError:
            skipped += 1
            continue
        if not 0 <= class_index < len(names):
            skipped += 1
            continue
        if len(coordinates) == 4:
            center_x, center_y, width, height = coordinates
            edges = (
                center_x - width / 2,
                center_y - height / 2,
                center_x + width / 2,
                center_y + height / 2,
            )
        elif len(coordinates) >= 6 and len(coordinates) % 2 == 0:
            xs, ys = coordinates[0::2], coordinates[1::2]
            edges = (min(xs), min(ys), max(xs), max(ys))
        else:
            skipped += 1
            continue
        box = _normalized_box(names[class_index], *edges)
        if box is None:
            skipped += 1
        else:
            boxes.append(box)
    return tuple(boxes), skipped


def _read_coco(root: Path, json_path: Path) -> LabeledDataset:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        categories = {int(item["id"]): str(item["name"]).strip() for item in data["categories"]}
        coco_images = {int(item["id"]): item for item in data["images"]}
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise LabeledImportError("invalid_coco", "the COCO file could not be read") from error
    if not categories or any(not name for name in categories.values()):
        raise LabeledImportError("invalid_class_names", "COCO categories need non-empty names")

    boxes_by_image: dict[int, list[LabeledBox]] = defaultdict(list)
    skipped = 0
    for annotation in data.get("annotations", []):
        box = _coco_box(annotation, coco_images, categories)
        if box is None:
            skipped += 1
        else:
            boxes_by_image[int(annotation["image_id"])].append(box)

    split = _split_of(json_path.relative_to(root).parts[:-1])
    images: list[LabeledImage] = []
    for image_id, image in sorted(
        coco_images.items(), key=lambda item: str(item[1].get("file_name"))
    ):
        file_path = (json_path.parent / str(image.get("file_name", ""))).resolve()
        if not file_path.is_file() or not file_path.is_relative_to(root):
            raise LabeledImportError(
                "image_not_found", f"COCO image {image.get('file_name')} is missing"
            )
        images.append(
            LabeledImage(
                path=file_path.relative_to(root).as_posix(),
                source_split=split,
                boxes=tuple(boxes_by_image.get(image_id, [])),
            )
        )
        _require_within_limit(len(images))
    ordered_names = tuple(dict.fromkeys(categories[key] for key in sorted(categories)))
    return LabeledDataset("coco", ordered_names, tuple(images), skipped)


def _coco_box(
    annotation: Any,
    coco_images: dict[int, Any],
    categories: dict[int, str],
) -> LabeledBox | None:
    try:
        if annotation.get("iscrowd"):
            return None
        image = coco_images[int(annotation["image_id"])]
        name = categories[int(annotation["category_id"])]
        x, y, width, height = (float(value) for value in annotation["bbox"])
        image_width, image_height = float(image["width"]), float(image["height"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if image_width <= 0 or image_height <= 0:
        return None
    return _normalized_box(
        name,
        x / image_width,
        y / image_height,
        (x + width) / image_width,
        (y + height) / image_height,
    )


def _normalized_box(
    name: str, left: float, top: float, right: float, bottom: float
) -> LabeledBox | None:
    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(1.0, right), min(1.0, bottom)
    if right <= left or bottom <= top:
        return None
    return LabeledBox(name, left, top, right, bottom)


def _split_of(directories: tuple[str, ...]) -> str | None:
    return next(
        (SPLIT_NAMES[part.lower()] for part in directories if part.lower() in SPLIT_NAMES), None
    )


def _require_within_limit(count: int) -> None:
    if count > MAX_LABELED_IMAGES:
        raise LabeledImportError(
            "too_many_images", f"import at most {MAX_LABELED_IMAGES} images at once"
        )
