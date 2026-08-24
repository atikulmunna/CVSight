from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, insert, select, update

from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_snapshot_annotations,
    dataset_snapshot_images,
    dataset_snapshot_sku_references,
    dataset_snapshot_skus,
    dataset_snapshots,
    dataset_version_annotations,
    dataset_version_images,
    dataset_versions,
    images,
    sku_reference_images,
    skus,
    snapshot_artifacts,
)

SNAPSHOT_SCHEMA_VERSION = "shelfsight-dataset-snapshot/v2"


class AnnotationNotFoundError(ValueError):
    """Raised when an annotation identity does not exist."""


class StaleAnnotationRevisionError(ValueError):
    """Raised when an annotation update targets an old revision."""


class DatasetVersionNotFoundError(ValueError):
    """Raised when a dataset version does not exist."""


class DatasetVersionFrozenError(ValueError):
    """Raised when a dataset version is already immutable."""


class DatasetSnapshotNotFoundError(ValueError):
    """Raised when an immutable dataset snapshot does not exist."""


class SnapshotArtifactConflictError(ValueError):
    """Raised when an artifact identity is bound to different lineage."""


def create_annotation(
    connection: Connection,
    image_id: UUID,
    revision_values: Mapping[str, Any],
    *,
    annotation_id: UUID | None = None,
) -> UUID:
    new_annotation_id = annotation_id or uuid4()
    connection.execute(
        insert(annotation_records).values(
            id=new_annotation_id,
            image_id=image_id,
            current_revision=1,
        )
    )
    connection.execute(
        insert(annotation_revisions).values(
            annotation_id=new_annotation_id,
            revision=1,
            **revision_values,
        )
    )
    return new_annotation_id


def append_annotation_revision(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    revision_values: Mapping[str, Any],
) -> int:
    next_revision = expected_revision + 1
    updated_revision = connection.execute(
        update(annotation_records)
        .where(
            annotation_records.c.id == annotation_id,
            annotation_records.c.current_revision == expected_revision,
        )
        .values(current_revision=next_revision, updated_at=func.now())
        .returning(annotation_records.c.current_revision)
    ).scalar_one_or_none()
    if updated_revision is None:
        exists = connection.execute(
            select(annotation_records.c.id).where(annotation_records.c.id == annotation_id)
        ).scalar_one_or_none()
        if exists is None:
            raise AnnotationNotFoundError("annotation does not exist")
        raise StaleAnnotationRevisionError("annotation revision is stale")

    connection.execute(
        insert(annotation_revisions).values(
            annotation_id=annotation_id,
            revision=next_revision,
            **revision_values,
        )
    )
    return next_revision


def snapshot_dataset_version(
    connection: Connection,
    dataset_version_id: UUID,
    *,
    snapshot_at: datetime | None = None,
) -> int:
    version = connection.execute(
        select(
            dataset_versions.c.dataset_id,
            dataset_versions.c.parent_version_id,
            dataset_versions.c.snapshot_at,
        )
        .where(dataset_versions.c.id == dataset_version_id)
        .with_for_update()
    ).one_or_none()
    if version is None:
        raise DatasetVersionNotFoundError("dataset version does not exist")
    if version.snapshot_at is not None:
        raise DatasetVersionFrozenError("dataset version is already snapshotted")

    captured_at = snapshot_at or datetime.now(UTC)
    image_rows = [
        dict(row)
        for row in connection.execute(
            select(
                images.c.id.label("image_id"),
                images.c.canonical_media_key,
                images.c.media_type,
                images.c.original_filename,
                images.c.content_sha256,
                images.c.canonical_width,
                images.c.canonical_height,
                images.c.capture_metadata,
                images.c.status,
            )
            .select_from(
                dataset_version_images.join(
                    images,
                    images.c.id == dataset_version_images.c.image_id,
                )
            )
            .where(dataset_version_images.c.dataset_version_id == dataset_version_id)
            .order_by(images.c.id)
        ).mappings()
    ]
    current_annotations = (
        select(
            dataset_version_images.c.image_id,
            annotation_records.c.id.label("annotation_id"),
            annotation_records.c.current_revision.label("annotation_revision"),
            annotation_revisions.c.x,
            annotation_revisions.c.y,
            annotation_revisions.c.width,
            annotation_revisions.c.height,
            annotation_revisions.c.class_type,
            annotation_revisions.c.sku_id,
            annotation_revisions.c.lifecycle_state,
            annotation_revisions.c.review_state,
            annotation_revisions.c.source,
            annotation_revisions.c.provenance,
            annotation_revisions.c.confidence,
            annotation_revisions.c.occluded,
            annotation_revisions.c.truncated,
            annotation_revisions.c.shelf_row,
        )
        .select_from(
            dataset_version_images.join(
                annotation_records,
                annotation_records.c.image_id == dataset_version_images.c.image_id,
            ).join(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision
                    == annotation_records.c.current_revision,
                ),
            )
        )
        .where(dataset_version_images.c.dataset_version_id == dataset_version_id)
        .order_by(annotation_records.c.id)
    )
    annotation_rows = [
        dict(row) for row in connection.execute(current_annotations).mappings()
    ]
    sku_rows = [
        {
            "sku_id": row["id"],
            "name": row["name"],
            "upc": row["upc"],
            "category": row["category"],
            "subcategory": row["subcategory"],
            "brand": row["brand"],
            "variant": row["variant"],
            "is_unknown": row["is_unknown"],
            "status": row["status"],
            "merged_into_id": row["merged_into_id"],
        }
        for row in connection.execute(select(skus).order_by(skus.c.id)).mappings()
    ]
    reference_rows = [
        {
            "reference_image_id": row["id"],
            "sku_id": row["sku_id"],
            "canonical_media_key": row["canonical_media_key"],
            "media_type": row["media_type"],
            "original_filename": row["original_filename"],
            "content_sha256": row["content_sha256"],
            "width": row["width"],
            "height": row["height"],
        }
        for row in connection.execute(
            select(sku_reference_images).order_by(sku_reference_images.c.id)
        ).mappings()
    ]
    content_sha256 = _snapshot_sha256(
        UUID(str(version.dataset_id)),
        dataset_version_id,
        UUID(str(version.parent_version_id)) if version.parent_version_id else None,
        image_rows,
        annotation_rows,
        sku_rows,
        reference_rows,
    )
    connection.execute(
        insert(dataset_snapshots).values(
            dataset_version_id=dataset_version_id,
            dataset_id=version.dataset_id,
            parent_version_id=version.parent_version_id,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            content_sha256=content_sha256,
            snapshot_at=captured_at,
        )
    )
    if image_rows:
        connection.execute(
            insert(dataset_snapshot_images),
            [dict(row, dataset_version_id=dataset_version_id) for row in image_rows],
        )
    if sku_rows:
        connection.execute(
            insert(dataset_snapshot_skus),
            [dict(row, dataset_version_id=dataset_version_id) for row in sku_rows],
        )
    if reference_rows:
        connection.execute(
            insert(dataset_snapshot_sku_references),
            [dict(row, dataset_version_id=dataset_version_id) for row in reference_rows],
        )
    if annotation_rows:
        connection.execute(
            insert(dataset_snapshot_annotations),
            [dict(row, dataset_version_id=dataset_version_id) for row in annotation_rows],
        )
        connection.execute(
            insert(dataset_version_annotations),
            [
                {
                    "dataset_id": version.dataset_id,
                    "dataset_version_id": dataset_version_id,
                    "image_id": row["image_id"],
                    "annotation_id": row["annotation_id"],
                    "annotation_revision": row["annotation_revision"],
                }
                for row in annotation_rows
            ],
        )
    connection.execute(
        update(dataset_versions)
        .where(
            dataset_versions.c.id == dataset_version_id,
            dataset_versions.c.snapshot_at.is_(None),
        )
        .values(snapshot_at=captured_at)
    )
    return len(annotation_rows)


def get_dataset_snapshot(
    connection: Connection,
    dataset_version_id: UUID,
) -> dict[str, Any]:
    snapshot = connection.execute(
        select(dataset_snapshots).where(
            dataset_snapshots.c.dataset_version_id == dataset_version_id
        )
    ).mappings().one_or_none()
    if snapshot is None:
        raise DatasetSnapshotNotFoundError("dataset snapshot does not exist")
    image_rows = _snapshot_rows(
        connection,
        dataset_snapshot_images,
        dataset_version_id,
        dataset_snapshot_images.c.image_id,
    )
    annotation_rows = _snapshot_rows(
        connection,
        dataset_snapshot_annotations,
        dataset_version_id,
        dataset_snapshot_annotations.c.annotation_id,
    )
    sku_rows = _snapshot_rows(
        connection,
        dataset_snapshot_skus,
        dataset_version_id,
        dataset_snapshot_skus.c.sku_id,
    )
    reference_rows = _snapshot_rows(
        connection,
        dataset_snapshot_sku_references,
        dataset_version_id,
        dataset_snapshot_sku_references.c.reference_image_id,
    )
    artifacts = _snapshot_rows(
        connection,
        snapshot_artifacts,
        dataset_version_id,
        snapshot_artifacts.c.created_at,
    )
    return {
        "dataset_id": snapshot["dataset_id"],
        "dataset_version_id": snapshot["dataset_version_id"],
        "parent_version_id": snapshot["parent_version_id"],
        "schema_version": snapshot["schema_version"],
        "content_sha256": snapshot["content_sha256"],
        "snapshot_at": snapshot["snapshot_at"],
        "images": image_rows,
        "annotations": annotation_rows,
        "skus": sku_rows,
        "sku_references": reference_rows,
        "artifacts": artifacts,
    }


def register_snapshot_artifact(
    connection: Connection,
    dataset_version_id: UUID,
    artifact_type: str,
    artifact_key: str,
    *,
    content_sha256: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if connection.execute(
        select(dataset_snapshots.c.dataset_version_id).where(
            dataset_snapshots.c.dataset_version_id == dataset_version_id
        )
    ).scalar_one_or_none() is None:
        raise DatasetSnapshotNotFoundError("dataset snapshot does not exist")
    existing = connection.execute(
        select(snapshot_artifacts).where(
            snapshot_artifacts.c.artifact_type == artifact_type,
            snapshot_artifacts.c.artifact_key == artifact_key,
        )
    ).mappings().one_or_none()
    values = {
        "dataset_version_id": dataset_version_id,
        "artifact_type": artifact_type,
        "artifact_key": artifact_key,
        "content_sha256": content_sha256,
        "metadata": dict(metadata or {}),
    }
    if existing is not None:
        if any(existing[key] != value for key, value in values.items()):
            raise SnapshotArtifactConflictError(
                "artifact identity is already bound to different lineage"
            )
        return dict(existing)
    return dict(
        connection.execute(
            insert(snapshot_artifacts).values(**values).returning(*snapshot_artifacts.c)
        ).mappings().one()
    )


def _snapshot_rows(
    connection: Connection,
    table: Any,
    dataset_version_id: UUID,
    order_column: Any,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(table)
        .where(table.c.dataset_version_id == dataset_version_id)
        .order_by(order_column)
    ).mappings()
    return [dict(row) for row in rows]


def _snapshot_sha256(
    dataset_id: UUID,
    dataset_version_id: UUID,
    parent_version_id: UUID | None,
    image_rows: list[dict[str, Any]],
    annotation_rows: list[dict[str, Any]],
    sku_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
) -> str:
    document = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "parent_version_id": parent_version_id,
        "images": image_rows,
        "annotations": annotation_rows,
        "skus": sku_rows,
        "sku_references": reference_rows,
    }
    encoded = json.dumps(
        document,
        default=_snapshot_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")
