from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Connection

from shelfsight_api.config import get_media_root
from shelfsight_api.database import get_engine
from shelfsight_api.job_service import JobClaim
from shelfsight_api.model_adapters import (
    ModelAdapter,
    load_clip_image_embedder_adapter,
    load_rfdetr_adapter,
    load_sam3_box_refiner_adapter,
)
from shelfsight_api.model_service import (
    ModelService,
    database_embedding_image_resolver,
    database_image_resolver,
    model_job_definitions,
)
from shelfsight_api.prelabel_service import write_detection_proposals
from shelfsight_api.propagation_service import write_propagation_embedding
from shelfsight_api.recognition_profile import T005_RECOGNITION_PROFILE
from shelfsight_api.recognition_service import write_recognition_embedding
from shelfsight_api.worker import JobDefinition

RFDETR_CHECKPOINT_ENV = "SHELFSIGHT_RFDETR_CHECKPOINT"
RFDETR_VERSION_ENV = "SHELFSIGHT_RFDETR_VERSION"
SAM3_CHECKPOINT_ENV = "SHELFSIGHT_SAM3_CHECKPOINT"
SAM3_VERSION_ENV = "SHELFSIGHT_SAM3_VERSION"
CLIP_MODEL_ROOT_ENV = "SHELFSIGHT_CLIP_MODEL_ROOT"


def configured_model_job_definitions() -> dict[str, JobDefinition]:
    adapters: dict[str, ModelAdapter] = {}
    operations: list[str] = []
    detector_checkpoint = os.environ.get(RFDETR_CHECKPOINT_ENV, "").strip()
    refiner_checkpoint = os.environ.get(SAM3_CHECKPOINT_ENV, "").strip()
    clip_model_root = os.environ.get(CLIP_MODEL_ROOT_ENV, "").strip()
    configured_models = [
        value
        for value in (detector_checkpoint, refiner_checkpoint, clip_model_root)
        if value
    ]
    if len(configured_models) > 1:
        raise ValueError("GPU models must run in separate worker processes")
    if detector_checkpoint:
        version = _required_version(RFDETR_VERSION_ENV, RFDETR_CHECKPOINT_ENV)
        adapters["known_sku_detector"] = load_rfdetr_adapter(
            Path(detector_checkpoint),
            model_version=version,
        )
        operations.append("detect")

    if refiner_checkpoint:
        version = _required_version(SAM3_VERSION_ENV, SAM3_CHECKPOINT_ENV)
        adapters["box_refiner"] = load_sam3_box_refiner_adapter(
            Path(refiner_checkpoint),
            model_version=version,
        )
        operations.append("refine")

    if clip_model_root:
        clip_adapter = load_clip_image_embedder_adapter(
            Path(clip_model_root),
            T005_RECOGNITION_PROFILE.model_provenance,
        )
        adapters["recognition_embedder"] = clip_adapter
        adapters["propagation_embedder"] = clip_adapter
        operations.append("embed")

    if not adapters:
        return {}
    engine = get_engine()
    image_resolver = (
        database_embedding_image_resolver(engine, get_media_root())
        if clip_model_root
        else database_image_resolver(engine, get_media_root())
    )
    definitions = model_job_definitions(
        ModelService(
            adapters,
            image_resolver,
        ),
        {
            "detect": write_detection_proposals,
            "embed": _write_embedding,
        },
    )
    return {operation: definitions[operation] for operation in operations}


def _write_embedding(
    connection: Connection,
    claim: JobClaim,
    result: Mapping[str, Any],
) -> None:
    purpose = claim.payload.get("purpose")
    if purpose == "recognition":
        write_recognition_embedding(connection, claim, result)
        return
    if purpose == "propagation":
        write_propagation_embedding(connection, claim, result)
        return
    raise ValueError("embedding job purpose is unsupported")


def _required_version(version_environment: str, checkpoint_environment: str) -> str:
    version = os.environ.get(version_environment, "").strip()
    if not version:
        raise ValueError(
            f"{version_environment} is required when {checkpoint_environment} is configured"
        )
    return version
