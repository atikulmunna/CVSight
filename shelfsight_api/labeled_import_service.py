"""Bring a labeled dataset into an open dataset version: images plus imported boxes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.annotation_service import import_image_annotations
from shelfsight_api.image_ingest import (
    DatasetVersionFrozenError,
    DatasetVersionNotFoundError,
    DuplicateImageError,
    ingest_prepared_image,
)
from shelfsight_api.labeled_import import LabeledDataset, LabeledImage, LabeledImportError
from shelfsight_api.media import (
    MediaValidationError,
    prepare_image,
    read_limited_file,
    resolve_import_path,
    validate_capture_metadata,
)
from shelfsight_api.models import annotation_records, dataset_versions, skus

MIN_BOX_PIXELS = 1.0


def require_open_version(
    connection: Connection, dataset_id: UUID, dataset_version_id: UUID
) -> None:
    version = connection.execute(
        select(dataset_versions.c.snapshot_at).where(
            dataset_versions.c.id == dataset_version_id,
            dataset_versions.c.dataset_id == dataset_id,
        )
    ).one_or_none()
    if version is None:
        raise DatasetVersionNotFoundError("dataset version does not exist")
    if version.snapshot_at is not None:
        raise DatasetVersionFrozenError("dataset version is already snapshotted")


def class_summary(dataset: LabeledDataset) -> list[dict[str, Any]]:
    boxes = Counter(box.class_name for image in dataset.images for box in image.boxes)
    images = Counter(
        name for image in dataset.images for name in {box.class_name for box in image.boxes}
    )
    return [
        {"name": name, "boxes": boxes[name], "images": images[name]} for name in dataset.class_names
    ]


def suggest_skus(connection: Connection, names: tuple[str, ...]) -> dict[str, UUID | None]:
    """An active catalog SKU whose name matches a class exactly, when exactly one does."""
    matches: dict[str, list[UUID]] = {name: [] for name in names}
    for name, sku_id in connection.execute(
        select(skus.c.name, skus.c.id).where(skus.c.name.in_(names), skus.c.status == "active")
    ):
        matches[name].append(sku_id)
    return {name: ids[0] if len(ids) == 1 else None for name, ids in matches.items()}


def require_mapping(
    connection: Connection,
    dataset: LabeledDataset,
    class_skus: Mapping[str, UUID | None],
) -> None:
    unmapped = [name for name in dataset.class_names if name not in class_skus]
    if unmapped:
        raise LabeledImportError(
            "unmapped_classes",
            "map every class to a SKU or to none: " + ", ".join(unmapped[:20]),
        )
    wanted = {sku_id for sku_id in class_skus.values() if sku_id is not None}
    active = set(
        connection.execute(
            select(skus.c.id).where(skus.c.id.in_(wanted), skus.c.status == "active")
        ).scalars()
    )
    if wanted - active:
        raise LabeledImportError("inactive_sku", "every mapped SKU must be active")


def import_labeled_images(
    engine: Engine,
    media_root: Path,
    import_root: Path,
    dataset_id: UUID,
    dataset_version_id: UUID,
    images: list[LabeledImage],
    class_skus: Mapping[str, UUID | None],
    *,
    verified: bool,
    actor: str,
    dataset_format: str,
) -> list[dict[str, Any]]:
    """Import each image and its boxes; rerunning the same images changes nothing."""
    results = []
    for image in images:
        try:
            results.append(
                _import_one(
                    engine,
                    media_root,
                    import_root,
                    dataset_id,
                    dataset_version_id,
                    image,
                    class_skus,
                    verified=verified,
                    actor=actor,
                    dataset_format=dataset_format,
                )
            )
        except MediaValidationError as error:
            results.append(
                _outcome(image, "rejected", str(getattr(error, "code", "invalid_image")))
            )
        except (OSError, SQLAlchemyError):
            results.append(_outcome(image, "rejected", "service_unavailable"))
    return results


def _import_one(
    engine: Engine,
    media_root: Path,
    import_root: Path,
    dataset_id: UUID,
    dataset_version_id: UUID,
    image: LabeledImage,
    class_skus: Mapping[str, UUID | None],
    *,
    verified: bool,
    actor: str,
    dataset_format: str,
) -> dict[str, Any]:
    source = resolve_import_path(import_root, image.path)
    metadata = {"source_split": image.source_split} if image.source_split else {}
    prepared = prepare_image(
        read_limited_file(source), source.name, validate_capture_metadata(metadata)
    )
    orientation = prepared.capture_metadata.get("ingest", {}).get("exif_orientation")
    # Labels drawn on the stored pixels no longer line up once EXIF rotation is applied,
    # and the source does not say which it meant, so such images are refused.
    if image.boxes and orientation not in (None, 1):
        return _outcome(image, "rejected", "rotated_image_labels")

    try:
        image_id = ingest_prepared_image(
            engine, media_root, dataset_id, dataset_version_id, prepared
        ).id
        outcome = "imported"
    except DuplicateImageError as error:
        image_id = error.image_id
        outcome = "completed"

    boxes = _pixel_boxes(image, prepared.canonical_width, prepared.canonical_height, class_skus)
    with engine.begin() as connection:
        existing = connection.execute(
            select(func.count())
            .select_from(annotation_records)
            .where(annotation_records.c.image_id == image_id)
        ).scalar_one()
        if outcome == "completed" and existing:
            return _outcome(image, "skipped", "already_labeled", image_id)
        written = import_image_annotations(
            connection,
            image_id,
            boxes,
            verified=verified,
            actor=actor,
            details={"format": dataset_format, "source": image.path},
        )
    return _outcome(image, outcome, "image_imported", image_id, written)


def _pixel_boxes(
    image: LabeledImage,
    width: int,
    height: int,
    class_skus: Mapping[str, UUID | None],
) -> list[dict[str, Any]]:
    boxes = []
    for box in image.boxes:
        x = round(box.left * width, 2)
        y = round(box.top * height, 2)
        box_width = min(round(box.right * width, 2), width) - x
        box_height = min(round(box.bottom * height, 2), height) - y
        if box_width < MIN_BOX_PIXELS or box_height < MIN_BOX_PIXELS:
            continue
        boxes.append(
            {
                "x": x,
                "y": y,
                "width": box_width,
                "height": box_height,
                "sku_id": class_skus.get(box.class_name),
            }
        )
    return boxes


def _outcome(
    image: LabeledImage,
    outcome: str,
    code: str,
    image_id: UUID | None = None,
    boxes: int = 0,
) -> dict[str, Any]:
    return {
        "path": image.path,
        "outcome": outcome,
        "code": code,
        "image_id": image_id,
        "boxes": boxes,
    }
