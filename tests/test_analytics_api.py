from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert

from shelfsight_api.analytics_service import (
    build_analytics_csv,
    build_analytics_json,
    build_snapshot_analytics,
)
from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation, snapshot_dataset_version
from shelfsight_api.models import (
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    skus,
)

client = TestClient(app)
UNKNOWN_SKU_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class AnalyticsTarget:
    dataset_id: UUID
    version_id: UUID
    reviewed_image_id: UUID
    incomplete_image_id: UUID
    sku_id: UUID


@pytest.fixture
def analytics_target(database_engine: Engine) -> Iterator[AnalyticsTarget]:
    dataset_id = uuid4()
    version_id = uuid4()
    reviewed_image_id = uuid4()
    incomplete_image_id = uuid4()
    sku_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"analytics-{dataset_id}"))
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        connection.execute(
            insert(skus).values(id=sku_id, name="=Formula Cola", status="active")
        )
        connection.execute(
            insert(images),
            [
                _image_values(
                    reviewed_image_id,
                    dataset_id,
                    "reviewed.jpg",
                    "a" * 64,
                    "reviewed",
                    view="frontal",
                ),
                _image_values(
                    incomplete_image_id,
                    dataset_id,
                    "incomplete.jpg",
                    "b" * 64,
                    "in_progress",
                ),
            ],
        )
        connection.execute(
            insert(dataset_version_images),
            [
                {
                    "dataset_id": dataset_id,
                    "dataset_version_id": version_id,
                    "image_id": reviewed_image_id,
                },
                {
                    "dataset_id": dataset_id,
                    "dataset_version_id": version_id,
                    "image_id": incomplete_image_id,
                },
            ],
        )
        _create_annotation(connection, reviewed_image_id, sku_id, 0, "verified", "accepted")
        _create_annotation(
            connection,
            reviewed_image_id,
            UNKNOWN_SKU_ID,
            20,
            "verified",
            "accepted",
        )
        _create_annotation(
            connection,
            reviewed_image_id,
            None,
            45,
            "verified",
            "accepted",
            class_type="gap",
        )
        _create_annotation(
            connection,
            incomplete_image_id,
            sku_id,
            0,
            "proposed",
            "unreviewed",
        )
        snapshot_dataset_version(
            connection,
            version_id,
            snapshot_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        )

    yield AnalyticsTarget(
        dataset_id=dataset_id,
        version_id=version_id,
        reviewed_image_id=reviewed_image_id,
        incomplete_image_id=incomplete_image_id,
        sku_id=sku_id,
    )

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(delete(skus).where(skus.c.id == sku_id))


def test_snapshot_analytics_are_reproducible_and_preserve_uncertainty(
    database_engine: Engine,
    analytics_target: AnalyticsTarget,
) -> None:
    with database_engine.connect() as connection:
        first = build_snapshot_analytics(
            connection,
            analytics_target.version_id,
            generated_at=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )
        second = build_snapshot_analytics(
            connection,
            analytics_target.version_id,
            generated_at=datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
        )

    assert first["result_sha256"] == second["result_sha256"]
    assert first["generated_at"] != second["generated_at"]
    assert first["snapshot"]["content_sha256"] == second["snapshot"]["content_sha256"]
    assert first["formula_version"] == "shelfsight-retail-analytics/v1"
    assert first["model"]["required"] is False
    assert first["planogram"]["status"] == "unsupported"
    assert first["planogram"]["is_final"] is False

    images_by_id = {image["image_id"]: image for image in first["images"]}
    reviewed = images_by_id[str(analytics_target.reviewed_image_id)]
    incomplete = images_by_id[str(analytics_target.incomplete_image_id)]
    assert reviewed["count_share"]["shares"] == {
        str(analytics_target.sku_id): 0.5,
        "unknown": 0.5,
    }
    assert reviewed["count_share"]["is_final"] is True
    assert reviewed["image_area_share"]["is_final"] is True
    assert reviewed["realogram"]["is_final"] is True
    assert reviewed["gaps"]["accepted_count"] == 1
    assert reviewed["gaps"]["visible_gap_fraction"]["is_final"] is False
    assert incomplete["count_share"]["status"] == "partial"
    assert incomplete["count_share"]["is_final"] is False
    assert incomplete["image_area_share"]["status"] == "unsupported"


def test_analytics_exports_are_machine_readable_and_spreadsheet_safe(
    database_engine: Engine,
    analytics_target: AnalyticsTarget,
) -> None:
    with database_engine.connect() as connection:
        report = build_snapshot_analytics(
            connection,
            analytics_target.version_id,
            generated_at=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )

    parsed_json = json.loads(build_analytics_json(report))
    csv_text = build_analytics_csv(report).decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(csv_text)))

    assert parsed_json["result_sha256"] == report["result_sha256"]
    assert rows
    assert tuple(rows[0]) == (
        "dataset_version_id",
        "snapshot_sha256",
        "formula_version",
        "result_sha256",
        "generated_at",
        "image_id",
        "image_name",
        "metric",
        "group_id",
        "group_label",
        "value",
        "unit",
        "status",
        "is_final",
        "confidence_status",
        "reason",
    )
    sku_row = next(row for row in rows if row["group_id"] == str(analytics_target.sku_id))
    assert sku_row["group_label"] == "'=Formula Cola"
    planogram_row = next(row for row in rows if row["metric"] == "planogram_compliance")
    assert planogram_row["value"] == ""
    assert planogram_row["status"] == "unsupported"
    assert planogram_row["is_final"] == "false"


def test_analytics_api_reads_and_exports_only_frozen_versions(
    database_engine: Engine,
    analytics_target: AnalyticsTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shelfsight_api.analytics_api.get_engine", lambda: database_engine)
    response = client.get(f"/api/dataset-versions/{analytics_target.version_id}/analytics")
    csv_response = client.get(
        f"/api/dataset-versions/{analytics_target.version_id}/analytics/export?format=csv"
    )
    json_response = client.get(
        f"/api/dataset-versions/{analytics_target.version_id}/analytics/export?format=json"
    )

    assert response.status_code == 200
    assert response.json()["dataset_version_id"] == str(analytics_target.version_id)
    assert response.json()["generated_at"]
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert csv_response.headers["content-disposition"].endswith('analytics.csv"')
    assert json_response.status_code == 200
    assert json.loads(json_response.content)["formula_version"] == "shelfsight-retail-analytics/v1"

    open_dataset_id = uuid4()
    open_version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=open_dataset_id, name=f"open-analytics-{open_dataset_id}")
        )
        connection.execute(
            insert(dataset_versions).values(
                id=open_version_id,
                dataset_id=open_dataset_id,
            )
        )
    try:
        open_response = client.get(f"/api/dataset-versions/{open_version_id}/analytics")
        missing_response = client.get(f"/api/dataset-versions/{uuid4()}/analytics")
    finally:
        with database_engine.begin() as connection:
            connection.execute(delete(datasets).where(datasets.c.id == open_dataset_id))

    assert open_response.status_code == 409
    assert open_response.json()["detail"]["code"] == "dataset_version_not_frozen"
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"]["code"] == "dataset_version_not_found"


def _image_values(
    image_id: UUID,
    dataset_id: UUID,
    name: str,
    content_sha256: str,
    status: str,
    *,
    view: str | None = None,
) -> dict[str, object]:
    provided = {"capture_session_id": f"session-{image_id}"}
    if view is not None:
        provided["view"] = view
    return {
        "id": image_id,
        "dataset_id": dataset_id,
        "original_media_key": f"original/{image_id}.jpg",
        "canonical_media_key": f"canonical/{image_id}.jpg",
        "thumbnail_media_key": f"thumbnails/{image_id}.jpg",
        "media_type": "image/jpeg",
        "original_filename": name,
        "content_sha256": content_sha256,
        "canonical_width": 100,
        "canonical_height": 80,
        "capture_metadata": {"provided": provided},
        "status": status,
    }


def _create_annotation(
    connection: object,
    image_id: UUID,
    sku_id: UUID | None,
    x: float,
    lifecycle_state: str,
    review_state: str,
    *,
    class_type: str = "product",
) -> None:
    create_annotation(
        connection,
        image_id,
        {
            "x": x,
            "y": 10.0,
            "width": 15.0,
            "height": 30.0,
            "class_type": class_type,
            "sku_id": sku_id,
            "lifecycle_state": lifecycle_state,
            "review_state": review_state,
            "source": "human",
            "provenance": {"fixture": "analytics"},
            "confidence": None,
            "occluded": False,
            "truncated": False,
            "shelf_row": 0,
        },
    )
