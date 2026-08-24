from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import (
    Connection,
    and_,
    case,
    distinct,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from shelfsight_api.annotation_service import get_current_annotation
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
    append_annotation_revision,
)
from shelfsight_api.job_service import (
    JobClaim,
    JobIdempotencyConflictError,
    enqueue_job,
)
from shelfsight_api.model_contract import (
    AnnotationEmbeddingTarget,
    EmbedRequest,
    EmbedResponse,
    parse_model_request,
    parse_model_response,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    embeddings,
    images,
    propagation_suggestion_sets,
    propagation_suggestions,
    sku_hard_pairs,
    skus,
)
from shelfsight_api.propagation_profile import (
    T005_EXPLORATORY_PROPAGATION_PROFILE,
    PropagationProfile,
)
from shelfsight_api.worker import JobExecutionError

MAX_PROPAGATION_EMBEDDING_BATCH = 250


class InvalidPropagationSeedError(ValueError):
    """Raised when a propagation seed is not a human-assigned known SKU."""


class PropagationEmbeddingNotReadyError(ValueError):
    """Raised when the seed has no current propagation embedding."""


class PropagationIndexNotCurrentError(ValueError):
    """Raised when eligible unlabeled crops are missing current embeddings."""


class PropagationSuggestionSetNotFoundError(ValueError):
    """Raised when a suggestion set does not exist."""


class InvalidPropagationDecisionError(ValueError):
    """Raised when a propagation decision is incomplete or unsafe."""


@dataclass(frozen=True)
class PropagationQueueOutcome:
    annotation_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class PropagationDecision:
    suggestion_id: UUID
    decision: Literal["confirm", "skip"]
    reviewed_individually: bool = False


def enqueue_propagation_embeddings(
    connection: Connection,
    annotation_ids: Sequence[UUID],
    *,
    profile: PropagationProfile = T005_EXPLORATORY_PROPAGATION_PROFILE,
) -> list[PropagationQueueOutcome]:
    rows = connection.execute(
        _current_annotation_select().where(annotation_records.c.id.in_(annotation_ids))
    ).mappings()
    annotations = {row["id"]: dict(row) for row in rows}
    outcomes: list[PropagationQueueOutcome] = []
    for annotation_id in annotation_ids:
        row = annotations.get(annotation_id)
        error_code = _embedding_eligibility_error(row)
        if error_code is not None:
            outcomes.append(
                PropagationQueueOutcome(
                    annotation_id=annotation_id,
                    status="rejected",
                    error_code=error_code,
                )
            )
            continue
        assert row is not None
        try:
            job, created = enqueue_job(
                connection,
                "embed",
                (
                    f"propagation:annotation:{annotation_id}:"
                    f"r{row['revision']}:{profile.index_version}"
                ),
                _embedding_payload(row, profile),
                max_attempts=3,
            )
        except JobIdempotencyConflictError:
            outcomes.append(
                PropagationQueueOutcome(
                    annotation_id=annotation_id,
                    status="rejected",
                    error_code="idempotency_conflict",
                )
            )
            continue
        outcomes.append(
            PropagationQueueOutcome(
                annotation_id=annotation_id,
                status="queued" if created else "deduplicated",
                job_id=job["id"],
                job_state=str(job["state"]),
            )
        )
    return outcomes


def create_propagation_suggestions(
    connection: Connection,
    seed_annotation_id: UUID,
    expected_revision: int,
    top_k: int,
    actor: str,
    *,
    profile: PropagationProfile = T005_EXPLORATORY_PROPAGATION_PROFILE,
) -> dict[str, Any]:
    seed = _current_annotation(connection, seed_annotation_id)
    _validate_seed(connection, seed, expected_revision)
    seed_embedding = connection.execute(
        select(embeddings).where(
            embeddings.c.subject_type == "annotation",
            embeddings.c.purpose == "propagation",
            embeddings.c.annotation_id == seed_annotation_id,
            embeddings.c.annotation_revision == expected_revision,
            *_profile_conditions(profile),
        )
    ).mappings().one_or_none()
    if seed_embedding is None:
        raise PropagationEmbeddingNotReadyError(
            "seed propagation embedding is not ready"
        )

    eligible = _eligible_candidate_select(seed)
    eligible_candidates = eligible.subquery()
    eligible_count = connection.execute(
        select(func.count()).select_from(eligible_candidates)
    ).scalar_one()
    current_embedding_count = connection.execute(
        select(func.count(distinct(embeddings.c.annotation_id)))
        .select_from(
            embeddings.join(
                eligible_candidates,
                and_(
                    embeddings.c.annotation_id
                    == eligible_candidates.c.annotation_id,
                    embeddings.c.annotation_revision
                    == eligible_candidates.c.annotation_revision,
                ),
            )
        )
        .where(
            embeddings.c.subject_type == "annotation",
            embeddings.c.purpose == "propagation",
            *_profile_conditions(profile),
        )
    ).scalar_one()
    if current_embedding_count != eligible_count:
        raise PropagationIndexNotCurrentError(
            "eligible crop propagation embeddings are not current"
        )

    hard_pairs = _hard_pairs(connection, UUID(str(seed["sku_id"])))
    requires_review = bool(hard_pairs)
    candidates = _rank_candidates(
        connection,
        list(seed_embedding["embedding"]),
        eligible,
        top_k,
        profile,
    )
    provenance = profile.model_provenance
    suggestion_set_id = connection.execute(
        insert(propagation_suggestion_sets)
        .values(
            seed_annotation_id=seed_annotation_id,
            seed_annotation_revision=expected_revision,
            seed_sku_id=seed["sku_id"],
            index_namespace=profile.index_namespace,
            index_version=profile.index_version,
            model_id=provenance.model_id,
            model_version=provenance.model_version,
            artifact_sha256=provenance.artifact_sha256,
            quality_evidence=profile.quality_evidence,
            created_by=actor,
        )
        .returning(propagation_suggestion_sets.c.id)
    ).scalar_one()
    if candidates:
        inserted = connection.execute(
            insert(propagation_suggestions)
            .values(
                [
                    {
                        "suggestion_set_id": suggestion_set_id,
                        "candidate_annotation_id": candidate["annotation_id"],
                        "candidate_annotation_revision": candidate[
                            "annotation_revision"
                        ],
                        "score": candidate["score"],
                        "requires_individual_review": requires_review,
                        "risk_reason": "hard_pair" if requires_review else None,
                    }
                    for candidate in candidates
                ]
            )
            .returning(
                propagation_suggestions.c.id,
                propagation_suggestions.c.candidate_annotation_id,
            )
        ).all()
        suggestion_ids = {
            candidate_annotation_id: suggestion_id
            for suggestion_id, candidate_annotation_id in inserted
        }
        for candidate in candidates:
            candidate["suggestion_id"] = suggestion_ids[candidate["annotation_id"]]
            candidate["selected"] = False
            candidate["requires_individual_review"] = requires_review
            candidate["risk_reason"] = "hard_pair" if requires_review else None

    return {
        "suggestion_set_id": suggestion_set_id,
        "seed_annotation_id": seed_annotation_id,
        "seed_annotation_revision": expected_revision,
        "seed_sku": {
            "sku_id": seed["sku_id"],
            "name": seed["sku_name"],
            "brand": seed["sku_brand"],
            "variant": seed["sku_variant"],
        },
        "index": {
            "namespace": profile.index_namespace,
            "version": profile.index_version,
            "purpose": "propagation",
            "quality_evidence": profile.quality_evidence,
        },
        "hard_pairs": hard_pairs,
        "candidates": candidates,
        "selected_count": 0,
        "skipped_count": len(candidates),
        "automatic_confirmation": False,
    }


def confirm_propagation_suggestions(
    connection: Connection,
    suggestion_set_id: UUID,
    decisions: Sequence[PropagationDecision],
    actor: str,
) -> dict[str, Any]:
    suggestion_set = connection.execute(
        select(propagation_suggestion_sets)
        .where(propagation_suggestion_sets.c.id == suggestion_set_id)
        .with_for_update()
    ).mappings().one_or_none()
    if suggestion_set is None:
        raise PropagationSuggestionSetNotFoundError(
            "propagation suggestion set does not exist"
        )
    if suggestion_set["status"] != "open":
        raise InvalidPropagationDecisionError(
            "propagation suggestion set is already completed"
        )

    suggestion_rows = list(
        connection.execute(
            select(propagation_suggestions)
            .where(
                propagation_suggestions.c.suggestion_set_id == suggestion_set_id
            )
            .order_by(propagation_suggestions.c.id)
            .with_for_update()
        ).mappings()
    )
    decisions_by_id = {decision.suggestion_id: decision for decision in decisions}
    expected_ids = {row["id"] for row in suggestion_rows}
    if len(decisions_by_id) != len(decisions) or set(decisions_by_id) != expected_ids:
        raise InvalidPropagationDecisionError(
            "every suggestion must receive exactly one decision"
        )

    seed = _current_annotation(
        connection,
        UUID(str(suggestion_set["seed_annotation_id"])),
    )
    _validate_seed(
        connection,
        seed,
        int(suggestion_set["seed_annotation_revision"]),
    )
    if seed["sku_id"] != suggestion_set["seed_sku_id"]:
        raise StaleAnnotationRevisionError("propagation seed SKU changed")

    confirmed_annotations: list[dict[str, Any]] = []
    selected_count = 0
    for row in suggestion_rows:
        decision = decisions_by_id[row["id"]]
        if decision.decision == "confirm":
            if row["requires_individual_review"] and not decision.reviewed_individually:
                raise InvalidPropagationDecisionError(
                    "hard-pair suggestions require individual review"
                )
            _confirm_candidate(
                connection,
                dict(row),
                dict(suggestion_set),
                seed,
                actor,
                decision.reviewed_individually,
            )
            selected_count += 1
            confirmed_annotations.append(
                get_current_annotation(
                    connection,
                    UUID(str(row["candidate_annotation_id"])),
                )
            )
        connection.execute(
            update(propagation_suggestions)
            .where(propagation_suggestions.c.id == row["id"])
            .values(
                status="confirmed" if decision.decision == "confirm" else "skipped",
                decided_by=actor,
                decided_at=func.now(),
            )
        )

    skipped_count = len(suggestion_rows) - selected_count
    connection.execute(
        update(propagation_suggestion_sets)
        .where(propagation_suggestion_sets.c.id == suggestion_set_id)
        .values(
            status="completed",
            completed_by=actor,
            completed_at=func.now(),
            selected_count=selected_count,
            skipped_count=skipped_count,
        )
    )
    return {
        "suggestion_set_id": suggestion_set_id,
        "selected_count": selected_count,
        "skipped_count": skipped_count,
        "annotations": confirmed_annotations,
    }


def write_propagation_embedding(
    connection: Connection,
    claim: JobClaim,
    result: Mapping[str, Any],
    *,
    profile: PropagationProfile = T005_EXPLORATORY_PROPAGATION_PROFILE,
) -> None:
    try:
        request = parse_model_request(claim.payload)
        response = parse_model_response(result)
    except ValidationError as error:
        raise JobExecutionError(
            "invalid_embedding_result",
            "embedding result cannot be stored",
            retryable=False,
        ) from error
    if not isinstance(request, EmbedRequest) or not isinstance(response, EmbedResponse):
        raise JobExecutionError(
            "invalid_embedding_result",
            "embedding result cannot be stored",
            retryable=False,
        )
    target = request.target
    if (
        request.purpose != "propagation"
        or not isinstance(target, AnnotationEmbeddingTarget)
        or response.request_id != request.request_id
        or response.embedding.dimension != profile.dimension
        or response.model_provenance != profile.model_provenance
    ):
        raise JobExecutionError(
            "embedding_profile_mismatch",
            "embedding result does not match the propagation profile",
            retryable=False,
        )

    row = _current_annotation(connection, target.annotation_id)
    if (
        row["revision"] != target.annotation_revision
        or _embedding_eligibility_error(row) is not None
        or request.image.image_id != row["image_id"]
        or (
            request.crop.x,
            request.crop.y,
            request.crop.width,
            request.crop.height,
        )
        != (
            float(row["x"]),
            float(row["y"]),
            float(row["width"]),
            float(row["height"]),
        )
    ):
        raise JobExecutionError(
            "stale_embedding_target",
            "annotation changed before embedding was stored",
            retryable=False,
        )

    connection.execute(
        postgresql_insert(embeddings)
        .values(
            id=response.embedding.embedding_id,
            purpose="propagation",
            subject_type="annotation",
            annotation_id=target.annotation_id,
            annotation_revision=target.annotation_revision,
            reference_image_id=None,
            target_fingerprint=response.input_fingerprint,
            model_id=response.model_provenance.model_id,
            model_version=response.model_provenance.model_version,
            artifact_sha256=response.model_provenance.artifact_sha256,
            configuration=response.configuration.model_dump(mode="json"),
            dimension=response.embedding.dimension,
            embedding=[float(value) for value in response.embedding.values],
        )
        .on_conflict_do_nothing()
    )


def _embedding_payload(
    row: Mapping[str, Any],
    profile: PropagationProfile,
) -> dict[str, Any]:
    annotation_id = UUID(str(row["id"]))
    revision = int(row["revision"])
    identity = (
        f"shelfsight:propagation:annotation:{annotation_id}:"
        f"{revision}:{profile.index_version}"
    )
    return {
        "request_id": f"embed:{uuid5(NAMESPACE_URL, identity)}",
        "operation": "embed",
        "image": {
            "image_id": str(row["image_id"]),
            "sha256": row["content_sha256"],
            "width": row["canonical_width"],
            "height": row["canonical_height"],
        },
        "model_role": "propagation_embedder",
        "crop": {
            "type": "axis_aligned_box",
            "x": row["x"],
            "y": row["y"],
            "width": row["width"],
            "height": row["height"],
        },
        "purpose": "propagation",
        "target": {
            "type": "annotation",
            "annotation_id": str(annotation_id),
            "annotation_revision": revision,
        },
        "configuration": {"normalize": True},
    }


def _current_annotation(
    connection: Connection,
    annotation_id: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        _current_annotation_select().where(annotation_records.c.id == annotation_id)
    ).mappings().one_or_none()
    if row is None:
        raise AnnotationNotFoundError("annotation does not exist")
    return dict(row)


def _current_annotation_select() -> Any:
    return (
        select(
            annotation_records.c.id,
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
            images.c.dataset_id,
            images.c.content_sha256,
            images.c.canonical_width,
            images.c.canonical_height,
            skus.c.name.label("sku_name"),
            skus.c.brand.label("sku_brand"),
            skus.c.variant.label("sku_variant"),
            skus.c.status.label("sku_status"),
            skus.c.is_unknown.label("sku_is_unknown"),
        )
        .join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
        .join(images, images.c.id == annotation_records.c.image_id)
        .outerjoin(skus, skus.c.id == annotation_revisions.c.sku_id)
    )


def _embedding_eligibility_error(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return "annotation_not_found"
    if (
        row["class_type"] != "product"
        or row["lifecycle_state"] != "verified"
        or row["review_state"] != "accepted"
    ):
        return "invalid_annotation_state"
    if row["sku_is_unknown"]:
        return "unknown_annotation"
    return None


def _validate_seed(
    connection: Connection,
    seed: Mapping[str, Any],
    expected_revision: int,
) -> None:
    if seed["revision"] != expected_revision:
        raise StaleAnnotationRevisionError("annotation revision is stale")
    if _embedding_eligibility_error(seed) is not None:
        raise InvalidPropagationSeedError(
            "propagation requires an accepted product annotation"
        )
    if (
        seed["sku_id"] is None
        or seed["sku_status"] != "active"
        or seed["sku_is_unknown"]
    ):
        raise InvalidPropagationSeedError(
            "propagation requires an active known SKU assignment"
        )
    human_assignment_exists = connection.execute(
        select(annotation_revisions.c.revision)
        .where(
            annotation_revisions.c.annotation_id == seed["id"],
            annotation_revisions.c.revision <= expected_revision,
            annotation_revisions.c.sku_id == seed["sku_id"],
            annotation_revisions.c.source == "human",
            annotation_revisions.c.provenance["action"].as_string() == "assign_sku",
        )
        .limit(1)
    ).first()
    if human_assignment_exists is None:
        raise InvalidPropagationSeedError(
            "propagation requires a human SKU assignment"
        )


def _eligible_candidate_select(seed: Mapping[str, Any]) -> Any:
    return (
        select(
            annotation_records.c.id.label("annotation_id"),
            annotation_records.c.current_revision.label("annotation_revision"),
            annotation_records.c.image_id,
            annotation_revisions.c.x,
            annotation_revisions.c.y,
            annotation_revisions.c.width,
            annotation_revisions.c.height,
            images.c.canonical_width.label("image_width"),
            images.c.canonical_height.label("image_height"),
        )
        .join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
        .join(images, images.c.id == annotation_records.c.image_id)
        .where(
            annotation_records.c.image_id == seed["image_id"],
            annotation_records.c.id != seed["id"],
            annotation_revisions.c.class_type == "product",
            annotation_revisions.c.lifecycle_state == "verified",
            annotation_revisions.c.review_state == "accepted",
            annotation_revisions.c.sku_id.is_(None),
        )
    )


def _rank_candidates(
    connection: Connection,
    query_vector: list[float],
    eligible: Any,
    top_k: int,
    profile: PropagationProfile,
) -> list[dict[str, Any]]:
    candidates = eligible.subquery()
    similarity = (1 - embeddings.c.embedding.cosine_distance(query_vector)).label(
        "score"
    )
    rows = connection.execute(
        select(
            candidates.c.annotation_id,
            candidates.c.annotation_revision,
            candidates.c.image_id,
            candidates.c.x,
            candidates.c.y,
            candidates.c.width,
            candidates.c.height,
            candidates.c.image_width,
            candidates.c.image_height,
            similarity,
        )
        .join(
            embeddings,
            and_(
                embeddings.c.annotation_id == candidates.c.annotation_id,
                embeddings.c.annotation_revision == candidates.c.annotation_revision,
            ),
        )
        .where(
            embeddings.c.subject_type == "annotation",
            embeddings.c.purpose == "propagation",
            *_profile_conditions(profile),
        )
        .order_by(similarity.desc(), candidates.c.annotation_id)
        .limit(top_k)
    ).mappings()
    return [
        {
            "annotation_id": row["annotation_id"],
            "annotation_revision": row["annotation_revision"],
            "image_id": row["image_id"],
            "image_url": f"/api/images/{row['image_id']}/media/canonical",
            "x": row["x"],
            "y": row["y"],
            "width": row["width"],
            "height": row["height"],
            "image_width": row["image_width"],
            "image_height": row["image_height"],
            "score": max(-1.0, min(1.0, float(row["score"]))),
        }
        for row in rows
    ]


def _hard_pairs(connection: Connection, seed_sku_id: UUID) -> list[dict[str, Any]]:
    other_sku_id = (
        case(
            (sku_hard_pairs.c.first_sku_id == seed_sku_id, sku_hard_pairs.c.second_sku_id),
            else_=sku_hard_pairs.c.first_sku_id,
        )
    ).label("other_sku_id")
    pairs = (
        select(other_sku_id, sku_hard_pairs.c.reason)
        .where(
            or_(
                sku_hard_pairs.c.first_sku_id == seed_sku_id,
                sku_hard_pairs.c.second_sku_id == seed_sku_id,
            )
        )
        .subquery()
    )
    rows = connection.execute(
        select(
            skus.c.id,
            skus.c.name,
            skus.c.brand,
            skus.c.variant,
            pairs.c.reason,
        )
        .join(pairs, pairs.c.other_sku_id == skus.c.id)
        .order_by(skus.c.name, skus.c.id)
    ).mappings()
    return [
        {
            "sku_id": row["id"],
            "name": row["name"],
            "brand": row["brand"],
            "variant": row["variant"],
            "reason": row["reason"],
        }
        for row in rows
    ]


def _confirm_candidate(
    connection: Connection,
    suggestion: Mapping[str, Any],
    suggestion_set: Mapping[str, Any],
    seed: Mapping[str, Any],
    actor: str,
    reviewed_individually: bool,
) -> None:
    annotation_id = UUID(str(suggestion["candidate_annotation_id"]))
    candidate = _current_annotation(connection, annotation_id)
    if (
        candidate["revision"] != suggestion["candidate_annotation_revision"]
        or candidate["sku_id"] is not None
        or _embedding_eligibility_error(candidate) is not None
    ):
        raise StaleAnnotationRevisionError("propagation candidate changed")
    values = {
        key: candidate[key]
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
        {
            "sku_id": seed["sku_id"],
            "source": "propagated",
            "provenance": {
                "actor": actor,
                "action": "confirm_propagation",
                "via": "api",
                "suggestion_id": str(suggestion["id"]),
                "suggestion_set_id": str(suggestion_set["id"]),
                "seed_annotation_id": str(seed["id"]),
                "seed_annotation_revision": seed["revision"],
                "score": suggestion["score"],
                "reviewed_individually": reviewed_individually,
            },
        }
    )
    append_annotation_revision(
        connection,
        annotation_id,
        int(suggestion["candidate_annotation_revision"]),
        values,
    )


def _profile_conditions(profile: PropagationProfile) -> tuple[Any, ...]:
    provenance = profile.model_provenance
    return (
        embeddings.c.model_id == provenance.model_id,
        embeddings.c.model_version == provenance.model_version,
        embeddings.c.artifact_sha256 == provenance.artifact_sha256,
        embeddings.c.dimension == profile.dimension,
    )
