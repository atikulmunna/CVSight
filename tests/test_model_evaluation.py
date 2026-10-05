from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import (
    DatasetSnapshotNotFoundError,
    create_annotation,
    snapshot_dataset_version,
)
from shelfsight_api.export_service import SnapshotData
from shelfsight_api.model_evaluation import (
    EVALUATION_JOB_TYPE,
    EvaluationNotReadyError,
    InvalidRuntimeUrlError,
    enqueue_detector_evaluation,
    evaluate_snapshot,
    evaluation_job_definition,
    register_evaluated_detector,
)
from shelfsight_api.model_service import ModelService, ResolvedModelImage
from shelfsight_api.models import dataset_version_images, dataset_versions, datasets, images, jobs
from shelfsight_api.worker import JobExecutionError, JobWorker

client = TestClient(app)
PRODUCTS = [(10.0, 10.0, 20.0, 30.0), (40.0, 10.0, 20.0, 30.0)]


def product_row(image_id: str, box: tuple[float, ...], **overrides: Any) -> dict[str, Any]:
    x, y, width, height = box
    return {
        "image_id": image_id,
        "class_type": "product",
        "lifecycle_state": "verified",
        "review_state": "accepted",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        **overrides,
    }


def snapshot(image_ids: list[str], annotations: list[dict[str, Any]]) -> SnapshotData:
    return SnapshotData(
        dataset_id=uuid4(),
        version_id=uuid4(),
        snapshot_at=datetime.now(UTC),
        schema_version="v1",
        content_sha256="d" * 64,
        images=[
            {
                "id": image_id,
                "content_sha256": "a" * 64,
                "canonical_width": 100,
                "canonical_height": 80,
            }
            for image_id in image_ids
        ],
        annotations=annotations,
        skus={},
    )


class FakeDetector:
    """Answers like a model service, optionally switching models after the first photo."""

    def __init__(self, boxes: list[tuple[float, ...]], versions: tuple[str, ...] = ("v1",)):
        self.boxes = boxes
        self.versions = versions
        self.calls = 0

    def execute(self, request: Any, image_path: Path | None) -> dict[str, Any]:
        version = self.versions[min(self.calls, len(self.versions) - 1)]
        self.calls += 1
        return {
            "model_provenance": {
                "model_id": "fake-detector",
                "model_version": version,
                "artifact_sha256": ("b" if version == "v1" else "c") * 64,
            },
            "predictions": [
                {
                    "proposal_id": f"{request.request_id}:{index}",
                    "geometry": {
                        "type": "axis_aligned_box",
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                    },
                    "class_type": "product",
                    "score": score,
                    "candidate_sku_id": None,
                }
                for index, (x, y, width, height, score) in enumerate(self.boxes, 1)
            ],
        }


def service(detector: FakeDetector, tmp_path: Path) -> ModelService:
    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    return ModelService(
        {"known_sku_detector": detector},
        lambda image: ResolvedModelImage(path, "a" * 64, 100, 80),
    )


def test_evaluation_scores_verified_products_and_reports_progress(tmp_path: Path) -> None:
    image_id = str(uuid4())
    release = snapshot(
        [image_id],
        [
            product_row(image_id, PRODUCTS[0]),
            product_row(image_id, PRODUCTS[1]),
            # Neither counts as ground truth: one is a gap, one was never verified.
            product_row(image_id, (70, 10, 20, 30), class_type="gap"),
            product_row(image_id, (70, 40, 20, 30), lifecycle_state="proposed"),
        ],
    )
    detector = FakeDetector([(*PRODUCTS[0], 0.9), (*PRODUCTS[1], 0.8), (70, 40, 20, 30, 0.1)])
    progress: list[tuple[int, int]] = []

    report = evaluate_snapshot(
        release,
        service(detector, tmp_path),
        "http://runtime.local",
        0.3,
        lambda current, total: progress.append((current, total)),
    )

    assert progress == [(1, 1)]
    assert report["model_id"] == "fake-detector"
    assert report["model_artifact_sha256"] == "b" * 64
    assert (report["ground_truth_boxes"], report["predicted_boxes"]) == (2, 3)
    assert report["metrics"]["product_recall_at_iou_50"] == 1
    assert report["metrics"]["precision_at_iou_50"] == pytest.approx(2 / 3)
    assert report["metrics"]["map_50"] == 1
    assert report["metrics"]["dense_scene_images"] == 0


def test_evaluation_refuses_a_model_that_changes_midway(tmp_path: Path) -> None:
    first, second = str(uuid4()), str(uuid4())
    release = snapshot([first, second], [product_row(first, PRODUCTS[0])])
    detector = FakeDetector([(*PRODUCTS[0], 0.9)], versions=("v1", "v2"))

    with pytest.raises(JobExecutionError) as error:
        evaluate_snapshot(release, service(detector, tmp_path), "http://r", 0.3, lambda *_: None)

    assert error.value.code == "model_changed"


def test_evaluation_needs_verified_products(tmp_path: Path) -> None:
    image_id = str(uuid4())
    release = snapshot([image_id], [product_row(image_id, PRODUCTS[0], review_state="rejected")])

    with pytest.raises(JobExecutionError) as error:
        evaluate_snapshot(
            release, service(FakeDetector([]), tmp_path), "http://r", 0.3, lambda *_: None
        )

    assert error.value.code == "no_verified_products"


@dataclass(frozen=True)
class Release:
    dataset_id: UUID
    version_id: UUID
    media_root: Path


@pytest.fixture
def release(database_engine: Engine, tmp_path: Path) -> Iterator[Release]:
    dataset_id, version_id, image_id = uuid4(), uuid4(), uuid4()
    media_root = tmp_path / "media"
    canonical = media_root / "canonical" / f"{image_id}.png"
    canonical.parent.mkdir(parents=True)
    pixels = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(pixels, format="PNG")
    canonical.write_bytes(pixels.getvalue())
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"evaluation-{dataset_id}"))
        connection.execute(insert(dataset_versions).values(id=version_id, dataset_id=dataset_id))
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.png",
                canonical_media_key=f"canonical/{image_id}.png",
                thumbnail_media_key=f"thumbnails/{image_id}.jpg",
                media_type="image/png",
                original_filename="shelf.png",
                content_sha256=uuid4().hex * 2,
                canonical_width=100,
                canonical_height=80,
            )
        )
        connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id, dataset_version_id=version_id, image_id=image_id
            )
        )
        for x, y, width, height in PRODUCTS:
            create_annotation(
                connection,
                image_id,
                {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "class_type": "product",
                    "sku_id": None,
                    "lifecycle_state": "verified",
                    "review_state": "accepted",
                    "source": "human",
                    "provenance": {},
                    "confidence": None,
                    "occluded": False,
                    "truncated": False,
                    "shelf_row": 0,
                },
            )
        snapshot_dataset_version(connection, version_id)

    yield Release(dataset_id, version_id, media_root)

    with database_engine.begin() as connection:
        connection.execute(delete(jobs).where(jobs.c.dataset_version_id == version_id))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


@pytest.fixture
def runtime() -> Iterator[str]:
    artifact_sha256 = uuid4().hex * 2

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            self.rfile.read(int(self.headers["Content-Length"]))
            answer = json.dumps(
                {
                    "model_provenance": {
                        "model_id": "served-detector",
                        "model_version": artifact_sha256[:12],
                        "artifact_sha256": artifact_sha256,
                    },
                    "predictions": [
                        {
                            "proposal_id": f"p{index}",
                            "geometry": {
                                "type": "axis_aligned_box",
                                "x": x,
                                "y": y,
                                "width": width,
                                "height": height,
                            },
                            "class_type": "product",
                            "score": 0.9,
                            "candidate_sku_id": None,
                        }
                        for index, (x, y, width, height) in enumerate(PRODUCTS)
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_worker_evaluation_registers_the_served_model(
    database_engine: Engine,
    release: Release,
    runtime: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shelfsight_api.model_evaluation.get_engine", lambda: database_engine)
    monkeypatch.setattr(
        "shelfsight_api.model_evaluation.get_media_root", lambda: release.media_root
    )
    with database_engine.begin() as connection:
        job = enqueue_detector_evaluation(connection, release.version_id, runtime, 0.3)
    assert job["progress_total"] == 1
    with database_engine.begin() as connection, pytest.raises(EvaluationNotReadyError):
        register_evaluated_detector(connection, job["id"], "Trained elsewhere", True, "owner:o")

    worker = JobWorker(
        database_engine,
        "evaluation-test",
        {EVALUATION_JOB_TYPE: evaluation_job_definition()},
        claim_registered_only=True,
    )
    assert worker.run_once() is True
    with database_engine.connect() as connection:
        finished = connection.execute(select(jobs).where(jobs.c.id == job["id"])).mappings().one()
    assert finished["state"] == "succeeded", finished["error_code"]
    assert finished["progress_current"] == 1
    assert finished["result"]["metrics"]["product_recall_at_iou_50"] == 1

    with database_engine.begin() as connection:
        entry = register_evaluated_detector(
            connection, job["id"], "Detector trained elsewhere", True, "owner:o"
        )
    with database_engine.begin() as connection:
        again = register_evaluated_detector(
            connection, job["id"], "Detector trained elsewhere", True, "owner:o"
        )
    assert entry["lineage"] == "external"
    assert entry["source"] == "Detector trained elsewhere"
    assert entry["deployment_status"] == "candidate"
    assert entry["evaluation_dataset_version_id"] == release.version_id
    assert again["id"] == entry["id"]


def test_evaluations_need_a_web_address_and_a_signed_off_release(
    database_engine: Engine,
    release: Release,
) -> None:
    unsigned = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(dataset_versions).values(id=unsigned, dataset_id=release.dataset_id)
        )
    with database_engine.begin() as connection, pytest.raises(InvalidRuntimeUrlError):
        enqueue_detector_evaluation(connection, release.version_id, "file:///etc/passwd", 0.3)
    with database_engine.begin() as connection, pytest.raises(DatasetSnapshotNotFoundError):
        enqueue_detector_evaluation(connection, unsigned, "http://runtime.local", 0.3)


def test_evaluation_api_queues_jobs_and_explains_refusals(
    database_engine: Engine,
    release: Release,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shelfsight_api.model_registry_api.get_engine", lambda: database_engine)
    monkeypatch.setenv("SHELFSIGHT_DETECTOR_RUNTIME_URL", "http://host.docker.internal:8091")

    defaults = client.get("/api/model-registry/evaluations/defaults")
    queued = client.post(
        "/api/model-registry/evaluations",
        json={"dataset_version_id": str(release.version_id), "runtime_url": "http://runtime.local"},
    )
    bad_url = client.post(
        "/api/model-registry/evaluations",
        json={"dataset_version_id": str(release.version_id), "runtime_url": "ftp://x"},
    )
    not_ready = client.post(
        f"/api/model-registry/evaluations/{queued.json()['id']}/register",
        json={"source": "Elsewhere", "licenses_approved": True},
    )

    assert defaults.json() == {"runtime_url": "http://host.docker.internal:8091"}
    assert queued.status_code == 202, queued.text
    assert queued.json()["job_type"] == EVALUATION_JOB_TYPE
    assert queued.json()["payload"]["confidence_threshold"] == 0.3
    assert bad_url.json()["detail"]["code"] == "invalid_runtime_url"
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["code"] == "evaluation_not_ready"
