from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import sleep
from typing import Any, Protocol

from sqlalchemy import Connection, Engine

from shelfsight_api.database import get_engine
from shelfsight_api.job_service import (
    JobClaim,
    JobClaimLostError,
    claim_next_job,
    complete_job,
    fail_job,
    job_cancellation_requested,
    prepare_job_completion,
    record_worker_heartbeat,
    recover_expired_jobs,
    register_worker,
    renew_job_lease,
    update_job_progress,
)

MAX_RESULT_BYTES = 256 * 1024
logger = logging.getLogger(__name__)


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, summary: str, *, retryable: bool) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary
        self.retryable = retryable


class JobReporter:
    def __init__(self, engine: Engine, claim: JobClaim, lease_seconds: int) -> None:
        self._engine = engine
        self._claim = claim
        self._lease_seconds = lease_seconds

    def progress(self, current: int, total: int | None = None) -> None:
        with self._engine.begin() as connection:
            update_job_progress(
                connection,
                self._claim,
                current,
                total,
                lease_seconds=self._lease_seconds,
            )
            record_worker_heartbeat(
                connection,
                self._claim.worker_id,
                "running",
                self._claim.id,
            )

    def cancellation_requested(self) -> bool:
        with self._engine.begin() as connection:
            return job_cancellation_requested(connection, self._claim)

    def heartbeat(self) -> None:
        with self._engine.begin() as connection:
            renew_job_lease(
                connection,
                self._claim,
                lease_seconds=self._lease_seconds,
            )
            record_worker_heartbeat(
                connection,
                self._claim.worker_id,
                "running",
                self._claim.id,
            )


class JobHandler(Protocol):
    def __call__(
        self,
        claim: JobClaim,
        reporter: JobReporter,
    ) -> Mapping[str, Any]: ...


class JobResultWriter(Protocol):
    def __call__(
        self,
        connection: Connection,
        claim: JobClaim,
        result: Mapping[str, Any],
    ) -> None: ...


@dataclass(frozen=True)
class JobDefinition:
    run: JobHandler
    write_result: JobResultWriter | None = None


class _LeaseKeeper:
    def __init__(self, reporter: JobReporter, lease_seconds: int) -> None:
        self._reporter = reporter
        self._interval = max(0.1, lease_seconds / 3)
        self._stopped = threading.Event()
        self._claim_lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def claim_lost(self) -> bool:
        return self._claim_lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self._reporter.heartbeat()
            except JobClaimLostError:
                self._claim_lost.set()
                return
            except Exception:
                logger.warning("Job lease heartbeat failed")
                self._claim_lost.set()
                return


class JobWorker:
    def __init__(
        self,
        engine: Engine,
        worker_id: str,
        definitions: Mapping[str, JobDefinition],
        *,
        lease_seconds: int = 60,
        claim_registered_only: bool = False,
    ) -> None:
        self._engine = engine
        self._worker_id = worker_id
        self._definitions = definitions
        self._lease_seconds = lease_seconds
        self._claim_registered_only = claim_registered_only

    def run_once(self) -> bool:
        with self._engine.begin() as connection:
            recover_expired_jobs(connection)
            claim = claim_next_job(
                connection,
                self._worker_id,
                lease_seconds=self._lease_seconds,
                job_types=(
                    self._definitions.keys()
                    if self._claim_registered_only
                    else None
                ),
            )
            record_worker_heartbeat(
                connection,
                self._worker_id,
                "running" if claim else "idle",
                claim.id if claim else None,
            )
        if claim is None:
            return False

        definition = self._definitions.get(claim.job_type)
        if definition is None:
            self._fail(
                claim,
                JobExecutionError(
                    "unsupported_job_type",
                    "job type is not registered",
                    retryable=False,
                ),
            )
            return True

        reporter = JobReporter(self._engine, claim, self._lease_seconds)
        lease_keeper = _LeaseKeeper(reporter, self._lease_seconds)
        lease_keeper.start()
        try:
            result = dict(definition.run(claim, reporter))
            if lease_keeper.claim_lost:
                raise JobClaimLostError("worker no longer owns this job claim")
            _validate_result(result)
            with self._engine.begin() as connection:
                if prepare_job_completion(connection, claim):
                    if definition.write_result is not None:
                        definition.write_result(connection, claim, result)
                    complete_job(connection, claim, result)
        except JobClaimLostError:
            pass
        except JobExecutionError as error:
            self._fail(claim, error)
        except Exception:
            self._fail(
                claim,
                JobExecutionError(
                    "job_execution_failed",
                    "job execution failed",
                    retryable=True,
                ),
            )
        finally:
            lease_keeper.stop()
            with self._engine.begin() as connection:
                record_worker_heartbeat(connection, self._worker_id, "idle")
        return True

    def start(self) -> None:
        with self._engine.begin() as connection:
            register_worker(connection, self._worker_id)

    def stop(self) -> None:
        with self._engine.begin() as connection:
            record_worker_heartbeat(connection, self._worker_id, "stopping")

    def _fail(self, claim: JobClaim, error: JobExecutionError) -> None:
        with self._engine.begin() as connection:
            fail_job(
                connection,
                claim,
                error.code,
                error.summary,
                retryable=error.retryable,
            )


def run_worker(
    worker: JobWorker,
    should_stop: Callable[[], bool],
    *,
    poll_seconds: float = 0.5,
) -> None:
    worker.start()
    try:
        while not should_stop():
            if not worker.run_once():
                sleep(poll_seconds)
    finally:
        worker.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ShelfSight job worker")
    parser.add_argument("--worker-id", required=True)
    arguments = parser.parse_args()
    stopped = threading.Event()

    def request_stop(_signal_number: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    from shelfsight_api.model_runtime import configured_model_job_definitions

    worker = JobWorker(
        get_engine(),
        arguments.worker_id,
        configured_model_job_definitions(),
        claim_registered_only=True,
    )
    run_worker(worker, stopped.is_set)


def _validate_result(result: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(
            result,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise JobExecutionError(
            "invalid_job_result",
            "job returned an invalid result",
            retryable=False,
        ) from error
    if len(encoded) > MAX_RESULT_BYTES:
        raise JobExecutionError(
            "job_result_too_large",
            "job result exceeds the size limit",
            retryable=False,
        )


if __name__ == "__main__":
    main()
