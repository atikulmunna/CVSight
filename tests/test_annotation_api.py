from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, select, update

from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation as create_annotation_record
from shelfsight_api.models import (
    annotation_revisions,
    datasets,
    images,
    skus,
)

client = TestClient(app)
ACTOR_HEADERS = {"X-ShelfSight-Actor": "annotator:test"}
UNKNOWN_SKU_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def annotation_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[UUID, UUID, UUID]:
    dataset_id = uuid4()
    image_id = uuid4()
    sku_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}")
        )
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.jpg",
                canonical_media_key=f"images/{image_id}.jpg",
                thumbnail_media_key=f"thumbnails/{image_id}.jpg",
                media_type="image/jpeg",
                original_filename=f"{image_id}.jpg",
                content_sha256=image_id.hex * 2,
                canonical_width=100,
                canonical_height=80,
            )
        )
        connection.execute(insert(skus).values(id=sku_id, name="Test SKU"))
    monkeypatch.setattr("shelfsight_api.annotation_api.get_engine", lambda: database_engine)

    yield dataset_id, image_id, sku_id

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(delete(skus).where(skus.c.id == sku_id))


def annotation_payload(active_sku_id: UUID, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "x": 10,
        "y": 10,
        "width": 20,
        "height": 30,
        "class_type": "product",
        "sku_id": str(active_sku_id),
        "review_state": "unreviewed",
        "confidence": 0.9,
        "occluded": False,
        "truncated": False,
        "shelf_row": 1,
    }
    payload.update(overrides)
    return payload


def create_annotation(
    image_id: UUID,
    active_sku_id: UUID,
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/api/images/{image_id}/annotations",
        headers=ACTOR_HEADERS,
        json=annotation_payload(active_sku_id, **overrides),
    )
    assert response.status_code == 201
    return response.json()


def test_create_and_read_annotation_records_actor_and_provenance(
    database_engine: Engine,
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target

    created = create_annotation(
        image_id,
        sku_id,
        lifecycle_state="verified",
        review_state="accepted",
    )

    assert created["revision"] == 1
    assert created["lifecycle_state"] == "verified"
    assert created["source"] == "human"
    assert created["provenance"] == {
        "actor": "annotator:test",
        "action": "create",
        "via": "api",
    }

    image_response = client.get(f"/api/images/{image_id}/annotations")
    annotation_response = client.get(f"/api/annotations/{created['id']}")
    assert image_response.status_code == 200
    assert image_response.json()["annotations"] == [created]
    assert annotation_response.status_code == 200
    assert annotation_response.json() == created

    with database_engine.connect() as connection:
        image_status = connection.execute(
            select(images.c.status).where(images.c.id == image_id)
        ).scalar_one()
    assert image_status == "in_progress"


def test_update_requires_current_revision_and_preserves_newer_work(
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    created = create_annotation(image_id, sku_id)
    update_payload = annotation_payload(sku_id, x=12, expected_revision=1)

    updated_response = client.put(
        f"/api/annotations/{created['id']}",
        headers=ACTOR_HEADERS,
        json=update_payload,
    )
    stale_response = client.put(
        f"/api/annotations/{created['id']}",
        headers=ACTOR_HEADERS,
        json=annotation_payload(sku_id, x=14, expected_revision=1),
    )

    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["revision"] == 2
    assert updated["x"] == 12
    assert updated["provenance"]["action"] == "update"
    assert stale_response.status_code == 409
    assert stale_response.json() == {
        "detail": {
            "code": "stale_revision",
            "message": "annotation changed after the expected revision",
        }
    }
    current = client.get(f"/api/annotations/{created['id']}").json()
    assert current["revision"] == 2
    assert current["x"] == 12


def test_annotation_change_reopens_a_reviewed_image(
    database_engine: Engine,
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    created = create_annotation(image_id, sku_id)
    with database_engine.begin() as connection:
        connection.execute(
            update(images).where(images.c.id == image_id).values(status="reviewed")
        )

    response = client.put(
        f"/api/annotations/{created['id']}",
        headers=ACTOR_HEADERS,
        json=annotation_payload(sku_id, x=12, expected_revision=1),
    )

    assert response.status_code == 200
    with database_engine.connect() as connection:
        assert connection.execute(
            select(images.c.status).where(images.c.id == image_id)
        ).scalar_one() == "in_progress"


def test_accept_transitions_proposal_and_rejects_repeated_or_stale_actions(
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    created = create_annotation(image_id, sku_id)

    accepted_response = client.post(
        f"/api/annotations/{created['id']}/accept",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1},
    )
    repeated_response = client.post(
        f"/api/annotations/{created['id']}/accept",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2},
    )
    stale_response = client.post(
        f"/api/annotations/{created['id']}/accept",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1},
    )

    assert accepted_response.status_code == 200
    accepted = accepted_response.json()
    assert accepted["revision"] == 2
    assert accepted["lifecycle_state"] == "verified"
    assert accepted["review_state"] == "accepted"
    assert accepted["provenance"]["action"] == "accept"
    assert repeated_response.status_code == 409
    assert repeated_response.json()["detail"]["code"] == "invalid_state_transition"
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "stale_revision"


def test_assignment_requires_verification_and_preserves_each_audit_revision(
    database_engine: Engine,
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    with database_engine.begin() as connection:
        annotation_id = create_annotation_record(
            connection,
            image_id,
            {
                "x": 10,
                "y": 10,
                "width": 20,
                "height": 30,
                "class_type": "product",
                "sku_id": None,
                "lifecycle_state": "proposed",
                "review_state": "unreviewed",
                "source": "model",
                "provenance": {
                    "action": "proposal",
                    "model": "yolo26",
                },
                "confidence": 0.9,
                "occluded": False,
                "truncated": False,
                "shelf_row": 1,
            },
        )
    created = client.get(f"/api/annotations/{annotation_id}").json()

    premature = client.post(
        f"/api/annotations/{created['id']}/assign-sku",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1, "sku_id": str(sku_id)},
    )
    assert premature.status_code == 409
    assert premature.json()["detail"]["code"] == "invalid_state_transition"

    accepted = client.post(
        f"/api/annotations/{created['id']}/accept",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1},
    ).json()
    assigned_response = client.post(
        f"/api/annotations/{created['id']}/assign-sku",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2, "sku_id": str(sku_id)},
    )
    stale_response = client.post(
        f"/api/annotations/{created['id']}/assign-sku",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2, "sku_id": str(UNKNOWN_SKU_ID)},
    )

    assert accepted["revision"] == 2
    assert assigned_response.status_code == 200
    assigned = assigned_response.json()
    assert assigned["revision"] == 3
    assert assigned["sku_id"] == str(sku_id)
    assert assigned["provenance"] == {
        "actor": "annotator:test",
        "action": "assign_sku",
        "via": "api",
        "assigned_sku_id": str(sku_id),
        "previous_revision": 2,
    }
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "stale_revision"

    unknown_response = client.post(
        f"/api/annotations/{created['id']}/assign-sku",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 3, "sku_id": str(UNKNOWN_SKU_ID)},
    )
    assert unknown_response.status_code == 200
    assert unknown_response.json()["sku_id"] == str(UNKNOWN_SKU_ID)
    assert client.get(f"/api/annotations/{created['id']}").json()["sku_id"] == str(
        UNKNOWN_SKU_ID
    )

    with database_engine.connect() as connection:
        revisions = connection.execute(
            select(
                annotation_revisions.c.revision,
                annotation_revisions.c.sku_id,
                annotation_revisions.c.provenance,
            )
            .where(annotation_revisions.c.annotation_id == UUID(created["id"]))
            .order_by(annotation_revisions.c.revision)
        ).all()
    assert [revision[0] for revision in revisions] == [1, 2, 3, 4]
    assert [revision[2]["action"] for revision in revisions] == [
        "proposal",
        "accept",
        "assign_sku",
        "assign_sku",
    ]
    assert revisions[0][2] == {"action": "proposal", "model": "yolo26"}
    assert revisions[0][1] is None
    assert revisions[2][1] == sku_id
    assert revisions[3][1] == UNKNOWN_SKU_ID


def test_assignment_rejects_inactive_skus(
    database_engine: Engine,
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    created = create_annotation(
        image_id,
        sku_id,
        sku_id=None,
        lifecycle_state="verified",
        review_state="accepted",
    )
    with database_engine.begin() as connection:
        connection.execute(
            skus.update().where(skus.c.id == sku_id).values(status="deprecated")
        )

    response = client.post(
        f"/api/annotations/{created['id']}/assign-sku",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1, "sku_id": str(sku_id)},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_sku"


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"x": 90, "width": 11}, "invalid_geometry"),
        ({"y": 70, "height": 11}, "invalid_geometry"),
        ({"sku_id": str(uuid4())}, "invalid_sku"),
        ({"class_type": "gap"}, "invalid_sku"),
    ],
)
def test_create_rejects_invalid_geometry_and_sku_rules(
    annotation_target: tuple[UUID, UUID, UUID],
    overrides: dict[str, Any],
    error_code: str,
) -> None:
    _, image_id, sku_id = annotation_target

    response = client.post(
        f"/api/images/{image_id}/annotations",
        headers=ACTOR_HEADERS,
        json=annotation_payload(sku_id, **overrides),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == error_code


def test_reject_and_restore_create_auditable_revisions(
    database_engine: Engine,
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    created = create_annotation(image_id, sku_id, lifecycle_state="verified")

    rejected_response = client.post(
        f"/api/annotations/{created['id']}/reject",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 1, "reason": "duplicate"},
    )
    assert rejected_response.status_code == 200
    rejected = rejected_response.json()
    assert rejected["revision"] == 2
    assert rejected["lifecycle_state"] == "rejected"
    assert rejected["provenance"]["reason"] == "duplicate"

    edit_response = client.put(
        f"/api/annotations/{created['id']}",
        headers=ACTOR_HEADERS,
        json=annotation_payload(sku_id, expected_revision=2),
    )
    repeated_reject = client.post(
        f"/api/annotations/{created['id']}/reject",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2},
    )
    assert edit_response.status_code == 409
    assert edit_response.json()["detail"]["code"] == "invalid_state_transition"
    assert repeated_reject.status_code == 409

    restored_response = client.post(
        f"/api/annotations/{created['id']}/restore",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 2},
    )
    assert restored_response.status_code == 200
    restored = restored_response.json()
    assert restored["revision"] == 3
    assert restored["lifecycle_state"] == "verified"
    assert restored["provenance"]["action"] == "restore"
    assert restored["provenance"]["restored_from_revision"] == 1

    repeated_restore = client.post(
        f"/api/annotations/{created['id']}/restore",
        headers=ACTOR_HEADERS,
        json={"expected_revision": 3},
    )
    assert repeated_restore.status_code == 409

    with database_engine.connect() as connection:
        states = connection.execute(
            select(
                annotation_revisions.c.revision,
                annotation_revisions.c.lifecycle_state,
            )
            .where(
                annotation_revisions.c.annotation_id == UUID(created["id"])
            )
            .order_by(annotation_revisions.c.revision)
        ).all()
    assert states == [(1, "verified"), (2, "rejected"), (3, "verified")]


def test_batch_operations_are_transactional(
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    first = create_annotation(image_id, sku_id)
    second = create_annotation(image_id, sku_id, x=40)

    failed_response = client.post(
        "/api/annotations/batch",
        headers=ACTOR_HEADERS,
        json={
            "operations": [
                {
                    "operation": "update",
                    "annotation_id": first["id"],
                    "expected_revision": 1,
                    **annotation_payload(sku_id, x=12),
                },
                {
                    "operation": "reject",
                    "annotation_id": second["id"],
                    "expected_revision": 99,
                },
            ]
        },
    )

    assert failed_response.status_code == 409
    assert client.get(f"/api/annotations/{first['id']}").json()["revision"] == 1
    assert client.get(f"/api/annotations/{first['id']}").json()["x"] == 10
    assert client.get(f"/api/annotations/{second['id']}").json()["revision"] == 1

    successful_response = client.post(
        "/api/annotations/batch",
        headers=ACTOR_HEADERS,
        json={
            "operations": [
                {
                    "operation": "update",
                    "annotation_id": first["id"],
                    "expected_revision": 1,
                    **annotation_payload(sku_id, x=12),
                },
                {
                    "operation": "reject",
                    "annotation_id": second["id"],
                    "expected_revision": 1,
                    "reason": "duplicate",
                },
            ]
        },
    )

    assert successful_response.status_code == 200
    results = successful_response.json()["annotations"]
    assert [(result["revision"], result["lifecycle_state"]) for result in results] == [
        (2, "proposed"),
        (2, "rejected"),
    ]


def test_missing_resources_and_authenticated_write_return_safe_results(
    annotation_target: tuple[UUID, UUID, UUID],
) -> None:
    _, image_id, sku_id = annotation_target
    missing_id = uuid4()

    image_response = client.get(f"/api/images/{missing_id}/annotations")
    annotation_response = client.get(f"/api/annotations/{missing_id}")
    create_response = client.post(
        f"/api/images/{image_id}/annotations",
        json=annotation_payload(sku_id),
    )

    assert image_response.status_code == 404
    assert image_response.json()["detail"]["code"] == "image_not_found"
    assert annotation_response.status_code == 404
    assert annotation_response.json()["detail"]["code"] == "annotation_not_found"
    assert create_response.status_code == 201
