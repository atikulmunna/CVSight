from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, PositiveInt
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.annotation_api import AnnotationResponse
from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import AnnotationActor
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
)
from shelfsight_api.database import get_engine
from shelfsight_api.propagation_service import (
    MAX_PROPAGATION_EMBEDDING_BATCH,
    InvalidPropagationDecisionError,
    InvalidPropagationSeedError,
    PropagationDecision,
    PropagationEmbeddingNotReadyError,
    PropagationIndexNotCurrentError,
    PropagationSuggestionSetNotFoundError,
    confirm_propagation_suggestions,
    create_propagation_suggestions,
    enqueue_propagation_embeddings,
)

router = APIRouter(prefix="/api")

class PropagationEmbeddingBatchRequest(BaseModel):
    annotation_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_PROPAGATION_EMBEDDING_BATCH,
    )


class PropagationQueueOutcomeResponse(BaseModel):
    annotation_id: UUID
    status: Literal["queued", "deduplicated", "rejected"]
    job_id: UUID | None = None
    job_state: str | None = None
    error_code: str | None = None


class PropagationEmbeddingBatchResponse(BaseModel):
    results: list[PropagationQueueOutcomeResponse]


class CreatePropagationSuggestionsRequest(BaseModel):
    expected_revision: PositiveInt
    top_k: Annotated[int, Field(ge=1, le=50)] = 18


class PropagationSkuResponse(BaseModel):
    sku_id: UUID
    name: str
    brand: str | None
    variant: str | None


class HardPairResponse(PropagationSkuResponse):
    reason: str


class PropagationIndexResponse(BaseModel):
    namespace: str
    version: str
    purpose: Literal["propagation"]
    quality_evidence: str


class PropagationCandidateResponse(BaseModel):
    suggestion_id: UUID
    annotation_id: UUID
    annotation_revision: int
    image_id: UUID
    image_url: str
    x: float
    y: float
    width: float
    height: float
    image_width: int
    image_height: int
    score: float
    selected: bool
    requires_individual_review: bool
    risk_reason: Literal["hard_pair"] | None


class PropagationSuggestionSetResponse(BaseModel):
    suggestion_set_id: UUID
    seed_annotation_id: UUID
    seed_annotation_revision: int
    seed_sku: PropagationSkuResponse
    index: PropagationIndexResponse
    hard_pairs: list[HardPairResponse]
    candidates: list[PropagationCandidateResponse]
    selected_count: int
    skipped_count: int
    automatic_confirmation: Literal[False]


class PropagationDecisionRequest(BaseModel):
    suggestion_id: UUID
    decision: Literal["confirm", "skip"]
    reviewed_individually: bool = False


class ConfirmPropagationRequest(BaseModel):
    decisions: list[PropagationDecisionRequest] = Field(min_length=1, max_length=50)


class ConfirmPropagationResponse(BaseModel):
    suggestion_set_id: UUID
    selected_count: int
    skipped_count: int
    annotations: list[AnnotationResponse]


@router.post(
    "/propagation/embeddings",
    response_model=PropagationEmbeddingBatchResponse,
)
def queue_propagation_embeddings(
    request: PropagationEmbeddingBatchRequest,
) -> PropagationEmbeddingBatchResponse:
    try:
        with get_engine().begin() as connection:
            outcomes = enqueue_propagation_embeddings(
                connection,
                request.annotation_ids,
            )
        return PropagationEmbeddingBatchResponse(
            results=[
                PropagationQueueOutcomeResponse(
                    annotation_id=outcome.annotation_id,
                    status=outcome.status,
                    job_id=outcome.job_id,
                    job_state=outcome.job_state,
                    error_code=outcome.error_code,
                )
                for outcome in outcomes
            ]
        )
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "propagation embedding queue is unavailable",
        ) from error


@router.post(
    "/annotations/{annotation_id}/propagation-suggestions",
    response_model=PropagationSuggestionSetResponse,
    status_code=status.HTTP_201_CREATED,
)
def propagation_suggestions(
    annotation_id: UUID,
    request: CreatePropagationSuggestionsRequest,
    actor: AnnotationActor,
) -> PropagationSuggestionSetResponse:
    try:
        with get_engine().begin() as connection:
            result = create_propagation_suggestions(
                connection,
                annotation_id,
                request.expected_revision,
                request.top_k,
                actor,
            )
        return PropagationSuggestionSetResponse.model_validate(result)
    except Exception as error:
        _raise_propagation_error(error)
        raise


@router.post(
    "/propagation/suggestion-sets/{suggestion_set_id}/confirm",
    response_model=ConfirmPropagationResponse,
)
def confirm_propagation(
    suggestion_set_id: UUID,
    request: ConfirmPropagationRequest,
    actor: AnnotationActor,
) -> ConfirmPropagationResponse:
    try:
        decisions = [
            PropagationDecision(
                suggestion_id=decision.suggestion_id,
                decision=decision.decision,
                reviewed_individually=decision.reviewed_individually,
            )
            for decision in request.decisions
        ]
        with get_engine().begin() as connection:
            result = confirm_propagation_suggestions(
                connection,
                suggestion_set_id,
                decisions,
                actor,
            )
        return ConfirmPropagationResponse.model_validate(result)
    except Exception as error:
        _raise_propagation_error(error)
        raise


def _raise_propagation_error(error: Exception) -> None:
    if isinstance(error, StaleAnnotationRevisionError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "stale_revision",
            "a propagation annotation changed after suggestions were created",
        ) from error
    if isinstance(error, InvalidPropagationSeedError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_propagation_seed",
            str(error),
        ) from error
    if isinstance(error, PropagationEmbeddingNotReadyError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "embedding_not_ready",
            str(error),
        ) from error
    if isinstance(error, PropagationIndexNotCurrentError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "propagation_index_not_current",
            str(error),
        ) from error
    if isinstance(error, InvalidPropagationDecisionError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_propagation_decision",
            str(error),
        ) from error
    if isinstance(error, PropagationSuggestionSetNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "suggestion_set_not_found",
            str(error),
        ) from error
    if isinstance(error, AnnotationNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "annotation_not_found",
            "annotation does not exist",
        ) from error
    if isinstance(error, SQLAlchemyError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "propagation service is unavailable",
        ) from error
