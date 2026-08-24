from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, delete, func, insert, select, update

from shelfsight_api.data_model import append_annotation_revision
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    sku_reference_images,
    skus,
)


class SkuNotFoundError(ValueError):
    """Raised when a catalog identity does not exist."""


class InvalidSkuStateError(ValueError):
    """Raised when a catalog lifecycle transition is invalid."""


def create_sku(
    connection: Connection,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    sku_id = connection.execute(
        insert(skus).values(**_clean_fields(values)).returning(skus.c.id)
    ).scalar_one()
    return get_sku(connection, sku_id)


def get_sku(connection: Connection, sku_id: UUID) -> dict[str, Any]:
    row = connection.execute(_sku_query().where(skus.c.id == sku_id)).mappings().one_or_none()
    if row is None:
        raise SkuNotFoundError("SKU does not exist")
    return _with_references(connection, dict(row))


def list_skus(
    connection: Connection,
    *,
    query: str | None = None,
    status: str | None = "active",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    if status is not None:
        conditions.append(skus.c.status == status)
    terms = _search_terms(query)
    if terms:
        document = func.lower(
            func.concat_ws(
                " ",
                skus.c.name,
                skus.c.upc,
                skus.c.category,
                skus.c.subcategory,
                skus.c.brand,
                skus.c.variant,
            )
        )
        conditions.extend(document.contains(term) for term in terms)

    filtered = select(skus.c.id)
    if conditions:
        filtered = filtered.where(and_(*conditions))
    total = connection.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()

    statement = _sku_query()
    if conditions:
        statement = statement.where(and_(*conditions))
    rows = [
        dict(row)
        for row in connection.execute(
            statement.order_by(
                skus.c.is_unknown.desc(),
                func.lower(skus.c.name),
                skus.c.id,
            )
            .limit(limit)
            .offset(offset)
        ).mappings()
    ]
    return _with_reference_groups(connection, rows), total


def update_sku(
    connection: Connection,
    sku_id: UUID,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    current = _locked_sku(connection, sku_id)
    _require_editable(current)
    connection.execute(
        update(skus)
        .where(skus.c.id == sku_id)
        .values(**_clean_fields(values), updated_at=func.now())
    )
    return get_sku(connection, sku_id)


def deprecate_sku(connection: Connection, sku_id: UUID) -> dict[str, Any]:
    current = _locked_sku(connection, sku_id)
    _require_editable(current)
    connection.execute(
        update(skus).where(skus.c.id == sku_id).values(status="deprecated", updated_at=func.now())
    )
    return get_sku(connection, sku_id)


def merge_skus(
    connection: Connection,
    source_id: UUID,
    target_id: UUID,
    actor: str,
) -> tuple[dict[str, Any], int]:
    if source_id == target_id:
        raise InvalidSkuStateError("source and target SKU must differ")
    rows = {
        row["id"]: dict(row)
        for row in connection.execute(
            select(skus).where(skus.c.id.in_((source_id, target_id))).with_for_update()
        ).mappings()
    }
    source = rows.get(source_id)
    target = rows.get(target_id)
    if source is None or target is None:
        raise SkuNotFoundError("source or target SKU does not exist")
    if source["is_unknown"] or target["is_unknown"]:
        raise InvalidSkuStateError("unknown SKU cannot participate in a merge")
    if source["status"] == "merged":
        raise InvalidSkuStateError("source SKU is already merged")
    if target["status"] != "active":
        raise InvalidSkuStateError("merge target must be active")

    repointed = _repoint_current_annotations(
        connection,
        source_id,
        target_id,
        actor,
    )
    _move_reference_images(connection, source_id, target_id)
    connection.execute(
        update(skus)
        .where(skus.c.id == source_id)
        .values(
            status="merged",
            merged_into_id=target_id,
            updated_at=func.now(),
        )
    )
    return get_sku(connection, source_id), repointed


def _repoint_current_annotations(
    connection: Connection,
    source_id: UUID,
    target_id: UUID,
    actor: str,
) -> int:
    current_rows = connection.execute(
        select(annotation_records.c.id, annotation_records.c.current_revision, annotation_revisions)
        .select_from(
            annotation_records.join(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision == annotation_records.c.current_revision,
                ),
            )
        )
        .where(annotation_revisions.c.sku_id == source_id)
        .with_for_update(of=annotation_records)
    ).mappings()
    count = 0
    for row in current_rows:
        values = {
            key: row[key]
            for key in (
                "x",
                "y",
                "width",
                "height",
                "class_type",
                "lifecycle_state",
                "review_state",
                "confidence",
                "occluded",
                "truncated",
                "shelf_row",
            )
        }
        values.update(
            sku_id=target_id,
            source="human",
            provenance={
                "actor": actor,
                "action": "sku_merge",
                "via": "api",
                "merged_from_sku_id": str(source_id),
                "merged_into_sku_id": str(target_id),
            },
        )
        append_annotation_revision(
            connection,
            row["id"],
            row["current_revision"],
            values,
        )
        count += 1
    return count


def _move_reference_images(
    connection: Connection,
    source_id: UUID,
    target_id: UUID,
) -> None:
    target_hashes = select(sku_reference_images.c.content_sha256).where(
        sku_reference_images.c.sku_id == target_id
    )
    connection.execute(
        delete(sku_reference_images).where(
            sku_reference_images.c.sku_id == source_id,
            sku_reference_images.c.content_sha256.in_(target_hashes),
        )
    )
    connection.execute(
        update(sku_reference_images)
        .where(sku_reference_images.c.sku_id == source_id)
        .values(sku_id=target_id)
    )


def _locked_sku(connection: Connection, sku_id: UUID) -> dict[str, Any]:
    row = (
        connection.execute(select(skus).where(skus.c.id == sku_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise SkuNotFoundError("SKU does not exist")
    return dict(row)


def _require_editable(row: Mapping[str, Any]) -> None:
    if row["is_unknown"]:
        raise InvalidSkuStateError("unknown SKU cannot be changed")
    if row["status"] != "active":
        raise InvalidSkuStateError("only active SKUs can be changed")


def _clean_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {"name": str(values["name"]).strip()}
    for key in ("upc", "category", "subcategory", "brand", "variant"):
        value = values.get(key)
        cleaned[key] = str(value).strip() if value is not None else None
        if cleaned[key] == "":
            cleaned[key] = None
    return cleaned


def _search_terms(query: str | None) -> list[str]:
    if not query:
        return []
    return [term for term in query.strip().lower().split() if term]


def _sku_query() -> Any:
    return select(
        skus.c.id,
        skus.c.name,
        skus.c.upc,
        skus.c.category,
        skus.c.subcategory,
        skus.c.brand,
        skus.c.variant,
        skus.c.is_unknown,
        skus.c.status,
        skus.c.merged_into_id,
        skus.c.created_at,
        skus.c.updated_at,
    )


def _with_references(
    connection: Connection,
    row: dict[str, Any],
) -> dict[str, Any]:
    return _with_reference_groups(connection, [row])[0]


def _with_reference_groups(
    connection: Connection,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    references: dict[UUID, list[dict[str, Any]]] = {}
    for reference in connection.execute(
        select(
            sku_reference_images.c.id,
            sku_reference_images.c.sku_id,
            sku_reference_images.c.original_filename,
            sku_reference_images.c.media_type,
            sku_reference_images.c.content_sha256,
            sku_reference_images.c.width,
            sku_reference_images.c.height,
            sku_reference_images.c.created_at,
        )
        .where(sku_reference_images.c.sku_id.in_([row["id"] for row in rows]))
        .order_by(sku_reference_images.c.created_at, sku_reference_images.c.id)
    ).mappings():
        references.setdefault(reference["sku_id"], []).append(dict(reference))
    for row in rows:
        row["reference_images"] = references.get(row["id"], [])
    return rows
