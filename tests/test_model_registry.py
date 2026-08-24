from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, delete, select, update
from sqlalchemy.exc import IntegrityError

from shelfsight_api.app import app
from shelfsight_api.data_model import (
    register_snapshot_artifact,
    snapshot_dataset_version,
)
from shelfsight_api.model_registry_service import (
    ModelCompatibilityError,
    ModelLineageError,
    ModelRollbackUnavailableError,
    StaleModelDeploymentError,
    get_model_deployment,
    list_model_candidates,
    promote_model,
    register_model_candidate,
    rollback_model,
)
from shelfsight_api.models import (
    dataset_snapshots,
    dataset_versions,
    datasets,
    model_deployment_events,
    model_registry_entries,
)

client = TestClient(app)


@dataclass(frozen=True)
class RegistryEvidence:
    training_dataset_id: UUID
    evaluation_dataset_id: UUID
    training_version_id: UUID
    evaluation_version_id: UUID
    model_artifact_ids: tuple[UUID, UUID]
    evaluation_artifact_ids: tuple[UUID, UUID]


@pytest.fixture
def registry_evidence(database_engine: Engine) -> Iterator[RegistryEvidence]:
    training_dataset_id = uuid4()
    evaluation_dataset_id = uuid4()
    training_version_id = uuid4()
    evaluation_version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            datasets.insert(),
            [
                {"id": training_dataset_id, "name": f"registry-train-{uuid4()}"},
                {"id": evaluation_dataset_id, "name": f"registry-eval-{uuid4()}"},
            ],
        )
        connection.execute(
            dataset_versions.insert(),
            [
                {"id": training_version_id, "dataset_id": training_dataset_id},
                {"id": evaluation_version_id, "dataset_id": evaluation_dataset_id},
            ],
        )
        snapshot_dataset_version(connection, training_version_id)
        snapshot_dataset_version(connection, evaluation_version_id)
        model_artifacts = []
        evaluation_artifacts = []
        for index, metrics in enumerate((_metrics(0.8), _metrics(0.6)), start=1):
            model_sha = str(index) * 64
            model = register_snapshot_artifact(
                connection,
                training_version_id,
                "model",
                f"models/registry-{uuid4()}.pth",
                content_sha256=model_sha,
                metadata=_model_metadata(),
            )
            evaluation = register_snapshot_artifact(
                connection,
                evaluation_version_id,
                "evaluation",
                f"evaluations/registry-{uuid4()}.json",
                content_sha256=str(index + 2) * 64,
                metadata=_evaluation_metadata(
                    model_sha,
                    _snapshot_sha(connection, evaluation_version_id),
                    metrics,
                ),
            )
            model_artifacts.append(model["id"])
            evaluation_artifacts.append(evaluation["id"])

    yield RegistryEvidence(
        training_dataset_id=training_dataset_id,
        evaluation_dataset_id=evaluation_dataset_id,
        training_version_id=training_version_id,
        evaluation_version_id=evaluation_version_id,
        model_artifact_ids=(model_artifacts[0], model_artifacts[1]),
        evaluation_artifact_ids=(evaluation_artifacts[0], evaluation_artifacts[1]),
    )

    with database_engine.begin() as connection:
        connection.execute(
            delete(datasets).where(
                datasets.c.id.in_((training_dataset_id, evaluation_dataset_id))
            )
        )


def test_registration_never_auto_promotes_and_rollback_restores_previous(
    database_engine: Engine,
    registry_evidence: RegistryEvidence,
) -> None:
    role = "known_sku_detector"
    with database_engine.begin() as connection:
        first = _register(connection, registry_evidence, role, 0)
        initial = promote_model(connection, role, first["id"], None, "operator:test")
        second = _register(connection, registry_evidence, role, 1)

        assert first["metrics"]["map_50_95"] == 0.8
        assert first["training_dataset_version_id"] == registry_evidence.training_version_id
        assert first["evaluation_dataset_version_id"] == (
            registry_evidence.evaluation_version_id
        )
        assert second["metrics"]["map_50_95"] == 0.6
        assert second["deployment_status"] == "candidate"
        assert get_model_deployment(connection, role)["active"]["id"] == first["id"]

        promoted = promote_model(
            connection,
            role,
            second["id"],
            initial["active"]["id"],
            "operator:test",
        )
        assert promoted["active"]["id"] == second["id"]
        assert promoted["previous"]["id"] == first["id"]

        rolled_back = rollback_model(
            connection,
            role,
            second["id"],
            "operator:test",
        )
        assert rolled_back["active"]["id"] == first["id"]
        assert rolled_back["previous"]["id"] == second["id"]
        events = connection.execute(
            select(model_deployment_events.c.action)
            .where(model_deployment_events.c.model_role == role)
            .order_by(model_deployment_events.c.created_at)
        ).scalars().all()
        assert events == ["promote", "promote", "rollback"]
        statuses = {
            row["model_version"]: row["deployment_status"]
            for row in list_model_candidates(connection, role)
        }
        assert statuses == {"candidate-1": "default", "candidate-2": "previous"}
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    update(model_registry_entries)
                    .where(model_registry_entries.c.id == first["id"])
                    .values(metrics=_metrics(0.1))
                )


def test_registry_rejects_unreproducible_lineage_stale_actions_and_no_previous(
    database_engine: Engine,
    registry_evidence: RegistryEvidence,
) -> None:
    role = "known_sku_detector"
    with database_engine.begin() as connection:
        first = _register(connection, registry_evidence, role, 0)
        deployment = promote_model(connection, role, first["id"], None, "operator:test")
        with pytest.raises(ModelRollbackUnavailableError, match="no previous"):
            rollback_model(connection, role, first["id"], "operator:test")
        with pytest.raises(StaleModelDeploymentError, match="changed"):
            promote_model(connection, role, first["id"], uuid4(), "operator:test")

        incompatible_model = register_snapshot_artifact(
            connection,
            registry_evidence.training_version_id,
            "model",
            f"models/incompatible-{uuid4()}.pth",
            content_sha256="a" * 64,
            metadata=_model_metadata(runtime="different-runtime"),
        )
        incompatible_evaluation = register_snapshot_artifact(
            connection,
            registry_evidence.evaluation_version_id,
            "evaluation",
            f"evaluations/incompatible-{uuid4()}.json",
            content_sha256="b" * 64,
            metadata=_evaluation_metadata(
                "a" * 64,
                _snapshot_sha(connection, registry_evidence.evaluation_version_id),
                _metrics(0.9),
            ),
        )
        incompatible = register_model_candidate(
            connection,
            model_role=role,
            model_id="rf-detr-nano",
            model_version="incompatible",
            model_artifact_id=incompatible_model["id"],
            evaluation_artifact_id=incompatible_evaluation["id"],
            actor="operator:test",
        )
        with pytest.raises(ModelCompatibilityError, match="incompatible"):
            promote_model(
                connection,
                role,
                incompatible["id"],
                deployment["active"]["id"],
                "operator:test",
            )

        wrong_evaluation = register_snapshot_artifact(
            connection,
            registry_evidence.evaluation_version_id,
            "evaluation",
            f"evaluations/wrong-{uuid4()}.json",
            content_sha256="c" * 64,
            metadata=_evaluation_metadata(
                "e" * 64,
                _snapshot_sha(connection, registry_evidence.evaluation_version_id),
                _metrics(0.7),
            ),
        )
        with pytest.raises(ModelLineageError, match="different model"):
            register_model_candidate(
                connection,
                model_role=role,
                model_id="rf-detr-nano",
                model_version="wrong-lineage",
                model_artifact_id=registry_evidence.model_artifact_ids[1],
                evaluation_artifact_id=wrong_evaluation["id"],
                actor="operator:test",
            )


def test_registry_api_requires_evidence_and_explicit_promotion(
    database_engine: Engine,
    registry_evidence: RegistryEvidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shelfsight_api.model_registry_api.get_engine",
        lambda: database_engine,
    )
    role = "known_sku_detector"
    headers = {"X-ShelfSight-Actor": "operator:test"}
    created = client.post(
        "/api/model-registry/candidates",
        headers=headers,
        json={
            "model_role": role,
            "model_id": "rf-detr-nano",
            "model_version": "candidate-1",
            "model_artifact_id": str(registry_evidence.model_artifact_ids[0]),
            "evaluation_artifact_id": str(registry_evidence.evaluation_artifact_ids[0]),
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["deployment_status"] == "candidate"
    assert client.get(f"/api/model-deployments/{role}").status_code == 404
    promoted = client.post(
        f"/api/model-deployments/{role}/promote",
        headers=headers,
        json={"entry_id": created.json()["id"], "expected_active_id": None},
    )
    assert promoted.status_code == 200
    assert promoted.json()["active"]["id"] == created.json()["id"]
    listed = client.get("/api/model-registry", params={"model_role": role})
    assert listed.status_code == 200
    assert listed.json()["models"][0]["deployment_status"] == "default"

    missing = client.post(
        "/api/model-registry/candidates",
        headers=headers,
        json={
            "model_role": role,
            "model_id": "missing",
            "model_version": "candidate",
            "model_artifact_id": str(uuid4()),
            "evaluation_artifact_id": str(uuid4()),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "model_artifact_not_found"


def _register(
    connection: Connection,
    evidence: RegistryEvidence,
    role: str,
    index: int,
) -> dict[str, Any]:
    return register_model_candidate(
        connection,
        model_role=role,
        model_id="rf-detr-nano",
        model_version=f"candidate-{index + 1}",
        model_artifact_id=evidence.model_artifact_ids[index],
        evaluation_artifact_id=evidence.evaluation_artifact_ids[index],
        actor="operator:test",
    )


def _model_metadata(runtime: str = "rfdetr-1.8.3-cu128") -> dict[str, Any]:
    return {
        "training_manifest_sha256": "d" * 64,
        "code_version": "commit-123",
        "random_seed": 1337,
        "licenses_approved": True,
        "configuration": {"resolution": 384, "epochs": 5},
        "compatibility": {
            "model_role": "known_sku_detector",
            "contract_version": "shelfsight-model-contract/v1",
            "input_geometry": "axis_aligned_box",
            "runtime": runtime,
        },
    }


def _evaluation_metadata(
    model_sha256: str,
    snapshot_sha256: str,
    metrics: dict[str, float],
) -> dict[str, Any]:
    return {
        "schema_version": "cvsight-detector-evaluation/v1",
        "model_artifact_sha256": model_sha256,
        "snapshot_content_sha256": snapshot_sha256,
        "code_version": "commit-123",
        "random_seed": 1337,
        "metrics": metrics,
    }


def _metrics(map_value: float) -> dict[str, float]:
    return {
        "map_50_95": map_value,
        "product_recall_at_iou_50": min(map_value + 0.1, 1.0),
        "duplicate_rate_at_iou_50": 0.08,
        "dense_scene_recall_at_iou_50": min(map_value + 0.05, 1.0),
        "overlapping_product_recall_at_iou_50": map_value,
    }


def _snapshot_sha(connection: Connection, version_id: UUID) -> str:
    return str(
        connection.scalar(
            select(dataset_snapshots.c.content_sha256).where(
                dataset_snapshots.c.dataset_version_id == version_id
            )
        )
    )
