"""Evaluate a detector served over HTTP on a signed-off release, then register it.

The evaluation runs in the worker, because a release can hold thousands of photos, and
stores its scores on the job. The owner reviews them on the Models page before the model
becomes a registry candidate, so nothing is registered without a human looking first.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from importlib.metadata import version
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select

from shelfsight_api.config import get_media_root
from shelfsight_api.data_model import register_snapshot_artifact
from shelfsight_api.database import get_engine
from shelfsight_api.detector_metrics import (
    DENSE_IMAGE_THRESHOLD,
    EVALUATION_SCHEMA,
    OVERLAP_IOU_THRESHOLD,
    detector_metrics,
)
from shelfsight_api.export_service import SnapshotData, is_training_annotation, load_snapshot
from shelfsight_api.job_service import JobClaim, enqueue_job, get_job
from shelfsight_api.model_adapters import AdapterExecutionError, HttpModelAdapter
from shelfsight_api.model_contract import DetectResponse
from shelfsight_api.model_registry_service import MODEL_CONTRACT_VERSION, register_model_candidate
from shelfsight_api.model_service import ModelService, ModelServiceError, database_image_resolver
from shelfsight_api.models import dataset_snapshot_images
from shelfsight_api.worker import JobDefinition, JobExecutionError, JobReporter

EVALUATION_JOB_TYPE = "evaluate_detector"
DETECTOR_ROLE = "known_sku_detector"
Progress = Callable[[int, int], None]


class EvaluationNotReadyError(ValueError):
    """Raised when a job is not a finished detector evaluation."""


class InvalidRuntimeUrlError(ValueError):
    """Raised when the model service address is not an http or https URL."""


def enqueue_detector_evaluation(
    connection: Connection,
    dataset_version_id: UUID,
    runtime_url: str,
    confidence_threshold: float,
) -> dict[str, Any]:
    try:
        HttpModelAdapter(runtime_url)
    except ValueError as error:
        raise InvalidRuntimeUrlError(str(error)) from error
    photos = connection.execute(
        select(func.count()).where(
            dataset_snapshot_images.c.dataset_version_id == dataset_version_id
        )
    ).scalar_one()
    row, _ = enqueue_job(
        connection,
        EVALUATION_JOB_TYPE,
        f"{EVALUATION_JOB_TYPE}:{uuid4()}",
        {
            "dataset_version_id": str(dataset_version_id),
            "runtime_url": runtime_url,
            "confidence_threshold": confidence_threshold,
        },
        dataset_version_id=dataset_version_id,
        progress_total=photos,
    )
    return row


def evaluation_job_definition() -> JobDefinition:
    def run(claim: JobClaim, reporter: JobReporter) -> Mapping[str, Any]:
        engine, media_root = get_engine(), get_media_root()
        payload = claim.payload
        runtime_url = str(payload["runtime_url"])
        with engine.connect() as connection:
            snapshot = load_snapshot(connection, UUID(str(payload["dataset_version_id"])))
        service = ModelService(
            {DETECTOR_ROLE: HttpModelAdapter(runtime_url)},
            database_image_resolver(engine, media_root),
        )
        return evaluate_snapshot(
            snapshot,
            service,
            runtime_url,
            float(payload["confidence_threshold"]),
            reporter.progress,
        )

    return JobDefinition(run=run)


def evaluate_snapshot(
    snapshot: SnapshotData,
    service: ModelService,
    runtime_url: str,
    confidence_threshold: float,
    progress: Progress,
) -> dict[str, Any]:
    """Send every photo to the model and score its boxes against the verified products."""
    numbers = {str(image["id"]): number for number, image in enumerate(snapshot.images, 1)}
    ground_truth: dict[int, list[list[float]]] = {number: [] for number in numbers.values()}
    for row in snapshot.annotations:
        if is_training_annotation(row) and row["class_type"] == "product":
            ground_truth[numbers[str(row["image_id"])]].append(_box(row))
    if not any(ground_truth.values()):
        raise JobExecutionError(
            "no_verified_products", "the release has no verified product boxes", retryable=False
        )

    predictions: list[dict[str, Any]] = []
    provenance = None
    for number, image in enumerate(snapshot.images, 1):
        response = _detect(service, image, confidence_threshold)
        if provenance is not None and response.model_provenance != provenance:
            raise JobExecutionError(
                "model_changed", "the model service switched models mid-evaluation", retryable=True
            )
        provenance = response.model_provenance
        for prediction in response.predictions:
            if prediction.class_type == "product":
                box = prediction.geometry
                predictions.append(
                    {
                        "image_id": number,
                        "bbox": [box.x, box.y, box.width, box.height],
                        "score": prediction.score,
                        "order": len(predictions),
                    }
                )
        progress(number, len(snapshot.images))
    assert provenance is not None
    return _report(
        snapshot,
        provenance.model_dump(),
        runtime_url,
        confidence_threshold,
        detector_metrics(ground_truth, predictions),
    )


def register_evaluated_detector(
    connection: Connection,
    job_id: UUID,
    source: str,
    licenses_approved: bool,
    actor: str,
) -> dict[str, Any]:
    job = get_job(connection, job_id)
    report = job["result"]
    if job["job_type"] != EVALUATION_JOB_TYPE or job["state"] != "succeeded" or not report:
        raise EvaluationNotReadyError("the evaluation has not finished successfully")
    version_id = UUID(report["dataset_version_id"])
    identity = f"{report['model_id']}/{report['model_version']}"
    model = register_snapshot_artifact(
        connection,
        version_id,
        "model",
        f"models/{identity}@{version_id}",
        content_sha256=report["model_artifact_sha256"],
        metadata={
            "lineage": "external",
            "source": source.strip(),
            "licenses_approved": licenses_approved,
            "configuration": {"confidence_threshold": report["confidence_threshold"]},
            "compatibility": {
                "model_role": DETECTOR_ROLE,
                "contract_version": MODEL_CONTRACT_VERSION,
                "input_geometry": "axis_aligned_box",
                "runtime": f"http: {report['runtime_url']}",
            },
        },
    )
    evaluation = register_snapshot_artifact(
        connection,
        version_id,
        "evaluation",
        f"evaluations/{identity}/{job_id}.json",
        content_sha256=hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        metadata=report,
    )
    return register_model_candidate(
        connection,
        model_role=DETECTOR_ROLE,
        model_id=str(report["model_id"]),
        model_version=str(report["model_version"]),
        model_artifact_id=model["id"],
        evaluation_artifact_id=evaluation["id"],
        actor=actor,
    )


def _detect(
    service: ModelService, image: Mapping[str, Any], confidence_threshold: float
) -> DetectResponse:
    request = {
        "request_id": f"evaluation-{image['id']}",
        "operation": "detect",
        "model_role": DETECTOR_ROLE,
        "image": {
            "image_id": str(image["id"]),
            "sha256": image["content_sha256"],
            "width": image["canonical_width"],
            "height": image["canonical_height"],
        },
        "configuration": {"confidence_threshold": confidence_threshold},
    }
    try:
        response = service.execute(request)
    except (ModelServiceError, AdapterExecutionError) as error:
        raise JobExecutionError(error.code, str(error), retryable=error.retryable) from error
    if not isinstance(response, DetectResponse):
        raise JobExecutionError("invalid_model_response", "not a detection", retryable=False)
    return response


def _report(
    snapshot: SnapshotData,
    provenance: Mapping[str, Any],
    runtime_url: str,
    confidence_threshold: float,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    dense = metrics["dense_scenes"]
    overlapping = metrics["overlapping_products"]
    return {
        "schema_version": EVALUATION_SCHEMA,
        "dataset_version_id": str(snapshot.version_id),
        "snapshot_content_sha256": snapshot.content_sha256,
        "code_version": f"cvsight {version('shelfsight')}",
        "model_id": provenance["model_id"],
        "model_version": provenance["model_version"],
        "model_artifact_sha256": provenance["artifact_sha256"],
        "runtime_url": runtime_url,
        "confidence_threshold": confidence_threshold,
        "photos": len(snapshot.images),
        "ground_truth_boxes": metrics["ground_truth"],
        "predicted_boxes": metrics["predictions"],
        "dense_image_threshold": DENSE_IMAGE_THRESHOLD,
        "overlap_iou_threshold": OVERLAP_IOU_THRESHOLD,
        "metrics": {
            "map_50": metrics["map_50"],
            "map_50_95": metrics["map_50_95"],
            "product_recall_at_iou_50": metrics["product_recall_at_iou_50"],
            "precision_at_iou_50": metrics["precision_at_iou_50"],
            "duplicate_rate_at_iou_50": metrics["duplicate_rate_at_iou_50"],
            "dense_scene_recall_at_iou_50": dense["recall_at_iou_50"],
            "dense_scene_images": dense["images"],
            "overlapping_product_recall_at_iou_50": overlapping["recall_at_iou_50"],
            "overlapping_products": overlapping["ground_truth"],
        },
    }


def _box(row: Mapping[str, Any]) -> list[float]:
    return [float(row["x"]), float(row["y"]), float(row["width"]), float(row["height"])]
