from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select

from shelfsight_api.app import app
from shelfsight_api.models import dataset_versions, datasets

client = TestClient(app)


def configure_dataset_api(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> None:
    monkeypatch.setattr("shelfsight_api.dataset_api.get_engine", lambda: database_engine)


def test_create_dataset_opens_a_version_and_normalizes_input(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    name = f"gate-dataset-{uuid4()}"

    response = client.post(
        "/api/datasets",
        json={"name": f"  {name}  ", "description": "   "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == name
    assert body["description"] is None
    with database_engine.connect() as connection:
        version = connection.execute(
            select(
                dataset_versions.c.dataset_id,
                dataset_versions.c.snapshot_at,
            ).where(dataset_versions.c.id == body["open_version_id"])
        ).one()
        assert str(version.dataset_id) == body["id"]
        assert version.snapshot_at is None

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == body["id"]))


def test_dataset_validation_duplicate_and_snapshot_errors_are_stable(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    name = f"gate-errors-{uuid4()}"
    blank = client.post("/api/datasets", json={"name": "   "})
    first = client.post("/api/datasets", json={"name": name})
    duplicate = client.post("/api/datasets", json={"name": name})

    assert blank.status_code == 422
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "dataset_name_conflict"

    version_id = first.json()["open_version_id"]
    snapshot = client.post(f"/api/dataset-versions/{version_id}/snapshot")
    repeated = client.post(f"/api/dataset-versions/{version_id}/snapshot")
    missing = client.post(f"/api/dataset-versions/{uuid4()}/snapshot")

    assert snapshot.status_code == 200
    assert snapshot.json()["captured_annotations"] == 0
    assert snapshot.json()["schema_version"] == "shelfsight-dataset-snapshot/v2"
    assert len(snapshot.json()["content_sha256"]) == 64
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "dataset_version_frozen"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"

    manifest = client.get(f"/api/dataset-versions/{version_id}/snapshot")
    artifact = client.post(
        f"/api/dataset-versions/{version_id}/artifacts",
        json={
            "artifact_type": "evaluation",
            "artifact_key": f"evaluations/{version_id}.json",
            "content_sha256": "a" * 64,
            "metadata": {"metric": "map50", "value": 0.8},
        },
    )
    conflict = client.post(
        f"/api/dataset-versions/{version_id}/artifacts",
        json={
            "artifact_type": "evaluation",
            "artifact_key": f"evaluations/{version_id}.json",
            "content_sha256": "b" * 64,
        },
    )
    missing_manifest = client.get(f"/api/dataset-versions/{uuid4()}/snapshot")

    assert manifest.status_code == 200
    assert manifest.json()["dataset_version_id"] == version_id
    assert artifact.status_code == 201
    assert artifact.json()["dataset_version_id"] == version_id
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "snapshot_artifact_conflict"
    assert missing_manifest.status_code == 404
    assert missing_manifest.json()["detail"]["code"] == "dataset_snapshot_not_found"

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == first.json()["id"]))
