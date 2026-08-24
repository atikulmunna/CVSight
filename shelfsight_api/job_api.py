from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.config import ConfigurationError
from shelfsight_api.data_model import DatasetSnapshotNotFoundError
from shelfsight_api.database import get_engine
from shelfsight_api.job_service import (
    JobIdempotencyConflictError,
    JobNotFoundError,
    cancel_job,
    enqueue_job,
    get_job,
    list_job_attempts,
    list_worker_heartbeats,
)

router = APIRouter(prefix="/api")
MAX_PAYLOAD_BYTES = 64 * 1024
WORKER_STALE_AFTER = timedelta(seconds=30)


class CreateJobRequest(BaseModel):
    job_type: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    ]
    idempotency_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
        ),
    ]
    payload: dict[str, Any]
    dataset_version_id: UUID | None = None
    max_attempts: Annotated[int, Field(ge=1, le=20)] = 3
    progress_total: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def validate_payload_size(self) -> CreateJobRequest:
        try:
            encoded = json.dumps(
                self.payload,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("payload must contain finite JSON values") from error
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the size limit")
        return self


class JobAttemptResponse(BaseModel):
    attempt: int
    worker_id: str
    state: Literal[
        "running",
        "retry",
        "succeeded",
        "failed",
        "cancelled",
        "abandoned",
    ]
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None
    error_summary: str | None


class JobResponse(BaseModel):
    id: UUID
    job_type: str
    idempotency_key: str
    payload: dict[str, Any]
    dataset_version_id: UUID | None
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress_current: int
    progress_total: int | None
    attempt_count: int
    max_attempts: int
    cancellation_requested: bool
    result: dict[str, Any] | None
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deduplicated: bool = False
    attempts: list[JobAttemptResponse] = Field(default_factory=list)


class WorkerResponse(BaseModel):
    worker_id: str
    state: Literal["idle", "running", "stopping"]
    current_job_id: UUID | None
    started_at: datetime
    heartbeat_at: datetime
    stale: bool


class WorkerHealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    workers: list[WorkerResponse]


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_job(request: CreateJobRequest, response: Response) -> JobResponse:
    try:
        with get_engine().begin() as connection:
            row, created = enqueue_job(
                connection,
                request.job_type,
                request.idempotency_key,
                request.payload,
                dataset_version_id=request.dataset_version_id,
                max_attempts=request.max_attempts,
                progress_total=request.progress_total,
            )
        if not created:
            response.status_code = status.HTTP_200_OK
        row["deduplicated"] = not created
        return JobResponse.model_validate(row)
    except JobIdempotencyConflictError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "idempotency key is already bound to different work",
        ) from error
    except DatasetSnapshotNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_snapshot_not_found",
            "dataset snapshot does not exist",
        ) from error
    except (IntegrityError, SQLAlchemyError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "job_service_unavailable",
            "job service is unavailable",
        ) from error


@router.get("/jobs/{job_id}", response_model=JobResponse)
def job(job_id: UUID) -> JobResponse:
    try:
        with get_engine().connect() as connection:
            row = get_job(connection, job_id)
            row["attempts"] = list_job_attempts(connection, job_id)
        return JobResponse.model_validate(row)
    except Exception as error:
        _raise_job_error(error)
        raise


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel(job_id: UUID) -> JobResponse:
    try:
        with get_engine().begin() as connection:
            row = cancel_job(connection, job_id)
        return JobResponse.model_validate(row)
    except Exception as error:
        _raise_job_error(error)
        raise


@router.get(
    "/workers/health",
    response_model=WorkerHealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": WorkerHealthResponse},
    },
)
def worker_health(response: Response) -> WorkerHealthResponse:
    try:
        with get_engine().connect() as connection:
            rows = list_worker_heartbeats(connection)
    except (ConfigurationError, SQLAlchemyError):
        rows = []
    now = datetime.now(UTC)
    workers = [
        WorkerResponse(
            **row,
            stale=now - row["heartbeat_at"] > WORKER_STALE_AFTER,
        )
        for row in rows
    ]
    available = any(
        not worker.stale and worker.state in {"idle", "running"}
        for worker in workers
    )
    if not available:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return WorkerHealthResponse(
        status="ok" if available else "unavailable",
        workers=workers,
    )


def _raise_job_error(error: Exception) -> None:
    if isinstance(error, JobNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "job_not_found",
            "job does not exist",
        ) from error
    if isinstance(error, (ConfigurationError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "job_service_unavailable",
            "job service is unavailable",
        ) from error
