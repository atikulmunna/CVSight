from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import Connection, insert, select

from shelfsight_api.data_model import (
    create_annotation,
    register_snapshot_artifact,
    snapshot_dataset_version,
)
from shelfsight_api.fiftyone_projection import delete_projection, rebuild_projection
from shelfsight_api.models import (
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
)
from shelfsight_api.projection import (
    ProjectionBoundaryError,
    ProjectionMediaError,
    build_projection_manifest,
)


def test_manifest_projects_exact_snapshot_fields_and_artifacts(
    database_connection: Connection,
    tmp_path: Path,
) -> None:
    version_id, image_id = _create_projection_fixture(database_connection, tmp_path)
    annotation_id = create_annotation(
        database_connection,
        image_id,
        _annotation_values(),
    )
    snapshot_dataset_version(database_connection, version_id)
    register_snapshot_artifact(
        database_connection,
        version_id,
        "evaluation",
        "evaluations/baseline.json",
        content_sha256="e" * 64,
        metadata={"map50": 0.81},
    )

    manifest = build_projection_manifest(database_connection, tmp_path, version_id)

    assert manifest.dataset_version_id == version_id
    assert manifest.projection_schema_version == "cvsight-fiftyone-projection/v1"
    assert len(manifest.samples) == 1
    sample = manifest.samples[0]
    assert sample.split == "validation"
    assert sample.near_duplicate_group == "shelf-burst-1"
    assert sample.capture_session_id == "capture-1"
    assert len(sample.product_crops) == 1
    detection = sample.detections[0]
    assert detection.annotation_id == annotation_id
    assert detection.bounding_box == pytest.approx((0.1, 0.25, 0.2, 0.375))
    assert manifest.artifacts[0]["artifact_key"] == "evaluations/baseline.json"


def test_projection_rejects_missing_media_and_split_leakage(
    database_connection: Connection,
    tmp_path: Path,
) -> None:
    version_id, _ = _create_projection_fixture(database_connection, tmp_path)
    second_image_id = _insert_image(
        database_connection,
        tmp_path,
        database_connection.execute(
            select(dataset_versions.c.dataset_id).where(dataset_versions.c.id == version_id)
        ).scalar_one(),
        split="test",
        near_duplicate_group="shelf-burst-1",
    )
    database_connection.execute(
        insert(dataset_version_images).values(
            dataset_id=database_connection.execute(
                select(dataset_versions.c.dataset_id).where(
                    dataset_versions.c.id == version_id
                )
            ).scalar_one(),
            dataset_version_id=version_id,
            image_id=second_image_id,
        )
    )
    snapshot_dataset_version(database_connection, version_id)

    with pytest.raises(ProjectionBoundaryError, match="near-duplicate group"):
        build_projection_manifest(database_connection, tmp_path, version_id)

    missing_version_id, _ = _create_projection_fixture(database_connection, tmp_path)
    snapshot_dataset_version(database_connection, missing_version_id)
    manifest = build_projection_manifest(database_connection, tmp_path, missing_version_id)
    manifest.samples[0].filepath.unlink()
    with pytest.raises(ProjectionMediaError, match="missing"):
        build_projection_manifest(database_connection, tmp_path, missing_version_id)


def test_fiftyone_rebuild_is_idempotent_and_cleans_failed_staging(
    database_connection: Connection,
    tmp_path: Path,
) -> None:
    version_id, image_id = _create_projection_fixture(database_connection, tmp_path)
    create_annotation(database_connection, image_id, _annotation_values())
    snapshot_dataset_version(database_connection, version_id)
    manifest = build_projection_manifest(database_connection, tmp_path, version_id)
    second_sample = replace(
        manifest.samples[0],
        image_id=uuid4(),
        split="test",
        near_duplicate_group="shelf-burst-2",
        capture_session_id="capture-2",
    )
    manifest = replace(manifest, samples=manifest.samples + (second_sample,))
    fiftyone = FakeFiftyOne()
    brain = FakeBrain()

    first = rebuild_projection(
        manifest,
        "cvsight-validation",
        fiftyone_module=fiftyone,
        brain_module=brain,
    )
    second = rebuild_projection(
        manifest,
        "cvsight-validation",
        fiftyone_module=fiftyone,
        brain_module=brain,
    )

    assert first == second
    assert list(fiftyone.datasets) == ["cvsight-validation"]
    dataset = fiftyone.datasets["cvsight-validation"]
    assert len(dataset.samples) == 2
    assert dataset.info["source_dataset_version_id"] == str(version_id)
    assert dataset.info["read_only_projection"] is True
    assert "split-validation" in dataset.saved_views
    assert "split-test" in dataset.saved_views
    assert "near-duplicates-validation" in dataset.saved_views
    assert "near-duplicates-test" in dataset.saved_views
    assert brain.calls == ["image", "crop", "duplicates", "duplicates"] * 2

    failing_brain = FakeBrain(fail=True)
    with pytest.raises(RuntimeError, match="similarity failed"):
        rebuild_projection(
            manifest,
            "cvsight-validation",
            fiftyone_module=fiftyone,
            brain_module=failing_brain,
        )
    assert list(fiftyone.datasets) == ["cvsight-validation"]
    assert database_connection.execute(
        select(dataset_versions.c.id).where(dataset_versions.c.id == version_id)
    ).scalar_one() == version_id
    assert delete_projection("cvsight-validation", fiftyone_module=fiftyone) is True
    assert delete_projection("cvsight-validation", fiftyone_module=fiftyone) is False
    with pytest.raises(ValueError, match="invalid"):
        delete_projection("../other", fiftyone_module=fiftyone)


def _create_projection_fixture(
    connection: Connection,
    media_root: Path,
) -> tuple[UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    connection.execute(
        insert(datasets).values(id=dataset_id, name=f"projection-{dataset_id}")
    )
    connection.execute(
        insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
    )
    image_id = _insert_image(
        connection,
        media_root,
        dataset_id,
        split="validation",
        near_duplicate_group="shelf-burst-1",
        path_suffix=str(version_id),
    )
    connection.execute(
        insert(dataset_version_images).values(
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            image_id=image_id,
        )
    )
    return version_id, image_id


def _insert_image(
    connection: Connection,
    media_root: Path,
    dataset_id: UUID,
    *,
    split: str,
    near_duplicate_group: str,
    path_suffix: str | None = None,
) -> UUID:
    image_id = uuid4()
    relative_path = f"canonical/{path_suffix or image_id}.png"
    path = media_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (100, 80), "#2a9d8f") as generated:
        generated.save(path, format="PNG")
    connection.execute(
        insert(images).values(
            id=image_id,
            dataset_id=dataset_id,
            original_media_key=f"original/{image_id}.png",
            canonical_media_key=relative_path,
            thumbnail_media_key=f"thumbnail/{image_id}.jpg",
            media_type="image/png",
            original_filename=f"{image_id}.png",
            content_sha256=image_id.hex * 2,
            canonical_width=100,
            canonical_height=80,
            capture_metadata={
                "provided": {
                    "split": split,
                    "near_duplicate_group": near_duplicate_group,
                    "capture_session_id": "capture-1",
                    "store_id": "store-1",
                    "fixture_id": "fixture-1",
                }
            },
            status="reviewed",
        )
    )
    return image_id


def _annotation_values() -> dict[str, Any]:
    return {
        "x": 10.0,
        "y": 20.0,
        "width": 20.0,
        "height": 30.0,
        "class_type": "product",
        "sku_id": None,
        "lifecycle_state": "verified",
        "review_state": "accepted",
        "source": "human",
        "provenance": {"actor": "projection-test"},
        "confidence": 0.9,
        "occluded": False,
        "truncated": False,
        "shelf_row": 1,
    }


class FakeRecord:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class FakeDataset:
    def __init__(self, module: FakeFiftyOne, name: str) -> None:
        self.module = module
        self._name = name
        self.samples: list[Any] = []
        self.saved_views: dict[str, Any] = {}
        self.info: dict[str, Any] = {}
        self.description = ""
        self.persistent = False

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self.module.datasets.pop(self._name, None)
        self._name = value
        self.module.datasets[value] = self

    def add_samples(self, samples: list[Any]) -> None:
        self.samples.extend(samples)

    def add_dynamic_sample_fields(self, *, fields: str) -> None:
        assert fields == "product_crops"

    def save(self) -> None:
        self.module.datasets[self.name] = self

    def save_view(self, name: str, view: Any) -> None:
        self.saved_views[name] = view

    def match_tags(self, tag: str) -> tuple[str, str]:
        return ("tags", tag)

    def count(self, field: str) -> int:
        if field != "product_crops.detections":
            return 0
        return sum(len(sample.product_crops.detections) for sample in self.samples)


class FakeFiftyOne:
    Detection = FakeRecord
    Detections = FakeRecord
    Sample = FakeRecord

    def __init__(self) -> None:
        self.datasets: dict[str, FakeDataset] = {}

    def Dataset(
        self,
        name: str,
        *,
        persistent: bool,
        overwrite: bool,
    ) -> FakeDataset:
        if overwrite:
            self.datasets.pop(name, None)
        dataset = FakeDataset(self, name)
        dataset.persistent = persistent
        self.datasets[name] = dataset
        return dataset

    def list_datasets(self) -> list[str]:
        return list(self.datasets)

    def delete_dataset(self, name: str) -> None:
        self.datasets.pop(name)


class FakeDuplicateIndex:
    neighbors_map: dict[str, list[tuple[str, float]]] = {}

    def duplicates_view(self, **fields: Any) -> tuple[str, dict[str, Any]]:
        return ("duplicates", fields)


class FakeBrain:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def compute_similarity(self, dataset: Any, **arguments: Any) -> object:
        del dataset
        if self.fail:
            raise RuntimeError("similarity failed")
        call = "crop" if arguments.get("patches_field") else "image"
        self.calls.append(call)
        return object()

    def compute_near_duplicates(
        self,
        dataset: Any,
        **arguments: Any,
    ) -> FakeDuplicateIndex:
        assert arguments["embeddings"] == "similarity_embedding"
        assert dataset[0] == "tags"
        self.calls.append("duplicates")
        return FakeDuplicateIndex()
