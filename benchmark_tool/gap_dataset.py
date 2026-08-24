from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import TypedDict, cast

from PIL import Image

SOURCE_PATTERN = re.compile(
    r"^(?P<family>.+?)_(?P<date>20\d{6})_(?P<time>\d{6})_"
    r"(?P<source>[0-9a-f]+)_jpg\.rf\.(?P<roboflow>[0-9a-f]+)$"
)
RETAKE_SUFFIX = re.compile(r"_retake_\d+$")


class SourceIdentity(TypedDict):
    family: str
    captured_at: datetime
    source_id: str
    roboflow_id: str


class EstimatedRow(TypedDict):
    center_y: float
    height: float
    boxes: list[Sequence[float]]


class GapCandidate(TypedDict):
    bbox: list[float]
    shelf_row_id: int
    score: float


@dataclass(frozen=True)
class GapDatasetRecord:
    image_path: Path
    label_path: Path
    family: str
    captured_at: datetime
    source_id: str
    content_sha256: str
    difference_hash: int
    width: int
    height: int
    annotation_count: int
    gap_score: float
    capture_group: str = ""
    split: str = ""


def audit_gap_dataset(dataset_root: Path) -> list[GapDatasetRecord]:
    image_root = dataset_root / "train" / "images"
    label_root = dataset_root / "train" / "labels"
    if not image_root.is_dir() or not label_root.is_dir():
        raise ValueError("dataset must contain train/images and train/labels")
    records = []
    for image_path in sorted(image_root.iterdir()):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        identity = parse_source_identity(image_path.stem)
        label_path = label_root / f"{image_path.stem}.txt"
        if not label_path.is_file():
            raise ValueError(f"missing label file for {image_path.name}")
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            width, height = image.size
            difference_hash = _difference_hash(image)
        boxes = read_normalized_polygon_boxes(label_path)
        records.append(
            GapDatasetRecord(
                image_path=image_path,
                label_path=label_path,
                family=identity["family"],
                captured_at=identity["captured_at"],
                source_id=identity["source_id"],
                content_sha256=_sha256(image_path),
                difference_hash=difference_hash,
                width=width,
                height=height,
                annotation_count=len(boxes),
                gap_score=estimate_interior_gap_score(boxes),
            )
        )
    if not records:
        raise ValueError("dataset contains no supported images")
    return assign_capture_groups(records)


def parse_source_identity(stem: str) -> SourceIdentity:
    match = SOURCE_PATTERN.fullmatch(stem)
    if match is None:
        raise ValueError(f"unsupported source filename: {stem}")
    family = RETAKE_SUFFIX.sub("", match.group("family"))
    captured_at = datetime.strptime(
        match.group("date") + match.group("time"),
        "%Y%m%d%H%M%S",
    )
    return {
        "family": family,
        "captured_at": captured_at,
        "source_id": match.group("source"),
        "roboflow_id": match.group("roboflow"),
    }


def read_normalized_polygon_boxes(label_path: Path) -> list[list[float]]:
    boxes = []
    for line_number, line in enumerate(
        label_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        fields = line.split()
        try:
            class_id = int(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except (IndexError, ValueError) as error:
            raise ValueError(f"line {line_number} is invalid") from error
        if class_id < 0 or len(coordinates) < 6 or len(coordinates) % 2:
            raise ValueError(f"line {line_number} is not a valid polygon")
        if any(value < 0 or value > 1 for value in coordinates):
            raise ValueError(f"line {line_number} leaves normalized image bounds")
        x_values = coordinates[0::2]
        y_values = coordinates[1::2]
        left = min(x_values)
        top = min(y_values)
        right = max(x_values)
        bottom = max(y_values)
        if right <= left or bottom <= top:
            raise ValueError(f"line {line_number} has empty geometry")
        boxes.append([left, top, right - left, bottom - top])
    return boxes


def estimate_interior_gap_score(boxes: Sequence[Sequence[float]]) -> float:
    if len(boxes) < 2:
        return 0.0
    scores: list[float] = []
    for row in _cluster_rows(boxes):
        row_boxes = row["boxes"]
        if len(row_boxes) < 2:
            continue
        intervals = sorted((box[0], box[0] + box[2]) for box in row_boxes)
        merged = _merge_intervals(intervals)
        widths = [box[2] for box in row_boxes]
        typical_width = median(widths)
        if typical_width <= 0:
            continue
        scores.extend(
            max(next_left - right, 0) / typical_width
            for (_, right), (next_left, _) in zip(merged, merged[1:], strict=False)
        )
    return max(scores, default=0.0)


def derive_gap_candidates(
    boxes: Sequence[Sequence[float]],
    *,
    minimum_relative_width: float = 0.45,
) -> list[GapCandidate]:
    """Create review-only gap candidates from normalized source product boxes."""
    if not 0 < minimum_relative_width <= 10:
        raise ValueError("minimum_relative_width must be greater than zero and at most ten")
    normalized = [_normalized_box(box) for box in boxes]
    candidates: list[GapCandidate] = []
    rows = sorted(_cluster_rows(normalized), key=lambda row: row["center_y"])
    for shelf_row_id, row in enumerate(rows):
        row_boxes = row["boxes"]
        if len(row_boxes) < 2:
            continue
        typical_width = median(box[2] for box in row_boxes)
        if typical_width <= 0:
            continue
        typical_height = median(box[3] for box in row_boxes)
        top = max(0.0, row["center_y"] - typical_height / 2)
        bottom = min(1.0, top + typical_height)
        intervals = _merge_intervals(
            sorted((box[0], box[0] + box[2]) for box in row_boxes)
        )
        for (_, right), (next_left, _) in zip(intervals, intervals[1:], strict=False):
            width = next_left - right
            ratio = width / typical_width
            if ratio < minimum_relative_width:
                continue
            candidates.append(
                {
                    "bbox": [right, top, width, bottom - top],
                    "shelf_row_id": shelf_row_id,
                    "score": min(ratio / 3, 1.0),
                }
            )
    return candidates


def assign_capture_groups(
    records: Sequence[GapDatasetRecord],
    *,
    maximum_session_gap_seconds: int = 120,
    maximum_hash_distance: int = 32,
) -> list[GapDatasetRecord]:
    if maximum_session_gap_seconds < 0 or not 0 <= maximum_hash_distance <= 256:
        raise ValueError("capture grouping thresholds are invalid")
    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    by_family_date: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        by_family_date[(record.family, record.captured_at.date().isoformat())].append(index)
    for indices in by_family_date.values():
        ordered = sorted(indices, key=lambda index: records[index].captured_at)
        for first, second in zip(ordered, ordered[1:], strict=False):
            difference = records[second].captured_at - records[first].captured_at
            if difference.total_seconds() <= maximum_session_gap_seconds:
                union(first, second)

    for first in range(len(records)):
        for second in range(first):
            if (
                records[first].difference_hash ^ records[second].difference_hash
            ).bit_count() <= maximum_hash_distance:
                union(first, second)

    members: defaultdict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        members[find(index)].append(index)
    group_names = {
        root: "capture-" + hashlib.sha256(
            "\n".join(sorted(records[index].source_id for index in indices)).encode()
        ).hexdigest()[:16]
        for root, indices in members.items()
    }
    return [
        GapDatasetRecord(
            **{
                **record.__dict__,
                "capture_group": group_names[find(index)],
            }
        )
        for index, record in enumerate(records)
    ]


def select_gap_benchmark(
    records: Sequence[GapDatasetRecord],
    *,
    validation_count: int = 20,
    test_count: int = 30,
) -> list[GapDatasetRecord]:
    total = validation_count + test_count
    if validation_count < 1 or test_count < 1:
        raise ValueError("validation and test counts must be positive")
    by_group: defaultdict[str, list[GapDatasetRecord]] = defaultdict(list)
    for record in records:
        if not record.capture_group:
            raise ValueError("every record requires a capture group")
        by_group[record.capture_group].append(record)
    if len(by_group) < total:
        raise ValueError("not enough independent capture groups for requested benchmark")

    group_representatives = [
        max(
            group,
            key=lambda value: (
                value.gap_score,
                value.annotation_count,
                value.image_path.name,
            ),
        )
        for group in by_group.values()
    ]
    high_count = round(total * 0.7)
    high = sorted(
        group_representatives,
        key=lambda value: (-value.gap_score, value.image_path.name),
    )[:high_count]
    high_groups = {record.capture_group for record in high}
    low_candidates = [
        min(group, key=lambda value: (value.gap_score, value.image_path.name))
        for group_id, group in by_group.items()
        if group_id not in high_groups
    ]
    low = sorted(low_candidates, key=lambda value: (value.gap_score, value.image_path.name))[
        : total - high_count
    ]
    selected = sorted(
        [*high, *low],
        key=lambda value: (-value.gap_score, value.family, value.image_path.name),
    )
    validation_positions = _even_positions(total, validation_count)
    output = []
    for index, record in enumerate(selected):
        split = "validation" if index in validation_positions else "test"
        output.append(GapDatasetRecord(**{**record.__dict__, "split": split}))
    return sorted(output, key=lambda value: (value.split, value.image_path.name))


def _even_positions(total: int, count: int) -> set[int]:
    return {min((index * total) // count, total - 1) for index in range(count)}


def _merge_intervals(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for left, right in intervals:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return [(left, right) for left, right in merged]


def _cluster_rows(boxes: Sequence[Sequence[float]]) -> list[EstimatedRow]:
    rows: list[EstimatedRow] = []
    for box in sorted(boxes, key=lambda value: value[1] + value[3] / 2):
        center_y = box[1] + box[3] / 2
        candidates = [
            row
            for row in rows
            if abs(row["center_y"] - center_y)
            <= max(0.01, min(row["height"], box[3]) * 0.55)
        ]
        if not candidates:
            rows.append({"center_y": center_y, "height": box[3], "boxes": [box]})
            continue
        row = min(candidates, key=lambda value: abs(value["center_y"] - center_y))
        count = len(row["boxes"])
        row["boxes"].append(box)
        row["center_y"] = (row["center_y"] * count + center_y) / (count + 1)
        row["height"] = (row["height"] * count + box[3]) / (count + 1)
    return rows


def _normalized_box(box: Sequence[float]) -> list[float]:
    if len(box) != 4:
        raise ValueError("normalized boxes must contain four values")
    values = [float(value) for value in box]
    left, top, width, height = values
    if (
        not all(math.isfinite(value) for value in values)
        or left < 0
        or top < 0
        or width <= 0
        or height <= 0
        or left + width > 1
        or top + height > 1
    ):
        raise ValueError("normalized boxes must be finite and inside image bounds")
    return values


def _difference_hash(image: Image.Image) -> int:
    grayscale = image.convert("L").resize((17, 16))
    pixels = list(grayscale.get_flattened_data())
    value = 0
    for row in range(16):
        for column in range(16):
            value <<= 1
            left = cast(int, pixels[row * 17 + column])
            right = cast(int, pixels[row * 17 + column + 1])
            value |= int(left > right)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
