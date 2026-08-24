from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, insert, select

from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    datasets,
    images,
    skus,
)

client = TestClient(app)
ACTOR_HEADERS = {"X-ShelfSight-Actor": "catalog:test"}
UNKNOWN_SKU_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def catalog_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Engine:
    monkeypatch.setattr("shelfsight_api.sku_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.sku_api.get_media_root", lambda: tmp_path)
    yield database_engine
    with database_engine.begin() as connection:
        connection.execute(delete(datasets))
        connection.execute(delete(skus).where(skus.c.is_unknown.is_(False)))


def sku_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Aurora Cola Zero 330ml",
        "upc": "012345678905",
        "category": "Beverages",
        "subcategory": "Soft drinks",
        "brand": "Aurora",
        "variant": "Zero 330ml",
    }
    payload.update(overrides)
    return payload


def create_sku(**overrides: Any) -> dict[str, Any]:
    response = client.post(
        "/api/skus",
        headers=ACTOR_HEADERS,
        json=sku_payload(**overrides),
    )
    assert response.status_code == 201
    return response.json()


def test_catalog_crud_search_deprecation_and_unknown(
    catalog_target: Engine,
) -> None:
    del catalog_target
    created = create_sku()

    search = client.get("/api/skus", params={"query": "aurora 330", "limit": 2500})
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["skus"][0]["id"] == created["id"]

    updated_response = client.put(
        f"/api/skus/{created['id']}",
        headers=ACTOR_HEADERS,
        json=sku_payload(name="Aurora Cola Zero 330 ml Can", variant="Zero can"),
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["variant"] == "Zero can"

    deprecated_response = client.post(
        f"/api/skus/{created['id']}/deprecate",
        headers=ACTOR_HEADERS,
    )
    repeated_response = client.post(
        f"/api/skus/{created['id']}/deprecate",
        headers=ACTOR_HEADERS,
    )
    assert deprecated_response.status_code == 200
    assert deprecated_response.json()["status"] == "deprecated"
    assert repeated_response.status_code == 409

    unknown = client.get(f"/api/skus/{UNKNOWN_SKU_ID}")
    unknown_update = client.put(
        f"/api/skus/{UNKNOWN_SKU_ID}",
        headers=ACTOR_HEADERS,
        json=sku_payload(upc=None),
    )
    unknown_deprecate = client.post(
        f"/api/skus/{UNKNOWN_SKU_ID}/deprecate",
        headers=ACTOR_HEADERS,
    )
    assert unknown.status_code == 200
    assert unknown.json()["is_unknown"] is True
    assert unknown_update.status_code == 409
    assert unknown_deprecate.status_code == 409


def test_duplicate_upc_is_rejected(catalog_target: Engine) -> None:
    del catalog_target
    create_sku()

    duplicate = client.post(
        "/api/skus",
        headers=ACTOR_HEADERS,
        json=sku_payload(name="Duplicate package"),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_upc"

    other = create_sku(name="Other package", upc="012345678912")
    duplicate_update = client.put(
        f"/api/skus/{other['id']}",
        headers=ACTOR_HEADERS,
        json=sku_payload(name="Other package"),
    )
    assert duplicate_update.status_code == 409
    assert duplicate_update.json()["detail"]["code"] == "duplicate_upc"


def test_merge_repoints_current_annotations_with_an_auditable_revision(
    catalog_target: Engine,
) -> None:
    source = create_sku(name="Aurora Zero old", upc="012345678905")
    target = create_sku(name="Aurora Zero canonical", upc="012345678912")
    dataset_id = uuid4()
    image_id = uuid4()
    annotation_id = uuid4()
    with catalog_target.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}")
        )
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.jpg",
                canonical_media_key=f"canonical/{image_id}.jpg",
                thumbnail_media_key=f"thumbnails/{image_id}.jpg",
                media_type="image/jpeg",
                original_filename="shelf.jpg",
                content_sha256=image_id.hex * 2,
                canonical_width=100,
                canonical_height=80,
            )
        )
        create_annotation(
            connection,
            image_id,
            annotation_values(UUID(source["id"])),
            annotation_id=annotation_id,
        )

    response = client.post(
        f"/api/skus/{source['id']}/merge",
        headers=ACTOR_HEADERS,
        json={"target_sku_id": target["id"]},
    )

    assert response.status_code == 200
    assert response.json()["source"]["status"] == "merged"
    assert response.json()["source"]["merged_into_id"] == target["id"]
    assert response.json()["repointed_annotations"] == 1
    with catalog_target.connect() as connection:
        current_revision = connection.execute(
            select(annotation_records.c.current_revision).where(
                annotation_records.c.id == annotation_id
            )
        ).scalar_one()
        revisions = connection.execute(
            select(
                annotation_revisions.c.revision,
                annotation_revisions.c.sku_id,
                annotation_revisions.c.provenance,
            )
            .where(annotation_revisions.c.annotation_id == annotation_id)
            .order_by(annotation_revisions.c.revision)
        ).all()
    assert current_revision == 2
    assert revisions[0].sku_id == UUID(source["id"])
    assert revisions[1].sku_id == UUID(target["id"])
    assert revisions[1].provenance["action"] == "sku_merge"


def test_failed_merge_leaves_source_and_annotations_unchanged(
    catalog_target: Engine,
) -> None:
    source = create_sku(name="Source", upc="012345678905")
    target = create_sku(name="Target", upc="012345678912")
    client.post(
        f"/api/skus/{target['id']}/deprecate",
        headers=ACTOR_HEADERS,
    )

    response = client.post(
        f"/api/skus/{source['id']}/merge",
        headers=ACTOR_HEADERS,
        json={"target_sku_id": target["id"]},
    )

    assert response.status_code == 409
    assert client.get(f"/api/skus/{source['id']}").json()["status"] == "active"

    unknown_target = client.post(
        f"/api/skus/{source['id']}/merge",
        headers=ACTOR_HEADERS,
        json={"target_sku_id": UNKNOWN_SKU_ID},
    )
    assert unknown_target.status_code == 409
    assert unknown_target.json()["detail"]["code"] == "invalid_sku_state"


def test_reference_images_use_the_managed_image_validation_pipeline(
    catalog_target: Engine,
) -> None:
    del catalog_target
    sku = create_sku()
    valid = png_bytes()

    uploaded = client.post(
        f"/api/skus/{sku['id']}/reference-images",
        headers=ACTOR_HEADERS,
        files={"file": ("reference.png", valid, "image/png")},
    )
    duplicate = client.post(
        f"/api/skus/{sku['id']}/reference-images",
        headers=ACTOR_HEADERS,
        files={"file": ("reference.png", valid, "image/png")},
    )
    invalid = client.post(
        f"/api/skus/{sku['id']}/reference-images",
        headers=ACTOR_HEADERS,
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    disguised_gif = client.post(
        f"/api/skus/{sku['id']}/reference-images",
        headers=ACTOR_HEADERS,
        files={"file": ("fake.png", gif_bytes(), "image/png")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["width"] == 12
    assert body["height"] == 8
    assert client.get(body["thumbnail_url"]).status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_reference_image"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "corrupt_image"
    assert disguised_gif.status_code == 422
    assert disguised_gif.json()["detail"]["code"] == "unsupported_image"


def test_catalog_handles_two_thousand_skus_and_variant_terms(
    catalog_target: Engine,
) -> None:
    with catalog_target.begin() as connection:
        connection.execute(
            insert(skus),
            [
                {
                    "name": f"Northstar Hydration Bottle {index:04d}",
                    "brand": "Northstar",
                    "category": "Beverages",
                    "subcategory": "Hydration",
                    "variant": f"Lime Zero {index % 12 + 1} pack",
                }
                for index in range(2_000)
            ],
        )

    all_response = client.get("/api/skus", params={"limit": 2500})
    variant_response = client.get(
        "/api/skus",
        params={"query": "northstar lime 10 pack", "limit": 2500},
    )

    assert all_response.status_code == 200
    assert all_response.json()["total"] == 2_001
    assert len(all_response.json()["skus"]) == 2_001
    assert variant_response.status_code == 200
    assert variant_response.json()["total"] > 0


def annotation_values(sku_id: UUID) -> dict[str, Any]:
    return {
        "x": 10,
        "y": 10,
        "width": 20,
        "height": 30,
        "class_type": "product",
        "sku_id": sku_id,
        "lifecycle_state": "verified",
        "review_state": "accepted",
        "source": "human",
        "provenance": {"actor": "test"},
        "confidence": 0.9,
        "occluded": False,
        "truncated": False,
        "shelf_row": 1,
    }


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (12, 8), "navy").save(output, format="PNG")
    return output.getvalue()


def gif_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (12, 8), "navy").save(output, format="GIF")
    return output.getvalue()
