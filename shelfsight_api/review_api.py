from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, PositiveInt, field_validator
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.annotation_api import AnnotationResponse
from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import ReviewerActor
from shelfsight_api.data_model import StaleAnnotationRevisionError
from shelfsight_api.database import get_engine
from shelfsight_api.review_service import (
    ReviewAnnotationNotFoundError,
    ReviewSignoffBlockedError,
    ReviewVersionFrozenError,
    ReviewVersionNotFoundError,
    list_review_queue,
    list_sku_lineup,
    record_review_decision,
    sign_off_dataset_version,
)
from shelfsight_api.sku_api import ReferenceImageResponse, reference_response
from shelfsight_api.sku_service import SkuNotFoundError

router = APIRouter(prefix="/api")


class ReviewRiskReasonResponse(BaseModel):
    code: Literal[
        "annotation_flagged",
        "hard_pair_confusion",
        "consistency_conflict",
        "low_agreement",
        "propagated_origin",
        "unknown_status",
        "manual_flag",
    ]
    label: str
    weight: int
    blocking: Literal[True]


class ReviewDecisionRecordResponse(BaseModel):
    id: UUID
    dataset_version_id: UUID
    annotation_id: UUID
    annotation_revision: int
    decision: Literal["approved", "flagged"]
    risk_reasons: list[str]
    reviewer: str
    note: str | None
    created_at: datetime


class ReviewItemResponse(BaseModel):
    annotation_id: UUID
    annotation_revision: int
    image_id: UUID
    image_name: str
    image_url: str
    class_type: Literal["product", "gap", "shelf_label"]
    sku_id: UUID | None
    review_state: Literal["unreviewed", "accepted", "flagged"]
    source: Literal["model", "human", "propagated", "imported"]
    risk_score: int
    risk_reasons: list[ReviewRiskReasonResponse]
    resolved: bool
    decision: ReviewDecisionRecordResponse | None


class ReviewSignoffResponse(BaseModel):
    dataset_version_id: UUID
    signed_by: str
    signed_at: datetime
    reviewed_annotation_count: int
    risk_item_count: int


class ReviewQueueResponse(BaseModel):
    dataset_version_id: UUID
    status: Literal["open", "signed"]
    total_risk_items: int
    unresolved_count: int
    resolved_count: int
    items: list[ReviewItemResponse]
    signoff: ReviewSignoffResponse | None


class ReviewDecisionRequest(BaseModel):
    expected_revision: PositiveInt
    decision: Literal["approve", "flag"]
    note: Annotated[str | None, Field(max_length=500)] = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ReviewDecisionResponse(BaseModel):
    annotation: AnnotationResponse
    decision: ReviewDecisionRecordResponse


class SkuLineupSkuResponse(BaseModel):
    id: UUID
    name: str
    reference_images: list[ReferenceImageResponse]


class SkuLineupItemResponse(BaseModel):
    annotation_id: UUID
    revision: int
    image_id: UUID
    image_name: str
    crop_url: str


class SkuLineupResponse(BaseModel):
    dataset_version_id: UUID
    sku: SkuLineupSkuResponse
    items: list[SkuLineupItemResponse]
    # True when the SKU has more accepted boxes than the lineup shows.
    truncated: bool


@router.get(
    "/dataset-versions/{dataset_version_id}/review-queue",
    response_model=ReviewQueueResponse,
)
def review_queue(
    dataset_version_id: UUID,
    include_resolved: Annotated[bool, Query()] = False,
) -> ReviewQueueResponse:
    try:
        with get_engine().connect() as connection:
            result = list_review_queue(
                connection,
                dataset_version_id,
                include_resolved=include_resolved,
            )
        return ReviewQueueResponse.model_validate(result)
    except Exception as error:
        _raise_review_error(error)
        raise


@router.get(
    "/dataset-versions/{dataset_version_id}/sku-lineup",
    response_model=SkuLineupResponse,
)
def sku_lineup(
    dataset_version_id: UUID,
    sku_id: Annotated[UUID, Query()],
) -> SkuLineupResponse:
    try:
        with get_engine().connect() as connection:
            result = list_sku_lineup(connection, dataset_version_id, sku_id)
    except Exception as error:
        _raise_review_error(error)
        raise
    sku = result["sku"]
    return SkuLineupResponse(
        dataset_version_id=result["dataset_version_id"],
        sku=SkuLineupSkuResponse(
            id=sku["id"],
            name=sku["name"],
            reference_images=[reference_response(row) for row in sku["reference_images"]],
        ),
        items=[SkuLineupItemResponse.model_validate(item) for item in result["items"]],
        truncated=result["truncated"],
    )


@router.post(
    "/dataset-versions/{dataset_version_id}/review-items/{annotation_id}/decision",
    response_model=ReviewDecisionResponse,
)
def review_decision(
    dataset_version_id: UUID,
    annotation_id: UUID,
    request: ReviewDecisionRequest,
    actor: ReviewerActor,
) -> ReviewDecisionResponse:
    try:
        with get_engine().begin() as connection:
            result = record_review_decision(
                connection,
                dataset_version_id,
                annotation_id,
                request.expected_revision,
                request.decision,
                actor,
                request.note,
            )
        return ReviewDecisionResponse.model_validate(result)
    except Exception as error:
        _raise_review_error(error)
        raise


@router.post(
    "/dataset-versions/{dataset_version_id}/review-signoff",
    response_model=ReviewSignoffResponse,
)
def review_signoff(
    dataset_version_id: UUID,
    actor: ReviewerActor,
) -> ReviewSignoffResponse:
    try:
        with get_engine().begin() as connection:
            result = sign_off_dataset_version(connection, dataset_version_id, actor)
        return ReviewSignoffResponse.model_validate(result)
    except Exception as error:
        _raise_review_error(error)
        raise


def _raise_review_error(error: Exception) -> None:
    if isinstance(error, ReviewVersionNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_version_not_found",
            "dataset version does not exist",
        ) from error
    if isinstance(error, SkuNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "sku_not_found",
            "SKU does not exist",
        ) from error
    if isinstance(error, ReviewAnnotationNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "review_item_not_found",
            "review item does not exist in this dataset version",
        ) from error
    if isinstance(error, StaleAnnotationRevisionError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "stale_revision",
            "annotation changed after the review item was loaded",
        ) from error
    if isinstance(error, ReviewVersionFrozenError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "review_version_frozen",
            str(error),
        ) from error
    if isinstance(error, ReviewSignoffBlockedError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "review_signoff_blocked",
            "resolve all blocking review items before sign-off",
        ) from error
    if isinstance(error, SQLAlchemyError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "review_service_unavailable",
            "review service is unavailable",
        ) from error
