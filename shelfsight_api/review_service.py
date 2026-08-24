from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, and_, insert, select

from shelfsight_api.annotation_service import get_current_annotation
from shelfsight_api.data_model import (
    StaleAnnotationRevisionError,
    append_annotation_revision,
    snapshot_dataset_version,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_review_signoffs,
    dataset_version_images,
    dataset_versions,
    images,
    propagation_suggestions,
    review_decisions,
    skus,
)

MODEL_AGREEMENT_IOU_THRESHOLD = 0.75

RISK_REASONS: dict[str, tuple[str, int]] = {
    "annotation_flagged": ("Annotation is flagged", 120),
    "hard_pair_confusion": ("Fine-grained hard-pair confusion", 100),
    "consistency_conflict": ("Propagated label conflicts with its seed", 90),
    "low_agreement": ("Human geometry has low model agreement", 70),
    "propagated_origin": ("Label originated from propagation", 60),
    "unknown_status": ("Product identity is Unknown or unassigned", 50),
    "manual_flag": ("Reviewer added a blocking flag", 120),
}


class ReviewVersionNotFoundError(ValueError):
    """Raised when a dataset version does not exist."""


class ReviewVersionFrozenError(ValueError):
    """Raised when an unsigned frozen version cannot enter review."""


class ReviewAnnotationNotFoundError(ValueError):
    """Raised when an annotation is not active in the reviewed version."""


class ReviewSignoffBlockedError(ValueError):
    """Raised when unresolved blocking review items remain."""

    def __init__(self, unresolved_count: int) -> None:
        super().__init__("unresolved blocking review items remain")
        self.unresolved_count = unresolved_count


def list_review_queue(
    connection: Connection,
    dataset_version_id: UUID,
    *,
    include_resolved: bool = False,
) -> dict[str, Any]:
    version = _version(connection, dataset_version_id)
    signoff = _signoff(connection, dataset_version_id)
    if version["snapshot_at"] is not None:
        if signoff is None:
            raise ReviewVersionFrozenError(
                "dataset version was snapshotted before review sign-off"
            )
        return _queue_response(dataset_version_id, [], signoff)

    rows = _current_annotation_rows(connection, dataset_version_id)
    items = _review_items(connection, dataset_version_id, rows)
    visible_items = items if include_resolved else [item for item in items if not item["resolved"]]
    return _queue_response(dataset_version_id, visible_items, None, all_items=items)


def record_review_decision(
    connection: Connection,
    dataset_version_id: UUID,
    annotation_id: UUID,
    expected_revision: int,
    decision: Literal["approve", "flag"],
    reviewer: str,
    note: str | None,
) -> dict[str, Any]:
    version = _version(connection, dataset_version_id, lock=True)
    if version["snapshot_at"] is not None:
        raise ReviewVersionFrozenError("signed review data is immutable")

    row = _review_annotation(
        connection,
        dataset_version_id,
        annotation_id,
    )
    if row is None:
        raise ReviewAnnotationNotFoundError(
            "annotation is not active in this dataset version"
        )
    if int(row["revision"]) != expected_revision:
        raise StaleAnnotationRevisionError("annotation revision is stale")

    current_items = _review_items(connection, dataset_version_id, [row])
    risk_codes = (
        [reason["code"] for reason in current_items[0]["risk_reasons"]]
        if current_items
        else []
    )
    if decision == "flag" and "manual_flag" not in risk_codes:
        risk_codes.append("manual_flag")

    revision_values = _copy_revision_values(row)
    revision_values["review_state"] = "accepted" if decision == "approve" else "flagged"
    revision_values["source"] = "human"
    revision_values["provenance"] = {
        "actor": reviewer,
        "action": "qa_approve" if decision == "approve" else "qa_flag",
        "via": "api",
        "reviewed_revision": expected_revision,
        **({"note": note} if note is not None else {}),
    }
    new_revision = append_annotation_revision(
        connection,
        annotation_id,
        expected_revision,
        revision_values,
    )
    decision_row = connection.execute(
        insert(review_decisions)
        .values(
            dataset_version_id=dataset_version_id,
            annotation_id=annotation_id,
            annotation_revision=new_revision,
            decision="approved" if decision == "approve" else "flagged",
            risk_reasons=risk_codes,
            reviewer=reviewer,
            note=note,
        )
        .returning(review_decisions)
    ).mappings().one()
    return {
        "annotation": get_current_annotation(connection, annotation_id),
        "decision": dict(decision_row),
    }


def sign_off_dataset_version(
    connection: Connection,
    dataset_version_id: UUID,
    reviewer: str,
) -> dict[str, Any]:
    version = _version(connection, dataset_version_id, lock=True)
    existing = _signoff(connection, dataset_version_id)
    if existing is not None:
        return existing
    if version["snapshot_at"] is not None:
        raise ReviewVersionFrozenError(
            "dataset version was snapshotted before review sign-off"
        )

    rows = _current_annotation_rows(connection, dataset_version_id)
    items = _review_items(connection, dataset_version_id, rows)
    unresolved_count = sum(not item["resolved"] for item in items)
    if unresolved_count:
        raise ReviewSignoffBlockedError(unresolved_count)

    snapshot_dataset_version(connection, dataset_version_id)
    signoff = connection.execute(
        insert(dataset_review_signoffs)
        .values(
            dataset_version_id=dataset_version_id,
            signed_by=reviewer,
            reviewed_annotation_count=len(rows),
            risk_item_count=len(items),
        )
        .returning(dataset_review_signoffs)
    ).mappings().one()
    return dict(signoff)


def _version(
    connection: Connection,
    dataset_version_id: UUID,
    *,
    lock: bool = False,
) -> dict[str, Any]:
    query = select(dataset_versions).where(dataset_versions.c.id == dataset_version_id)
    if lock:
        query = query.with_for_update()
    row = connection.execute(query).mappings().one_or_none()
    if row is None:
        raise ReviewVersionNotFoundError("dataset version does not exist")
    return dict(row)


def _signoff(
    connection: Connection,
    dataset_version_id: UUID,
) -> dict[str, Any] | None:
    row = connection.execute(
        select(dataset_review_signoffs).where(
            dataset_review_signoffs.c.dataset_version_id == dataset_version_id
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _current_annotation_rows(
    connection: Connection,
    dataset_version_id: UUID,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        _current_annotation_query().where(
            dataset_version_images.c.dataset_version_id == dataset_version_id,
            annotation_revisions.c.lifecycle_state == "verified",
        )
    ).mappings()
    return [dict(row) for row in rows]


def _review_annotation(
    connection: Connection,
    dataset_version_id: UUID,
    annotation_id: UUID,
) -> dict[str, Any] | None:
    row = connection.execute(
        _current_annotation_query().where(
            dataset_version_images.c.dataset_version_id == dataset_version_id,
            annotation_records.c.id == annotation_id,
            annotation_revisions.c.lifecycle_state == "verified",
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _current_annotation_query() -> Any:
    return (
        select(
            annotation_records.c.id.label("annotation_id"),
            annotation_records.c.image_id,
            annotation_records.c.current_revision.label("revision"),
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
            images.c.original_filename,
            images.c.canonical_width,
            images.c.canonical_height,
            skus.c.is_unknown.label("sku_is_unknown"),
        )
        .join(
            dataset_version_images,
            dataset_version_images.c.image_id == annotation_records.c.image_id,
        )
        .join(images, images.c.id == annotation_records.c.image_id)
        .join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
        .outerjoin(skus, skus.c.id == annotation_revisions.c.sku_id)
    )


def _review_items(
    connection: Connection,
    dataset_version_id: UUID,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    annotation_ids = [UUID(str(row["annotation_id"])) for row in rows]
    models = _first_model_revisions(connection, annotation_ids)
    suggestions = _hard_pair_suggestions(connection, rows)
    seed_skus = _seed_skus(connection, rows)
    decisions = _latest_decisions(connection, dataset_version_id, annotation_ids)

    items = []
    for row in rows:
        annotation_id = UUID(str(row["annotation_id"]))
        key = (annotation_id, int(row["revision"]))
        decision = decisions.get(key)
        risk_codes = _risk_codes(row, models.get(annotation_id), suggestions, seed_skus)
        if decision is not None:
            for code in decision["risk_reasons"]:
                if code in RISK_REASONS and code not in risk_codes:
                    risk_codes.append(code)
        if not risk_codes:
            continue
        reasons = [_risk_reason(code) for code in risk_codes]
        resolved = decision is not None and decision["decision"] == "approved"
        items.append(
            {
                "annotation_id": annotation_id,
                "annotation_revision": int(row["revision"]),
                "image_id": row["image_id"],
                "image_name": row["original_filename"],
                "image_url": f"/api/images/{row['image_id']}/media/canonical",
                "class_type": row["class_type"],
                "sku_id": row["sku_id"],
                "review_state": row["review_state"],
                "source": row["source"],
                "risk_score": sum(reason["weight"] for reason in reasons),
                "risk_reasons": reasons,
                "resolved": resolved,
                "decision": decision,
            }
        )
    return sorted(
        items,
        key=lambda item: (-int(item["risk_score"]), str(item["annotation_id"])),
    )


def _risk_codes(
    row: Mapping[str, Any],
    model_revision: Mapping[str, Any] | None,
    suggestions: set[UUID],
    seed_skus: Mapping[UUID, UUID | None],
) -> list[str]:
    codes = []
    if row["review_state"] == "flagged":
        codes.append("annotation_flagged")
    if UUID(str(row["annotation_id"])) in suggestions:
        codes.append("hard_pair_confusion")
    if _has_seed_conflict(row, seed_skus):
        codes.append("consistency_conflict")
    if model_revision is not None and _has_low_agreement(row, model_revision):
        codes.append("low_agreement")
    if row["source"] == "propagated":
        codes.append("propagated_origin")
    if row["class_type"] == "product" and (
        row["sku_id"] is None or row["sku_is_unknown"] is True
    ):
        codes.append("unknown_status")
    return codes


def _first_model_revisions(
    connection: Connection,
    annotation_ids: list[UUID],
) -> dict[UUID, dict[str, Any]]:
    rows = connection.execute(
        select(annotation_revisions)
        .where(
            annotation_revisions.c.annotation_id.in_(annotation_ids),
            annotation_revisions.c.source == "model",
        )
        .order_by(annotation_revisions.c.annotation_id, annotation_revisions.c.revision)
    ).mappings()
    revisions: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        annotation_id = UUID(str(row["annotation_id"]))
        revisions.setdefault(annotation_id, dict(row))
    return revisions


def _hard_pair_suggestions(
    connection: Connection,
    rows: list[dict[str, Any]],
) -> set[UUID]:
    by_suggestion: dict[UUID, UUID] = {}
    for row in rows:
        suggestion_id = _provenance_uuid(row["provenance"], "suggestion_id")
        if suggestion_id is not None:
            by_suggestion[suggestion_id] = UUID(str(row["annotation_id"]))
    if not by_suggestion:
        return set()
    hard_pair_ids = connection.execute(
        select(propagation_suggestions.c.id).where(
            propagation_suggestions.c.id.in_(by_suggestion),
            propagation_suggestions.c.risk_reason == "hard_pair",
        )
    ).scalars()
    return {by_suggestion[suggestion_id] for suggestion_id in hard_pair_ids}


def _seed_skus(
    connection: Connection,
    rows: list[dict[str, Any]],
) -> dict[UUID, UUID | None]:
    seed_ids = {
        seed_id
        for row in rows
        if (seed_id := _provenance_uuid(row["provenance"], "seed_annotation_id"))
        is not None
    }
    if not seed_ids:
        return {}
    values = connection.execute(
        select(annotation_records.c.id, annotation_revisions.c.sku_id)
        .join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
        .where(annotation_records.c.id.in_(seed_ids))
    )
    return {UUID(str(annotation_id)): sku_id for annotation_id, sku_id in values}


def _latest_decisions(
    connection: Connection,
    dataset_version_id: UUID,
    annotation_ids: list[UUID],
) -> dict[tuple[UUID, int], dict[str, Any]]:
    rows = connection.execute(
        select(review_decisions)
        .where(
            review_decisions.c.dataset_version_id == dataset_version_id,
            review_decisions.c.annotation_id.in_(annotation_ids),
        )
        .order_by(review_decisions.c.created_at.desc(), review_decisions.c.id.desc())
    ).mappings()
    decisions: dict[tuple[UUID, int], dict[str, Any]] = {}
    for row in rows:
        key = (UUID(str(row["annotation_id"])), int(row["annotation_revision"]))
        decisions.setdefault(key, dict(row))
    return decisions


def _has_seed_conflict(
    row: Mapping[str, Any],
    seed_skus: Mapping[UUID, UUID | None],
) -> bool:
    if row["source"] != "propagated":
        return False
    seed_id = _provenance_uuid(row["provenance"], "seed_annotation_id")
    return seed_id is not None and seed_skus.get(seed_id) != row["sku_id"]


def _has_low_agreement(
    current: Mapping[str, Any],
    model: Mapping[str, Any],
) -> bool:
    if current["class_type"] != model["class_type"]:
        return True
    return _intersection_over_union(current, model) < MODEL_AGREEMENT_IOU_THRESHOLD


def _intersection_over_union(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> float:
    left = max(float(first["x"]), float(second["x"]))
    top = max(float(first["y"]), float(second["y"]))
    right = min(
        float(first["x"]) + float(first["width"]),
        float(second["x"]) + float(second["width"]),
    )
    bottom = min(
        float(first["y"]) + float(first["height"]),
        float(second["y"]) + float(second["height"]),
    )
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = float(first["width"]) * float(first["height"])
    second_area = float(second["width"]) * float(second["height"])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _provenance_uuid(value: Any, key: str) -> UUID | None:
    if not isinstance(value, dict):
        return None
    candidate = value.get(key)
    try:
        return UUID(str(candidate)) if candidate is not None else None
    except ValueError:
        return None


def _risk_reason(code: str) -> dict[str, Any]:
    label, weight = RISK_REASONS[code]
    return {"code": code, "label": label, "weight": weight, "blocking": True}


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


def _queue_response(
    dataset_version_id: UUID,
    visible_items: list[dict[str, Any]],
    signoff: dict[str, Any] | None,
    *,
    all_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if signoff is not None:
        total_risk_items = int(signoff["risk_item_count"])
        return {
            "dataset_version_id": dataset_version_id,
            "status": "signed",
            "total_risk_items": total_risk_items,
            "unresolved_count": 0,
            "resolved_count": total_risk_items,
            "items": visible_items,
            "signoff": signoff,
        }
    items = all_items if all_items is not None else visible_items
    unresolved_count = sum(not item["resolved"] for item in items)
    return {
        "dataset_version_id": dataset_version_id,
        "status": "open",
        "total_risk_items": len(items),
        "unresolved_count": unresolved_count,
        "resolved_count": len(items) - unresolved_count,
        "items": visible_items,
        "signoff": signoff,
    }
