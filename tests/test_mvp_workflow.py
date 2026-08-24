from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete

from shelfsight_api.app import app
from shelfsight_api.export_service import (
    read_detection_export,
    read_recognition_export,
)
from shelfsight_api.models import datasets, skus

client = TestClient(app)
ACTOR_HEADERS = {"X-ShelfSight-Actor": "annotator:mvp-gate"}


def test_complete_mvp_workflow_from_ingest_through_exports(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media"

    monkeypatch.setattr("shelfsight_api.dataset_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.image_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.image_api.get_media_root", lambda: media_root)
    monkeypatch.setattr(
        "shelfsight_api.annotation_api.get_engine",
        lambda: database_engine,
    )
    monkeypatch.setattr("shelfsight_api.export_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.export_api.get_media_root", lambda: media_root)

    dataset_id: UUID | None = None
    sku_id: UUID | None = None
    try:
        created_dataset = client.post(
            "/api/datasets",
            json={"name": f"mvp-gate-{uuid4()}"},
        )
        assert created_dataset.status_code == 201
        dataset_id = UUID(created_dataset.json()["id"])
        version_id = UUID(created_dataset.json()["open_version_id"])

        created_sku = client.post(
            "/api/skus",
            headers={"X-ShelfSight-Actor": "owner:mvp-gate"},
            json={"name": "Gate Cola"},
        )
        assert created_sku.status_code == 201
        sku_id = UUID(created_sku.json()["id"])

        upload = client.post(
            f"/api/datasets/{dataset_id}/versions/{version_id}/images",
            files={"file": ("gate.png", _png_bytes(), "image/png")},
        )
        assert upload.status_code == 201
        image_id = UUID(upload.json()["id"])

        proposed = client.post(
            f"/api/images/{image_id}/annotations",
            headers=ACTOR_HEADERS,
            json={
                "x": 12.5,
                "y": 10.5,
                "width": 24.0,
                "height": 36.0,
                "class_type": "product",
                "lifecycle_state": "proposed",
                "confidence": 0.9,
                "shelf_row": 0,
            },
        )
        assert proposed.status_code == 201
        annotation_id = UUID(proposed.json()["id"])

        accepted = client.post(
            f"/api/annotations/{annotation_id}/accept",
            headers=ACTOR_HEADERS,
            json={"expected_revision": 1},
        )
        assert accepted.status_code == 200
        assigned = client.post(
            f"/api/annotations/{annotation_id}/assign-sku",
            headers=ACTOR_HEADERS,
            json={"expected_revision": 2, "sku_id": str(sku_id)},
        )
        assert assigned.status_code == 200

        reloaded = client.get(f"/api/annotations/{annotation_id}")
        assert reloaded.status_code == 200
        assert reloaded.json()["revision"] == 3
        assert reloaded.json()["sku_id"] == str(sku_id)
        assert reloaded.json()["provenance"]["action"] == "assign_sku"

        snapshot = client.post(f"/api/dataset-versions/{version_id}/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["captured_annotations"] == 1

        detection_response = client.get(
            f"/api/dataset-versions/{version_id}/exports/detection"
        )
        recognition_response = client.get(
            f"/api/dataset-versions/{version_id}/exports/recognition"
        )
        assert detection_response.status_code == 200
        assert recognition_response.status_code == 200

        detection = read_detection_export(detection_response.content)
        recognition = read_recognition_export(recognition_response.content)
        assert len(detection["images"]) == 1
        assert len(detection["annotations"]) == 1
        assert detection["annotations"][0]["shelfsight_annotation_revision"] == 3
        assert len(recognition) == 1
        assert recognition[0]["annotation_revision"] == 3
        assert recognition[0]["label"]["training_sku_id"] == str(sku_id)
        assert recognition[0]["trainable"] is True
    finally:
        with database_engine.begin() as connection:
            if dataset_id is not None:
                connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
            if sku_id is not None:
                connection.execute(delete(skus).where(skus.c.id == sku_id))


def _png_bytes() -> bytes:
    image = Image.new("RGB", (100, 80), "#2a9d8f")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()
