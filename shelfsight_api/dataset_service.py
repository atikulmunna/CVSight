from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, insert, literal, select
from sqlalchemy.sql.selectable import Subquery

from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_review_signoffs,
    dataset_snapshot_annotations,
    dataset_snapshot_images,
    dataset_snapshot_skus,
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    skus,
    snapshot_artifacts,
)


class DatasetVersionProgressNotFoundError(ValueError):
    """Raised when progress references a missing dataset version."""


class DatasetNotFoundError(ValueError):
    """Raised when version management references a missing dataset."""


class DatasetOpenVersionExistsError(ValueError):
    """Raised when a dataset already has a working version."""


def list_datasets(connection: Connection) -> list[dict[str, Any]]:
    open_versions = dataset_versions.alias("open_versions")
    latest_version_id = (
        select(dataset_versions.c.id)
        .where(dataset_versions.c.dataset_id == datasets.c.id)
        .order_by(dataset_versions.c.created_at.desc(), dataset_versions.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    image_counts = (
        select(
            images.c.dataset_id,
            func.count(images.c.id).label("image_count"),
        )
        .group_by(images.c.dataset_id)
        .subquery()
    )
    rows = connection.execute(
        select(
            datasets.c.id,
            datasets.c.name,
            datasets.c.description,
            datasets.c.created_at,
            open_versions.c.id.label("open_version_id"),
            latest_version_id.label("latest_version_id"),
            func.coalesce(image_counts.c.image_count, 0).label("image_count"),
        )
        .outerjoin(
            open_versions,
            and_(
                open_versions.c.dataset_id == datasets.c.id,
                open_versions.c.snapshot_at.is_(None),
            ),
        )
        .outerjoin(image_counts, image_counts.c.dataset_id == datasets.c.id)
        .order_by(datasets.c.created_at.desc(), datasets.c.name)
    ).mappings()
    return [dict(row) for row in rows]


def create_dataset_with_open_version(
    connection: Connection,
    name: str,
    description: str | None,
    catalog: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    dataset_id = uuid4()
    version_id = uuid4()
    connection.execute(
        insert(datasets).values(
            id=dataset_id,
            name=name,
            description=description,
        )
    )
    connection.execute(
        insert(dataset_versions).values(
            id=version_id,
            dataset_id=dataset_id,
        )
    )
    if catalog:
        connection.execute(insert(skus), catalog)
    return {
        "id": dataset_id,
        "name": name,
        "description": description,
        "open_version_id": version_id,
        "imported_skus": len(catalog or []),
    }


def list_dataset_versions(
    connection: Connection,
    dataset_id: UUID,
) -> list[dict[str, Any]]:
    if connection.execute(
        select(datasets.c.id).where(datasets.c.id == dataset_id)
    ).scalar_one_or_none() is None:
        raise DatasetNotFoundError("dataset does not exist")

    image_counts = (
        select(
            dataset_version_images.c.dataset_version_id,
            func.count(dataset_version_images.c.image_id).label("image_count"),
        )
        .group_by(dataset_version_images.c.dataset_version_id)
        .subquery()
    )
    rows = connection.execute(
        select(
            dataset_versions.c.id,
            dataset_versions.c.dataset_id,
            dataset_versions.c.parent_version_id,
            dataset_versions.c.created_at,
            dataset_versions.c.snapshot_at,
            func.coalesce(image_counts.c.image_count, 0).label("image_count"),
            dataset_review_signoffs.c.signed_by,
            dataset_review_signoffs.c.signed_at,
            dataset_review_signoffs.c.reviewed_annotation_count,
            dataset_review_signoffs.c.risk_item_count,
        )
        .outerjoin(
            image_counts,
            image_counts.c.dataset_version_id == dataset_versions.c.id,
        )
        .outerjoin(
            dataset_review_signoffs,
            dataset_review_signoffs.c.dataset_version_id == dataset_versions.c.id,
        )
        .where(dataset_versions.c.dataset_id == dataset_id)
        .order_by(dataset_versions.c.created_at.desc(), dataset_versions.c.id.desc())
    ).mappings()
    versions = [dict(row) for row in rows]

    export_rows = connection.execute(
        select(snapshot_artifacts.c.dataset_version_id, snapshot_artifacts.c.metadata).where(
            snapshot_artifacts.c.dataset_version_id.in_([version["id"] for version in versions]),
            snapshot_artifacts.c.artifact_type == "export",
        )
    ).mappings()
    exports_by_version: dict[UUID, set[str]] = {}
    for row in export_rows:
        export_type = row["metadata"].get("export_type")
        if export_type in {"detection", "recognition"}:
            exports_by_version.setdefault(row["dataset_version_id"], set()).add(export_type)

    for version in versions:
        if version["snapshot_at"] is None:
            version["status"] = "working"
        elif version["signed_at"] is not None:
            version["status"] = "released"
        else:
            version["status"] = "frozen"
        version["review_signoff"] = (
            {
                "signed_by": version["signed_by"],
                "signed_at": version["signed_at"],
                "reviewed_annotation_count": version["reviewed_annotation_count"],
                "risk_item_count": version["risk_item_count"],
            }
            if version["signed_at"] is not None
            else None
        )
        version["export_types"] = sorted(exports_by_version.get(version["id"], set()))
    return versions


def create_next_dataset_version(
    connection: Connection,
    dataset_id: UUID,
) -> dict[str, Any]:
    if connection.execute(
        select(datasets.c.id).where(datasets.c.id == dataset_id)
    ).scalar_one_or_none() is None:
        raise DatasetNotFoundError("dataset does not exist")

    open_version = connection.execute(
        select(dataset_versions.c.id).where(
            dataset_versions.c.dataset_id == dataset_id,
            dataset_versions.c.snapshot_at.is_(None),
        )
    ).scalar_one_or_none()
    if open_version is not None:
        raise DatasetOpenVersionExistsError("dataset already has a working version")

    parent_version_id = connection.execute(
        select(dataset_versions.c.id)
        .where(dataset_versions.c.dataset_id == dataset_id)
        .order_by(dataset_versions.c.created_at.desc(), dataset_versions.c.id.desc())
        .limit(1)
    ).scalar_one()
    version_id = uuid4()
    created = connection.execute(
        insert(dataset_versions)
        .values(
            id=version_id,
            dataset_id=dataset_id,
            parent_version_id=parent_version_id,
        )
        .returning(
            dataset_versions.c.id,
            dataset_versions.c.dataset_id,
            dataset_versions.c.parent_version_id,
            dataset_versions.c.created_at,
        )
    ).mappings().one()
    connection.execute(
        insert(dataset_version_images).from_select(
            ["dataset_id", "dataset_version_id", "image_id"],
            select(
                dataset_version_images.c.dataset_id,
                literal(version_id),
                dataset_version_images.c.image_id,
            ).where(dataset_version_images.c.dataset_version_id == parent_version_id),
        )
    )
    return {**dict(created), "image_count": connection.execute(
        select(func.count(dataset_version_images.c.image_id)).where(
            dataset_version_images.c.dataset_version_id == version_id
        )
    ).scalar_one()}


def get_dataset_version_progress(
    connection: Connection,
    dataset_version_id: UUID,
) -> dict[str, Any]:
    version = connection.execute(
        select(dataset_versions.c.snapshot_at).where(
            dataset_versions.c.id == dataset_version_id
        )
    ).mappings().one_or_none()
    if version is None:
        raise DatasetVersionProgressNotFoundError("dataset version does not exist")

    frozen = version["snapshot_at"] is not None
    image_rows = _progress_images(dataset_version_id, frozen)
    annotation_rows = _progress_annotations(dataset_version_id, frozen)

    image_counts = connection.execute(
        select(
            func.count(image_rows.c.image_id).label("total"),
            func.count(image_rows.c.image_id)
            .filter(image_rows.c.status == "reviewed")
            .label("reviewed"),
        )
    ).mappings().one()

    accepted_product = and_(
        annotation_rows.c.class_type == "product",
        annotation_rows.c.lifecycle_state == "verified",
        annotation_rows.c.review_state == "accepted",
    )
    annotation_counts = connection.execute(
        select(
            func.count(annotation_rows.c.annotation_id).label("total"),
            func.count(annotation_rows.c.annotation_id)
            .filter(annotation_rows.c.lifecycle_state != "proposed")
            .label("decided"),
            func.count(annotation_rows.c.annotation_id)
            .filter(accepted_product)
            .label("accepted_products"),
            func.count(annotation_rows.c.annotation_id)
            .filter(
                accepted_product,
                annotation_rows.c.sku_id.is_not(None),
                annotation_rows.c.sku_is_unknown.is_(False),
            )
            .label("known_products"),
            func.count(annotation_rows.c.annotation_id)
            .filter(accepted_product, annotation_rows.c.sku_is_unknown.is_(True))
            .label("unknown_products"),
            func.count(annotation_rows.c.annotation_id)
            .filter(accepted_product, annotation_rows.c.sku_id.is_(None))
            .label("unassigned_products"),
            func.count(annotation_rows.c.annotation_id)
            .filter(
                annotation_rows.c.lifecycle_state != "rejected",
                annotation_rows.c.review_state == "flagged",
            )
            .label("flagged"),
        )
    ).mappings().one()

    return {
        "dataset_version_id": dataset_version_id,
        "images": {
            "total": int(image_counts["total"]),
        },
        "annotations": {
            "total": int(annotation_counts["total"]),
            "decided": int(annotation_counts["decided"]),
        },
        "identity": {
            "accepted_products": int(annotation_counts["accepted_products"]),
            "known_products": int(annotation_counts["known_products"]),
            "unknown_products": int(annotation_counts["unknown_products"]),
            "unassigned_products": int(annotation_counts["unassigned_products"]),
        },
        "qa": {
            "reviewed_images": int(image_counts["reviewed"]),
            "flagged_annotations": int(annotation_counts["flagged"]),
        },
    }


def _progress_images(dataset_version_id: UUID, frozen: bool) -> Subquery:
    if frozen:
        return (
            select(
                dataset_snapshot_images.c.image_id,
                dataset_snapshot_images.c.status,
            )
            .where(dataset_snapshot_images.c.dataset_version_id == dataset_version_id)
            .subquery()
        )
    return (
        select(dataset_version_images.c.image_id, images.c.status)
        .join(images, images.c.id == dataset_version_images.c.image_id)
        .where(dataset_version_images.c.dataset_version_id == dataset_version_id)
        .subquery()
    )


def _progress_annotations(dataset_version_id: UUID, frozen: bool) -> Subquery:
    if frozen:
        return (
            select(
                dataset_snapshot_annotations.c.annotation_id,
                dataset_snapshot_annotations.c.class_type,
                dataset_snapshot_annotations.c.sku_id,
                dataset_snapshot_annotations.c.lifecycle_state,
                dataset_snapshot_annotations.c.review_state,
                dataset_snapshot_skus.c.is_unknown.label("sku_is_unknown"),
            )
            .select_from(
                dataset_snapshot_annotations.outerjoin(
                    dataset_snapshot_skus,
                    and_(
                        dataset_snapshot_skus.c.dataset_version_id
                        == dataset_snapshot_annotations.c.dataset_version_id,
                        dataset_snapshot_skus.c.sku_id
                        == dataset_snapshot_annotations.c.sku_id,
                    ),
                )
            )
            .where(
                dataset_snapshot_annotations.c.dataset_version_id
                == dataset_version_id
            )
            .subquery()
        )
    return (
        select(
            annotation_records.c.id.label("annotation_id"),
            annotation_revisions.c.class_type,
            annotation_revisions.c.sku_id,
            annotation_revisions.c.lifecycle_state,
            annotation_revisions.c.review_state,
            skus.c.is_unknown.label("sku_is_unknown"),
        )
        .select_from(
            dataset_version_images.join(
                annotation_records,
                annotation_records.c.image_id == dataset_version_images.c.image_id,
            )
            .join(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision
                    == annotation_records.c.current_revision,
                ),
            )
            .outerjoin(skus, skus.c.id == annotation_revisions.c.sku_id)
        )
        .where(dataset_version_images.c.dataset_version_id == dataset_version_id)
        .subquery()
    )
