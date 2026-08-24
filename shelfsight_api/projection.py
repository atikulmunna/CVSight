from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from shelfsight_api.data_model import get_dataset_snapshot
from shelfsight_api.media import resolve_media_path

PROJECTION_SCHEMA_VERSION = "cvsight-fiftyone-projection/v1"


class ProjectionBoundaryError(ValueError):
    """Raised when related images cross an evaluation boundary."""


class ProjectionMediaError(ValueError):
    """Raised when immutable snapshot media cannot be projected."""


@dataclass(frozen=True)
class ProjectionDetection:
    annotation_id: UUID
    annotation_revision: int
    label: str
    bounding_box: tuple[float, float, float, float]
    sku_id: UUID | None
    sku_name: str | None
    lifecycle_state: str
    review_state: str
    source: str
    confidence: float | None
    occluded: bool
    truncated: bool
    shelf_row: int | None
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ProjectionSample:
    image_id: UUID
    filepath: Path
    content_sha256: str
    width: int
    height: int
    status: str
    split: str | None
    near_duplicate_group: str | None
    capture_session_id: str | None
    store_id: str | None
    fixture_id: str | None
    detections: tuple[ProjectionDetection, ...]
    product_crops: tuple[ProjectionDetection, ...]


@dataclass(frozen=True)
class ProjectionManifest:
    dataset_id: UUID
    dataset_version_id: UUID
    source_schema_version: str
    source_content_sha256: str
    projection_schema_version: str
    samples: tuple[ProjectionSample, ...]
    artifacts: tuple[dict[str, Any], ...]


def build_projection_manifest(
    connection: Connection,
    media_root: Path,
    dataset_version_id: UUID,
) -> ProjectionManifest:
    snapshot = get_dataset_snapshot(connection, dataset_version_id)
    sku_names = {row["sku_id"]: str(row["name"]) for row in snapshot["skus"]}
    annotations_by_image: dict[UUID, list[dict[str, Any]]] = {}
    for annotation in snapshot["annotations"]:
        annotations_by_image.setdefault(annotation["image_id"], []).append(annotation)

    samples = tuple(
        _projection_sample(
            media_root,
            image,
            annotations_by_image.get(image["image_id"], []),
            sku_names,
        )
        for image in snapshot["images"]
    )
    _validate_evaluation_boundaries(samples)
    return ProjectionManifest(
        dataset_id=snapshot["dataset_id"],
        dataset_version_id=snapshot["dataset_version_id"],
        source_schema_version=str(snapshot["schema_version"]),
        source_content_sha256=str(snapshot["content_sha256"]),
        projection_schema_version=PROJECTION_SCHEMA_VERSION,
        samples=samples,
        artifacts=tuple(snapshot["artifacts"]),
    )


def _projection_sample(
    media_root: Path,
    image: dict[str, Any],
    annotation_rows: list[dict[str, Any]],
    sku_names: dict[UUID, str],
) -> ProjectionSample:
    filepath = resolve_media_path(media_root, str(image["canonical_media_key"]))
    if not filepath.is_file():
        raise ProjectionMediaError("canonical image is missing")
    width = int(image["canonical_width"])
    height = int(image["canonical_height"])
    detections = tuple(
        _projection_detection(annotation, width, height, sku_names)
        for annotation in annotation_rows
    )
    provided = _provided_capture_metadata(image.get("capture_metadata"))
    return ProjectionSample(
        image_id=image["image_id"],
        filepath=filepath,
        content_sha256=str(image["content_sha256"]),
        width=width,
        height=height,
        status=str(image["status"]),
        split=_metadata_string(provided, "split"),
        near_duplicate_group=_metadata_string(provided, "near_duplicate_group"),
        capture_session_id=_metadata_string(provided, "capture_session_id"),
        store_id=_metadata_string(provided, "store_id"),
        fixture_id=_metadata_string(provided, "fixture_id"),
        detections=detections,
        product_crops=tuple(
            detection
            for detection in detections
            if detection.label == "product"
            and detection.lifecycle_state == "verified"
            and detection.review_state == "accepted"
        ),
    )


def _projection_detection(
    annotation: dict[str, Any],
    image_width: int,
    image_height: int,
    sku_names: dict[UUID, str],
) -> ProjectionDetection:
    sku_id = annotation["sku_id"]
    return ProjectionDetection(
        annotation_id=annotation["annotation_id"],
        annotation_revision=int(annotation["annotation_revision"]),
        label=str(annotation["class_type"]),
        bounding_box=(
            float(annotation["x"]) / image_width,
            float(annotation["y"]) / image_height,
            float(annotation["width"]) / image_width,
            float(annotation["height"]) / image_height,
        ),
        sku_id=sku_id,
        sku_name=sku_names.get(sku_id),
        lifecycle_state=str(annotation["lifecycle_state"]),
        review_state=str(annotation["review_state"]),
        source=str(annotation["source"]),
        confidence=(
            float(annotation["confidence"])
            if annotation["confidence"] is not None
            else None
        ),
        occluded=bool(annotation["occluded"]),
        truncated=bool(annotation["truncated"]),
        shelf_row=(
            int(annotation["shelf_row"])
            if annotation["shelf_row"] is not None
            else None
        ),
        provenance=dict(annotation["provenance"]),
    )


def _provided_capture_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    provided = value.get("provided")
    return provided if isinstance(provided, dict) else value


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _validate_evaluation_boundaries(samples: tuple[ProjectionSample, ...]) -> None:
    _validate_group_splits(
        ((sample.content_sha256, sample.split) for sample in samples),
        "exact duplicate",
    )
    _validate_group_splits(
        (
            (sample.near_duplicate_group, sample.split)
            for sample in samples
            if sample.near_duplicate_group is not None
        ),
        "near-duplicate group",
    )
    _validate_group_splits(
        (
            (sample.capture_session_id, sample.split)
            for sample in samples
            if sample.capture_session_id is not None
        ),
        "capture session",
    )


def _validate_group_splits(
    pairs: Any,
    group_label: str,
) -> None:
    groups: dict[str, set[str]] = {}
    for group, split in pairs:
        if group is not None and split is not None:
            groups.setdefault(group, set()).add(split)
    if any(len(splits) > 1 for splits in groups.values()):
        raise ProjectionBoundaryError(f"{group_label} crosses evaluation splits")
