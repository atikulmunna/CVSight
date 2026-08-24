from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, select, update

from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
    append_annotation_revision,
    create_annotation,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    images,
    skus,
)


class AnnotationImageNotFoundError(ValueError):
    """Raised when an annotation image does not exist."""


class InvalidAnnotationGeometryError(ValueError):
    """Raised when a box is outside canonical image bounds."""


class InvalidAnnotationStateError(ValueError):
    """Raised when an annotation lifecycle transition is invalid."""


class InvalidAnnotationSkuError(ValueError):
    """Raised when an annotation references an unavailable SKU."""


def accept_image_annotation(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    actor: str,
) -> dict[str, Any]:
    current = get_current_annotation(connection, annotation_id)
    _require_expected_revision(current, expected_revision)
    if current["lifecycle_state"] == "rejected":
        raise InvalidAnnotationStateError("rejected annotation must be restored before accepting")
    if (
        current["lifecycle_state"] == "verified"
        and current["review_state"] == "accepted"
    ):
        raise InvalidAnnotationStateError("annotation is already accepted")

    revision_values = _copy_revision_values(current)
    revision_values["lifecycle_state"] = "verified"
    revision_values["review_state"] = "accepted"
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(actor, "accept")
    append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    _mark_image_in_progress(connection, current["image_id"])
    return get_current_annotation(connection, annotation_id)


def assign_annotation_sku(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    sku_id: UUID,
    actor: str,
) -> dict[str, Any]:
    current = get_current_annotation(connection, annotation_id)
    _require_expected_revision(current, expected_revision)
    if (
        current["lifecycle_state"] != "verified"
        or current["review_state"] != "accepted"
    ):
        raise InvalidAnnotationStateError(
            "annotation must be accepted before assigning a SKU"
        )
    if current["class_type"] != "product":
        raise InvalidAnnotationSkuError("only product annotations may reference a SKU")
    _require_active_sku(connection, sku_id)

    revision_values = _copy_revision_values(current)
    revision_values["sku_id"] = sku_id
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(
        actor,
        "assign_sku",
        assigned_sku_id=str(sku_id),
        previous_revision=expected_revision,
        previous_sku_id=(
            str(current["sku_id"]) if current["sku_id"] is not None else None
        ),
    )
    append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    _mark_image_in_progress(connection, current["image_id"])
    return get_current_annotation(connection, annotation_id)


def list_current_annotations(
    connection: Connection,
    image_id: UUID,
) -> list[dict[str, Any]]:
    if connection.execute(select(images.c.id).where(images.c.id == image_id)).first() is None:
        raise AnnotationImageNotFoundError("image does not exist")
    return [
        dict(row)
        for row in connection.execute(
            _current_annotation_query()
            .where(annotation_records.c.image_id == image_id)
            .order_by(annotation_records.c.created_at, annotation_records.c.id)
        ).mappings()
    ]


def get_current_annotation(
    connection: Connection,
    annotation_id: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        _current_annotation_query().where(annotation_records.c.id == annotation_id)
    ).mappings().one_or_none()
    if row is None:
        raise AnnotationNotFoundError("annotation does not exist")
    return dict(row)


def create_image_annotation(
    connection: Connection,
    image_id: UUID,
    values: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    _validate_values(connection, image_id, values)
    revision_values = dict(values)
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(actor, "create")
    annotation_id = create_annotation(connection, image_id, revision_values)
    _mark_image_in_progress(connection, image_id)
    return get_current_annotation(connection, annotation_id)


def update_image_annotation(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    values: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    current = get_current_annotation(connection, annotation_id)
    _require_expected_revision(current, expected_revision)
    if current["lifecycle_state"] == "rejected":
        raise InvalidAnnotationStateError("rejected annotation must be restored before editing")
    _validate_values(connection, current["image_id"], values)

    revision_values = dict(values)
    revision_values["lifecycle_state"] = current["lifecycle_state"]
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(actor, "update")
    append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    _mark_image_in_progress(connection, current["image_id"])
    return get_current_annotation(connection, annotation_id)


def reject_image_annotation(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    actor: str,
    reason: str | None = None,
) -> dict[str, Any]:
    current = get_current_annotation(connection, annotation_id)
    _require_expected_revision(current, expected_revision)
    if current["lifecycle_state"] == "rejected":
        raise InvalidAnnotationStateError("annotation is already rejected")

    revision_values = _copy_revision_values(current)
    revision_values["lifecycle_state"] = "rejected"
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(actor, "reject", reason=reason)
    append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    _mark_image_in_progress(connection, current["image_id"])
    return get_current_annotation(connection, annotation_id)


def restore_image_annotation(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    actor: str,
) -> dict[str, Any]:
    current = get_current_annotation(connection, annotation_id)
    _require_expected_revision(current, expected_revision)
    if current["lifecycle_state"] != "rejected":
        raise InvalidAnnotationStateError("only a rejected annotation can be restored")

    previous = connection.execute(
        select(annotation_revisions)
        .where(
            annotation_revisions.c.annotation_id == annotation_id,
            annotation_revisions.c.revision < expected_revision,
            annotation_revisions.c.lifecycle_state != "rejected",
        )
        .order_by(annotation_revisions.c.revision.desc())
        .limit(1)
    ).mappings().one_or_none()
    if previous is None:
        raise InvalidAnnotationStateError("annotation has no restorable revision")

    revision_values = _copy_revision_values(dict(previous))
    revision_values["source"] = "human"
    revision_values["provenance"] = _provenance(
        actor,
        "restore",
        restored_from_revision=previous["revision"],
    )
    append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    _mark_image_in_progress(connection, current["image_id"])
    return get_current_annotation(connection, annotation_id)


def _mark_image_in_progress(connection: Connection, image_id: UUID) -> None:
    connection.execute(
        update(images)
        .where(images.c.id == image_id, images.c.status != "in_progress")
        .values(status="in_progress")
    )


def _current_annotation_query() -> Any:
    return select(
        annotation_records.c.id,
        annotation_records.c.image_id,
        annotation_records.c.current_revision.label("revision"),
        annotation_records.c.created_at,
        annotation_records.c.updated_at,
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
        annotation_revisions.c.created_at.label("revision_created_at"),
    ).select_from(
        annotation_records.join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
    )


def _validate_values(
    connection: Connection,
    image_id: UUID,
    values: Mapping[str, Any],
) -> None:
    image_size = connection.execute(
        select(images.c.canonical_width, images.c.canonical_height).where(
            images.c.id == image_id
        )
    ).one_or_none()
    if image_size is None:
        raise AnnotationImageNotFoundError("image does not exist")

    x = float(values["x"])
    y = float(values["y"])
    width = float(values["width"])
    height = float(values["height"])
    if (
        not all(math.isfinite(value) for value in (x, y, width, height))
        or x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > image_size.canonical_width
        or y + height > image_size.canonical_height
    ):
        raise InvalidAnnotationGeometryError(
            "box must fit inside canonical image bounds"
        )

    sku_id = values.get("sku_id")
    if values["class_type"] != "product" and sku_id is not None:
        raise InvalidAnnotationSkuError("only product annotations may reference a SKU")
    if sku_id is None:
        return
    _require_active_sku(connection, sku_id)


def _require_active_sku(connection: Connection, sku_id: UUID) -> None:
    sku_status = connection.execute(
        select(skus.c.status).where(skus.c.id == sku_id)
    ).scalar_one_or_none()
    if sku_status != "active":
        raise InvalidAnnotationSkuError("SKU does not exist or is not active")


def _require_expected_revision(
    current: Mapping[str, Any],
    expected_revision: int,
) -> None:
    if current["revision"] != expected_revision:
        raise StaleAnnotationRevisionError("annotation revision is stale")


def _copy_revision_values(values: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "x",
        "y",
        "width",
        "height",
        "class_type",
        "sku_id",
        "lifecycle_state",
        "review_state",
        "source",
        "provenance",
        "confidence",
        "occluded",
        "truncated",
        "shelf_row",
    )
    return {key: values[key] for key in keys}


def _provenance(actor: str, action: str, **details: Any) -> dict[str, Any]:
    return {
        "actor": actor,
        "action": action,
        "via": "api",
        **{key: value for key, value in details.items() if value is not None},
    }
