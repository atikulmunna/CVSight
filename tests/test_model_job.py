from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, delete, func, insert, select

from shelfsight_api.data_model import create_annotation
from shelfsight_api.job_service import enqueue_job, get_job
from shelfsight_api.model_service import (
    ModelService,
    ResolvedModelImage,
    model_job_definitions,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    jobs,
    worker_heartbeats,
)
from shelfsight_api.worker import JobWorker


def test_model_failure_cannot_mutate_existing_annotations(
    database_engine: Engine,
) -> None:
    dataset_id = uuid4()
    version_id = uuid4()
    image_id = uuid4()
    annotation_id = uuid4()
    image_sha256 = "a" * 64
    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(insert(datasets).values(id=dataset_id, name=f"model-{dataset_id}"))
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.png",
                canonical_media_key=f"canonical/{image_id}.png",
                thumbnail_media_key=f"thumbnail/{image_id}.jpg",
                media_type="image/png",
                original_filename="model.png",
                content_sha256=image_sha256,
                canonical_width=100,
                canonical_height=80,
            )
        )
        connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id,
                dataset_version_id=version_id,
                image_id=image_id,
            )
        )
        create_annotation(
            connection,
            image_id,
            {
                "x": 10,
                "y": 10,
                "width": 20,
                "height": 30,
                "class_type": "product",
                "sku_id": None,
                "lifecycle_state": "verified",
                "review_state": "accepted",
                "source": "human",
                "provenance": {"actor": "test"},
                "confidence": None,
                "occluded": False,
                "truncated": False,
                "shelf_row": 0,
            },
            annotation_id=annotation_id,
        )

    class FailingAdapter:
        def execute(
            self,
            _request: object,
            _image_path: Path | None,
        ) -> Mapping[str, Any]:
            raise RuntimeError("model process crashed")

    service = ModelService(
        {"known_sku_detector": FailingAdapter()},
        lambda _image: ResolvedModelImage(
            path=Path("unused"),
            sha256=image_sha256,
            width=100,
            height=80,
        ),
    )
    request = {
        "request_id": "failure-safety",
        "operation": "detect",
        "image": {
            "image_id": str(image_id),
            "sha256": image_sha256,
            "width": 100,
            "height": 80,
        },
        "model_role": "known_sku_detector",
        "configuration": {
            "confidence_threshold": 0.3,
            "inference_strategy": "full_image",
        },
    }
    with database_engine.begin() as connection:
        job, _ = enqueue_job(
            connection,
            "detect",
            f"failure:{image_id}",
            request,
            max_attempts=1,
        )

    worker = JobWorker(
        database_engine,
        "model-failure-worker",
        model_job_definitions(service),
    )
    assert worker.run_once() is True

    with database_engine.connect() as connection:
        failed_job = get_job(connection, job["id"])
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

    assert failed_job["state"] == "failed"
    assert failed_job["error_code"] == "model_execution_failed"
    assert current_revision == 1
    assert revision_count == 1

    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
