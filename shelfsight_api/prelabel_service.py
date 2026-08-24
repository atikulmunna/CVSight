from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Connection, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from shelfsight_api.annotation_service import (
    InvalidAnnotationStateError,
    get_current_annotation,
)
from shelfsight_api.data_model import StaleAnnotationRevisionError
from shelfsight_api.job_service import (
    JobClaim,
    JobIdempotencyConflictError,
    enqueue_job,
)
from shelfsight_api.model_contract import (
    DetectConfiguration,
    DetectRequest,
    DetectResponse,
    RefineConfiguration,
    parse_model_request,
    parse_model_response,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    images,
    skus,
)
from shelfsight_api.worker import JobExecutionError

MAX_PRELABEL_BATCH = 250


@dataclass(frozen=True)
class BatchQueueOutcome:
    image_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


class RefinementImageNotFoundError(ValueError):
    """Raised when the annotation's canonical image is unavailable."""


def enqueue_detection_batch(
    connection: Connection,
    image_ids: Sequence[UUID],
    idempotency_key: str,
    configuration: DetectConfiguration,
) -> list[BatchQueueOutcome]:
    image_rows = connection.execute(
        select(
            images.c.id,
            images.c.content_sha256,
            images.c.canonical_width,
            images.c.canonical_height,
        ).where(images.c.id.in_(image_ids))
    ).mappings()
    images_by_id = {row["id"]: dict(row) for row in image_rows}
    outcomes: list[BatchQueueOutcome] = []

    for image_id in image_ids:
        image = images_by_id.get(image_id)
        if image is None:
            outcomes.append(
                BatchQueueOutcome(
                    image_id=image_id,
                    status="rejected",
                    error_code="image_not_found",
                )
            )
            continue

        payload = _detect_payload(image, idempotency_key, configuration)
        try:
            job, created = enqueue_job(
                connection,
                "detect",
                _image_idempotency_key(idempotency_key, image_id),
                payload,
                max_attempts=3,
            )
        except JobIdempotencyConflictError:
            outcomes.append(
                BatchQueueOutcome(
                    image_id=image_id,
                    status="rejected",
                    error_code="idempotency_conflict",
                )
            )
            continue
        outcomes.append(
            BatchQueueOutcome(
                image_id=image_id,
                status="queued" if created else "deduplicated",
                job_id=job["id"],
                job_state=str(job["state"]),
            )
        )
    return outcomes


def enqueue_annotation_refinement(
    connection: Connection,
    annotation_id: UUID,
    expected_revision: int,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    annotation = get_current_annotation(connection, annotation_id)
    if annotation["revision"] != expected_revision:
        raise StaleAnnotationRevisionError("annotation revision is stale")
    if annotation["class_type"] != "product":
        raise InvalidAnnotationStateError("only product boxes can be refined")
    if annotation["lifecycle_state"] == "rejected":
        raise InvalidAnnotationStateError("rejected annotation must be restored before refining")

    image = connection.execute(
        select(
            images.c.id,
            images.c.content_sha256,
            images.c.canonical_width,
            images.c.canonical_height,
        ).where(images.c.id == annotation["image_id"])
    ).mappings().one_or_none()
    if image is None:
        raise RefinementImageNotFoundError("annotation image does not exist")

    request_id = _request_id(
        "refine",
        idempotency_key,
        annotation_id,
        expected_revision,
    )
    payload = {
        "request_id": request_id,
        "operation": "refine",
        "image": _image_input(dict(image)),
        "model_role": "box_refiner",
        "box_hint": {
            "type": "axis_aligned_box",
            "x": annotation["x"],
            "y": annotation["y"],
            "width": annotation["width"],
            "height": annotation["height"],
        },
        "configuration": RefineConfiguration().model_dump(mode="json"),
    }
    return enqueue_job(
        connection,
        "refine",
        idempotency_key,
        payload,
        max_attempts=2,
    )


def write_detection_proposals(
    connection: Connection,
    claim: JobClaim,
    result: Mapping[str, Any],
) -> None:
    try:
        request = parse_model_request(claim.payload)
        response = parse_model_response(result)
    except ValidationError as error:
        raise JobExecutionError(
            "invalid_proposal_result",
            "detector result cannot be stored",
            retryable=False,
        ) from error
    if not isinstance(request, DetectRequest) or not isinstance(response, DetectResponse):
        raise JobExecutionError(
            "invalid_proposal_result",
            "detector result cannot be stored",
            retryable=False,
        )
    if response.request_id != request.request_id:
        raise JobExecutionError(
            "invalid_proposal_result",
            "detector result cannot be stored",
            retryable=False,
        )

    _validate_candidate_skus(connection, response)
    created_count = 0
    for prediction in response.predictions:
        fingerprint = _proposal_fingerprint(request, response, prediction.model_dump(mode="json"))
        annotation_id = uuid5(NAMESPACE_URL, f"shelfsight:proposal:{fingerprint}")
        created = connection.execute(
            postgresql_insert(annotation_records)
            .values(
                id=annotation_id,
                image_id=request.image.image_id,
                current_revision=1,
            )
            .on_conflict_do_nothing(index_elements=[annotation_records.c.id])
            .returning(annotation_records.c.id)
        ).scalar_one_or_none()
        if created is None:
            continue

        connection.execute(
            insert(annotation_revisions).values(
                annotation_id=annotation_id,
                revision=1,
                x=prediction.geometry.x,
                y=prediction.geometry.y,
                width=prediction.geometry.width,
                height=prediction.geometry.height,
                class_type=prediction.class_type,
                sku_id=prediction.candidate_sku_id,
                lifecycle_state="proposed",
                review_state="unreviewed",
                source="model",
                provenance={
                    "operation": "detect",
                    "job_id": str(claim.id),
                    "request_id": response.request_id,
                    "proposal_id": prediction.proposal_id,
                    "proposal_fingerprint": fingerprint,
                    "input_fingerprint": response.input_fingerprint,
                    "model": response.model_provenance.model_dump(mode="json"),
                    "configuration": response.configuration.model_dump(mode="json"),
                },
                confidence=prediction.score,
                occluded=False,
                truncated=False,
                shelf_row=None,
            )
        )
        created_count += 1

    if created_count:
        connection.execute(
            update(images)
            .where(
                images.c.id == request.image.image_id,
                images.c.status == "unlabeled",
            )
            .values(status="pre_labeled")
        )


def _detect_payload(
    image: Mapping[str, Any],
    idempotency_key: str,
    configuration: DetectConfiguration,
) -> dict[str, Any]:
    image_id = image["id"]
    return {
        "request_id": _request_id("detect", idempotency_key, image_id),
        "operation": "detect",
        "image": _image_input(image),
        "model_role": "known_sku_detector",
        "configuration": configuration.model_dump(mode="json"),
    }


def _image_input(image: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "image_id": str(image["id"]),
        "sha256": image["content_sha256"],
        "width": image["canonical_width"],
        "height": image["canonical_height"],
    }


def _image_idempotency_key(prefix: str, image_id: UUID) -> str:
    return f"{prefix}:{image_id}"


def _request_id(operation: str, *parts: object) -> str:
    identity = ":".join(str(part) for part in parts)
    return f"{operation}:{uuid5(NAMESPACE_URL, f'shelfsight:{operation}:{identity}')}"


def _validate_candidate_skus(
    connection: Connection,
    response: DetectResponse,
) -> None:
    candidate_ids = {
        prediction.candidate_sku_id
        for prediction in response.predictions
        if prediction.candidate_sku_id is not None
    }
    if not candidate_ids:
        return
    active_ids = set(
        connection.execute(
            select(skus.c.id).where(
                skus.c.id.in_(candidate_ids),
                skus.c.status == "active",
            )
        ).scalars()
    )
    if active_ids != candidate_ids:
        raise JobExecutionError(
            "invalid_proposal_sku",
            "detector returned an unavailable SKU candidate",
            retryable=False,
        )


def _proposal_fingerprint(
    request: DetectRequest,
    response: DetectResponse,
    prediction: Mapping[str, Any],
) -> str:
    value = {
        "image_id": str(request.image.image_id),
        "input_fingerprint": response.input_fingerprint,
        "model": response.model_provenance.model_dump(mode="json"),
        "configuration": response.configuration.model_dump(mode="json"),
        "geometry": prediction["geometry"],
        "class_type": prediction["class_type"],
        "candidate_sku_id": prediction["candidate_sku_id"],
    }
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
