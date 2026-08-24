from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, PositiveInt, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.annotation_service import InvalidAnnotationStateError
from shelfsight_api.api_errors import api_error
from shelfsight_api.config import ConfigurationError
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
)
from shelfsight_api.database import get_engine
from shelfsight_api.job_service import JobIdempotencyConflictError
from shelfsight_api.model_contract import DetectConfiguration
from shelfsight_api.prelabel_service import (
    MAX_PRELABEL_BATCH,
    BatchQueueOutcome,
    RefinementImageNotFoundError,
    enqueue_annotation_refinement,
    enqueue_detection_batch,
)

router = APIRouter(prefix="/api")

IdempotencyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    ),
]


class BatchPrelabelRequest(BaseModel):
    image_ids: list[UUID] = Field(min_length=1, max_length=MAX_PRELABEL_BATCH)
    idempotency_key: IdempotencyKey
    configuration: DetectConfiguration = Field(default_factory=DetectConfiguration)

    @field_validator("image_ids")
    @classmethod
    def require_unique_images(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("image_ids must be unique")
        return value


class BatchPrelabelItemResponse(BaseModel):
    image_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


class BatchPrelabelResponse(BaseModel):
    items: list[BatchPrelabelItemResponse]


class RefineAnnotationRequest(BaseModel):
    expected_revision: PositiveInt
    idempotency_key: IdempotencyKey


class PrelabelJobResponse(BaseModel):
    job_id: UUID
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    deduplicated: bool


@router.post(
    "/prelabels/batch",
    response_model=BatchPrelabelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def batch_prelabel(request: BatchPrelabelRequest) -> BatchPrelabelResponse:
    try:
        with get_engine().begin() as connection:
            outcomes = enqueue_detection_batch(
                connection,
                request.image_ids,
                request.idempotency_key,
                request.configuration,
            )
        return BatchPrelabelResponse(
            items=[
                BatchPrelabelItemResponse.model_validate(_outcome_value(outcome))
                for outcome in outcomes
            ]
        )
    except (ConfigurationError, IntegrityError, SQLAlchemyError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "prelabel_service_unavailable",
            "pre-label service is unavailable",
        ) from error


@router.post(
    "/annotations/{annotation_id}/refine",
    response_model=PrelabelJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def refine_annotation(
    annotation_id: UUID,
    request: RefineAnnotationRequest,
    response: Response,
) -> PrelabelJobResponse:
    try:
        with get_engine().begin() as connection:
            job, created = enqueue_annotation_refinement(
                connection,
                annotation_id,
                request.expected_revision,
                request.idempotency_key,
            )
        if not created:
            response.status_code = status.HTTP_200_OK
        return PrelabelJobResponse(
            job_id=job["id"],
            state=job["state"],
            deduplicated=not created,
        )
    except Exception as error:
        _raise_refinement_error(error)
        raise


def _outcome_value(outcome: BatchQueueOutcome) -> dict[str, object]:
    return {
        "image_id": outcome.image_id,
        "status": outcome.status,
        "job_id": outcome.job_id,
        "job_state": outcome.job_state,
        "error_code": outcome.error_code,
    }


def _raise_refinement_error(error: Exception) -> None:
    if isinstance(error, AnnotationNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "annotation_not_found",
            "annotation does not exist",
        ) from error
    if isinstance(error, RefinementImageNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "image_not_found",
            "annotation image does not exist",
        ) from error
    if isinstance(error, StaleAnnotationRevisionError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "stale_revision",
            "annotation revision is stale",
        ) from error
    if isinstance(error, InvalidAnnotationStateError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_annotation_state",
            str(error),
        ) from error
    if isinstance(error, JobIdempotencyConflictError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "idempotency key is already bound to different work",
        ) from error
    if isinstance(error, (ConfigurationError, IntegrityError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "prelabel_service_unavailable",
            "pre-label service is unavailable",
        ) from error
