from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.media import PreparedImage, remove_media_keys, write_media_bundle
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_version_images,
    dataset_versions,
    images,
)

logger = logging.getLogger(__name__)


class DatasetVersionNotFoundError(ValueError):
    """Raised when an ingest target version does not exist."""


class DatasetVersionFrozenError(ValueError):
    """Raised when an ingest target version is immutable."""


class DuplicateImageError(ValueError):
    """Raised when a dataset already contains the exact upload."""

    def __init__(self, image_id: UUID) -> None:
        super().__init__("dataset already contains this exact image")
        self.image_id = image_id


class ImageNotFoundError(ValueError):
    """Raised when an image identity does not exist."""


class ImageReviewBlockedError(ValueError):
    """Raised when an image still has unresolved active annotations."""


@dataclass(frozen=True)
class IngestedImage:
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    prepared: PreparedImage


def ingest_prepared_image(
    engine: Engine,
    media_root: Path,
    dataset_id: UUID,
    dataset_version_id: UUID,
    prepared: PreparedImage,
) -> IngestedImage:
    image_id = uuid4()
    created_media_keys: tuple[str, ...] = ()
    try:
        with engine.begin() as connection:
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

            existing_image_id = connection.execute(
                select(images.c.id).where(
                    images.c.dataset_id == dataset_id,
                    images.c.content_sha256 == prepared.content_sha256,
                )
            ).scalar_one_or_none()
            if existing_image_id is not None:
                raise DuplicateImageError(existing_image_id)

            connection.execute(
                insert(images).values(
                    id=image_id,
                    dataset_id=dataset_id,
                    original_media_key=prepared.original_media_key,
                    canonical_media_key=prepared.canonical_media_key,
                    thumbnail_media_key=prepared.thumbnail_media_key,
                    media_type=prepared.media_type,
                    original_filename=prepared.original_filename,
                    content_sha256=prepared.content_sha256,
                    canonical_width=prepared.canonical_width,
                    canonical_height=prepared.canonical_height,
                    capture_metadata=prepared.capture_metadata,
                )
            )
            connection.execute(
                insert(dataset_version_images).values(
                    dataset_id=dataset_id,
                    dataset_version_id=dataset_version_id,
                    image_id=image_id,
                )
            )
            created_media_keys = write_media_bundle(media_root, prepared)
    except IntegrityError as error:
        if _constraint_name(error) == "uq_images_dataset_content":
            duplicate_id = _find_duplicate_id(
                engine,
                dataset_id,
                prepared.content_sha256,
            )
            if duplicate_id is not None:
                raise DuplicateImageError(duplicate_id) from error
        _cleanup_if_unreferenced(
            engine,
            media_root,
            prepared.content_sha256,
            created_media_keys,
        )
        raise
    except Exception:
        _cleanup_if_unreferenced(
            engine,
            media_root,
            prepared.content_sha256,
            created_media_keys,
        )
        raise

    return IngestedImage(
        id=image_id,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        prepared=prepared,
    )


def get_image_record(engine: Engine, image_id: UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            select(
                images.c.id,
                images.c.dataset_id,
                images.c.original_media_key,
                images.c.canonical_media_key,
                images.c.thumbnail_media_key,
                images.c.media_type,
                images.c.original_filename,
                images.c.content_sha256,
                images.c.canonical_width,
                images.c.canonical_height,
                images.c.capture_metadata,
                images.c.status,
            ).where(images.c.id == image_id)
        ).mappings().one_or_none()
    if row is None:
        raise ImageNotFoundError("image does not exist")
    return dict(row)


def mark_image_reviewed(engine: Engine, image_id: UUID) -> dict[str, Any]:
    with engine.begin() as connection:
        image = connection.execute(
            select(images.c.id, images.c.status)
            .where(images.c.id == image_id)
            .with_for_update()
        ).mappings().one_or_none()
        if image is None:
            raise ImageNotFoundError("image does not exist")
        unresolved = connection.execute(
            select(annotation_records.c.id)
            .join(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision
                    == annotation_records.c.current_revision,
                ),
            )
            .where(
                annotation_records.c.image_id == image_id,
                annotation_revisions.c.lifecycle_state != "rejected",
                ~and_(
                    annotation_revisions.c.lifecycle_state == "verified",
                    annotation_revisions.c.review_state == "accepted",
                ),
            )
            .limit(1)
        ).first()
        if unresolved is not None:
            raise ImageReviewBlockedError(
                "accept or reject every active annotation before reviewing the image"
            )
        if image["status"] != "reviewed":
            connection.execute(
                update(images).where(images.c.id == image_id).values(status="reviewed")
            )
    return {"id": image_id, "status": "reviewed"}


def media_variant(record: dict[str, Any], variant: str) -> tuple[str, str]:
    if variant == "original":
        return str(record["original_media_key"]), str(record["media_type"])
    if variant == "canonical":
        return str(record["canonical_media_key"]), str(record["media_type"])
    if variant == "thumbnail":
        return str(record["thumbnail_media_key"]), "image/jpeg"
    raise ValueError("unsupported media variant")


def _find_duplicate_id(
    engine: Engine,
    dataset_id: UUID,
    content_sha256: str,
) -> UUID | None:
    with engine.connect() as connection:
        return connection.execute(
            select(images.c.id).where(
                images.c.dataset_id == dataset_id,
                images.c.content_sha256 == content_sha256,
            )
        ).scalar_one_or_none()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return str(value) if value is not None else None


def _cleanup_if_unreferenced(
    engine: Engine,
    media_root: Path,
    content_sha256: str,
    created_media_keys: tuple[str, ...],
) -> None:
    if not created_media_keys:
        return
    try:
        with engine.connect() as connection:
            is_referenced = connection.execute(
                select(images.c.id)
                .where(images.c.content_sha256 == content_sha256)
                .limit(1)
            ).first()
    except SQLAlchemyError:
        logger.warning("Skipped media cleanup because the reference check failed")
        return
    if is_referenced is None:
        remove_media_keys(media_root, created_media_keys)
