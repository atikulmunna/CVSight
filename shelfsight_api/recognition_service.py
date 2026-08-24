from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Connection, and_, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from shelfsight_api.annotation_service import InvalidAnnotationStateError
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
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
    SkuReferenceEmbeddingTarget,
    parse_model_request,
    parse_model_response,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    embeddings,
    images,
    sku_reference_images,
    skus,
)
from shelfsight_api.recognition_profile import (
    T005_RECOGNITION_PROFILE,
    RecognitionProfile,
)
from shelfsight_api.worker import JobExecutionError

MAX_GALLERY_EMBEDDING_BATCH = 250


class RecognitionEmbeddingNotReadyError(ValueError):
    """Raised when an annotation has no current recognition embedding."""


class StaleRecognitionEmbeddingError(ValueError):
    """Raised when only an old annotation embedding exists."""


class RecognitionGalleryNotCurrentError(ValueError):
    """Raised when active gallery references are not completely embedded."""


@dataclass(frozen=True)
class GalleryQueueOutcome:
    reference_image_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


def enqueue_annotation_recognition_embedding(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    *,
    profile: RecognitionProfile = T005_RECOGNITION_PROFILE,
) -> tuple[dict[str, Any], bool]:
    row = _current_annotation_query(connection, annotation_id)
    _validate_query_annotation(row, expected_revision)
    payload = _annotation_embedding_payload(row, profile)
    return enqueue_job(
        connection,
        "embed",
        _annotation_idempotency_key(annotation_id, expected_revision, profile),
        payload,
        max_attempts=3,
    )


def enqueue_gallery_recognition_embeddings(
    connection: Connection,
    reference_image_ids: Sequence[UUID],
    *,
    profile: RecognitionProfile = T005_RECOGNITION_PROFILE,
) -> list[GalleryQueueOutcome]:
    rows = connection.execute(
        select(
            sku_reference_images,
            skus.c.status.label("sku_status"),
            skus.c.is_unknown,
        )
        .join(skus, skus.c.id == sku_reference_images.c.sku_id)
        .where(sku_reference_images.c.id.in_(reference_image_ids))
    ).mappings()
    references = {row["id"]: dict(row) for row in rows}
    outcomes: list[GalleryQueueOutcome] = []

    for reference_id in reference_image_ids:
        row = references.get(reference_id)
        if row is None:
            outcomes.append(
                GalleryQueueOutcome(
                    reference_image_id=reference_id,
                    status="rejected",
                    error_code="reference_not_found",
                )
            )
            continue
        if row["sku_status"] != "active" or row["is_unknown"]:
            outcomes.append(
                GalleryQueueOutcome(
                    reference_image_id=reference_id,
                    status="rejected",
                    error_code="invalid_sku_state",
                )
            )
            continue
        try:
            job, created = enqueue_job(
                connection,
                "embed",
                _reference_idempotency_key(reference_id, profile),
                _reference_embedding_payload(row, profile),
                max_attempts=3,
            )
        except JobIdempotencyConflictError:
            outcomes.append(
                GalleryQueueOutcome(
                    reference_image_id=reference_id,
                    status="rejected",
                    error_code="idempotency_conflict",
                )
            )
            continue
        outcomes.append(
            GalleryQueueOutcome(
                reference_image_id=reference_id,
                status="queued" if created else "deduplicated",
                job_id=job["id"],
                job_state=str(job["state"]),
            )
        )
    return outcomes


def get_annotation_sku_candidates(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    top_k: int,
    *,
    profile: RecognitionProfile = T005_RECOGNITION_PROFILE,
) -> dict[str, Any]:
    annotation = _current_annotation_query(connection, annotation_id)
    _validate_query_annotation(annotation, expected_revision)
    query_embedding = connection.execute(
        select(embeddings).where(
            embeddings.c.subject_type == "annotation",
            embeddings.c.purpose == "recognition",
            embeddings.c.annotation_id == annotation_id,
            embeddings.c.annotation_revision == expected_revision,
            *_profile_conditions(profile),
        )
    ).mappings().one_or_none()
    if query_embedding is None:
        older_exists = connection.execute(
            select(embeddings.c.id)
            .where(
                embeddings.c.subject_type == "annotation",
                embeddings.c.purpose == "recognition",
                embeddings.c.annotation_id == annotation_id,
            )
            .limit(1)
        ).first()
        if older_exists is not None:
            raise StaleRecognitionEmbeddingError(
                "annotation recognition embedding is stale"
            )
        raise RecognitionEmbeddingNotReadyError(
            "annotation recognition embedding is not ready"
        )

    _require_current_gallery(connection, profile)
    candidates = _rank_gallery(
        connection,
        list(query_embedding["embedding"]),
        top_k,
        profile,
    )
    best_score = candidates[0]["score"] if candidates else None
    return {
        "annotation_id": annotation_id,
        "annotation_revision": expected_revision,
        "embedding_id": query_embedding["id"],
        "index": {
            "namespace": profile.index_namespace,
            "version": profile.index_version,
            "purpose": "recognition",
        },
        "candidates": candidates,
        "unknown_decision": {
            "is_unknown": best_score is None or best_score < profile.unknown_threshold,
            "threshold": profile.unknown_threshold,
            "calibration_version": profile.calibration_version,
            "automatic_confirmation": False,
        },
    }


def write_recognition_embedding(
    connection: Connection,
    claim: JobClaim,
    result: Mapping[str, Any],
    *,
    profile: RecognitionProfile = T005_RECOGNITION_PROFILE,
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
    if (
        request.purpose != "recognition"
        or response.request_id != request.request_id
        or response.embedding.dimension != profile.dimension
        or response.model_provenance != profile.model_provenance
    ):
        raise JobExecutionError(
            "embedding_profile_mismatch",
            "embedding result does not match the recognition profile",
            retryable=False,
        )

    subject_values = _validate_embedding_target(connection, request)
    connection.execute(
        postgresql_insert(embeddings)
        .values(
            id=response.embedding.embedding_id,
            purpose="recognition",
            target_fingerprint=response.input_fingerprint,
            model_id=response.model_provenance.model_id,
            model_version=response.model_provenance.model_version,
            artifact_sha256=response.model_provenance.artifact_sha256,
            configuration=response.configuration.model_dump(mode="json"),
            dimension=response.embedding.dimension,
            embedding=[float(value) for value in response.embedding.values],
            **subject_values,
        )
        .on_conflict_do_nothing()
    )


def _current_annotation_query(
    connection: Connection,
    annotation_id: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        select(
            annotation_records.c.id,
            annotation_records.c.image_id,
            annotation_records.c.current_revision.label("revision"),
            annotation_revisions.c.x,
            annotation_revisions.c.y,
            annotation_revisions.c.width,
            annotation_revisions.c.height,
            annotation_revisions.c.class_type,
            annotation_revisions.c.lifecycle_state,
            annotation_revisions.c.review_state,
            images.c.content_sha256,
            images.c.canonical_width,
            images.c.canonical_height,
        )
        .join(
            annotation_revisions,
            and_(
                annotation_revisions.c.annotation_id == annotation_records.c.id,
                annotation_revisions.c.revision == annotation_records.c.current_revision,
            ),
        )
        .join(images, images.c.id == annotation_records.c.image_id)
        .where(annotation_records.c.id == annotation_id)
    ).mappings().one_or_none()
    if row is None:
        raise AnnotationNotFoundError("annotation does not exist")
    return dict(row)


def _validate_query_annotation(row: Mapping[str, Any], expected_revision: int) -> None:
    if row["revision"] != expected_revision:
        raise StaleAnnotationRevisionError("annotation revision is stale")
    if row["class_type"] != "product":
        raise InvalidAnnotationStateError("only product annotations can be recognized")
    if (
        row["lifecycle_state"] != "verified"
        or row["review_state"] != "accepted"
    ):
        raise InvalidAnnotationStateError(
            "annotation must be accepted before recognition"
        )


def _annotation_embedding_payload(
    row: Mapping[str, Any],
    profile: RecognitionProfile,
) -> dict[str, Any]:
    annotation_id = UUID(str(row["id"]))
    revision = int(row["revision"])
    return {
        "request_id": _request_id("annotation", annotation_id, revision, profile),
        "operation": "embed",
        "image": {
            "image_id": str(row["image_id"]),
            "sha256": row["content_sha256"],
            "width": row["canonical_width"],
            "height": row["canonical_height"],
        },
        "model_role": "recognition_embedder",
        "crop": {
            "type": "axis_aligned_box",
            "x": row["x"],
            "y": row["y"],
            "width": row["width"],
            "height": row["height"],
        },
        "purpose": "recognition",
        "target": {
            "type": "annotation",
            "annotation_id": str(annotation_id),
            "annotation_revision": revision,
        },
        "configuration": {"normalize": True},
    }


def _reference_embedding_payload(
    row: Mapping[str, Any],
    profile: RecognitionProfile,
) -> dict[str, Any]:
    reference_id = UUID(str(row["id"]))
    return {
        "request_id": _request_id("reference", reference_id, profile),
        "operation": "embed",
        "image": {
            "image_id": str(reference_id),
            "sha256": row["content_sha256"],
            "width": row["width"],
            "height": row["height"],
        },
        "model_role": "recognition_embedder",
        "crop": {
            "type": "axis_aligned_box",
            "x": 0,
            "y": 0,
            "width": row["width"],
            "height": row["height"],
        },
        "purpose": "recognition",
        "target": {
            "type": "sku_reference",
            "reference_image_id": str(reference_id),
        },
        "configuration": {"normalize": True},
    }


def _validate_embedding_target(
    connection: Connection,
    request: EmbedRequest,
) -> dict[str, Any]:
    target = request.target
    if isinstance(target, AnnotationEmbeddingTarget):
        row = _current_annotation_query(connection, target.annotation_id)
        try:
            _validate_query_annotation(row, target.annotation_revision)
        except (StaleAnnotationRevisionError, InvalidAnnotationStateError) as error:
            raise JobExecutionError(
                "stale_embedding_target",
                "annotation changed before embedding was stored",
                retryable=False,
            ) from error
        expected_crop = (
            float(row["x"]),
            float(row["y"]),
            float(row["width"]),
            float(row["height"]),
        )
        request_crop = (
            request.crop.x,
            request.crop.y,
            request.crop.width,
            request.crop.height,
        )
        if request.image.image_id != row["image_id"] or request_crop != expected_crop:
            raise JobExecutionError(
                "stale_embedding_target",
                "annotation changed before embedding was stored",
                retryable=False,
            )
        return {
            "subject_type": "annotation",
            "annotation_id": target.annotation_id,
            "annotation_revision": target.annotation_revision,
            "reference_image_id": None,
        }
    if isinstance(target, SkuReferenceEmbeddingTarget):
        reference = connection.execute(
            select(
                sku_reference_images,
                skus.c.status.label("sku_status"),
                skus.c.is_unknown,
            )
            .join(skus, skus.c.id == sku_reference_images.c.sku_id)
            .where(sku_reference_images.c.id == target.reference_image_id)
        ).mappings().one_or_none()
        if reference is None:
            raise JobExecutionError(
                "stale_embedding_target",
                "gallery reference no longer exists",
                retryable=False,
            )
        expected_crop = (
            0.0,
            0.0,
            float(reference["width"]),
            float(reference["height"]),
        )
        request_crop = (
            request.crop.x,
            request.crop.y,
            request.crop.width,
            request.crop.height,
        )
        if (
            reference["sku_status"] != "active"
            or reference["is_unknown"]
            or request.image.image_id != target.reference_image_id
            or request_crop != expected_crop
        ):
            raise JobExecutionError(
                "stale_embedding_target",
                "gallery reference changed before embedding was stored",
                retryable=False,
            )
        return {
            "subject_type": "sku_reference",
            "annotation_id": None,
            "annotation_revision": None,
            "reference_image_id": target.reference_image_id,
        }
    raise JobExecutionError(
        "invalid_embedding_target",
        "embedding target is required",
        retryable=False,
    )


def _require_current_gallery(
    connection: Connection,
    profile: RecognitionProfile,
) -> None:
    active_count = connection.execute(
        select(func.count())
        .select_from(sku_reference_images)
        .join(skus, skus.c.id == sku_reference_images.c.sku_id)
        .where(
            skus.c.status == "active",
            skus.c.is_unknown.is_(False),
        )
    ).scalar_one()
    current_count = connection.execute(
        select(func.count(distinct(embeddings.c.reference_image_id)))
        .select_from(embeddings)
        .join(
            sku_reference_images,
            sku_reference_images.c.id == embeddings.c.reference_image_id,
        )
        .join(skus, skus.c.id == sku_reference_images.c.sku_id)
        .where(
            embeddings.c.subject_type == "sku_reference",
            embeddings.c.purpose == "recognition",
            skus.c.status == "active",
            skus.c.is_unknown.is_(False),
            *_profile_conditions(profile),
        )
    ).scalar_one()
    if active_count == 0 or current_count != active_count:
        raise RecognitionGalleryNotCurrentError(
            "recognition gallery embeddings are not current"
        )


def _rank_gallery(
    connection: Connection,
    query_vector: list[float],
    top_k: int,
    profile: RecognitionProfile,
) -> list[dict[str, Any]]:
    similarity = (1 - embeddings.c.embedding.cosine_distance(query_vector)).label(
        "score"
    )
    reference_scores = (
        select(
            sku_reference_images.c.sku_id,
            similarity,
        )
        .select_from(
            embeddings.join(
                sku_reference_images,
                sku_reference_images.c.id == embeddings.c.reference_image_id,
            ).join(skus, skus.c.id == sku_reference_images.c.sku_id)
        )
        .where(
            embeddings.c.subject_type == "sku_reference",
            embeddings.c.purpose == "recognition",
            skus.c.status == "active",
            skus.c.is_unknown.is_(False),
            *_profile_conditions(profile),
        )
        .subquery()
    )
    best_score = func.max(reference_scores.c.score).label("score")
    rows = connection.execute(
        select(
            skus.c.id,
            skus.c.name,
            skus.c.brand,
            skus.c.variant,
            best_score,
        )
        .join(reference_scores, reference_scores.c.sku_id == skus.c.id)
        .group_by(skus.c.id, skus.c.name, skus.c.brand, skus.c.variant)
        .order_by(best_score.desc(), skus.c.id)
        .limit(top_k)
    ).mappings()
    return [
        {
            "sku_id": row["id"],
            "name": row["name"],
            "brand": row["brand"],
            "variant": row["variant"],
            "score": max(-1.0, min(1.0, float(row["score"]))),
        }
        for row in rows
    ]


def _profile_conditions(profile: RecognitionProfile) -> tuple[Any, ...]:
    provenance = profile.model_provenance
    return (
        embeddings.c.model_id == provenance.model_id,
        embeddings.c.model_version == provenance.model_version,
        embeddings.c.artifact_sha256 == provenance.artifact_sha256,
        embeddings.c.dimension == profile.dimension,
    )


def _annotation_idempotency_key(
    annotation_id: UUID,
    revision: int,
    profile: RecognitionProfile,
) -> str:
    return (
        f"recognition:annotation:{annotation_id}:r{revision}:"
        f"{profile.index_version}"
    )


def _reference_idempotency_key(
    reference_id: UUID,
    profile: RecognitionProfile,
) -> str:
    return f"recognition:reference:{reference_id}:{profile.index_version}"


def _request_id(
    subject_type: str,
    subject_id: UUID,
    *parts: object,
) -> str:
    identity = ":".join(str(part) for part in parts)
    value = f"shelfsight:recognition:{subject_type}:{subject_id}:{identity}"
    return f"embed:{uuid5(NAMESPACE_URL, value)}"
