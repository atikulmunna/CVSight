from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Connection, Engine, delete, func, insert, select, update

from shelfsight_api.app import app
from shelfsight_api.data_model import create_annotation, snapshot_dataset_version
from shelfsight_api.export_service import (
    ExportMediaError,
    ExportVersionNotFrozenError,
    InvalidExportArchiveError,
    build_detection_export,
    build_recognition_export,
    build_yolo_export,
    read_detection_export,
    read_recognition_export,
    validate_export_archive,
)
from shelfsight_api.models import (
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    skus,
    snapshot_artifacts,
)

client = TestClient(app)
UNKNOWN_SKU_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class ExportTarget:
    dataset_id: UUID
    version_id: UUID
    image_id: UUID
    media_root: Path
    sku_ids: dict[str, UUID]


@pytest.fixture
def export_target(
    database_engine: Engine,
    tmp_path: Path,
) -> Iterator[ExportTarget]:
    dataset_id = uuid4()
    version_id = uuid4()
    image_id = uuid4()
    active_id = uuid4()
    deprecated_id = uuid4()
    merged_id = uuid4()
    target_id = uuid4()
    media_root = tmp_path / "media"
    canonical_key = f"canonical/{image_id}.png"
    canonical_bytes = _png_bytes(120, 80)
    canonical_path = media_root / canonical_key
    canonical_path.parent.mkdir(parents=True)
    canonical_path.write_bytes(canonical_bytes)

    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(id=dataset_id, name=f"export-{dataset_id}")
        )
        connection.execute(
            insert(dataset_versions).values(
                id=version_id,
                dataset_id=dataset_id,
            )
        )
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.png",
                canonical_media_key=canonical_key,
                thumbnail_media_key=f"thumbnails/{image_id}.jpg",
                media_type="image/png",
                original_filename="shelf.png",
                content_sha256="a" * 64,
                canonical_width=120,
                canonical_height=80,
                capture_metadata={
                    "provided": {
                        "split": "train",
                        "capture_session_id": "export-session",
                    }
                },
            )
        )
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
                {"id": active_id, "name": "Active Cola", "status": "active"},
                {"id": deprecated_id, "name": "Old Cola", "status": "deprecated"},
                {"id": target_id, "name": "Merged Target", "status": "active"},
            ],
        )
        connection.execute(
            insert(skus).values(
                id=merged_id,
                name="Merged Source",
                status="merged",
                merged_into_id=target_id,
            )
        )
        annotation_specs = [
            ("product", active_id, 2, 2, "verified", "accepted"),
            ("product", UNKNOWN_SKU_ID, 22, 2, "verified", "accepted"),
            ("product", deprecated_id, 42, 2, "verified", "accepted"),
            ("product", merged_id, 62, 2, "verified", "accepted"),
            ("product", None, 82, 2, "verified", "accepted"),
            ("gap", None, 2, 42, "verified", "accepted"),
            ("shelf_label", None, 42, 42, "verified", "accepted"),
            ("product", active_id, 82, 42, "proposed", "unreviewed"),
        ]
        for index, (
            class_type,
            sku_id,
            x,
            y,
            lifecycle,
            review,
        ) in enumerate(annotation_specs):
            create_annotation(
                connection,
                image_id,
                {
                    "x": float(x) + 0.25,
                    "y": float(y) + 0.25,
                    "width": 16.5,
                    "height": 24.5 if class_type != "shelf_label" else 8.5,
                    "class_type": class_type,
                    "sku_id": sku_id,
                    "lifecycle_state": lifecycle,
                    "review_state": review,
                    "source": "human",
                    "provenance": {"fixture_index": index},
                    "confidence": 0.9,
                    "occluded": index == 0,
                    "truncated": False,
                    "shelf_row": 0 if y < 40 else 1,
                },
            )
        snapshot_dataset_version(connection, version_id)

    yield ExportTarget(
        dataset_id=dataset_id,
        version_id=version_id,
        image_id=image_id,
        media_root=media_root,
        sku_ids={
            "active": active_id,
            "deprecated": deprecated_id,
            "merged": merged_id,
            "target": target_id,
        },
    )

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(delete(skus).where(skus.c.id == merged_id))
        connection.execute(
            delete(skus).where(
                skus.c.id.in_((active_id, deprecated_id, target_id))
            )
        )


def test_exports_are_deterministic_and_importable(
    database_engine: Engine,
    export_target: ExportTarget,
) -> None:
    with database_engine.connect() as connection:
        first_detection = build_detection_export(
            connection,
            export_target.media_root,
            export_target.version_id,
        )
        first_recognition = build_recognition_export(
            connection,
            export_target.media_root,
            export_target.version_id,
        )

    with database_engine.begin() as connection:
        connection.execute(
            update(skus)
            .where(skus.c.id == export_target.sku_ids["active"])
            .values(name="Live catalog rename")
        )

    with database_engine.connect() as connection:
        second_recognition = build_recognition_export(
            connection,
            export_target.media_root,
            export_target.version_id,
        )
        second_detection = build_detection_export(
            connection,
            export_target.media_root,
            export_target.version_id,
        )

    assert first_detection == second_detection
    assert first_recognition == second_recognition
    detection_manifest = validate_export_archive(first_detection, "detection")
    recognition_manifest = validate_export_archive(first_recognition, "recognition")
    coco = read_detection_export(first_detection)
    samples = read_recognition_export(first_recognition)

    assert detection_manifest["dataset_version_id"] == str(export_target.version_id)
    assert detection_manifest["artifact_schema_version"] == (
        "shelfsight-coco-detection/v1"
    )
    assert detection_manifest["counts"] == {
        "annotations": 7,
        "excluded_annotations": 1,
        "images": 1,
    }
    assert [category["id"] for category in coco["categories"]] == [1, 2, 3]
    assert [category["name"] for category in coco["categories"]] == [
        "product",
        "gap",
        "shelf_label",
    ]
    assert len(coco["images"]) == 1
    assert coco["images"][0]["evaluation"] == {
        "capture_session_id": "export-session",
        "split": "train",
    }
    assert len(coco["annotations"]) == 7
    assert {annotation["category_id"] for annotation in coco["annotations"]} == {
        1,
        2,
        3,
    }
    _reference_coco_import(coco)

    assert recognition_manifest["counts"]["samples"] == 5
    assert recognition_manifest["counts"]["trainable_samples"] == 2
    by_status = {sample["label"]["status"]: sample for sample in samples}
    assert set(by_status) == {
        "active",
        "unknown",
        "deprecated",
        "merged",
        "unassigned",
    }
    assert by_status["active"]["label"]["training_sku_id"] == str(
        export_target.sku_ids["active"]
    )
    assert by_status["merged"]["label"]["source_sku_id"] == str(
        export_target.sku_ids["merged"]
    )
    assert by_status["merged"]["label"]["training_sku_id"] == str(
        export_target.sku_ids["target"]
    )
    assert by_status["unknown"]["trainable"] is False
    assert by_status["deprecated"]["trainable"] is False
    assert by_status["unassigned"]["trainable"] is False
    assert all(sample["crop_sha256"] for sample in samples)
    assert all(sample["annotation_revision"] == 1 for sample in samples)
    assert all(sample["provenance"] for sample in samples)


def test_yolo_export_lays_out_verified_boxes_by_split(
    database_engine: Engine,
    export_target: ExportTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with database_engine.connect() as connection:
        first = build_yolo_export(connection, export_target.media_root, export_target.version_id)
        second = build_yolo_export(connection, export_target.media_root, export_target.version_id)

    assert first == second
    with ZipFile(BytesIO(first)) as bundle:
        names = set(bundle.namelist())
        labels = bundle.read(f"labels/train/{export_target.image_id}.txt").decode().splitlines()
        data_yaml = bundle.read("data.yaml").decode()
        manifest = json.loads(bundle.read("manifest.json"))
    assert f"images/train/{export_target.image_id}.png" in names
    assert len(labels) == 7
    assert "0 0.087500 0.181250 0.137500 0.306250" in labels
    assert sorted({line.split()[0] for line in labels}) == ["0", "1", "2"]
    assert "train: images/train" in data_yaml
    assert "val:" not in data_yaml
    assert "0: product" in data_yaml and "2: shelf_label" in data_yaml
    assert manifest["export_type"] == "yolo"
    assert manifest["counts"]["annotations"] == 7
    assert manifest["counts"]["excluded_annotations"] == 1
    assert manifest["counts"]["images_by_folder"] == {"train": 1}

    monkeypatch.setattr("shelfsight_api.export_api.get_engine", lambda: database_engine)
    monkeypatch.setattr(
        "shelfsight_api.export_api.get_media_root", lambda: export_target.media_root
    )
    response = client.get(f"/api/dataset-versions/{export_target.version_id}/exports/yolo")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('-yolo.zip"')


def test_export_api_returns_zip_and_rejects_mutable_versions(
    database_engine: Engine,
    export_target: ExportTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shelfsight_api.export_api.get_engine", lambda: database_engine)
    monkeypatch.setattr(
        "shelfsight_api.export_api.get_media_root",
        lambda: export_target.media_root,
    )

    response = client.get(
        f"/api/dataset-versions/{export_target.version_id}/exports/detection"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert str(export_target.version_id) in response.headers["content-disposition"]
    assert read_detection_export(response.content)["images"][0][
        "shelfsight_image_id"
    ] == str(export_target.image_id)

    open_dataset_id = uuid4()
    open_version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets).values(
                id=open_dataset_id,
                name=f"open-export-{open_dataset_id}",
            )
        )
        connection.execute(
            insert(dataset_versions).values(
                id=open_version_id,
                dataset_id=open_dataset_id,
            )
        )
    try:
        mutable_response = client.get(
            f"/api/dataset-versions/{open_version_id}/exports/recognition"
        )
        assert mutable_response.status_code == 409
        assert mutable_response.json()["detail"]["code"] == (
            "dataset_version_not_frozen"
        )
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                delete(datasets).where(datasets.c.id == open_dataset_id)
            )


def test_export_rejects_missing_media_and_malformed_archives(
    database_engine: Engine,
    export_target: ExportTarget,
) -> None:
    canonical_path = (
        export_target.media_root / "canonical" / f"{export_target.image_id}.png"
    )
    canonical_path.unlink()
    with database_engine.connect() as connection:
        with pytest.raises(ExportMediaError, match="missing"):
            build_detection_export(
                connection,
                export_target.media_root,
                export_target.version_id,
            )

    with pytest.raises(InvalidExportArchiveError, match="cannot be imported"):
        validate_export_archive(b"not a zip", "detection")

    unsafe = BytesIO()
    with ZipFile(unsafe, "w", compression=ZIP_DEFLATED) as bundle:
        bundle.writestr("../escape.json", b"{}")
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "shelfsight-export-manifest/v1",
                    "export_type": "detection",
                    "files": [],
                }
            ),
        )
    with pytest.raises(InvalidExportArchiveError, match="unsafe path"):
        validate_export_archive(unsafe.getvalue(), "detection")


def test_interrupted_export_does_not_register_an_artifact(
    database_engine: Engine,
    export_target: ExportTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shelfsight_api.export_api.get_engine", lambda: database_engine)
    monkeypatch.setattr(
        "shelfsight_api.export_api.get_media_root",
        lambda: export_target.media_root,
    )
    monkeypatch.setattr(
        "shelfsight_api.export_api.build_detection_export",
        lambda *_args: (_ for _ in ()).throw(OSError("interrupted export")),
    )
    with database_engine.connect() as connection:
        before = connection.execute(
            select(func.count())
            .select_from(snapshot_artifacts)
            .where(
                snapshot_artifacts.c.dataset_version_id == export_target.version_id
            )
        ).scalar_one()

    response = client.get(
        f"/api/dataset-versions/{export_target.version_id}/exports/detection"
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "export_unavailable"
    with database_engine.connect() as connection:
        after = connection.execute(
            select(func.count())
            .select_from(snapshot_artifacts)
            .where(
                snapshot_artifacts.c.dataset_version_id == export_target.version_id
            )
        ).scalar_one()
    assert after == before


def test_export_service_rejects_an_open_snapshot(
    database_connection: Connection,
) -> None:
    dataset_id = uuid4()
    version_id = uuid4()
    database_connection.execute(
        insert(datasets).values(id=dataset_id, name=f"mutable-{dataset_id}")
    )
    database_connection.execute(
        insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
    )

    with pytest.raises(ExportVersionNotFrozenError, match="snapshotted"):
        build_detection_export(database_connection, Path("unused"), version_id)


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "#2a9d8f")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _reference_coco_import(coco: dict[str, Any]) -> None:
    image_ids = {image["id"] for image in coco["images"]}
    category_ids = {category["id"] for category in coco["categories"]}
    for annotation in coco["annotations"]:
        assert annotation["image_id"] in image_ids
        assert annotation["category_id"] in category_ids
        x, y, width, height = annotation["bbox"]
        assert x >= 0 and y >= 0 and width > 0 and height > 0
        assert annotation["area"] == width * height
