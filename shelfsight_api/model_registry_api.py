from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import OwnerActor
from shelfsight_api.data_model import (
    DatasetSnapshotNotFoundError,
    SnapshotArtifactConflictError,
)
from shelfsight_api.database import get_engine
from shelfsight_api.job_api import JobResponse
from shelfsight_api.job_service import JobNotFoundError
from shelfsight_api.model_evaluation import (
    EvaluationNotReadyError,
    InvalidRuntimeUrlError,
    enqueue_detector_evaluation,
    register_evaluated_detector,
)
from shelfsight_api.model_registry_service import (
    ModelArtifactNotFoundError,
    ModelCompatibilityError,
    ModelDeploymentNotFoundError,
    ModelEntryNotFoundError,
    ModelLineageError,
    ModelRegistrationConflictError,
    ModelRollbackUnavailableError,
    StaleModelDeploymentError,
    get_model_deployment,
    list_model_candidates,
    promote_model,
    register_model_candidate,
    rollback_model,
)

router = APIRouter(prefix="/api")
ModelRole = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
class RegisterModelRequest(BaseModel):
    model_role: ModelRole
    model_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    model_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    model_artifact_id: UUID
    evaluation_artifact_id: UUID


class EvaluateModelRequest(BaseModel):
    dataset_version_id: UUID
    runtime_url: str = Field(min_length=1, max_length=2048)
    confidence_threshold: float = Field(default=0.3, ge=0, le=1)


class RegisterEvaluationRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    licenses_approved: bool


class EvaluationDefaultsResponse(BaseModel):
    runtime_url: str | None


class PromoteModelRequest(BaseModel):
    entry_id: UUID
    expected_active_id: UUID | None = None


class RollbackModelRequest(BaseModel):
    expected_active_id: UUID


class ModelEntryResponse(BaseModel):
    id: UUID
    model_role: str
    model_id: str
    model_version: str
    model_artifact_id: UUID
    evaluation_artifact_id: UUID
    model_artifact_key: str
    evaluation_artifact_key: str
    lineage: Literal["snapshot", "external"]
    source: str | None
    training_dataset_version_id: UUID | None
    evaluation_dataset_version_id: UUID
    model_artifact_sha256: str
    evaluation_artifact_sha256: str
    configuration: dict[str, Any]
    compatibility: dict[str, Any]
    metrics: dict[str, Any]
    registered_by: str
    registered_at: datetime
    deployment_status: Literal["candidate", "default", "previous"]


class ModelListResponse(BaseModel):
    models: list[ModelEntryResponse]


class ModelDeploymentResponse(BaseModel):
    model_role: str
    active: ModelEntryResponse
    previous: ModelEntryResponse | None
    updated_by: str
    updated_at: datetime


@router.post(
    "/model-registry/candidates",
    response_model=ModelEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_model_candidate(
    request: RegisterModelRequest,
    actor: OwnerActor,
) -> ModelEntryResponse:
    try:
        with get_engine().begin() as connection:
            row = register_model_candidate(
                connection,
                model_role=request.model_role,
                model_id=request.model_id,
                model_version=request.model_version,
                model_artifact_id=request.model_artifact_id,
                evaluation_artifact_id=request.evaluation_artifact_id,
                actor=actor,
            )
        return ModelEntryResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.get(
    "/model-registry/evaluations/defaults",
    response_model=EvaluationDefaultsResponse,
)
def model_evaluation_defaults() -> EvaluationDefaultsResponse:
    # The address the worker uses for pre-labels, offered as the form's starting value.
    runtime_url = os.environ.get("SHELFSIGHT_DETECTOR_RUNTIME_URL", "").strip()
    return EvaluationDefaultsResponse(runtime_url=runtime_url or None)


@router.post(
    "/model-registry/evaluations",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_model_evaluation(request: EvaluateModelRequest) -> JobResponse:
    """Queue a worker job that scores the served model on a signed-off release."""
    try:
        with get_engine().begin() as connection:
            row = enqueue_detector_evaluation(
                connection,
                request.dataset_version_id,
                request.runtime_url.strip(),
                request.confidence_threshold,
            )
        return JobResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.post(
    "/model-registry/evaluations/{job_id}/register",
    response_model=ModelEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_model_evaluation(
    job_id: UUID,
    request: RegisterEvaluationRequest,
    actor: OwnerActor,
) -> ModelEntryResponse:
    try:
        with get_engine().begin() as connection:
            row = register_evaluated_detector(
                connection, job_id, request.source, request.licenses_approved, actor
            )
        return ModelEntryResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.get("/model-registry", response_model=ModelListResponse)
def model_candidates(
    model_role: Annotated[
        str,
        Query(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    ],
) -> ModelListResponse:
    try:
        with get_engine().connect() as connection:
            rows = list_model_candidates(connection, model_role)
        return ModelListResponse(
            models=[ModelEntryResponse.model_validate(row) for row in rows]
        )
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.get(
    "/model-deployments/{model_role}",
    response_model=ModelDeploymentResponse,
)
def model_deployment(model_role: ModelRole) -> ModelDeploymentResponse:
    try:
        with get_engine().connect() as connection:
            row = get_model_deployment(connection, model_role)
        return ModelDeploymentResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.post(
    "/model-deployments/{model_role}/promote",
    response_model=ModelDeploymentResponse,
)
def promote_registered_model(
    model_role: ModelRole,
    request: PromoteModelRequest,
    actor: OwnerActor,
) -> ModelDeploymentResponse:
    try:
        with get_engine().begin() as connection:
            row = promote_model(
                connection,
                model_role,
                request.entry_id,
                request.expected_active_id,
                actor,
            )
        return ModelDeploymentResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


@router.post(
    "/model-deployments/{model_role}/rollback",
    response_model=ModelDeploymentResponse,
)
def rollback_registered_model(
    model_role: ModelRole,
    request: RollbackModelRequest,
    actor: OwnerActor,
) -> ModelDeploymentResponse:
    try:
        with get_engine().begin() as connection:
            row = rollback_model(
                connection,
                model_role,
                request.expected_active_id,
                actor,
            )
        return ModelDeploymentResponse.model_validate(row)
    except Exception as error:
        _raise_registry_error(error)
        raise


def _raise_registry_error(error: Exception) -> None:
    if isinstance(error, InvalidRuntimeUrlError):
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_runtime_url", str(error))
    if isinstance(error, DatasetSnapshotNotFoundError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_version_not_frozen",
            "only a signed-off release can be used for evaluation",
        )
    if isinstance(error, JobNotFoundError):
        raise api_error(status.HTTP_404_NOT_FOUND, "evaluation_not_found", str(error))
    if isinstance(error, EvaluationNotReadyError):
        raise api_error(status.HTTP_409_CONFLICT, "evaluation_not_ready", str(error))
    if isinstance(error, SnapshotArtifactConflictError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "model_registration_conflict",
            "model identity is already registered with different evidence",
        )
    if isinstance(error, ModelArtifactNotFoundError):
        raise api_error(status.HTTP_404_NOT_FOUND, "model_artifact_not_found", str(error))
    if isinstance(error, ModelEntryNotFoundError):
        raise api_error(status.HTTP_404_NOT_FOUND, "model_entry_not_found", str(error))
    if isinstance(error, ModelDeploymentNotFoundError):
        raise api_error(status.HTTP_404_NOT_FOUND, "model_deployment_not_found", str(error))
    if isinstance(error, ModelLineageError):
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_model_lineage", str(error))
    if isinstance(error, ModelCompatibilityError):
        raise api_error(status.HTTP_409_CONFLICT, "model_incompatible", str(error))
    if isinstance(error, ModelRollbackUnavailableError):
        raise api_error(status.HTTP_409_CONFLICT, "model_rollback_unavailable", str(error))
    if isinstance(error, StaleModelDeploymentError):
        raise api_error(status.HTTP_409_CONFLICT, "stale_model_deployment", str(error))
    if isinstance(error, (ModelRegistrationConflictError, IntegrityError)):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "model_registration_conflict",
            "model identity is already registered with different evidence",
        )
    if isinstance(error, SQLAlchemyError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "model_registry_unavailable",
            "model registry is unavailable",
        )
