from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, delete, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation
from shelfsight_api.models import (
    dataset_review_signoffs,
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    skus,
    snapshot_artifacts,
)

client = TestClient(app)


def configure_dataset_api(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> None:
    monkeypatch.setattr("shelfsight_api.dataset_api.get_engine", lambda: database_engine)


def test_create_dataset_opens_a_version_and_normalizes_input(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    name = f"gate-dataset-{uuid4()}"

    response = client.post(
        "/api/datasets",
        json={"name": f"  {name}  ", "description": "   "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == name
    assert body["description"] is None
    assert body["imported_skus"] == 0
    with database_engine.connect() as connection:
        version = connection.execute(
            select(
                dataset_versions.c.dataset_id,
                dataset_versions.c.snapshot_at,
            ).where(dataset_versions.c.id == body["open_version_id"])
        ).one()
        assert str(version.dataset_id) == body["id"]
        assert version.snapshot_at is None

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == body["id"]))


def test_project_catalog_import_is_validated_and_transactional(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    project_name = f"catalog-onboarding-{uuid4()}"
    created = client.post(
        "/api/datasets",
        json={
            "name": project_name,
            "catalog": [
                {
                    "name": "  Aurora Cola Zero  ",
                    "upc": "012345678905",
                    "category": " Beverages ",
                    "brand": "Aurora",
                },
                {"name": "Northstar Water", "upc": None, "variant": "1 L"},
            ],
        },
    )

    assert created.status_code == 201
    assert created.json()["imported_skus"] == 2
    with database_engine.connect() as connection:
        imported = connection.execute(
            select(skus.c.name, skus.c.upc, skus.c.category)
            .where(skus.c.is_unknown.is_(False))
            .order_by(skus.c.name)
        ).all()
    assert imported == [
        ("Aurora Cola Zero", "012345678905", "Beverages"),
        ("Northstar Water", None, None),
    ]

    repeated_payload = client.post(
        "/api/datasets",
        json={
            "name": f"catalog-duplicate-payload-{uuid4()}",
            "catalog": [
                {"name": "First", "upc": "012345678912"},
                {"name": "Second", "upc": "012345678912"},
            ],
        },
    )
    conflicting_project = f"catalog-conflict-{uuid4()}"
    existing_conflict = client.post(
        "/api/datasets",
        json={
            "name": conflicting_project,
            "catalog": [{"name": "Duplicate", "upc": "012345678905"}],
        },
    )
    invalid_upc = client.post(
        "/api/datasets",
        json={
            "name": f"catalog-invalid-{uuid4()}",
            "catalog": [{"name": "Invalid", "upc": "123"}],
        },
    )

    assert repeated_payload.status_code == 422
    assert existing_conflict.status_code == 409
    assert existing_conflict.json()["detail"]["code"] == "duplicate_upc"
    assert invalid_upc.status_code == 422
    with database_engine.connect() as connection:
        rolled_back = connection.execute(
            select(datasets.c.id).where(datasets.c.name == conflicting_project)
        ).scalar_one_or_none()
    assert rolled_back is None

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == created.json()["id"]))
        connection.execute(delete(skus).where(skus.c.is_unknown.is_(False)))


def test_list_datasets_returns_project_summary(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    name = f"project-list-{uuid4()}"
    created = client.post(
        "/api/datasets",
        json={"name": name, "description": "Quarterly retail audit"},
    ).json()
    with database_engine.begin() as connection:
        connection.execute(
            insert(images).values(
                dataset_id=created["id"],
                original_media_key="original/fixture.jpg",
                canonical_media_key="canonical/fixture.jpg",
                thumbnail_media_key="thumbnail/fixture.jpg",
                media_type="image/jpeg",
                original_filename="fixture.jpg",
                content_sha256="a" * 64,
                canonical_width=100,
                canonical_height=80,
            )
        )

    response = client.get("/api/datasets")

    assert response.status_code == 200
    project = next(item for item in response.json() if item["id"] == created["id"])
    assert project["name"] == name
    assert project["description"] == "Quarterly retail audit"
    assert project["open_version_id"] == created["open_version_id"]
    assert project["latest_version_id"] == created["open_version_id"]
    assert project["image_count"] == 1
    assert project["created_at"]

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == created["id"]))


def test_version_management_lists_releases_and_starts_the_next_working_version(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    created = client.post(
        "/api/datasets",
        json={"name": f"version-management-{uuid4()}"},
    ).json()
    dataset_id = created["id"]
    version_id = created["open_version_id"]
    image_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(images).values(**_progress_image(dataset_id, image_id, "reviewed"))
        )
        connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id,
                dataset_version_id=version_id,
                image_id=image_id,
            )
        )

    assert client.post(f"/api/dataset-versions/{version_id}/snapshot").status_code == 200
    frozen = client.get(f"/api/datasets/{dataset_id}/versions")
    assert frozen.status_code == 200
    assert frozen.json()[0]["status"] == "frozen"
    assert frozen.json()[0]["review_signoff"] is None

    with database_engine.begin() as connection:
        connection.execute(
            insert(dataset_review_signoffs).values(
                dataset_version_id=version_id,
                signed_by="owner:release-test",
                reviewed_annotation_count=0,
                risk_item_count=0,
            )
        )
        connection.execute(
            insert(snapshot_artifacts).values(
                dataset_version_id=version_id,
                artifact_type="export",
                artifact_key=f"{version_id}:detection:test",
                content_sha256="b" * 64,
                metadata={"export_type": "detection"},
            )
        )

    released = client.get(f"/api/datasets/{dataset_id}/versions")
    assert released.status_code == 200
    release = released.json()[0]
    assert release["status"] == "released"
    assert release["image_count"] == 1
    assert release["review_signoff"]["signed_by"] == "owner:release-test"
    assert release["export_types"] == ["detection"]

    next_version = client.post(f"/api/datasets/{dataset_id}/versions")
    repeated = client.post(f"/api/datasets/{dataset_id}/versions")
    assert next_version.status_code == 201
    assert next_version.json()["parent_version_id"] == version_id
    assert next_version.json()["image_count"] == 1
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "dataset_open_version_exists"

    versions = client.get(f"/api/datasets/{dataset_id}/versions").json()
    assert [version["status"] for version in versions] == ["working", "released"]
    assert versions[0]["parent_version_id"] == version_id

    missing_id = uuid4()
    assert client.get(f"/api/datasets/{missing_id}/versions").status_code == 404
    assert client.post(f"/api/datasets/{missing_id}/versions").status_code == 404

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def test_dataset_version_progress_tracks_decisions_identity_and_qa(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    created = client.post(
        "/api/datasets",
        json={"name": f"project-progress-{uuid4()}"},
    ).json()
    dataset_id = created["id"]
    version_id = created["open_version_id"]
    first_image_id = uuid4()
    second_image_id = uuid4()
    known_sku_id = uuid4()

    with database_engine.begin() as connection:
        unknown_sku_id = connection.execute(
            select(skus.c.id).where(skus.c.is_unknown.is_(True))
        ).scalar_one()
        connection.execute(
            insert(skus).values(
                id=known_sku_id,
                name=f"Known {known_sku_id}",
            )
        )
        connection.execute(
            insert(images),
            [
                _progress_image(dataset_id, first_image_id, "in_progress"),
                _progress_image(dataset_id, second_image_id, "reviewed"),
            ],
        )
        connection.execute(
            insert(dataset_version_images),
            [
                {
                    "dataset_id": dataset_id,
                    "dataset_version_id": version_id,
                    "image_id": first_image_id,
                },
                {
                    "dataset_id": dataset_id,
                    "dataset_version_id": version_id,
                    "image_id": second_image_id,
                },
            ],
        )
        _progress_annotation(connection, first_image_id, None, "proposed", "unreviewed")
        _progress_annotation(
            connection,
            first_image_id,
            known_sku_id,
            "verified",
            "accepted",
        )
        _progress_annotation(
            connection,
            second_image_id,
            unknown_sku_id,
            "verified",
            "accepted",
        )
        _progress_annotation(connection, second_image_id, None, "rejected", "unreviewed")
        _progress_annotation(connection, second_image_id, None, "verified", "flagged")

    response = client.get(f"/api/dataset-versions/{version_id}/progress")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_version_id": version_id,
        "images": {"total": 2},
        "annotations": {"total": 5, "decided": 4},
        "identity": {
            "accepted_products": 2,
            "known_products": 1,
            "unknown_products": 1,
            "unassigned_products": 0,
        },
        "qa": {"reviewed_images": 1, "flagged_annotations": 1},
    }

    snapshot = client.post(f"/api/dataset-versions/{version_id}/snapshot")
    frozen = client.get(f"/api/dataset-versions/{version_id}/progress")
    assert snapshot.status_code == 200
    assert frozen.status_code == 200
    assert frozen.json() == response.json()

    missing = client.get(f"/api/dataset-versions/{uuid4()}/progress")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(delete(skus).where(skus.c.id == known_sku_id))


def test_dataset_validation_duplicate_and_snapshot_errors_are_stable(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dataset_api(monkeypatch, database_engine)
    name = f"gate-errors-{uuid4()}"
    blank = client.post("/api/datasets", json={"name": "   "})
    first = client.post("/api/datasets", json={"name": name})
    duplicate = client.post("/api/datasets", json={"name": name})

    assert blank.status_code == 422
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "dataset_name_conflict"

    version_id = first.json()["open_version_id"]
    snapshot = client.post(f"/api/dataset-versions/{version_id}/snapshot")
    repeated = client.post(f"/api/dataset-versions/{version_id}/snapshot")
    missing = client.post(f"/api/dataset-versions/{uuid4()}/snapshot")

    assert snapshot.status_code == 200
    assert snapshot.json()["captured_annotations"] == 0
    assert snapshot.json()["schema_version"] == "shelfsight-dataset-snapshot/v2"
    assert len(snapshot.json()["content_sha256"]) == 64
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "dataset_version_frozen"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"
    listed = client.get("/api/datasets")
    frozen_project = next(
        item for item in listed.json() if item["id"] == first.json()["id"]
    )
    assert frozen_project["open_version_id"] is None
    assert frozen_project["latest_version_id"] == version_id

    manifest = client.get(f"/api/dataset-versions/{version_id}/snapshot")
    artifact = client.post(
        f"/api/dataset-versions/{version_id}/artifacts",
        json={
            "artifact_type": "evaluation",
            "artifact_key": f"evaluations/{version_id}.json",
            "content_sha256": "a" * 64,
            "metadata": {"metric": "map50", "value": 0.8},
        },
    )
    conflict = client.post(
        f"/api/dataset-versions/{version_id}/artifacts",
        json={
            "artifact_type": "evaluation",
            "artifact_key": f"evaluations/{version_id}.json",
            "content_sha256": "b" * 64,
        },
    )
    missing_manifest = client.get(f"/api/dataset-versions/{uuid4()}/snapshot")

    assert manifest.status_code == 200
    assert manifest.json()["dataset_version_id"] == version_id
    assert artifact.status_code == 201
    assert artifact.json()["dataset_version_id"] == version_id
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "snapshot_artifact_conflict"
    assert missing_manifest.status_code == 404
    assert missing_manifest.json()["detail"]["code"] == "dataset_snapshot_not_found"

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == first.json()["id"]))


def _progress_image(dataset_id: str, image_id: UUID, status: str) -> dict[str, object]:
    return {
        "id": image_id,
        "dataset_id": dataset_id,
        "original_media_key": f"original/{image_id}.jpg",
        "canonical_media_key": f"canonical/{image_id}.jpg",
        "thumbnail_media_key": f"thumbnail/{image_id}.jpg",
        "media_type": "image/jpeg",
        "original_filename": f"{image_id}.jpg",
        "content_sha256": str(image_id).replace("-", "") * 2,
        "canonical_width": 100,
        "canonical_height": 80,
        "status": status,
    }


def _progress_annotation(
    connection: Connection,
    image_id: UUID,
    sku_id: UUID | None,
    lifecycle_state: str,
    review_state: str,
) -> None:
    create_annotation(
        connection,
        image_id,
        {
            "x": 10.0,
            "y": 10.0,
            "width": 20.0,
            "height": 30.0,
            "class_type": "product",
            "sku_id": sku_id,
            "lifecycle_state": lifecycle_state,
            "review_state": review_state,
            "source": "human",
            "provenance": {"fixture": "project-progress"},
            "confidence": None,
            "occluded": False,
            "truncated": False,
            "shelf_row": 0,
        },
    )
