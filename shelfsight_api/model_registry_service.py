from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, func, insert, select, update

from shelfsight_api.models import (
    dataset_snapshots,
    model_deployment_events,
    model_deployments,
    model_registry_entries,
    snapshot_artifacts,
)

EVALUATION_SCHEMA = "cvsight-detector-evaluation/v1"


class ModelArtifactNotFoundError(ValueError):
    """Raised when a registry artifact does not exist."""


class ModelLineageError(ValueError):
    """Raised when model and evaluation artifacts do not share reproducible lineage."""


class ModelRegistrationConflictError(ValueError):
    """Raised when a model identity is already bound to different evidence."""


class ModelEntryNotFoundError(ValueError):
    """Raised when a registry entry does not exist for a role."""


class ModelDeploymentNotFoundError(ValueError):
    """Raised when a model role has no active deployment."""


class StaleModelDeploymentError(ValueError):
    """Raised when a deployment action targets stale active state."""


class ModelCompatibilityError(ValueError):
    """Raised when a candidate cannot replace the active runtime contract."""


class ModelRollbackUnavailableError(ValueError):
    """Raised when a model role has no previous deployment."""


def register_model_candidate(
    connection: Connection,
    *,
    model_role: str,
    model_id: str,
    model_version: str,
    model_artifact_id: UUID,
    evaluation_artifact_id: UUID,
    actor: str,
) -> dict[str, Any]:
    model_artifact = _artifact(connection, model_artifact_id, "model")
    evaluation_artifact = _artifact(connection, evaluation_artifact_id, "evaluation")
    model_metadata = _metadata(model_artifact)
    evaluation_metadata = _metadata(evaluation_artifact)
    lineage, configuration, compatibility = _validate_model_metadata(
        model_metadata,
        model_role,
    )
    metrics = _validate_evaluation_metadata(
        evaluation_metadata,
        model_artifact,
        evaluation_artifact,
        model_metadata,
        lineage,
    )
    values = {
        "model_role": model_role,
        "model_id": model_id,
        "model_version": model_version,
        "model_artifact_id": model_artifact_id,
        "evaluation_artifact_id": evaluation_artifact_id,
        "lineage": lineage,
        # An externally trained model has no CVSight training snapshot; its artifact
        # record sits on the evaluation snapshot, so that version is not its training.
        "training_dataset_version_id": (
            model_artifact["dataset_version_id"] if lineage == "snapshot" else None
        ),
        "evaluation_dataset_version_id": evaluation_artifact["dataset_version_id"],
        "model_artifact_sha256": model_artifact["content_sha256"],
        "evaluation_artifact_sha256": evaluation_artifact["content_sha256"],
        "configuration": configuration,
        "compatibility": compatibility,
        "metrics": metrics,
        "registered_by": actor,
    }
    existing = connection.execute(
        select(model_registry_entries).where(
            model_registry_entries.c.model_role == model_role,
            model_registry_entries.c.model_id == model_id,
            model_registry_entries.c.model_version == model_version,
        )
    ).mappings().one_or_none()
    if existing is not None:
        comparable_keys = tuple(values.keys() - {"registered_by"})
        if any(existing[key] != values[key] for key in comparable_keys):
            raise ModelRegistrationConflictError(
                "model identity is already bound to different evidence"
            )
        return _entry_response(connection, dict(existing))
    artifact_owner = connection.execute(
        select(model_registry_entries.c.id).where(
            model_registry_entries.c.model_role == model_role,
            model_registry_entries.c.model_artifact_sha256
            == model_artifact["content_sha256"],
        )
    ).scalar_one_or_none()
    if artifact_owner is not None:
        raise ModelRegistrationConflictError(
            "model artifact is already registered under another version"
        )
    row = connection.execute(
        insert(model_registry_entries)
        .values(**values)
        .returning(*model_registry_entries.c)
    ).mappings().one()
    return _entry_response(connection, dict(row))


def list_model_candidates(
    connection: Connection,
    model_role: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(model_registry_entries)
        .where(model_registry_entries.c.model_role == model_role)
        .order_by(
            model_registry_entries.c.registered_at.desc(),
            model_registry_entries.c.id.desc(),
        )
    ).mappings()
    return [_entry_response(connection, dict(row)) for row in rows]


def get_model_deployment(
    connection: Connection,
    model_role: str,
) -> dict[str, Any]:
    deployment = connection.execute(
        select(model_deployments).where(model_deployments.c.model_role == model_role)
    ).mappings().one_or_none()
    if deployment is None:
        raise ModelDeploymentNotFoundError("model role has no active deployment")
    return _deployment_response(connection, dict(deployment))


def promote_model(
    connection: Connection,
    model_role: str,
    entry_id: UUID,
    expected_active_id: UUID | None,
    actor: str,
) -> dict[str, Any]:
    candidate = _entry(connection, model_role, entry_id)
    deployment = connection.execute(
        select(model_deployments)
        .where(model_deployments.c.model_role == model_role)
        .with_for_update()
    ).mappings().one_or_none()
    if deployment is None:
        if expected_active_id is not None:
            raise StaleModelDeploymentError("active model changed before promotion")
        connection.execute(
            insert(model_deployments).values(
                model_role=model_role,
                active_entry_id=entry_id,
                previous_entry_id=None,
                updated_by=actor,
            )
        )
        _record_event(connection, model_role, None, entry_id, "promote", actor)
        return get_model_deployment(connection, model_role)

    active_id = UUID(str(deployment["active_entry_id"]))
    if active_id != expected_active_id:
        raise StaleModelDeploymentError("active model changed before promotion")
    if active_id == entry_id:
        return _deployment_response(connection, dict(deployment))
    active = _entry(connection, model_role, active_id)
    if active["compatibility"] != candidate["compatibility"]:
        raise ModelCompatibilityError("candidate is incompatible with the active model")
    connection.execute(
        update(model_deployments)
        .where(model_deployments.c.model_role == model_role)
        .values(
            active_entry_id=entry_id,
            previous_entry_id=active_id,
            updated_by=actor,
            updated_at=func.now(),
        )
    )
    _record_event(connection, model_role, active_id, entry_id, "promote", actor)
    return get_model_deployment(connection, model_role)


def rollback_model(
    connection: Connection,
    model_role: str,
    expected_active_id: UUID,
    actor: str,
) -> dict[str, Any]:
    deployment = connection.execute(
        select(model_deployments)
        .where(model_deployments.c.model_role == model_role)
        .with_for_update()
    ).mappings().one_or_none()
    if deployment is None:
        raise ModelDeploymentNotFoundError("model role has no active deployment")
    active_id = UUID(str(deployment["active_entry_id"]))
    if active_id != expected_active_id:
        raise StaleModelDeploymentError("active model changed before rollback")
    previous = deployment["previous_entry_id"]
    if previous is None:
        raise ModelRollbackUnavailableError("model role has no previous deployment")
    previous_id = UUID(str(previous))
    connection.execute(
        update(model_deployments)
        .where(model_deployments.c.model_role == model_role)
        .values(
            active_entry_id=previous_id,
            previous_entry_id=active_id,
            updated_by=actor,
            updated_at=func.now(),
        )
    )
    _record_event(connection, model_role, active_id, previous_id, "rollback", actor)
    return get_model_deployment(connection, model_role)


def _artifact(
    connection: Connection,
    artifact_id: UUID,
    artifact_type: str,
) -> dict[str, Any]:
    row = connection.execute(
        select(
            snapshot_artifacts,
            dataset_snapshots.c.content_sha256.label("snapshot_content_sha256"),
        )
        .join(
            dataset_snapshots,
            dataset_snapshots.c.dataset_version_id
            == snapshot_artifacts.c.dataset_version_id,
        )
        .where(
            snapshot_artifacts.c.id == artifact_id,
            snapshot_artifacts.c.artifact_type == artifact_type,
        )
    ).mappings().one_or_none()
    if row is None or row["content_sha256"] is None:
        raise ModelArtifactNotFoundError(f"{artifact_type} artifact does not exist")
    return dict(row)


def _validate_model_metadata(
    metadata: Mapping[str, Any],
    model_role: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if metadata.get("licenses_approved") is not True:
        raise ModelLineageError("model artifact licenses are not approved")
    lineage = metadata.get("lineage", "snapshot")
    if lineage == "snapshot":
        _require_training_lineage(metadata)
    elif lineage == "external":
        source = metadata.get("source")
        if not isinstance(source, str) or not source.strip() or len(source) > 500:
            raise ModelLineageError("an externally trained model must name its source")
    else:
        raise ModelLineageError("model artifact lineage is invalid")
    configuration = metadata.get("configuration")
    compatibility = metadata.get("compatibility")
    if not isinstance(configuration, dict) or not configuration:
        raise ModelLineageError("model artifact configuration is missing")
    if not isinstance(compatibility, dict):
        raise ModelLineageError("model artifact compatibility is missing")
    required_compatibility = {
        "model_role": model_role,
        "contract_version": "shelfsight-model-contract/v1",
        "input_geometry": "axis_aligned_box",
    }
    if any(compatibility.get(key) != value for key, value in required_compatibility.items()):
        raise ModelLineageError("model artifact compatibility is invalid")
    runtime = compatibility.get("runtime")
    if not isinstance(runtime, str) or not runtime.strip():
        raise ModelLineageError("model artifact runtime compatibility is missing")
    return str(lineage), dict(configuration), dict(compatibility)


def _require_training_lineage(metadata: Mapping[str, Any]) -> None:
    _sha256(metadata.get("training_manifest_sha256"), "training manifest")
    code_version = metadata.get("code_version")
    seed = metadata.get("random_seed")
    if not isinstance(code_version, str) or not code_version.strip():
        raise ModelLineageError("model artifact code version is missing")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ModelLineageError("model artifact random seed is invalid")


def _validate_evaluation_metadata(
    metadata: Mapping[str, Any],
    model_artifact: Mapping[str, Any],
    evaluation_artifact: Mapping[str, Any],
    model_metadata: Mapping[str, Any],
    lineage: str,
) -> dict[str, Any]:
    if metadata.get("schema_version") != EVALUATION_SCHEMA:
        raise ModelLineageError("evaluation schema is incompatible")
    if metadata.get("model_artifact_sha256") != model_artifact["content_sha256"]:
        raise ModelLineageError("evaluation targets a different model artifact")
    if metadata.get("snapshot_content_sha256") != evaluation_artifact[
        "snapshot_content_sha256"
    ]:
        raise ModelLineageError("evaluation targets a different frozen snapshot")
    if lineage == "snapshot":
        if metadata.get("code_version") != model_metadata.get("code_version"):
            raise ModelLineageError("model and evaluation code versions differ")
        if metadata.get("random_seed") != model_metadata.get("random_seed"):
            raise ModelLineageError("model and evaluation random seeds differ")
    else:
        # An external model has no CVSight code version; the evaluator's must be known.
        code_version = metadata.get("code_version")
        if not isinstance(code_version, str) or not code_version.strip():
            raise ModelLineageError("evaluation code version is missing")
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        raise ModelLineageError("evaluation metrics are missing")
    required_metrics = (
        "map_50_95",
        "product_recall_at_iou_50",
        "duplicate_rate_at_iou_50",
        "dense_scene_recall_at_iou_50",
        "overlapping_product_recall_at_iou_50",
    )
    for key in required_metrics:
        _score(metrics.get(key), key)
    return dict(metrics)


def _metadata(artifact: Mapping[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        raise ModelLineageError("artifact metadata is invalid")
    return metadata


def _entry(
    connection: Connection,
    model_role: str,
    entry_id: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        select(model_registry_entries).where(
            model_registry_entries.c.id == entry_id,
            model_registry_entries.c.model_role == model_role,
        )
    ).mappings().one_or_none()
    if row is None:
        raise ModelEntryNotFoundError("model registry entry does not exist for this role")
    return dict(row)


def _entry_response(
    connection: Connection,
    entry: dict[str, Any],
) -> dict[str, Any]:
    deployment = connection.execute(
        select(
            model_deployments.c.active_entry_id,
            model_deployments.c.previous_entry_id,
        ).where(model_deployments.c.model_role == entry["model_role"])
    ).one_or_none()
    status = "candidate"
    if deployment is not None and deployment.active_entry_id == entry["id"]:
        status = "default"
    elif deployment is not None and deployment.previous_entry_id == entry["id"]:
        status = "previous"
    artifacts = {
        row.id: row
        for row in connection.execute(
            select(
                snapshot_artifacts.c.id,
                snapshot_artifacts.c.artifact_key,
                snapshot_artifacts.c.metadata,
            ).where(
                snapshot_artifacts.c.id.in_(
                    (entry["model_artifact_id"], entry["evaluation_artifact_id"])
                )
            )
        )
    }
    model_artifact = artifacts[entry["model_artifact_id"]]
    return {
        **entry,
        "model_artifact_key": model_artifact.artifact_key,
        "evaluation_artifact_key": artifacts[entry["evaluation_artifact_id"]].artifact_key,
        "source": model_artifact.metadata.get("source") if entry["lineage"] == "external" else None,
        "deployment_status": status,
    }


def _deployment_response(
    connection: Connection,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    active = _entry_response(
        connection,
        _entry(connection, deployment["model_role"], deployment["active_entry_id"]),
    )
    previous = (
        _entry_response(
            connection,
            _entry(connection, deployment["model_role"], deployment["previous_entry_id"]),
        )
        if deployment["previous_entry_id"] is not None
        else None
    )
    return {
        "model_role": deployment["model_role"],
        "active": {**active, "deployment_status": "default"},
        "previous": (
            {**previous, "deployment_status": "previous"}
            if previous is not None
            else None
        ),
        "updated_by": deployment["updated_by"],
        "updated_at": deployment["updated_at"],
    }


def _record_event(
    connection: Connection,
    model_role: str,
    from_entry_id: UUID | None,
    to_entry_id: UUID,
    action: str,
    actor: str,
) -> None:
    connection.execute(
        insert(model_deployment_events).values(
            model_role=model_role,
            from_entry_id=from_entry_id,
            to_entry_id=to_entry_id,
            action=action,
            actor=actor,
        )
    )


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ModelLineageError(f"{label} checksum is invalid")
    return value


def _score(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ModelLineageError(f"evaluation metric {label} is invalid")
    return float(value)
