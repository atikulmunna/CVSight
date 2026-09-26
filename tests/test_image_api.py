from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, func, insert, select, update

from shelfsight_api.app import app
from shelfsight_api.models import dataset_versions, datasets, images

client = TestClient(app)


def make_image_bytes(color: str = "blue", image_format: str = "JPEG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (40, 20), color).save(output, format=image_format)
    return output.getvalue()


@pytest.fixture
def ingest_target(database_engine: Engine) -> tuple[UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}")
        )
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
    yield dataset_id, version_id
    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def configure_image_api(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
    media_root: Path,
    import_root: Path,
) -> None:
    monkeypatch.setattr("shelfsight_api.image_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.image_api.get_media_root", lambda: media_root)
    monkeypatch.setattr("shelfsight_api.image_api.get_import_root", lambda: import_root)


def upload_url(dataset_id: UUID, version_id: UUID) -> str:
    return f"/api/datasets/{dataset_id}/versions/{version_id}/images"


def test_valid_image_survives_upload_and_retrieval_round_trip(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    media_root = tmp_path / "media"
    configure_image_api(monkeypatch, database_engine, media_root, tmp_path / "imports")
    original = make_image_bytes()

    response = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("shelf.jpg", original, "image/jpeg")},
        data={"capture_metadata": '{"store_id":"store-1"}'},
    )

    assert response.status_code == 201
    body = response.json()
    image_id = body["id"]
    assert (body["width"], body["height"]) == (40, 20)
    assert body["capture_metadata"]["provided"] == {"store_id": "store-1"}

    metadata = client.get(f"/api/images/{image_id}")
    assert metadata.status_code == 200
    assert metadata.json() == body

    original_response = client.get(body["original_url"])
    canonical_response = client.get(body["canonical_url"])
    thumbnail_response = client.get(body["thumbnail_url"])
    assert original_response.content == original
    assert original_response.headers["cache-control"] == "private, no-store"
    assert original_response.headers["x-content-type-options"] == "nosniff"
    assert canonical_response.status_code == 200
    assert canonical_response.headers["cache-control"] == "private, no-store"
    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(canonical_response.content)) as canonical:
        assert canonical.size == (40, 20)
    with Image.open(BytesIO(thumbnail_response.content)) as thumbnail:
        assert thumbnail.size == (40, 20)


def test_image_review_requires_all_active_annotations_to_be_resolved(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")
    monkeypatch.setattr("shelfsight_api.annotation_api.get_engine", lambda: database_engine)
    image_id = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("shelf.jpg", make_image_bytes(), "image/jpeg")},
    ).json()["id"]
    annotation = client.post(
        f"/api/images/{image_id}/annotations",
        headers={"X-ShelfSight-Actor": "annotator:test"},
        json={
            "x": 2,
            "y": 2,
            "width": 10,
            "height": 10,
            "class_type": "gap",
            "shelf_row": 0,
        },
    ).json()

    blocked = client.post(f"/api/images/{image_id}/reviewed")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "image_review_blocked"

    accepted = client.post(
        f"/api/annotations/{annotation['id']}/accept",
        headers={"X-ShelfSight-Actor": "annotator:test"},
        json={"expected_revision": annotation["revision"]},
    )
    assert accepted.status_code == 200
    reviewed = client.post(f"/api/images/{image_id}/reviewed")
    assert reviewed.status_code == 200
    assert reviewed.json() == {"id": image_id, "status": "reviewed"}
    assert client.post(f"/api/images/{image_id}/reviewed").json() == reviewed.json()


def test_project_image_list_is_filtered_and_bounded(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")
    first_id = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("first.jpg", make_image_bytes("blue"), "image/jpeg")},
    ).json()["id"]
    second_id = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("second.jpg", make_image_bytes("green"), "image/jpeg")},
    ).json()["id"]
    with database_engine.begin() as connection:
        connection.execute(
            update(images).where(images.c.id == second_id).values(status="reviewed")
        )

    first_page = client.get(f"{upload_url(dataset_id, version_id)}?limit=1")
    reviewed = client.get(
        f"{upload_url(dataset_id, version_id)}?status=reviewed&limit=60&offset=0"
    )
    missing = client.get(upload_url(dataset_id, uuid4()))

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert len(first_page.json()["images"]) == 1
    assert first_page.json()["limit"] == 1
    assert reviewed.status_code == 200
    assert reviewed.json()["total"] == 1
    assert reviewed.json()["images"][0]["id"] == second_id
    assert reviewed.json()["images"][0]["status"] == "reviewed"
    assert reviewed.json()["images"][0]["thumbnail_url"].endswith(
        f"/{second_id}/media/thumbnail"
    )
    assert first_id != second_id
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"


def test_image_neighbors_follow_the_grid_order(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")
    uploaded = [
        client.post(
            upload_url(dataset_id, version_id),
            files={"file": (f"{color}.jpg", make_image_bytes(color), "image/jpeg")},
        ).json()["id"]
        for color in ("blue", "green", "red")
    ]
    grid = [
        image["id"]
        for image in client.get(f"{upload_url(dataset_id, version_id)}?limit=10").json()["images"]
    ]

    def neighbors(image_id: str) -> dict[str, object]:
        response = client.get(f"{upload_url(dataset_id, version_id)}/{image_id}/neighbors")
        assert response.status_code == 200
        return response.json()

    def place(previous: str | None, following: str | None, position: int) -> dict[str, object]:
        return {"previous_id": previous, "next_id": following, "position": position, "total": 3}

    assert sorted(grid) == sorted(uploaded)
    assert neighbors(grid[0]) == place(None, grid[1], 1)
    assert neighbors(grid[1]) == place(grid[0], grid[2], 2)
    assert neighbors(grid[2]) == place(grid[1], None, 3)

    outside = client.get(f"{upload_url(dataset_id, version_id)}/{uuid4()}/neighbors")
    other_version = client.get(f"{upload_url(dataset_id, uuid4())}/{grid[0]}/neighbors")
    assert outside.status_code == 404
    assert outside.json()["detail"]["code"] == "image_not_found"
    assert other_version.status_code == 404


def test_duplicate_corrupt_and_unsupported_uploads_are_deterministic(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")
    valid = make_image_bytes()

    first = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("shelf.jpg", valid, "image/jpeg")},
    )
    duplicate = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("copy.jpg", valid, "image/jpeg")},
    )
    corrupt = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("broken.jpg", b"broken", "image/jpeg")},
    )
    unsupported = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("image.gif", make_image_bytes(image_format="GIF"), "image/gif")},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {
        "code": "duplicate_image",
        "message": "dataset already contains this exact image",
        "image_id": first.json()["id"],
    }
    assert corrupt.status_code == 422
    assert corrupt.json()["detail"]["code"] == "corrupt_image"
    assert unsupported.status_code == 415
    assert unsupported.json()["detail"]["code"] == "unsupported_image"


def test_manifest_import_is_bounded_and_reports_each_outcome(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    import_root = tmp_path / "imports"
    media_root = tmp_path / "media"
    import_root.mkdir()
    (import_root / "one.jpg").write_bytes(make_image_bytes("blue"))
    (import_root / "duplicate.jpg").write_bytes(make_image_bytes("blue"))
    (import_root / "two.png").write_bytes(make_image_bytes("green", "PNG"))
    (import_root / "broken.jpg").write_bytes(b"broken")
    configure_image_api(monkeypatch, database_engine, media_root, import_root)

    response = client.post(
        f"{upload_url(dataset_id, version_id)}/import",
        json={
            "items": [
                {"path": "one.jpg"},
                {"path": "duplicate.jpg"},
                {"path": "two.png", "capture_metadata": {"aisle": "A1"}},
                {"path": "broken.jpg"},
                {"path": "../outside.jpg"},
            ]
        },
    )

    assert response.status_code == 200
    outcomes = [(item["outcome"], item["code"]) for item in response.json()["results"]]
    assert outcomes == [
        ("imported", "image_imported"),
        ("duplicate", "duplicate_image"),
        ("imported", "image_imported"),
        ("rejected", "corrupt_image"),
        ("rejected", "unsafe_path"),
    ]

    oversized_manifest = client.post(
        f"{upload_url(dataset_id, version_id)}/import",
        json={"items": [{"path": f"{index}.jpg"} for index in range(251)]},
    )
    assert oversized_manifest.status_code == 422


def test_frozen_and_missing_targets_are_rejected_without_writes(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")
    with database_engine.begin() as connection:
        connection.execute(
            update(dataset_versions)
            .where(dataset_versions.c.id == version_id)
            .values(snapshot_at=func.now())
        )

    frozen = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("shelf.jpg", make_image_bytes(), "image/jpeg")},
    )
    missing = client.post(
        upload_url(dataset_id, uuid4()),
        files={"file": ("shelf.jpg", make_image_bytes(), "image/jpeg")},
    )

    assert frozen.status_code == 409
    assert frozen.json()["detail"]["code"] == "dataset_version_frozen"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "dataset_version_not_found"


def test_storage_failure_rolls_back_the_image_row(
    database_engine: Engine,
    ingest_target: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_id, version_id = ingest_target
    configure_image_api(monkeypatch, database_engine, tmp_path / "media", tmp_path / "imports")

    def fail_storage(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("shelfsight_api.image_ingest.write_media_bundle", fail_storage)
    response = client.post(
        upload_url(dataset_id, version_id),
        files={"file": ("shelf.jpg", make_image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "service_unavailable"
    with database_engine.connect() as connection:
        image_count = connection.execute(
            select(func.count()).select_from(images).where(images.c.dataset_id == dataset_id)
        ).scalar_one()
    assert image_count == 0
