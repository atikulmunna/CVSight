from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from shelfsight_api.data_model import DatasetSnapshotNotFoundError
from shelfsight_api.models import dataset_snapshots, job_attempts, jobs, worker_heartbeats

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
WorkerState = Literal["idle", "running", "stopping"]


class JobNotFoundError(ValueError):
    """Raised when a job does not exist."""


class JobIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused for different work."""


class JobClaimLostError(RuntimeError):
    """Raised when a worker no longer owns a claimed job."""


class InvalidJobProgressError(ValueError):
    """Raised when job progress is inconsistent."""


@dataclass(frozen=True)
class JobClaim:
    id: UUID
    job_type: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    worker_id: str


def enqueue_job(
    connection: Connection,
    job_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    *,
    dataset_version_id: UUID | None = None,
    max_attempts: int = 3,
    progress_total: int | None = None,
) -> tuple[dict[str, Any], bool]:
    if dataset_version_id is not None and connection.execute(
        select(dataset_snapshots.c.dataset_version_id).where(
            dataset_snapshots.c.dataset_version_id == dataset_version_id
        )
    ).scalar_one_or_none() is None:
        raise DatasetSnapshotNotFoundError("dataset snapshot does not exist")
    job_id = uuid4()
    statement = (
        postgresql_insert(jobs)
        .values(
            id=job_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
            payload=dict(payload),
            dataset_version_id=dataset_version_id,
            max_attempts=max_attempts,
            progress_total=progress_total,
            available_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            index_elements=[jobs.c.job_type, jobs.c.idempotency_key]
        )
        .returning(*jobs.c)
    )
    created = connection.execute(statement).mappings().one_or_none()
    if created is not None:
        return dict(created), True

    existing = connection.execute(
        select(jobs).where(
            jobs.c.job_type == job_type,
            jobs.c.idempotency_key == idempotency_key,
        )
    ).mappings().one()
    if (
        existing["payload"] != dict(payload)
        or existing["dataset_version_id"] != dataset_version_id
        or existing["max_attempts"] != max_attempts
        or existing["progress_total"] != progress_total
    ):
        raise JobIdempotencyConflictError(
            "idempotency key is already bound to different work"
        )
    return dict(existing), False


def get_job(connection: Connection, job_id: UUID) -> dict[str, Any]:
    row = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one_or_none()
    if row is None:
        raise JobNotFoundError("job does not exist")
    return dict(row)


def list_job_attempts(connection: Connection, job_id: UUID) -> list[dict[str, Any]]:
    get_job(connection, job_id)
    rows = connection.execute(
        select(job_attempts)
        .where(job_attempts.c.job_id == job_id)
        .order_by(job_attempts.c.attempt)
    ).mappings()
    return [dict(row) for row in rows]


def cancel_job(connection: Connection, job_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        select(jobs).where(jobs.c.id == job_id).with_for_update()
    ).mappings().one_or_none()
    if row is None:
        raise JobNotFoundError("job does not exist")
    if row["state"] in {"succeeded", "failed", "cancelled"}:
        return dict(row)

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "cancellation_requested": True,
        "updated_at": now,
    }
    if row["state"] == "queued":
        values.update(
            state="cancelled",
            claimed_by=None,
            lease_expires_at=None,
            completed_at=now,
        )
    cancelled = connection.execute(
        update(jobs).where(jobs.c.id == job_id).values(**values).returning(*jobs.c)
    ).mappings().one()
    return dict(cancelled)


def claim_next_job(
    connection: Connection,
    worker_id: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
    job_types: Collection[str] | None = None,
) -> JobClaim | None:
    claimed_at = now or datetime.now(UTC)
    conditions = [
        jobs.c.state == "queued",
        jobs.c.available_at <= claimed_at,
    ]
    if job_types is not None:
        if not job_types:
            return None
        conditions.append(jobs.c.job_type.in_(job_types))
    candidate = connection.execute(
        select(jobs)
        .where(*conditions)
        .order_by(jobs.c.created_at, jobs.c.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if candidate is None:
        return None

    attempt = int(candidate["attempt_count"]) + 1
    updated = connection.execute(
        update(jobs)
        .where(jobs.c.id == candidate["id"])
        .values(
            state="running",
            attempt_count=attempt,
            claimed_by=worker_id,
            lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
            started_at=candidate["started_at"] or claimed_at,
            updated_at=claimed_at,
        )
        .returning(*jobs.c)
    ).mappings().one()
    connection.execute(
        insert(job_attempts).values(
            job_id=updated["id"],
            attempt=attempt,
            worker_id=worker_id,
            state="running",
            started_at=claimed_at,
        )
    )
    return JobClaim(
        id=updated["id"],
        job_type=str(updated["job_type"]),
        idempotency_key=str(updated["idempotency_key"]),
        payload=dict(updated["payload"]),
        attempt=attempt,
        max_attempts=int(updated["max_attempts"]),
        worker_id=worker_id,
    )


def update_job_progress(
    connection: Connection,
    claim: JobClaim,
    current: int,
    total: int | None,
    *,
    lease_seconds: int,
) -> None:
    if current < 0 or total is not None and (total <= 0 or current > total):
        raise InvalidJobProgressError("job progress is invalid")
    row = _lock_claim(connection, claim)
    effective_total = total if total is not None else row["progress_total"]
    if effective_total is not None and current > effective_total:
        raise InvalidJobProgressError("job progress exceeds its total")
    now = datetime.now(UTC)
    connection.execute(
        update(jobs)
        .where(jobs.c.id == claim.id)
        .values(
            progress_current=current,
            progress_total=effective_total,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )


def job_cancellation_requested(connection: Connection, claim: JobClaim) -> bool:
    row = _lock_claim(connection, claim)
    return bool(row["cancellation_requested"])


def renew_job_lease(
    connection: Connection,
    claim: JobClaim,
    *,
    lease_seconds: int,
) -> None:
    _lock_claim(connection, claim)
    now = datetime.now(UTC)
    connection.execute(
        update(jobs)
        .where(jobs.c.id == claim.id)
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )


def prepare_job_completion(connection: Connection, claim: JobClaim) -> bool:
    row = _lock_claim(connection, claim)
    if not row["cancellation_requested"]:
        return True
    _finish_cancelled(connection, claim, datetime.now(UTC))
    return False


def complete_job(
    connection: Connection,
    claim: JobClaim,
    result: Mapping[str, Any],
) -> None:
    row = _lock_claim(connection, claim)
    now = datetime.now(UTC)
    connection.execute(
        update(jobs)
        .where(jobs.c.id == claim.id)
        .values(
            state="succeeded",
            result=dict(result),
            progress_current=row["progress_total"] or row["progress_current"],
            claimed_by=None,
            lease_expires_at=None,
            error_code=None,
            error_summary=None,
            updated_at=now,
            completed_at=now,
        )
    )
    _finish_attempt(connection, claim, "succeeded", now)


def fail_job(
    connection: Connection,
    claim: JobClaim,
    error_code: str,
    error_summary: str,
    *,
    retryable: bool,
    retry_delay_seconds: int | None = None,
) -> JobState:
    row = _lock_claim(connection, claim)
    now = datetime.now(UTC)
    safe_code = error_code[:64]
    safe_summary = error_summary[:500]
    if row["cancellation_requested"]:
        _finish_cancelled(connection, claim, now)
        return "cancelled"

    should_retry = retryable and claim.attempt < claim.max_attempts
    if should_retry:
        delay = retry_delay_seconds
        if delay is None:
            delay = min(2 ** (claim.attempt - 1), 300)
        connection.execute(
            update(jobs)
            .where(jobs.c.id == claim.id)
            .values(
                state="queued",
                available_at=now + timedelta(seconds=delay),
                claimed_by=None,
                lease_expires_at=None,
                error_code=safe_code,
                error_summary=safe_summary,
                updated_at=now,
            )
        )
        _finish_attempt(
            connection,
            claim,
            "retry",
            now,
            safe_code,
            safe_summary,
        )
        return "queued"

    connection.execute(
        update(jobs)
        .where(jobs.c.id == claim.id)
        .values(
            state="failed",
            claimed_by=None,
            lease_expires_at=None,
            error_code=safe_code,
            error_summary=safe_summary,
            updated_at=now,
            completed_at=now,
        )
    )
    _finish_attempt(
        connection,
        claim,
        "failed",
        now,
        safe_code,
        safe_summary,
    )
    return "failed"


def recover_expired_jobs(
    connection: Connection,
    *,
    now: datetime | None = None,
) -> int:
    recovered_at = now or datetime.now(UTC)
    expired = connection.execute(
        select(jobs)
        .where(
            jobs.c.state == "running",
            jobs.c.lease_expires_at < recovered_at,
        )
        .order_by(jobs.c.lease_expires_at, jobs.c.id)
        .with_for_update(skip_locked=True)
    ).mappings().all()
    recovered = 0
    for row in expired:
        recovered += 1
        claim = _claim_from_row(dict(row))
        if row["cancellation_requested"]:
            _finish_cancelled(connection, claim, recovered_at)
            continue
        terminal = int(row["attempt_count"]) >= int(row["max_attempts"])
        connection.execute(
            update(jobs)
            .where(jobs.c.id == row["id"])
            .values(
                state="failed" if terminal else "queued",
                available_at=recovered_at,
                claimed_by=None,
                lease_expires_at=None,
                error_code="worker_lease_expired",
                error_summary="worker stopped before completing the job",
                updated_at=recovered_at,
                completed_at=recovered_at if terminal else None,
            )
        )
        _finish_attempt(
            connection,
            claim,
            "failed" if terminal else "abandoned",
            recovered_at,
            "worker_lease_expired",
            "worker stopped before completing the job",
        )
    return recovered


def record_worker_heartbeat(
    connection: Connection,
    worker_id: str,
    state: WorkerState,
    current_job_id: UUID | None = None,
) -> None:
    now = datetime.now(UTC)
    statement = (
        postgresql_insert(worker_heartbeats)
        .values(
            worker_id=worker_id,
            state=state,
            current_job_id=current_job_id,
            started_at=now,
            heartbeat_at=now,
        )
        .on_conflict_do_update(
            index_elements=[worker_heartbeats.c.worker_id],
            set_={
                "state": state,
                "current_job_id": current_job_id,
                "heartbeat_at": now,
            },
        )
    )
    connection.execute(statement)


def register_worker(connection: Connection, worker_id: str) -> None:
    now = datetime.now(UTC)
    statement = (
        postgresql_insert(worker_heartbeats)
        .values(
            worker_id=worker_id,
            state="idle",
            current_job_id=None,
            started_at=now,
            heartbeat_at=now,
        )
        .on_conflict_do_update(
            index_elements=[worker_heartbeats.c.worker_id],
            set_={
                "state": "idle",
                "current_job_id": None,
                "started_at": now,
                "heartbeat_at": now,
            },
        )
    )
    connection.execute(statement)


def list_worker_heartbeats(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(worker_heartbeats).order_by(worker_heartbeats.c.worker_id)
    ).mappings()
    return [dict(row) for row in rows]


def _lock_claim(connection: Connection, claim: JobClaim) -> Mapping[str, Any]:
    row = connection.execute(
        select(jobs)
        .where(
            jobs.c.id == claim.id,
            jobs.c.state == "running",
            jobs.c.claimed_by == claim.worker_id,
            jobs.c.attempt_count == claim.attempt,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if row is None:
        raise JobClaimLostError("worker no longer owns this job claim")
    return dict(row)


def _claim_from_row(row: Mapping[str, Any]) -> JobClaim:
    return JobClaim(
        id=row["id"],
        job_type=str(row["job_type"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=dict(row["payload"]),
        attempt=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        worker_id=str(row["claimed_by"]),
    )


def _finish_cancelled(
    connection: Connection,
    claim: JobClaim,
    finished_at: datetime,
) -> None:
    connection.execute(
        update(jobs)
        .where(jobs.c.id == claim.id)
        .values(
            state="cancelled",
            cancellation_requested=True,
            claimed_by=None,
            lease_expires_at=None,
            updated_at=finished_at,
            completed_at=finished_at,
        )
    )
    _finish_attempt(connection, claim, "cancelled", finished_at)


def _finish_attempt(
    connection: Connection,
    claim: JobClaim,
    state: str,
    finished_at: datetime,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> None:
    connection.execute(
        update(job_attempts)
        .where(
            job_attempts.c.job_id == claim.id,
            job_attempts.c.attempt == claim.attempt,
        )
        .values(
            state=state,
            finished_at=finished_at,
            error_code=error_code,
            error_summary=error_summary,
        )
    )
