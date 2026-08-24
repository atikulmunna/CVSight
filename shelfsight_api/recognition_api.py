from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, FiniteFloat, PositiveInt, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.annotation_service import InvalidAnnotationStateError
from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import OwnerUser
from shelfsight_api.config import ConfigurationError
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
)
from shelfsight_api.database import get_engine
from shelfsight_api.recognition_service import (
    MAX_GALLERY_EMBEDDING_BATCH,
    GalleryQueueOutcome,
    RecognitionEmbeddingNotReadyError,
    RecognitionGalleryNotCurrentError,
    StaleRecognitionEmbeddingError,
    enqueue_annotation_recognition_embedding,
    enqueue_gallery_recognition_embeddings,
    get_annotation_sku_candidates,
)

router = APIRouter(prefix="/api")


class AnnotationEmbeddingRequest(BaseModel):
    expected_revision: PositiveInt


class EmbeddingJobResponse(BaseModel):
    job_id: UUID
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    deduplicated: bool


class GalleryEmbeddingRequest(BaseModel):
    reference_image_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_GALLERY_EMBEDDING_BATCH,
    )

    @field_validator("reference_image_ids")
    @classmethod
    def require_unique_references(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("reference_image_ids must be unique")
        return value


class GalleryEmbeddingItemResponse(BaseModel):
    reference_image_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


class GalleryEmbeddingResponse(BaseModel):
    items: list[GalleryEmbeddingItemResponse]


class RecognitionIndexResponse(BaseModel):
    namespace: str
    version: str
    purpose: Literal["recognition"]


class SkuCandidateResponse(BaseModel):
    sku_id: UUID
    name: str
    brand: str | None
    variant: str | None
    score: Annotated[FiniteFloat, Field(ge=-1, le=1)]


class UnknownDecisionResponse(BaseModel):
    is_unknown: bool
    threshold: Annotated[FiniteFloat, Field(ge=-1, le=1)]
    calibration_version: str
    automatic_confirmation: Literal[False]


class SkuCandidatesResponse(BaseModel):
    annotation_id: UUID
    annotation_revision: int
    embedding_id: UUID
    index: RecognitionIndexResponse
    candidates: list[SkuCandidateResponse]
    unknown_decision: UnknownDecisionResponse


@router.post(
    "/annotations/{annotation_id}/recognition-embedding",
    response_model=EmbeddingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_annotation_embedding(
    annotation_id: UUID,
    request: AnnotationEmbeddingRequest,
    response: Response,
) -> EmbeddingJobResponse:
    try:
        with get_engine().begin() as connection:
            job, created = enqueue_annotation_recognition_embedding(
                connection,
                annotation_id,
                request.expected_revision,
            )
        if not created:
            response.status_code = status.HTTP_200_OK
        return EmbeddingJobResponse(
            job_id=job["id"],
            state=job["state"],
            deduplicated=not created,
        )
    except Exception as error:
        _raise_recognition_error(error)
        raise


@router.post(
    "/recognition/gallery-embeddings",
    response_model=GalleryEmbeddingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_gallery_embeddings(
    request: GalleryEmbeddingRequest,
    user: OwnerUser,
) -> GalleryEmbeddingResponse:
    del user
    try:
        with get_engine().begin() as connection:
            outcomes = enqueue_gallery_recognition_embeddings(
                connection,
                request.reference_image_ids,
            )
        return GalleryEmbeddingResponse(
            items=[
                GalleryEmbeddingItemResponse.model_validate(_outcome_value(outcome))
                for outcome in outcomes
            ]
        )
    except (ConfigurationError, IntegrityError, SQLAlchemyError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recognition_service_unavailable",
            "recognition service is unavailable",
        ) from error


@router.get(
    "/annotations/{annotation_id}/sku-candidates",
    response_model=SkuCandidatesResponse,
)
def sku_candidates(
    annotation_id: UUID,
    expected_revision: PositiveInt,
    top_k: Annotated[int, Field(ge=1, le=9)] = 5,
) -> SkuCandidatesResponse:
    try:
        with get_engine().connect() as connection:
            result = get_annotation_sku_candidates(
                connection,
                annotation_id,
                expected_revision,
                top_k,
            )
        return SkuCandidatesResponse.model_validate(result)
    except Exception as error:
        _raise_recognition_error(error)
        raise


def _outcome_value(outcome: GalleryQueueOutcome) -> dict[str, object]:
    return {
        "reference_image_id": outcome.reference_image_id,
        "status": outcome.status,
        "job_id": outcome.job_id,
        "job_state": outcome.job_state,
        "error_code": outcome.error_code,
    }


def _raise_recognition_error(error: Exception) -> None:
    if isinstance(error, AnnotationNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "annotation_not_found",
            "annotation does not exist",
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
    if isinstance(error, RecognitionEmbeddingNotReadyError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "embedding_not_ready",
            "annotation recognition embedding is not ready",
        ) from error
    if isinstance(error, StaleRecognitionEmbeddingError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "stale_embedding",
            "annotation recognition embedding is stale",
        ) from error
    if isinstance(error, RecognitionGalleryNotCurrentError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "gallery_not_current",
            "recognition gallery embeddings are not current",
        ) from error
    if isinstance(error, (ConfigurationError, IntegrityError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recognition_service_unavailable",
            "recognition service is unavailable",
        ) from error
