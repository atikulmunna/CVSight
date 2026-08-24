from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation
from shelfsight_api.job_service import enqueue_job, get_job
from shelfsight_api.model_adapters import (
    FakeModelAdapter,
    Sam3BoxRefinerAdapter,
)
from shelfsight_api.model_contract import (
    DetectRequest,
    ModelProvenance,
    RefineRequest,
)
from shelfsight_api.model_service import (
    ModelService,
    ResolvedModelImage,
    model_job_definitions,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    datasets,
    images,
    jobs,
    worker_heartbeats,
)
from shelfsight_api.prelabel_service import write_detection_proposals
from shelfsight_api.worker import JobWorker

client = TestClient(app)


@pytest.fixture
def prelabel_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[UUID, UUID, UUID]:
    dataset_id = uuid4()
    image_id = uuid4()
    annotation_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"prelabel-{dataset_id}"))
        _insert_image(connection, dataset_id, image_id)
        create_annotation(
            connection,
            image_id,
            _annotation_values(),
            annotation_id=annotation_id,
        )
    monkeypatch.setattr("shelfsight_api.prelabel_api.get_engine", lambda: database_engine)

    yield dataset_id, image_id, annotation_id

    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def test_batch_api_queues_valid_images_and_reports_partial_rejections(
    database_engine: Engine,
    prelabel_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, _ = prelabel_target
    missing_id = uuid4()
    request = {
        "image_ids": [str(image_id), str(missing_id)],
        "idempotency_key": "shelf-run-001",
        "configuration": {
            "confidence_threshold": 0.35,
            "inference_strategy": "full_image",
        },
    }

    created = client.post("/api/prelabels/batch", json=request)
    repeated = client.post("/api/prelabels/batch", json=request)
    conflict = client.post(
        "/api/prelabels/batch",
        json={
            **request,
            "configuration": {
                "confidence_threshold": 0.5,
                "inference_strategy": "full_image",
            },
        },
    )

    assert created.status_code == 202
    assert created.json()["items"][0]["status"] == "queued"
    assert created.json()["items"][1] == {
        "image_id": str(missing_id),
        "status": "rejected",
        "job_id": None,
        "job_state": None,
        "error_code": "image_not_found",
    }
    assert repeated.json()["items"][0]["status"] == "deduplicated"
    assert repeated.json()["items"][0]["job_id"] == created.json()["items"][0]["job_id"]
    assert conflict.json()["items"][0]["error_code"] == "idempotency_conflict"
    assert conflict.json()["items"][1]["error_code"] == "image_not_found"

    with database_engine.connect() as connection:
        queued = get_job(connection, UUID(created.json()["items"][0]["job_id"]))
    assert queued["payload"]["image"]["image_id"] == str(image_id)
    assert queued["payload"]["model_role"] == "known_sku_detector"


def test_refinement_api_is_revision_safe_and_never_mutates_annotation(
    database_engine: Engine,
    prelabel_target: tuple[UUID, UUID, UUID],
) -> None:
    _, _, annotation_id = prelabel_target
    request = {"expected_revision": 1, "idempotency_key": "refine-001"}

    created = client.post(f"/api/annotations/{annotation_id}/refine", json=request)
    repeated = client.post(f"/api/annotations/{annotation_id}/refine", json=request)
    stale = client.post(
        f"/api/annotations/{annotation_id}/refine",
        json={"expected_revision": 2, "idempotency_key": "refine-stale"},
    )

    assert created.status_code == 202
    assert created.json()["deduplicated"] is False
    assert repeated.status_code == 200
    assert repeated.json()["deduplicated"] is True
    assert repeated.json()["job_id"] == created.json()["job_id"]
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_revision"

    with database_engine.connect() as connection:
        current_revision = connection.execute(
            select(annotation_records.c.current_revision).where(
                annotation_records.c.id == annotation_id
            )
        ).scalar_one()
        revision_count = connection.execute(
            select(func.count())
            .select_from(annotation_revisions)
            .where(annotation_revisions.c.annotation_id == annotation_id)
        ).scalar_one()
        refine_job = get_job(connection, UUID(created.json()["job_id"]))
    assert current_revision == 1
    assert revision_count == 1
    assert refine_job["payload"]["box_hint"]["x"] == 10


def test_detection_writer_deduplicates_exact_results_but_keeps_dense_neighbors(
    database_engine: Engine,
    prelabel_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, human_annotation_id = prelabel_target
    service = ModelService(
        {"known_sku_detector": DenseDetector()},
        _resolver(image_id),
    )
    definitions = model_job_definitions(
        service,
        {"detect": write_detection_proposals},
    )
    first_id = _enqueue_detect(database_engine, image_id, "dense-first", "request-first")
    second_id = _enqueue_detect(database_engine, image_id, "dense-second", "request-second")
    worker = JobWorker(database_engine, "prelabel-worker", {"detect": definitions["detect"]})

    assert worker.run_once() is True
    assert worker.run_once() is True

    with database_engine.connect() as connection:
        first_job = get_job(connection, first_id)
        second_job = get_job(connection, second_id)
        proposals = connection.execute(
            select(annotation_records.c.id, annotation_revisions)
            .join(
                annotation_revisions,
                annotation_revisions.c.annotation_id == annotation_records.c.id,
            )
            .where(
                annotation_records.c.image_id == image_id,
                annotation_revisions.c.source == "model",
            )
            .order_by(annotation_revisions.c.x)
        ).mappings().all()
        human_revision = connection.execute(
            select(annotation_records.c.current_revision).where(
                annotation_records.c.id == human_annotation_id
            )
        ).scalar_one()
        image_status = connection.execute(
            select(images.c.status).where(images.c.id == image_id)
        ).scalar_one()

    assert first_job["state"] == "succeeded"
    assert second_job["state"] == "succeeded"
    assert len(proposals) == 2
    assert [proposal["x"] for proposal in proposals] == [10, 11]
    assert all(proposal["lifecycle_state"] == "proposed" for proposal in proposals)
    assert all(proposal["review_state"] == "unreviewed" for proposal in proposals)
    assert all(proposal["confidence"] == 0.9 for proposal in proposals)
    assert proposals[0]["provenance"]["job_id"] == str(first_id)
    assert proposals[0]["provenance"]["model"]["model_version"] == "dense-001"
    assert proposals[0]["provenance"]["configuration"]["inference_strategy"] == "full_image"
    assert human_revision == 1
    assert image_status == "pre_labeled"


def test_invalid_candidate_rolls_back_one_image_without_blocking_another(
    database_engine: Engine,
) -> None:
    dataset_id = uuid4()
    failing_image_id = uuid4()
    successful_image_id = uuid4()
    missing_sku_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"partial-{dataset_id}"))
        _insert_image(connection, dataset_id, failing_image_id)
        _insert_image(connection, dataset_id, successful_image_id)

    detector = ConditionalCandidateDetector(failing_image_id, missing_sku_id)
    service = ModelService(
        {"known_sku_detector": detector},
        lambda image: ResolvedModelImage(
            path=Path("unused"),
            sha256=image.sha256,
            width=image.width,
            height=image.height,
        ),
    )
    definitions = model_job_definitions(
        service,
        {"detect": write_detection_proposals},
    )
    failed_id = _enqueue_detect(database_engine, failing_image_id, "partial-fail", "fail")
    succeeded_id = _enqueue_detect(
        database_engine,
        successful_image_id,
        "partial-success",
        "success",
    )
    worker = JobWorker(database_engine, "partial-worker", {"detect": definitions["detect"]})

    assert worker.run_once() is True
    assert worker.run_once() is True

    with database_engine.connect() as connection:
        failed = get_job(connection, failed_id)
        succeeded = get_job(connection, succeeded_id)
        failed_proposals = connection.execute(
            select(func.count())
            .select_from(annotation_records)
            .where(annotation_records.c.image_id == failing_image_id)
        ).scalar_one()
        succeeded_proposals = connection.execute(
            select(func.count())
            .select_from(annotation_records)
            .where(annotation_records.c.image_id == successful_image_id)
        ).scalar_one()
    assert failed["state"] == "failed"
    assert failed["error_code"] == "invalid_proposal_sku"
    assert failed_proposals == 0
    assert succeeded["state"] == "succeeded"
    assert succeeded_proposals == 1

    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def test_sam_refiner_selects_the_box_most_aligned_with_the_hint() -> None:
    request = RefineRequest.model_validate(
        {
            "request_id": "refine-selection",
            "operation": "refine",
            "image": {
                "image_id": str(uuid4()),
                "sha256": "a" * 64,
                "width": 100,
                "height": 80,
            },
            "model_role": "box_refiner",
            "box_hint": {
                "type": "axis_aligned_box",
                "x": 10,
                "y": 10,
                "width": 20,
                "height": 20,
            },
        }
    )
    adapter = Sam3BoxRefinerAdapter(
        lambda _path, _request: [
            {"bbox": [60, 10, 20, 20], "score": 0.99},
            {"bbox": [11, 11, 20, 20], "score": 0.8},
            {"bbox": [-1, 0, 5, 5], "score": 0.9},
        ],
        ModelProvenance(
            model_id="sam3-box-refiner",
            model_version="test",
            artifact_sha256="b" * 64,
        ),
    )

    result = adapter.execute(request, Path("unused"))

    assert result["result"]["geometry"] == {
        "type": "axis_aligned_box",
        "x": 11,
        "y": 11,
        "width": 20,
        "height": 20,
    }
    assert result["result"]["score"] == 0.8


class DenseDetector:
    provenance = ModelProvenance(
        model_id="dense-detector",
        model_version="dense-001",
        artifact_sha256="c" * 64,
    )

    def execute(
        self,
        request: object,
        _image_path: Path | None,
    ) -> Mapping[str, Any]:
        assert isinstance(request, DetectRequest)
        return {
            "model_provenance": self.provenance.model_dump(mode="json"),
            "predictions": [
                _prediction(request.request_id, "one", 10),
                _prediction(request.request_id, "duplicate", 10),
                _prediction(request.request_id, "neighbor", 11),
            ],
        }


class ConditionalCandidateDetector(FakeModelAdapter):
    def __init__(self, failing_image_id: UUID, missing_sku_id: UUID) -> None:
        super().__init__()
        self._failing_image_id = failing_image_id
        self._missing_sku_id = missing_sku_id

    def execute(
        self,
        request: object,
        image_path: Path | None,
    ) -> Mapping[str, Any]:
        result = dict(super().execute(request, image_path))
        assert isinstance(request, DetectRequest)
        if request.image.image_id == self._failing_image_id:
            predictions = list(result["predictions"])
            predictions[0] = {
                **predictions[0],
                "candidate_sku_id": str(self._missing_sku_id),
            }
            result["predictions"] = predictions
        return result


def _insert_image(
    connection: Any,
    dataset_id: UUID,
    image_id: UUID,
) -> None:
    connection.execute(
        insert(images).values(
            id=image_id,
            dataset_id=dataset_id,
            original_media_key=f"original/{image_id}.png",
            canonical_media_key=f"canonical/{image_id}.png",
            thumbnail_media_key=f"thumbnail/{image_id}.jpg",
            media_type="image/png",
            original_filename=f"{image_id}.png",
            content_sha256=image_id.hex * 2,
            canonical_width=100,
            canonical_height=80,
        )
    )


def _annotation_values() -> dict[str, Any]:
    return {
        "x": 10,
        "y": 10,
        "width": 20,
        "height": 20,
        "class_type": "product",
        "sku_id": None,
        "lifecycle_state": "proposed",
        "review_state": "unreviewed",
        "source": "human",
        "provenance": {"actor": "test"},
        "confidence": None,
        "occluded": False,
        "truncated": False,
        "shelf_row": None,
    }


def _resolver(image_id: UUID) -> Any:
    def resolve(image: Any) -> ResolvedModelImage:
        assert image.image_id == image_id
        return ResolvedModelImage(
            path=Path("unused"),
            sha256=image.sha256,
            width=image.width,
            height=image.height,
        )

    return resolve


def _enqueue_detect(
    engine: Engine,
    image_id: UUID,
    idempotency_key: str,
    request_id: str,
) -> UUID:
    payload = {
        "request_id": request_id,
        "operation": "detect",
        "image": {
            "image_id": str(image_id),
            "sha256": image_id.hex * 2,
            "width": 100,
            "height": 80,
        },
        "model_role": "known_sku_detector",
        "configuration": {
            "confidence_threshold": 0.3,
            "inference_strategy": "full_image",
        },
    }
    with engine.begin() as connection:
        job, _ = enqueue_job(
            connection,
            "detect",
            idempotency_key,
            payload,
            max_attempts=1,
        )
    return job["id"]


def _prediction(request_id: str, suffix: str, x: int) -> dict[str, Any]:
    return {
        "proposal_id": f"{request_id}:{suffix}",
        "geometry": {
            "type": "axis_aligned_box",
            "x": x,
            "y": 10,
            "width": 20,
            "height": 20,
        },
        "class_type": "product",
        "score": 0.9,
        "candidate_sku_id": None,
    }
