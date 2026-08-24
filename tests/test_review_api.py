from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Connection, Engine, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from shelfsight_api.app import app
from shelfsight_api.data_model import (
    append_annotation_revision,
    create_annotation,
    snapshot_dataset_version,
)
from shelfsight_api.models import (
    annotation_revisions,
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    propagation_suggestion_sets,
    propagation_suggestions,
    review_decisions,
    skus,
)

client = TestClient(app)
ACTOR_HEADERS = {"X-ShelfSight-Actor": "reviewer:test"}


@pytest.fixture
def review_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, UUID]]:
    dataset_id = uuid4()
    version_id = uuid4()
    image_id = uuid4()
    first_sku_id = uuid4()
    second_sku_id = uuid4()
    seed_annotation_id = uuid4()
    propagated_annotation_id = uuid4()
    unknown_annotation_id = uuid4()
    clean_annotation_id = uuid4()
    suggestion_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"review-{dataset_id}")
        )
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        _insert_image(connection, dataset_id, image_id)
        connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id,
                dataset_version_id=version_id,
                image_id=image_id,
            )
        )
        connection.execute(
            insert(skus),
            [
                {"id": first_sku_id, "name": "Review SKU One"},
                {"id": second_sku_id, "name": "Review SKU Two"},
            ],
        )
        create_annotation(
            connection,
            image_id,
            _annotation_values(x=5, sku_id=first_sku_id),
            annotation_id=seed_annotation_id,
        )
        create_annotation(
            connection,
            image_id,
            _annotation_values(
                x=30,
                sku_id=first_sku_id,
                source="propagated",
                provenance={
                    "action": "confirm_propagation",
                    "seed_annotation_id": str(seed_annotation_id),
                    "suggestion_id": str(suggestion_id),
                },
            ),
            annotation_id=propagated_annotation_id,
        )
        create_annotation(
            connection,
            image_id,
            _annotation_values(
                x=55,
                lifecycle_state="proposed",
                review_state="unreviewed",
                source="model",
            ),
            annotation_id=unknown_annotation_id,
        )
        append_annotation_revision(
            connection,
            unknown_annotation_id,
            1,
            _annotation_values(x=75, source="human"),
        )
        create_annotation(
            connection,
            image_id,
            _annotation_values(x=90, width=8, sku_id=first_sku_id),
            annotation_id=clean_annotation_id,
        )
        suggestion_set_id = connection.execute(
            insert(propagation_suggestion_sets)
            .values(
                seed_annotation_id=seed_annotation_id,
                seed_annotation_revision=1,
                seed_sku_id=first_sku_id,
                index_namespace="review-test",
                index_version="v1",
                model_id="review-model",
                model_version="v1",
                artifact_sha256="a" * 64,
                quality_evidence="review-test",
                created_by="annotator:test",
            )
            .returning(propagation_suggestion_sets.c.id)
        ).scalar_one()
        connection.execute(
            insert(propagation_suggestions).values(
                id=suggestion_id,
                suggestion_set_id=suggestion_set_id,
                candidate_annotation_id=propagated_annotation_id,
                candidate_annotation_revision=1,
                score=0.9,
                requires_individual_review=True,
                risk_reason="hard_pair",
            )
        )
        append_annotation_revision(
            connection,
            seed_annotation_id,
            1,
            _annotation_values(x=5, sku_id=second_sku_id),
        )

    monkeypatch.setattr("shelfsight_api.review_api.get_engine", lambda: database_engine)
    target = {
        "dataset_id": dataset_id,
        "version_id": version_id,
        "image_id": image_id,
        "first_sku_id": first_sku_id,
        "second_sku_id": second_sku_id,
        "seed_annotation_id": seed_annotation_id,
        "propagated_annotation_id": propagated_annotation_id,
        "unknown_annotation_id": unknown_annotation_id,
        "clean_annotation_id": clean_annotation_id,
    }
    yield target

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(
            delete(skus).where(skus.c.id.in_((first_sku_id, second_sku_id)))
        )


def test_risk_queue_decisions_flags_and_signoff(
    database_engine: Engine,
    review_target: dict[str, UUID],
) -> None:
    version_id = review_target["version_id"]
    queue = client.get(f"/api/dataset-versions/{version_id}/review-queue")

    assert queue.status_code == 200
    body = queue.json()
    assert body["unresolved_count"] == 2
    assert [item["annotation_id"] for item in body["items"]] == [
        str(review_target["propagated_annotation_id"]),
        str(review_target["unknown_annotation_id"]),
    ]
    assert [reason["code"] for reason in body["items"][0]["risk_reasons"]] == [
        "hard_pair_confusion",
        "consistency_conflict",
        "propagated_origin",
    ]
    assert [reason["code"] for reason in body["items"][1]["risk_reasons"]] == [
        "low_agreement",
        "unknown_status",
    ]
    assert all(
        reason["blocking"]
        for item in body["items"]
        for reason in item["risk_reasons"]
    )

    approved = _decide(
        version_id,
        review_target["propagated_annotation_id"],
        revision=1,
        decision="approve",
    )
    assert approved.status_code == 200
    assert approved.json()["annotation"]["revision"] == 2
    assert approved.json()["annotation"]["provenance"]["action"] == "qa_approve"
    assert approved.json()["decision"]["risk_reasons"] == [
        "hard_pair_confusion",
        "consistency_conflict",
        "propagated_origin",
    ]
    with pytest.raises(IntegrityError, match="snapshot state is immutable"):
        with database_engine.begin() as connection:
            connection.execute(
                update(review_decisions)
                .where(
                    review_decisions.c.id == UUID(approved.json()["decision"]["id"])
                )
                .values(note="rewritten")
            )

    stale = _decide(
        version_id,
        review_target["propagated_annotation_id"],
        revision=1,
        decision="approve",
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_revision"

    flagged = _decide(
        version_id,
        review_target["clean_annotation_id"],
        revision=1,
        decision="flag",
        note="Check edge alignment",
    )
    assert flagged.status_code == 200
    assert flagged.json()["annotation"]["review_state"] == "flagged"
    after_flag = client.get(f"/api/dataset-versions/{version_id}/review-queue").json()
    flagged_item = next(
        item
        for item in after_flag["items"]
        if item["annotation_id"] == str(review_target["clean_annotation_id"])
    )
    assert {reason["code"] for reason in flagged_item["risk_reasons"]} == {
        "annotation_flagged",
        "manual_flag",
    }

    blocked = client.post(
        f"/api/dataset-versions/{version_id}/review-signoff",
        headers=ACTOR_HEADERS,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "review_signoff_blocked"

    assert _decide(
        version_id,
        review_target["clean_annotation_id"],
        revision=2,
        decision="approve",
    ).status_code == 200
    assert _decide(
        version_id,
        review_target["unknown_annotation_id"],
        revision=2,
        decision="approve",
    ).status_code == 200

    resolved = client.get(
        f"/api/dataset-versions/{version_id}/review-queue?include_resolved=true"
    ).json()
    assert resolved["unresolved_count"] == 0
    assert resolved["resolved_count"] == 3
    assert len(resolved["items"]) == 3

    signed = client.post(
        f"/api/dataset-versions/{version_id}/review-signoff",
        headers=ACTOR_HEADERS,
    )
    repeated = client.post(
        f"/api/dataset-versions/{version_id}/review-signoff",
        headers=ACTOR_HEADERS,
    )
    assert signed.status_code == 200
    assert repeated.json() == signed.json()
    assert signed.json()["reviewed_annotation_count"] == 4
    assert signed.json()["risk_item_count"] == 3

    with database_engine.connect() as connection:
        version = connection.execute(
            select(dataset_versions.c.snapshot_at).where(dataset_versions.c.id == version_id)
        ).scalar_one()
        revisions = connection.execute(
            select(
                annotation_revisions.c.annotation_id,
                annotation_revisions.c.revision,
                annotation_revisions.c.provenance,
            ).where(
                annotation_revisions.c.annotation_id.in_(
                    (
                        review_target["propagated_annotation_id"],
                        review_target["unknown_annotation_id"],
                        review_target["clean_annotation_id"],
                    )
                )
            )
        ).all()
    assert version is not None
    assert len(revisions) == 8
    assert any(value.provenance.get("action") == "confirm_propagation" for value in revisions)
    assert any(value.provenance.get("action") == "qa_approve" for value in revisions)


def test_missing_and_pre_frozen_review_versions_are_rejected(
    database_engine: Engine,
    review_target: dict[str, UUID],
) -> None:
    missing = client.get(f"/api/dataset-versions/{uuid4()}/review-queue")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"

    with database_engine.begin() as connection:
        snapshot_dataset_version(connection, review_target["version_id"])
    frozen = client.get(
        f"/api/dataset-versions/{review_target['version_id']}/review-queue"
    )
    assert frozen.status_code == 409
    assert frozen.json()["detail"]["code"] == "review_version_frozen"


def _decide(
    version_id: UUID,
    annotation_id: UUID,
    *,
    revision: int,
    decision: str,
    note: str | None = None,
) -> Response:
    return cast(
        Response,
        client.post(
            f"/api/dataset-versions/{version_id}/review-items/{annotation_id}/decision",
            headers=ACTOR_HEADERS,
            json={
                "expected_revision": revision,
                "decision": decision,
                "note": note,
            },
        ),
    )


def _insert_image(connection: Connection, dataset_id: UUID, image_id: UUID) -> None:
    connection.execute(
        insert(images).values(
            id=image_id,
            dataset_id=dataset_id,
            original_media_key=f"original/{image_id}.png",
            canonical_media_key=f"canonical/{image_id}.png",
            thumbnail_media_key=f"thumbnail/{image_id}.jpg",
            media_type="image/png",
            original_filename="review-shelf.png",
            content_sha256=image_id.hex * 2,
            canonical_width=100,
            canonical_height=80,
            status="labeled",
        )
    )


def _annotation_values(
    *,
    x: float,
    width: float = 15,
    sku_id: UUID | None = None,
    lifecycle_state: str = "verified",
    review_state: str = "accepted",
    source: str = "human",
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "x": x,
        "y": 20.0,
        "width": width,
        "height": 30.0,
        "class_type": "product",
        "sku_id": sku_id,
        "lifecycle_state": lifecycle_state,
        "review_state": review_state,
        "source": source,
        "provenance": dict(provenance or {"actor": "annotator:test"}),
        "confidence": 0.8,
        "occluded": False,
        "truncated": False,
        "shelf_row": 1,
    }
