from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.media import PreparedImage, remove_media_keys, write_media_bundle
from shelfsight_api.models import images, sku_reference_images, skus
from shelfsight_api.sku_service import InvalidSkuStateError, SkuNotFoundError

logger = logging.getLogger(__name__)


class SkuReferenceNotFoundError(ValueError):
    """Raised when a SKU reference image does not exist."""


class DuplicateSkuReferenceError(ValueError):
    """Raised when a SKU already has the same reference image."""


@dataclass(frozen=True)
class IngestedSkuReference:
    id: UUID
    sku_id: UUID
    prepared: PreparedImage


def ingest_sku_reference_image(
    engine: Engine,
    media_root: Path,
    sku_id: UUID,
    prepared: PreparedImage,
) -> IngestedSkuReference:
    reference_id = uuid4()
    created_media_keys: tuple[str, ...] = ()
    try:
        with engine.begin() as connection:
            sku = connection.execute(
                select(skus.c.status, skus.c.is_unknown)
                .where(skus.c.id == sku_id)
                .with_for_update()
            ).one_or_none()
            if sku is None:
                raise SkuNotFoundError("SKU does not exist")
            if sku.status != "active" or sku.is_unknown:
                raise InvalidSkuStateError("reference images require an active non-unknown SKU")
            duplicate = connection.execute(
                select(sku_reference_images.c.id).where(
                    sku_reference_images.c.sku_id == sku_id,
                    sku_reference_images.c.content_sha256 == prepared.content_sha256,
                )
            ).first()
            if duplicate is not None:
                raise DuplicateSkuReferenceError("SKU already has this reference image")
            connection.execute(
                insert(sku_reference_images).values(
                    id=reference_id,
                    sku_id=sku_id,
                    original_media_key=prepared.original_media_key,
                    canonical_media_key=prepared.canonical_media_key,
                    thumbnail_media_key=prepared.thumbnail_media_key,
                    media_type=prepared.media_type,
                    original_filename=prepared.original_filename,
                    content_sha256=prepared.content_sha256,
                    width=prepared.canonical_width,
                    height=prepared.canonical_height,
                )
            )
            created_media_keys = write_media_bundle(media_root, prepared)
    except Exception:
        _cleanup_if_unreferenced(
            engine,
            media_root,
            prepared.content_sha256,
            created_media_keys,
        )
        raise
    return IngestedSkuReference(reference_id, sku_id, prepared)


def get_sku_reference(
    engine: Engine,
    reference_id: UUID,
) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(sku_reference_images).where(sku_reference_images.c.id == reference_id)
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise SkuReferenceNotFoundError("SKU reference image does not exist")
    return dict(row)


def reference_media_variant(
    record: dict[str, Any],
    variant: str,
) -> tuple[str, str]:
    if variant == "original":
        return str(record["original_media_key"]), str(record["media_type"])
    if variant == "canonical":
        return str(record["canonical_media_key"]), str(record["media_type"])
    if variant == "thumbnail":
        return str(record["thumbnail_media_key"]), "image/jpeg"
    raise ValueError("unsupported media variant")


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
            image_reference = connection.execute(
                select(images.c.id).where(images.c.content_sha256 == content_sha256).limit(1)
            ).first()
            sku_reference = connection.execute(
                select(sku_reference_images.c.id)
                .where(sku_reference_images.c.content_sha256 == content_sha256)
                .limit(1)
            ).first()
    except SQLAlchemyError:
        logger.warning("Skipped media cleanup because the reference check failed")
        return
    if image_reference is None and sku_reference is None:
        remove_media_keys(media_root, created_media_keys)
