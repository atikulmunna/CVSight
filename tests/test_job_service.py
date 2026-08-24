from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, delete, func, insert, select, update

from shelfsight_api.job_service import (
    JobClaim,
    JobIdempotencyConflictError,
    cancel_job,
    claim_next_job,
    enqueue_job,
    fail_job,
    get_job,
    list_job_attempts,
    recover_expired_jobs,
)
from shelfsight_api.models import datasets, jobs, worker_heartbeats
from shelfsight_api.worker import JobDefinition, JobReporter, JobWorker


@pytest.fixture(autouse=True)
def clean_jobs(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
    yield
    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))


def test_enqueue_is_idempotent_and_rejects_key_reuse(
    database_engine: Engine,
) -> None:
    with database_engine.begin() as connection:
        first, created = enqueue_job(
            connection,
            "detect",
            "image:one",
            {"image_id": "one"},
            progress_total=10,
        )
        repeated, repeated_created = enqueue_job(
            connection,
            "detect",
            "image:one",
            {"image_id": "one"},
            progress_total=10,
        )
        with pytest.raises(JobIdempotencyConflictError):
            enqueue_job(
                connection,
                "detect",
                "image:one",
                {"image_id": "different"},
                progress_total=10,
            )

    assert created is True
    assert repeated_created is False
    assert repeated["id"] == first["id"]


def test_concurrent_workers_skip_locked_jobs(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        enqueue_job(connection, "detect", "first", {"index": 1})
        enqueue_job(connection, "detect", "second", {"index": 2})

    first_connection = database_engine.connect()
    second_connection = database_engine.connect()
    first_transaction = first_connection.begin()
    second_transaction = second_connection.begin()
    try:
        first = claim_next_job(first_connection, "worker-one", lease_seconds=30)
        second = claim_next_job(second_connection, "worker-two", lease_seconds=30)

        assert first is not None
        assert second is not None
        assert second.id != first.id
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_connection.close()
        first_connection.close()


def test_retries_are_bounded_and_attempt_errors_remain_visible(
    database_engine: Engine,
) -> None:
    with database_engine.begin() as connection:
        row, _ = enqueue_job(
            connection,
            "detect",
            "bounded",
            {"image_id": "one"},
            max_attempts=2,
        )
    job_id = UUID(str(row["id"]))

    with database_engine.begin() as connection:
        first = claim_next_job(connection, "worker-one", lease_seconds=30)
        assert first is not None
        assert (
            fail_job(
                connection,
                first,
                "model_busy",
                "model is temporarily unavailable",
                retryable=True,
                retry_delay_seconds=0,
            )
            == "queued"
        )
    with database_engine.begin() as connection:
        second = claim_next_job(connection, "worker-two", lease_seconds=30)
        assert second is not None
        assert (
            fail_job(
                connection,
                second,
                "model_busy",
                "model is temporarily unavailable",
                retryable=True,
            )
            == "failed"
        )

    with database_engine.connect() as connection:
        final = get_job(connection, job_id)
        attempts = list_job_attempts(connection, job_id)
    assert final["state"] == "failed"
    assert final["attempt_count"] == 2
    assert final["error_code"] == "model_busy"
    assert [attempt["state"] for attempt in attempts] == ["retry", "failed"]


def test_queued_and_running_jobs_can_be_cancelled(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        queued, _ = enqueue_job(connection, "detect", "queued-cancel", {})
        running, _ = enqueue_job(connection, "detect", "running-cancel", {})
        cancelled_queued = cancel_job(connection, queued["id"])
    assert cancelled_queued["state"] == "cancelled"

    with database_engine.begin() as connection:
        claim = claim_next_job(connection, "worker-one", lease_seconds=30)
        assert claim is not None
        assert claim.id == running["id"]
        requested = cancel_job(connection, claim.id)
        assert requested["state"] == "running"
        assert requested["cancellation_requested"] is True
        final_state = fail_job(
            connection,
            claim,
            "ignored",
            "ignored",
            retryable=True,
        )
    assert final_state == "cancelled"


def test_expired_worker_claims_recover_or_fail_at_the_attempt_limit(
    database_engine: Engine,
) -> None:
    baseline = datetime.now(UTC) + timedelta(seconds=1)
    with database_engine.begin() as connection:
        recoverable, _ = enqueue_job(
            connection,
            "detect",
            "recoverable",
            {},
            max_attempts=2,
        )
        terminal, _ = enqueue_job(
            connection,
            "detect",
            "terminal",
            {},
            max_attempts=1,
        )
        first = claim_next_job(
            connection,
            "worker-one",
            lease_seconds=1,
            now=baseline,
        )
        second = claim_next_job(
            connection,
            "worker-two",
            lease_seconds=1,
            now=baseline,
        )
        assert first is not None
        assert second is not None

    with database_engine.begin() as connection:
        assert recover_expired_jobs(
            connection,
            now=baseline + timedelta(seconds=2),
        ) == 2

    with database_engine.connect() as connection:
        recovered = get_job(connection, recoverable["id"])
        failed = get_job(connection, terminal["id"])
    states = {recovered["id"]: recovered["state"], failed["id"]: failed["state"]}
    assert sorted(states.values()) == ["failed", "queued"]
    assert recovered["error_code"] == "worker_lease_expired"
    assert failed["error_code"] == "worker_lease_expired"


def test_result_writer_and_success_commit_atomically_across_retry(
    database_engine: Engine,
) -> None:
    dataset_name = f"job-effect-{uuid4()}"
    writer_calls = 0

    def run(_claim: JobClaim, reporter: JobReporter) -> dict[str, int]:
        reporter.progress(1, 1)
        return {"written": 1}

    def write_result(
        connection: Connection,
        _claim: JobClaim,
        _result: Mapping[str, Any],
    ) -> None:
        nonlocal writer_calls
        writer_calls += 1
        connection.execute(insert(datasets).values(name=dataset_name))
        if writer_calls == 1:
            raise RuntimeError("simulated transaction loss")

    with database_engine.begin() as connection:
        row, _ = enqueue_job(
            connection,
            "test_write",
            "atomic-result",
            {},
            max_attempts=2,
            progress_total=1,
        )
    job_id = row["id"]
    worker = JobWorker(
        database_engine,
        "worker-atomic",
        {
            "test_write": JobDefinition(
                run=run,
                write_result=write_result,
            )
        },
    )

    assert worker.run_once() is True
    with database_engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert worker.run_once() is True

    with database_engine.connect() as connection:
        final = get_job(connection, job_id)
        effect_count = connection.execute(
            select(func.count()).select_from(datasets).where(datasets.c.name == dataset_name)
        ).scalar_one()
    assert final["state"] == "succeeded"
    assert final["attempt_count"] == 2
    assert effect_count == 1

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.name == dataset_name))


def test_unsupported_job_type_fails_once_without_leaking_details(
    database_engine: Engine,
) -> None:
    with database_engine.begin() as connection:
        row, _ = enqueue_job(connection, "not_registered", "invalid", {})
    worker = JobWorker(database_engine, "worker-invalid", {})

    assert worker.run_once() is True

    with database_engine.connect() as connection:
        final = get_job(connection, row["id"])
    assert final["state"] == "failed"
    assert final["attempt_count"] == 1
    assert final["error_code"] == "unsupported_job_type"
    assert final["error_summary"] == "job type is not registered"


def test_capability_filtered_worker_leaves_other_job_types_queued(
    database_engine: Engine,
) -> None:
    with database_engine.begin() as connection:
        row, _ = enqueue_job(connection, "refine", "other-capability", {})
    worker = JobWorker(
        database_engine,
        "detector-only",
        {"detect": JobDefinition(run=lambda _claim, _reporter: {})},
        claim_registered_only=True,
    )

    assert worker.run_once() is False

    with database_engine.connect() as connection:
        queued = get_job(connection, row["id"])
    assert queued["state"] == "queued"
    assert queued["attempt_count"] == 0
