from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import append_annotation_revision, create_annotation
from shelfsight_api.job_service import get_job
from shelfsight_api.model_adapters import ClipImageEmbedderAdapter
from shelfsight_api.model_contract import (
    AnnotationEmbeddingTarget,
    EmbedRequest,
    SkuReferenceEmbeddingTarget,
)
from shelfsight_api.model_service import (
    ModelService,
    ResolvedModelImage,
    model_job_definitions,
)
from shelfsight_api.models import (
    datasets,
    embeddings,
    images,
    jobs,
    sku_reference_images,
    skus,
    worker_heartbeats,
)
from shelfsight_api.recognition_profile import T005_RECOGNITION_PROFILE
from shelfsight_api.recognition_service import (
    StaleRecognitionEmbeddingError,
    get_annotation_sku_candidates,
    write_recognition_embedding,
)
from shelfsight_api.worker import JobWorker

client = TestClient(app)


@pytest.fixture
def recognition_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    dataset_id = uuid4()
    image_id = uuid4()
    annotation_id = uuid4()
    first_sku_id = uuid4()
    second_sku_id = uuid4()
    first_reference_id = uuid4()
    first_alternate_reference_id = uuid4()
    second_reference_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"recognition-{dataset_id}"))
        _insert_image(connection, dataset_id, image_id)
        create_annotation(
            connection,
            image_id,
            _accepted_annotation_values(),
            annotation_id=annotation_id,
        )
        connection.execute(
            insert(skus),
            [
                {"id": first_sku_id, "name": "First SKU", "brand": "Brand A"},
                {"id": second_sku_id, "name": "Second SKU", "brand": "Brand B"},
            ],
        )
        _insert_reference(connection, first_reference_id, first_sku_id)
        _insert_reference(connection, first_alternate_reference_id, first_sku_id)
        _insert_reference(connection, second_reference_id, second_sku_id)
    monkeypatch.setattr("shelfsight_api.recognition_api.get_engine", lambda: database_engine)

    target = {
        "dataset_id": dataset_id,
        "image_id": image_id,
        "annotation_id": annotation_id,
        "first_sku_id": first_sku_id,
        "second_sku_id": second_sku_id,
        "reference_ids": [
            first_reference_id,
            first_alternate_reference_id,
            second_reference_id,
        ],
        "vectors": {
            first_reference_id: _unit_vector(0),
            first_alternate_reference_id: _unit_vector(2),
            second_reference_id: _unit_vector(1),
            annotation_id: _unit_vector(0),
        },
    }
    target["worker"] = _recognition_worker(
        database_engine,
        target["vectors"],
        "recognition-worker",
    )
    yield target

    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(
            delete(skus).where(skus.c.id.in_((first_sku_id, second_sku_id)))
        )


def test_async_gallery_and_crop_embeddings_return_grouped_candidates(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = recognition_target["annotation_id"]
    reference_ids = recognition_target["reference_ids"]
    missing_reference_id = uuid4()
    gallery_request = {
        "reference_image_ids": [
            *(str(reference_id) for reference_id in reference_ids),
            str(missing_reference_id),
        ]
    }

    gallery = client.post("/api/recognition/gallery-embeddings", json=gallery_request)
    repeated_gallery = client.post(
        "/api/recognition/gallery-embeddings",
        json={"reference_image_ids": [str(reference_ids[0])]},
    )
    query = client.post(
        f"/api/annotations/{annotation_id}/recognition-embedding",
        json={"expected_revision": 1},
    )
    repeated_query = client.post(
        f"/api/annotations/{annotation_id}/recognition-embedding",
        json={"expected_revision": 1},
    )
    not_ready = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1},
    )

    assert gallery.status_code == 202
    assert [item["status"] for item in gallery.json()["items"]] == [
        "queued",
        "queued",
        "queued",
        "rejected",
    ]
    assert gallery.json()["items"][-1]["error_code"] == "reference_not_found"
    assert repeated_gallery.json()["items"][0]["status"] == "deduplicated"
    assert query.status_code == 202
    assert repeated_query.status_code == 200
    assert repeated_query.json()["job_id"] == query.json()["job_id"]
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["code"] == "embedding_not_ready"

    _drain_worker(recognition_target["worker"])
    candidates = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1, "top_k": 5},
    )

    assert candidates.status_code == 200
    body = candidates.json()
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["sku_id"] == str(recognition_target["first_sku_id"])
    assert body["candidates"][0]["score"] == pytest.approx(1)
    assert body["candidates"][1]["sku_id"] == str(recognition_target["second_sku_id"])
    assert body["candidates"][1]["score"] == pytest.approx(0)
    assert body["unknown_decision"] == {
        "is_unknown": False,
        "threshold": T005_RECOGNITION_PROFILE.unknown_threshold,
        "calibration_version": T005_RECOGNITION_PROFILE.calibration_version,
        "automatic_confirmation": False,
    }
    assert body["index"]["namespace"] == T005_RECOGNITION_PROFILE.index_namespace

    with database_engine.connect() as connection:
        stored = connection.execute(
            select(embeddings).order_by(embeddings.c.subject_type, embeddings.c.id)
        ).mappings().all()
    assert len(stored) == 4
    assert sum(row["subject_type"] == "annotation" for row in stored) == 1
    assert sum(row["subject_type"] == "sku_reference" for row in stored) == 3
    assert all(
        row["model_version"]
        == T005_RECOGNITION_PROFILE.model_provenance.model_version
        for row in stored
    )


def test_new_reference_blocks_partial_gallery_until_embedded(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = recognition_target["annotation_id"]
    _queue_and_embed_current_gallery(recognition_target)

    initial = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1, "top_k": 3},
    )
    assert initial.status_code == 200

    new_sku_id = uuid4()
    new_reference_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(skus).values(id=new_sku_id, name="Cold Start SKU", brand="Brand C")
        )
        _insert_reference(connection, new_reference_id, new_sku_id)
    recognition_target["vectors"][new_reference_id] = _unit_vector(3)

    partial = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1, "top_k": 3},
    )
    queued = client.post(
        "/api/recognition/gallery-embeddings",
        json={"reference_image_ids": [str(new_reference_id)]},
    )
    assert partial.status_code == 409
    assert partial.json()["detail"]["code"] == "gallery_not_current"
    assert queued.status_code == 202

    _drain_worker(recognition_target["worker"])
    current = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1, "top_k": 3},
    )
    assert current.status_code == 200
    assert str(new_sku_id) in {
        candidate["sku_id"] for candidate in current.json()["candidates"]
    }

    with database_engine.begin() as connection:
        connection.execute(delete(skus).where(skus.c.id == new_sku_id))


def test_annotation_edit_makes_old_embedding_explicitly_stale(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = recognition_target["annotation_id"]
    _queue_and_embed_current_gallery(recognition_target)

    with database_engine.begin() as connection:
        append_annotation_revision(
            connection,
            annotation_id,
            1,
            {
                **_accepted_annotation_values(),
                "x": 11,
                "source": "human",
                "provenance": {"actor": "test", "action": "update"},
            },
        )

    stale = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 2},
    )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_embedding"


def test_model_profile_change_cannot_serve_previous_embeddings(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    _queue_and_embed_current_gallery(recognition_target)
    previous = T005_RECOGNITION_PROFILE.model_provenance
    next_profile = replace(
        T005_RECOGNITION_PROFILE,
        index_version="replacement-001",
        model_provenance=previous.model_copy(
            update={"model_version": "replacement-model-001"}
        ),
    )

    with database_engine.connect() as connection:
        with pytest.raises(StaleRecognitionEmbeddingError):
            get_annotation_sku_candidates(
                connection,
                recognition_target["annotation_id"],
                1,
                5,
                profile=next_profile,
            )


def test_calibrated_threshold_marks_low_similarity_query_unknown(
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = recognition_target["annotation_id"]
    recognition_target["vectors"][annotation_id] = _unit_vector(4)
    _queue_and_embed_current_gallery(recognition_target)

    response = client.get(
        f"/api/annotations/{annotation_id}/sku-candidates",
        params={"expected_revision": 1},
    )

    assert response.status_code == 200
    assert response.json()["candidates"][0]["score"] == pytest.approx(0)
    assert response.json()["unknown_decision"]["is_unknown"] is True
    assert response.json()["unknown_decision"]["automatic_confirmation"] is False


def test_edit_during_embedding_job_rolls_back_derived_row(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = recognition_target["annotation_id"]
    queued = client.post(
        f"/api/annotations/{annotation_id}/recognition-embedding",
        json={"expected_revision": 1},
    )
    with database_engine.begin() as connection:
        append_annotation_revision(
            connection,
            annotation_id,
            1,
            {
                **_accepted_annotation_values(),
                "x": 12,
                "source": "human",
                "provenance": {"actor": "test", "action": "update"},
            },
        )

    assert recognition_target["worker"].run_once() is True

    with database_engine.connect() as connection:
        job = get_job(connection, UUID(queued.json()["job_id"]))
        embedding_count = connection.execute(
            select(func.count())
            .select_from(embeddings)
            .where(embeddings.c.annotation_id == annotation_id)
        ).scalar_one()
    assert job["state"] == "failed"
    assert job["error_code"] == "stale_embedding_target"
    assert embedding_count == 0


def test_unaccepted_annotation_cannot_be_embedded(
    database_engine: Engine,
    recognition_target: dict[str, Any],
) -> None:
    annotation_id = uuid4()
    with database_engine.begin() as connection:
        create_annotation(
            connection,
            recognition_target["image_id"],
            {
                **_accepted_annotation_values(),
                "lifecycle_state": "proposed",
                "review_state": "unreviewed",
            },
            annotation_id=annotation_id,
        )

    response = client.post(
        f"/api/annotations/{annotation_id}/recognition-embedding",
        json={"expected_revision": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_annotation_state"


class RecognitionPredictor:
    def __init__(self, vectors: Mapping[UUID, list[float]]) -> None:
        self._vectors = vectors

    def __call__(self, _path: Path, request: EmbedRequest) -> list[float]:
        target = request.target
        if isinstance(target, AnnotationEmbeddingTarget):
            return self._vectors[target.annotation_id]
        if isinstance(target, SkuReferenceEmbeddingTarget):
            return self._vectors[target.reference_image_id]
        raise AssertionError("recognition target is required")


def _recognition_worker(
    engine: Engine,
    vectors: Mapping[UUID, list[float]],
    worker_id: str,
) -> JobWorker:
    adapter = ClipImageEmbedderAdapter(
        RecognitionPredictor(vectors),
        T005_RECOGNITION_PROFILE.model_provenance,
    )
    service = ModelService(
        {"recognition_embedder": adapter},
        lambda image: ResolvedModelImage(
            path=Path("unused"),
            sha256=image.sha256,
            width=image.width,
            height=image.height,
        ),
    )
    definitions = model_job_definitions(
        service,
        {"embed": write_recognition_embedding},
    )
    return JobWorker(engine, worker_id, {"embed": definitions["embed"]})


def _queue_and_embed_current_gallery(target: dict[str, Any]) -> None:
    gallery = client.post(
        "/api/recognition/gallery-embeddings",
        json={
            "reference_image_ids": [
                str(reference_id) for reference_id in target["reference_ids"]
            ]
        },
    )
    query = client.post(
        f"/api/annotations/{target['annotation_id']}/recognition-embedding",
        json={"expected_revision": 1},
    )
    assert gallery.status_code == 202
    assert query.status_code == 202
    _drain_worker(target["worker"])


def _drain_worker(worker: JobWorker) -> None:
    while worker.run_once():
        pass


def _insert_image(connection: Any, dataset_id: UUID, image_id: UUID) -> None:
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


def _insert_reference(
    connection: Any,
    reference_id: UUID,
    sku_id: UUID,
) -> None:
    connection.execute(
        insert(sku_reference_images).values(
            id=reference_id,
            sku_id=sku_id,
            original_media_key=f"original/reference-{reference_id}.png",
            canonical_media_key=f"canonical/reference-{reference_id}.png",
            thumbnail_media_key=f"thumbnail/reference-{reference_id}.jpg",
            media_type="image/png",
            original_filename=f"{reference_id}.png",
            content_sha256=reference_id.hex * 2,
            width=40,
            height=60,
        )
    )


def _accepted_annotation_values() -> dict[str, Any]:
    return {
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
    }


def _unit_vector(index: int) -> list[float]:
    values = [0.0] * T005_RECOGNITION_PROFILE.dimension
    values[index] = 1.0
    return values
