from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import append_annotation_revision, create_annotation
from shelfsight_api.model_adapters import ClipImageEmbedderAdapter
from shelfsight_api.model_contract import AnnotationEmbeddingTarget, EmbedRequest
from shelfsight_api.model_service import (
    ModelService,
    ResolvedModelImage,
    model_job_definitions,
)
from shelfsight_api.models import (
    annotation_revisions,
    datasets,
    images,
    jobs,
    propagation_suggestion_sets,
    propagation_suggestions,
    sku_hard_pairs,
    skus,
    worker_heartbeats,
)
from shelfsight_api.propagation_profile import (
    T005_EXPLORATORY_PROPAGATION_PROFILE,
)
from shelfsight_api.propagation_service import write_propagation_embedding
from shelfsight_api.worker import JobWorker

client = TestClient(app)
ACTOR_HEADERS = {"X-ShelfSight-Actor": "annotator:test"}
UNKNOWN_SKU_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def propagation_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    dataset_id = uuid4()
    image_id = uuid4()
    other_image_id = uuid4()
    seed_annotation_id = uuid4()
    first_candidate_id = uuid4()
    second_candidate_id = uuid4()
    unknown_candidate_id = uuid4()
    other_image_candidate_id = uuid4()
    seed_sku_id = uuid4()
    hard_pair_sku_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"propagation-{dataset_id}")
        )
        _insert_image(connection, dataset_id, image_id)
        _insert_image(connection, dataset_id, other_image_id)
        connection.execute(
            insert(skus),
            [
                {
                    "id": seed_sku_id,
                    "name": "Seed SKU",
                    "brand": "Shared Brand",
                    "variant": "Large",
                },
                {
                    "id": hard_pair_sku_id,
                    "name": "Confusable SKU",
                    "brand": "Shared Brand",
                    "variant": "Small",
                },
            ],
        )
        create_annotation(
            connection,
            image_id,
            _accepted_values(x=5),
            annotation_id=seed_annotation_id,
        )
        append_annotation_revision(
            connection,
            seed_annotation_id,
            1,
            _accepted_values(
                x=5,
                sku_id=seed_sku_id,
                provenance={
                    "actor": "annotator:test",
                    "action": "assign_sku",
                    "via": "api",
                },
            ),
        )
        create_annotation(
            connection,
            image_id,
            _accepted_values(x=30),
            annotation_id=first_candidate_id,
        )
        create_annotation(
            connection,
            image_id,
            _accepted_values(x=55),
            annotation_id=second_candidate_id,
        )
        create_annotation(
            connection,
            image_id,
            _accepted_values(x=80, width=15, sku_id=UNKNOWN_SKU_ID),
            annotation_id=unknown_candidate_id,
        )
        create_annotation(
            connection,
            other_image_id,
            _accepted_values(x=30),
            annotation_id=other_image_candidate_id,
        )
    monkeypatch.setattr("shelfsight_api.propagation_api.get_engine", lambda: database_engine)

    vectors = {
        seed_annotation_id: _unit_vector(0),
        first_candidate_id: _unit_vector(0),
        second_candidate_id: _mixed_vector(),
        unknown_candidate_id: _unit_vector(0),
    }
    target = {
        "dataset_id": dataset_id,
        "image_id": image_id,
        "other_image_id": other_image_id,
        "seed_annotation_id": seed_annotation_id,
        "first_candidate_id": first_candidate_id,
        "second_candidate_id": second_candidate_id,
        "unknown_candidate_id": unknown_candidate_id,
        "other_image_candidate_id": other_image_candidate_id,
        "seed_sku_id": seed_sku_id,
        "hard_pair_sku_id": hard_pair_sku_id,
        "vectors": vectors,
        "worker": _propagation_worker(database_engine, vectors),
    }
    yield target

    with database_engine.begin() as connection:
        connection.execute(delete(worker_heartbeats))
        connection.execute(delete(jobs))
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(
            delete(skus).where(skus.c.id.in_((seed_sku_id, hard_pair_sku_id)))
        )


def test_suggestions_remain_separate_until_transactional_confirmation(
    database_engine: Engine,
    propagation_target: dict[str, Any],
) -> None:
    _queue_and_embed_all(propagation_target)
    created = _create_suggestions(propagation_target)

    assert created.status_code == 201
    body = created.json()
    assert body["automatic_confirmation"] is False
    assert body["selected_count"] == 0
    assert body["skipped_count"] == 2
    assert [candidate["annotation_id"] for candidate in body["candidates"]] == [
        str(propagation_target["first_candidate_id"]),
        str(propagation_target["second_candidate_id"]),
    ]
    assert body["candidates"][0]["score"] == pytest.approx(1)
    assert all(candidate["selected"] is False for candidate in body["candidates"])

    with database_engine.connect() as connection:
        before = connection.execute(
            select(annotation_revisions.c.sku_id).where(
                annotation_revisions.c.annotation_id.in_(
                    (
                        propagation_target["first_candidate_id"],
                        propagation_target["second_candidate_id"],
                    )
                )
            )
        ).scalars().all()
        statuses = connection.execute(
            select(propagation_suggestions.c.status).where(
                propagation_suggestions.c.suggestion_set_id
                == UUID(body["suggestion_set_id"])
            )
        ).scalars().all()
    assert len(before) == 2
    assert all(sku_id is None for sku_id in before)
    assert statuses == ["suggested", "suggested"]

    response = _confirm(
        body,
        confirm_ids={body["candidates"][0]["suggestion_id"]},
    )

    assert response.status_code == 200
    assert response.json()["selected_count"] == 1
    assert response.json()["skipped_count"] == 1
    confirmed = response.json()["annotations"][0]
    assert confirmed["sku_id"] == str(propagation_target["seed_sku_id"])
    assert confirmed["source"] == "propagated"
    assert confirmed["provenance"]["action"] == "confirm_propagation"
    assert confirmed["provenance"]["actor"] == "annotator:test"

    with database_engine.connect() as connection:
        completed = connection.execute(
            select(propagation_suggestion_sets).where(
                propagation_suggestion_sets.c.id == UUID(body["suggestion_set_id"])
            )
        ).mappings().one()
        decisions = connection.execute(
            select(propagation_suggestions.c.status)
            .where(
                propagation_suggestions.c.suggestion_set_id
                == UUID(body["suggestion_set_id"])
            )
            .order_by(propagation_suggestions.c.score.desc())
        ).scalars().all()
    assert completed["status"] == "completed"
    assert completed["selected_count"] == 1
    assert completed["skipped_count"] == 1
    assert decisions == ["confirmed", "skipped"]


def test_hard_pair_requires_explicit_individual_review(
    database_engine: Engine,
    propagation_target: dict[str, Any],
) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            insert(sku_hard_pairs).values(
                first_sku_id=propagation_target["seed_sku_id"],
                second_sku_id=propagation_target["hard_pair_sku_id"],
                reason="same brand, different size",
            )
        )
    _queue_and_embed_all(propagation_target)
    body = _create_suggestions(propagation_target).json()

    assert body["hard_pairs"] == [
        {
            "sku_id": str(propagation_target["hard_pair_sku_id"]),
            "name": "Confusable SKU",
            "brand": "Shared Brand",
            "variant": "Small",
            "reason": "same brand, different size",
        }
    ]
    assert all(
        candidate["requires_individual_review"] is True
        and candidate["risk_reason"] == "hard_pair"
        for candidate in body["candidates"]
    )

    rejected = _confirm(
        body,
        confirm_ids={body["candidates"][0]["suggestion_id"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "invalid_propagation_decision"

    with database_engine.connect() as connection:
        candidate_revision = connection.execute(
            select(func.count(annotation_revisions.c.revision)).where(
                annotation_revisions.c.annotation_id
                == propagation_target["first_candidate_id"]
            )
        ).scalar_one()
    assert candidate_revision == 1

    accepted = _confirm(
        body,
        confirm_ids={body["candidates"][0]["suggestion_id"]},
        reviewed_ids={body["candidates"][0]["suggestion_id"]},
    )
    assert accepted.status_code == 200


def test_stale_or_unknown_candidate_rolls_back_entire_batch(
    database_engine: Engine,
    propagation_target: dict[str, Any],
) -> None:
    _queue_and_embed_all(propagation_target)
    body = _create_suggestions(propagation_target).json()
    with database_engine.begin() as connection:
        append_annotation_revision(
            connection,
            propagation_target["second_candidate_id"],
            1,
            _accepted_values(
                x=55,
                sku_id=UNKNOWN_SKU_ID,
                provenance={
                    "actor": "annotator:other",
                    "action": "assign_sku",
                    "via": "api",
                },
            ),
        )

    response = _confirm(
        body,
        confirm_ids={
            candidate["suggestion_id"] for candidate in body["candidates"]
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_revision"
    with database_engine.connect() as connection:
        first_revisions = connection.execute(
            select(func.count(annotation_revisions.c.revision)).where(
                annotation_revisions.c.annotation_id
                == propagation_target["first_candidate_id"]
            )
        ).scalar_one()
        set_status = connection.execute(
            select(propagation_suggestion_sets.c.status).where(
                propagation_suggestion_sets.c.id == UUID(body["suggestion_set_id"])
            )
        ).scalar_one()
    assert first_revisions == 1
    assert set_status == "open"


def test_incomplete_index_and_unknown_seed_are_rejected(
    database_engine: Engine,
    propagation_target: dict[str, Any],
) -> None:
    seed_id = propagation_target["seed_annotation_id"]
    queued = client.post(
        "/api/propagation/embeddings",
        json={"annotation_ids": [str(seed_id)]},
    )
    assert queued.status_code == 200
    _drain_worker(propagation_target["worker"])

    incomplete = _create_suggestions(propagation_target)
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"]["code"] == "propagation_index_not_current"

    unknown_seed_id = uuid4()
    with database_engine.begin() as connection:
        create_annotation(
            connection,
            propagation_target["image_id"],
            _accepted_values(x=2, width=2),
            annotation_id=unknown_seed_id,
        )
        append_annotation_revision(
            connection,
            unknown_seed_id,
            1,
            _accepted_values(
                x=2,
                width=2,
                sku_id=UNKNOWN_SKU_ID,
                provenance={
                    "actor": "annotator:test",
                    "action": "assign_sku",
                    "via": "api",
                },
            ),
        )
    rejected = client.post(
        f"/api/annotations/{unknown_seed_id}/propagation-suggestions",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2, "top_k": 18},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "invalid_propagation_seed"


def test_embedding_queue_reports_unknown_and_missing_targets(
    propagation_target: dict[str, Any],
) -> None:
    response = client.post(
        "/api/propagation/embeddings",
        json={
            "annotation_ids": [
                str(propagation_target["unknown_candidate_id"]),
                str(uuid4()),
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "annotation_id": str(propagation_target["unknown_candidate_id"]),
            "status": "rejected",
            "job_id": None,
            "job_state": None,
            "error_code": "unknown_annotation",
        },
        {
            "annotation_id": response.json()["results"][1]["annotation_id"],
            "status": "rejected",
            "job_id": None,
            "job_state": None,
            "error_code": "annotation_not_found",
        },
    ]


class PropagationPredictor:
    def __init__(self, vectors: Mapping[UUID, list[float]]) -> None:
        self._vectors = vectors

    def __call__(self, _path: Path, request: EmbedRequest) -> list[float]:
        target = request.target
        if not isinstance(target, AnnotationEmbeddingTarget):
            raise AssertionError("propagation target is required")
        return self._vectors[target.annotation_id]


def _propagation_worker(
    engine: Engine,
    vectors: Mapping[UUID, list[float]],
) -> JobWorker:
    adapter = ClipImageEmbedderAdapter(
        PropagationPredictor(vectors),
        T005_EXPLORATORY_PROPAGATION_PROFILE.model_provenance,
    )
    service = ModelService(
        {"propagation_embedder": adapter},
        lambda image: ResolvedModelImage(
            path=Path("unused"),
            sha256=image.sha256,
            width=image.width,
            height=image.height,
        ),
    )
    definitions = model_job_definitions(
        service,
        {"embed": write_propagation_embedding},
    )
    return JobWorker(
        engine,
        "propagation-worker",
        {"embed": definitions["embed"]},
    )


def _queue_and_embed_all(target: dict[str, Any]) -> None:
    response = client.post(
        "/api/propagation/embeddings",
        json={
            "annotation_ids": [
                str(target["seed_annotation_id"]),
                str(target["first_candidate_id"]),
                str(target["second_candidate_id"]),
                str(target["unknown_candidate_id"]),
            ]
        },
    )
    assert response.status_code == 200
    assert [result["status"] for result in response.json()["results"]] == [
        "queued",
        "queued",
        "queued",
        "rejected",
    ]
    _drain_worker(target["worker"])


def _create_suggestions(target: dict[str, Any]) -> Any:
    return client.post(
        f"/api/annotations/{target['seed_annotation_id']}/propagation-suggestions",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2, "top_k": 18},
    )


def _confirm(
    suggestion_set: dict[str, Any],
    *,
    confirm_ids: set[str],
    reviewed_ids: set[str] | None = None,
) -> Any:
    reviewed = reviewed_ids or set()
    return client.post(
        (
            "/api/propagation/suggestion-sets/"
            f"{suggestion_set['suggestion_set_id']}/confirm"
        ),
        headers=ACTOR_HEADERS,
        json={
            "decisions": [
                {
                    "suggestion_id": candidate["suggestion_id"],
                    "decision": (
                        "confirm"
                        if candidate["suggestion_id"] in confirm_ids
                        else "skip"
                    ),
                    "reviewed_individually": (
                        candidate["suggestion_id"] in reviewed
                    ),
                }
                for candidate in suggestion_set["candidates"]
            ]
        },
    )


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


def _accepted_values(
    *,
    x: float,
    width: float = 20,
    sku_id: UUID | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "x": x,
        "y": 10,
        "width": width,
        "height": 30,
        "class_type": "product",
        "sku_id": sku_id,
        "lifecycle_state": "verified",
        "review_state": "accepted",
        "source": "human",
        "provenance": provenance or {"actor": "test"},
        "confidence": None,
        "occluded": False,
        "truncated": False,
        "shelf_row": 0,
    }


def _unit_vector(index: int) -> list[float]:
    values = [0.0] * T005_EXPLORATORY_PROPAGATION_PROFILE.dimension
    values[index] = 1.0
    return values


def _mixed_vector() -> list[float]:
    values = [0.0] * T005_EXPLORATORY_PROPAGATION_PROFILE.dimension
    values[0] = 0.8
    values[1] = 0.6
    return values
