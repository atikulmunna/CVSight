from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, FiniteFloat, PositiveInt
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.annotation_service import (
    AnnotationImageNotFoundError,
    InvalidAnnotationGeometryError,
    InvalidAnnotationSkuError,
    InvalidAnnotationStateError,
    accept_image_annotation,
    assign_annotation_sku,
    create_image_annotation,
    get_current_annotation,
    list_current_annotations,
    reject_image_annotation,
    restore_image_annotation,
    update_image_annotation,
)
from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import AnnotationActor
from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    StaleAnnotationRevisionError,
)
from shelfsight_api.database import get_engine

router = APIRouter(prefix="/api")

MAX_BATCH_OPERATIONS = 250


class AnnotationFields(BaseModel):
    x: Annotated[FiniteFloat, Field(ge=0)]
    y: Annotated[FiniteFloat, Field(ge=0)]
    width: Annotated[FiniteFloat, Field(gt=0)]
    height: Annotated[FiniteFloat, Field(gt=0)]
    class_type: Literal["product", "gap", "shelf_label"] = "product"
    sku_id: UUID | None = None
    review_state: Literal["unreviewed", "accepted", "flagged"] = "unreviewed"
    confidence: Annotated[FiniteFloat, Field(ge=0, le=1)] | None = None
    occluded: bool = False
    truncated: bool = False
    shelf_row: Annotated[int, Field(ge=0)] | None = None


class CreateAnnotationRequest(AnnotationFields):
    lifecycle_state: Literal["proposed", "verified"] = "proposed"


class UpdateAnnotationRequest(AnnotationFields):
    expected_revision: PositiveInt


class RevisionActionRequest(BaseModel):
    expected_revision: PositiveInt


class AssignSkuRequest(RevisionActionRequest):
    sku_id: UUID


class RejectAnnotationRequest(RevisionActionRequest):
    reason: str | None = Field(default=None, max_length=500)


class BatchUpdateOperation(UpdateAnnotationRequest):
    operation: Literal["update"]
    annotation_id: UUID


class BatchRejectOperation(RejectAnnotationRequest):
    operation: Literal["reject"]
    annotation_id: UUID


class BatchRestoreOperation(RevisionActionRequest):
    operation: Literal["restore"]
    annotation_id: UUID


BatchOperation = Annotated[
    BatchUpdateOperation | BatchRejectOperation | BatchRestoreOperation,
    Field(discriminator="operation"),
]


class BatchAnnotationRequest(BaseModel):
    operations: list[BatchOperation] = Field(
        min_length=1,
        max_length=MAX_BATCH_OPERATIONS,
    )


class AnnotationResponse(BaseModel):
    id: UUID
    image_id: UUID
    revision: int
    x: float
    y: float
    width: float
    height: float
    class_type: Literal["product", "gap", "shelf_label"]
    sku_id: UUID | None
    lifecycle_state: Literal["proposed", "verified", "rejected"]
    review_state: Literal["unreviewed", "accepted", "flagged"]
    source: Literal["model", "human", "propagated", "imported"]
    provenance: dict[str, Any]
    confidence: float | None
    occluded: bool
    truncated: bool
    shelf_row: int | None
    created_at: datetime
    updated_at: datetime
    revision_created_at: datetime


class AnnotationListResponse(BaseModel):
    annotations: list[AnnotationResponse]


class BatchAnnotationResponse(BaseModel):
    annotations: list[AnnotationResponse]


@router.get(
    "/images/{image_id}/annotations",
    response_model=AnnotationListResponse,
)
def image_annotations(image_id: UUID) -> AnnotationListResponse:
    try:
        with get_engine().connect() as connection:
            rows = list_current_annotations(connection, image_id)
        return AnnotationListResponse(
            annotations=[AnnotationResponse.model_validate(row) for row in rows]
        )
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.get("/annotations/{annotation_id}", response_model=AnnotationResponse)
def annotation(annotation_id: UUID) -> AnnotationResponse:
    try:
        with get_engine().connect() as connection:
            row = get_current_annotation(connection, annotation_id)
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post(
    "/images/{image_id}/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation(
    image_id: UUID,
    request: CreateAnnotationRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        with get_engine().begin() as connection:
            row = create_image_annotation(
                connection,
                image_id,
                request.model_dump(),
                actor,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.put("/annotations/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(
    annotation_id: UUID,
    request: UpdateAnnotationRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        values = request.model_dump(exclude={"expected_revision"})
        with get_engine().begin() as connection:
            row = update_image_annotation(
                connection,
                annotation_id,
                request.expected_revision,
                values,
                actor,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post(
    "/annotations/{annotation_id}/accept",
    response_model=AnnotationResponse,
)
def accept_annotation(
    annotation_id: UUID,
    request: RevisionActionRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        with get_engine().begin() as connection:
            row = accept_image_annotation(
                connection,
                annotation_id,
                request.expected_revision,
                actor,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post(
    "/annotations/{annotation_id}/assign-sku",
    response_model=AnnotationResponse,
)
def assign_sku(
    annotation_id: UUID,
    request: AssignSkuRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        with get_engine().begin() as connection:
            row = assign_annotation_sku(
                connection,
                annotation_id,
                request.expected_revision,
                request.sku_id,
                actor,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post(
    "/annotations/{annotation_id}/reject",
    response_model=AnnotationResponse,
)
def reject_annotation(
    annotation_id: UUID,
    request: RejectAnnotationRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        with get_engine().begin() as connection:
            row = reject_image_annotation(
                connection,
                annotation_id,
                request.expected_revision,
                actor,
                request.reason,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post(
    "/annotations/{annotation_id}/restore",
    response_model=AnnotationResponse,
)
def restore_annotation(
    annotation_id: UUID,
    request: RevisionActionRequest,
    actor: AnnotationActor,
) -> AnnotationResponse:
    try:
        with get_engine().begin() as connection:
            row = restore_image_annotation(
                connection,
                annotation_id,
                request.expected_revision,
                actor,
            )
        return AnnotationResponse.model_validate(row)
    except Exception as error:
        _raise_annotation_error(error)
        raise


@router.post("/annotations/batch", response_model=BatchAnnotationResponse)
def batch_annotations(
    request: BatchAnnotationRequest,
    actor: AnnotationActor,
) -> BatchAnnotationResponse:
    try:
        rows: list[dict[str, Any]] = []
        with get_engine().begin() as connection:
            for operation in request.operations:
                if isinstance(operation, BatchUpdateOperation):
                    values = operation.model_dump(
                        exclude={"operation", "annotation_id", "expected_revision"}
                    )
                    row = update_image_annotation(
                        connection,
                        operation.annotation_id,
                        operation.expected_revision,
                        values,
                        actor,
                    )
                elif isinstance(operation, BatchRejectOperation):
                    row = reject_image_annotation(
                        connection,
                        operation.annotation_id,
                        operation.expected_revision,
                        actor,
                        operation.reason,
                    )
                else:
                    row = restore_image_annotation(
                        connection,
                        operation.annotation_id,
                        operation.expected_revision,
                        actor,
                    )
                rows.append(row)
        return BatchAnnotationResponse(
            annotations=[AnnotationResponse.model_validate(row) for row in rows]
        )
    except Exception as error:
        _raise_annotation_error(error)
        raise


def _raise_annotation_error(error: Exception) -> None:
    if isinstance(error, StaleAnnotationRevisionError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "stale_revision",
            "annotation changed after the expected revision",
        ) from error
    if isinstance(error, InvalidAnnotationStateError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_state_transition",
            str(error),
        ) from error
    if isinstance(error, AnnotationNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "annotation_not_found",
            "annotation does not exist",
        ) from error
    if isinstance(error, AnnotationImageNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "image_not_found",
            "image does not exist",
        ) from error
    if isinstance(error, InvalidAnnotationGeometryError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_geometry",
            str(error),
        ) from error
    if isinstance(error, InvalidAnnotationSkuError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_sku",
            str(error),
        ) from error
    if isinstance(error, IntegrityError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "annotation_constraint_violation",
            "annotation violates a data constraint",
        ) from error
    if isinstance(error, SQLAlchemyError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            "annotation service is unavailable",
        ) from error
