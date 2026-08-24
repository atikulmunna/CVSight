from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, update

from shelfsight_api.app import app
from shelfsight_api.data_model import snapshot_dataset_version
from shelfsight_api.job_service import record_worker_heartbeat
from shelfsight_api.models import dataset_versions, datasets, jobs, worker_heartbeats

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
    yield
    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))


def configure_job_api(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> None:
    monkeypatch.setattr("shelfsight_api.job_api.get_engine", lambda: database_engine)


def test_job_api_creates_deduplicates_reads_and_cancels(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_job_api(monkeypatch, database_engine)
    request = {
        "job_type": "detect",
        "idempotency_key": f"image:{uuid4()}",
        "payload": {"image_id": str(uuid4())},
        "progress_total": 4,
    }

    created = client.post("/api/jobs", json=request)
    repeated = client.post("/api/jobs", json=request)
    conflict = client.post(
        "/api/jobs",
        json={**request, "payload": {"image_id": str(uuid4())}},
    )
    loaded = client.get(f"/api/jobs/{created.json()['id']}")
    cancelled = client.post(f"/api/jobs/{created.json()['id']}/cancel")

    assert created.status_code == 201
    assert created.json()["deduplicated"] is False
    assert repeated.status_code == 200
    assert repeated.json()["deduplicated"] is True
    assert repeated.json()["id"] == created.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert loaded.status_code == 200
    assert loaded.json()["attempts"] == []
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


def test_job_api_rejects_invalid_payloads_and_missing_jobs(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_job_api(monkeypatch, database_engine)

    invalid_type = client.post(
        "/api/jobs",
        json={
            "job_type": "../detect",
            "idempotency_key": "valid",
            "payload": {},
        },
    )
    oversized = client.post(
        "/api/jobs",
        json={
            "job_type": "detect",
            "idempotency_key": "oversized",
            "payload": {"value": "x" * (65 * 1024)},
        },
    )
    missing = client.get(f"/api/jobs/{uuid4()}")

    assert invalid_type.status_code == 422
    assert oversized.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "job_not_found"


def test_job_api_links_work_to_an_immutable_snapshot(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_job_api(monkeypatch, database_engine)
    dataset_id = uuid4()
    version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"job-snapshot-{dataset_id}")
        )
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        snapshot_dataset_version(connection, version_id)

    try:
        created = client.post(
            "/api/jobs",
            json={
                "job_type": "evaluate",
                "idempotency_key": f"snapshot:{version_id}",
                "dataset_version_id": str(version_id),
                "payload": {"metric": "map50"},
            },
        )
        missing = client.post(
            "/api/jobs",
            json={
                "job_type": "evaluate",
                "idempotency_key": f"snapshot:{uuid4()}",
                "dataset_version_id": str(uuid4()),
                "payload": {},
            },
        )

        assert created.status_code == 201
        assert created.json()["dataset_version_id"] == str(version_id)
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "dataset_snapshot_not_found"
    finally:
        with database_engine.begin() as connection:
            connection.execute(delete(jobs).where(jobs.c.dataset_version_id == version_id))
            connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def test_worker_health_reports_live_missing_and_stale_workers(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_job_api(monkeypatch, database_engine)

    missing = client.get("/api/workers/health")
    assert missing.status_code == 503
    assert missing.json() == {"status": "unavailable", "workers": []}

    with database_engine.begin() as connection:
        record_worker_heartbeat(connection, "worker-health", "idle")
    live = client.get("/api/workers/health")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert live.json()["workers"][0]["stale"] is False

    with database_engine.begin() as connection:
        connection.execute(
            update(worker_heartbeats)
            .where(worker_heartbeats.c.worker_id == "worker-health")
            .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    stale = client.get("/api/workers/health")
    assert stale.status_code == 503
    assert stale.json()["workers"][0]["stale"] is True
