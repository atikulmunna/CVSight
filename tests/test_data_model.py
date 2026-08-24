from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from shelfsight_api.data_model import (
    AnnotationNotFoundError,
    DatasetSnapshotNotFoundError,
    DatasetVersionFrozenError,
    DatasetVersionNotFoundError,
    SnapshotArtifactConflictError,
    StaleAnnotationRevisionError,
    append_annotation_revision,
    create_annotation,
    get_dataset_snapshot,
    register_snapshot_artifact,
    snapshot_dataset_version,
)
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_snapshots,
    dataset_version_annotations,
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    skus,
)


def create_dataset_fixture(
    connection: Connection,
    *,
    name: str | None = None,
    width: int = 100,
    height: int = 80,
) -> tuple[UUID, UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    image_id = uuid4()
    connection.execute(
        insert(datasets).values(id=dataset_id, name=name or f"dataset-{dataset_id}")
    )
    connection.execute(
        insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
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
            canonical_width=width,
            canonical_height=height,
        )
    )
    connection.execute(
        insert(dataset_version_images).values(
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            image_id=image_id,
        )
    )
    return dataset_id, version_id, image_id


def revision_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "x": 10.0,
        "y": 10.0,
        "width": 20.0,
        "height": 30.0,
        "class_type": "product",
        "lifecycle_state": "verified",
        "review_state": "accepted",
        "source": "human",
        "provenance": {"actor": "test"},
        "confidence": 0.9,
    }
    values.update(overrides)
    return values


def assert_insert_rejected(
    connection: Connection,
    table_values: Mapping[str, Any],
) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError):
        connection.execute(insert(images).values(**table_values))
    savepoint.rollback()


def test_version_membership_requires_the_same_dataset(
    database_connection: Connection,
) -> None:
    dataset_id, version_id, _ = create_dataset_fixture(database_connection)
    other_dataset_id, _, other_image_id = create_dataset_fixture(database_connection)

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError):
        database_connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id,
                dataset_version_id=version_id,
                image_id=other_image_id,
            )
        )
    savepoint.rollback()

    assert other_dataset_id != dataset_id


@pytest.mark.parametrize(
    "invalid_geometry",
    [
        {"x": -1.0},
        {"width": 0.0},
        {"x": 90.0, "width": 11.0},
        {"y": 70.0, "height": 11.0},
        {"x": math.nan},
        {"width": math.inf},
    ],
)
def test_annotation_geometry_must_fit_canonical_image(
    database_connection: Connection,
    invalid_geometry: dict[str, float],
) -> None:
    _, _, image_id = create_dataset_fixture(database_connection)
    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError):
        create_annotation(
            database_connection,
            image_id,
            revision_values(**invalid_geometry),
        )
    savepoint.rollback()


def test_annotation_revision_updates_reject_stale_and_missing_ids(
    database_connection: Connection,
) -> None:
    _, _, image_id = create_dataset_fixture(database_connection)
    annotation_id = create_annotation(
        database_connection,
        image_id,
        revision_values(),
    )

    assert append_annotation_revision(
        database_connection,
        annotation_id,
        1,
        revision_values(x=12.0),
    ) == 2

    with pytest.raises(StaleAnnotationRevisionError, match="revision is stale"):
        append_annotation_revision(
            database_connection,
            annotation_id,
            1,
            revision_values(x=14.0),
        )
    with pytest.raises(AnnotationNotFoundError, match="does not exist"):
        append_annotation_revision(
            database_connection,
            uuid4(),
            1,
            revision_values(),
        )

    current_revision = database_connection.execute(
        select(annotation_records.c.current_revision).where(
            annotation_records.c.id == annotation_id
        )
    ).scalar_one()
    revisions = database_connection.execute(
        select(annotation_revisions.c.revision)
        .where(annotation_revisions.c.annotation_id == annotation_id)
        .order_by(annotation_revisions.c.revision)
    ).scalars()

    assert current_revision == 2
    assert list(revisions) == [1, 2]


def test_snapshot_pins_revisions_and_freezes_membership(
    database_connection: Connection,
) -> None:
    dataset_id, version_id, image_id = create_dataset_fixture(database_connection)
    annotation_id = create_annotation(
        database_connection,
        image_id,
        revision_values(),
    )
    append_annotation_revision(
        database_connection,
        annotation_id,
        1,
        revision_values(x=12.0),
    )

    assert snapshot_dataset_version(database_connection, version_id) == 1
    assert append_annotation_revision(
        database_connection,
        annotation_id,
        2,
        revision_values(x=14.0),
    ) == 3

    captured_revision = database_connection.execute(
        select(dataset_version_annotations.c.annotation_revision).where(
            dataset_version_annotations.c.dataset_version_id == version_id,
            dataset_version_annotations.c.annotation_id == annotation_id,
        )
    ).scalar_one()
    assert captured_revision == 2

    other_image_id = uuid4()
    database_connection.execute(
        insert(images).values(
            id=other_image_id,
            dataset_id=dataset_id,
            original_media_key=f"original/{other_image_id}.jpg",
            canonical_media_key=f"images/{other_image_id}.jpg",
            thumbnail_media_key=f"thumbnails/{other_image_id}.jpg",
            media_type="image/jpeg",
            original_filename=f"{other_image_id}.jpg",
            content_sha256=other_image_id.hex * 2,
            canonical_width=100,
            canonical_height=80,
        )
    )
    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="immutable after snapshot"):
        database_connection.execute(
            insert(dataset_version_images).values(
                dataset_id=dataset_id,
                dataset_version_id=version_id,
                image_id=other_image_id,
            )
        )
    savepoint.rollback()

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="immutable after snapshot"):
        database_connection.execute(
            delete(dataset_version_annotations).where(
                dataset_version_annotations.c.dataset_version_id == version_id
            )
        )
    savepoint.rollback()

    with pytest.raises(DatasetVersionFrozenError, match="already snapshotted"):
        snapshot_dataset_version(database_connection, version_id)
    with pytest.raises(DatasetVersionNotFoundError, match="does not exist"):
        snapshot_dataset_version(database_connection, uuid4())


def test_empty_version_can_be_snapshotted(database_connection: Connection) -> None:
    dataset_id = uuid4()
    version_id = uuid4()
    database_connection.execute(
        insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}")
    )
    database_connection.execute(
        insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
    )

    assert snapshot_dataset_version(database_connection, version_id) == 0


def test_snapshot_reconstructs_historical_state_after_live_edits(
    database_connection: Connection,
) -> None:
    _, version_id, image_id = create_dataset_fixture(database_connection)
    sku_id = uuid4()
    database_connection.execute(
        insert(skus).values(id=sku_id, name="Original Cola", brand="Original Brand")
    )
    annotation_id = create_annotation(
        database_connection,
        image_id,
        revision_values(sku_id=sku_id, x=11.0),
    )

    snapshot_dataset_version(database_connection, version_id)
    captured = get_dataset_snapshot(database_connection, version_id)

    append_annotation_revision(
        database_connection,
        annotation_id,
        1,
        revision_values(sku_id=sku_id, x=44.0),
    )
    database_connection.execute(
        update(skus)
        .where(skus.c.id == sku_id)
        .values(name="Renamed Cola", brand="Renamed Brand")
    )

    reconstructed = get_dataset_snapshot(database_connection, version_id)
    assert reconstructed == captured
    captured_annotation = next(
        row for row in reconstructed["annotations"] if row["annotation_id"] == annotation_id
    )
    captured_sku = next(row for row in reconstructed["skus"] if row["sku_id"] == sku_id)
    assert captured_annotation["annotation_revision"] == 1
    assert captured_annotation["x"] == 11.0
    assert captured_sku["name"] == "Original Cola"
    assert len(reconstructed["content_sha256"]) == 64


def test_snapshot_state_is_immutable_and_artifacts_are_append_only(
    database_connection: Connection,
) -> None:
    _, version_id, _ = create_dataset_fixture(database_connection)
    snapshot_dataset_version(database_connection, version_id)

    artifact = register_snapshot_artifact(
        database_connection,
        version_id,
        "model",
        "models/detector-v1.pt",
        content_sha256="a" * 64,
        metadata={"framework": "pytorch"},
    )
    repeated = register_snapshot_artifact(
        database_connection,
        version_id,
        "model",
        "models/detector-v1.pt",
        content_sha256="a" * 64,
        metadata={"framework": "pytorch"},
    )
    assert repeated["id"] == artifact["id"]

    with pytest.raises(SnapshotArtifactConflictError, match="different lineage"):
        register_snapshot_artifact(
            database_connection,
            version_id,
            "model",
            "models/detector-v1.pt",
            content_sha256="b" * 64,
            metadata={"framework": "pytorch"},
        )
    with pytest.raises(DatasetSnapshotNotFoundError, match="does not exist"):
        register_snapshot_artifact(
            database_connection,
            uuid4(),
            "evaluation",
            "evaluations/missing.json",
        )

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="snapshot state is immutable"):
        database_connection.execute(
            update(dataset_snapshots)
            .where(dataset_snapshots.c.dataset_version_id == version_id)
            .values(content_sha256="c" * 64)
        )
    savepoint.rollback()

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="snapshot state is immutable"):
        database_connection.execute(
            delete(dataset_snapshots).where(
                dataset_snapshots.c.dataset_version_id == version_id
            )
        )
    savepoint.rollback()


def test_version_parent_must_be_a_snapshot_from_the_same_dataset(
    database_connection: Connection,
) -> None:
    dataset_id, parent_version_id, _ = create_dataset_fixture(database_connection)

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="parent version must be an immutable snapshot"):
        database_connection.execute(
            insert(dataset_versions).values(
                id=uuid4(),
                dataset_id=dataset_id,
                parent_version_id=parent_version_id,
            )
        )
    savepoint.rollback()

    snapshot_dataset_version(database_connection, parent_version_id)
    database_connection.execute(
        insert(dataset_versions).values(
            id=uuid4(),
            dataset_id=dataset_id,
            parent_version_id=parent_version_id,
        )
    )

    _, other_parent_id, _ = create_dataset_fixture(database_connection)
    snapshot_dataset_version(database_connection, other_parent_id)
    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="parent version must be an immutable snapshot"):
        database_connection.execute(
            insert(dataset_versions).values(
                id=uuid4(),
                dataset_id=dataset_id,
                parent_version_id=other_parent_id,
            )
        )
    savepoint.rollback()


def test_sku_merge_state_and_cycles_are_rejected(
    database_connection: Connection,
) -> None:
    first_id = uuid4()
    second_id = uuid4()
    database_connection.execute(
        insert(skus),
        [
            {"id": first_id, "name": "First"},
            {"id": second_id, "name": "Second"},
        ],
    )
    database_connection.execute(
        update(skus)
        .where(skus.c.id == first_id)
        .values(status="merged", merged_into_id=second_id)
    )

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError, match="merge would create a cycle"):
        database_connection.execute(
            update(skus)
            .where(skus.c.id == second_id)
            .values(status="merged", merged_into_id=first_id)
        )
    savepoint.rollback()

    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError):
        database_connection.execute(
            insert(skus).values(
                id=uuid4(),
                name="Broken status",
                status="active",
                merged_into_id=second_id,
            )
        )
    savepoint.rollback()

    missing_target_id = uuid4()
    savepoint = database_connection.begin_nested()
    with pytest.raises(IntegrityError):
        database_connection.execute(
            insert(skus).values(
                id=uuid4(),
                name="Missing target",
                status="merged",
                merged_into_id=missing_target_id,
            )
        )
    savepoint.rollback()


@pytest.mark.parametrize(
    "invalid_key",
    [
        "/absolute/image.jpg",
        "C:/absolute/image.jpg",
        "../outside/image.jpg",
        "images\\windows-path.jpg",
    ],
)
def test_images_reject_unmanaged_media_keys(
    database_connection: Connection,
    invalid_key: str,
) -> None:
    dataset_id = uuid4()
    database_connection.execute(
        insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}")
    )

    assert_insert_rejected(
        database_connection,
        {
            "id": uuid4(),
            "dataset_id": dataset_id,
            "original_media_key": "original/a.jpg",
            "canonical_media_key": invalid_key,
            "thumbnail_media_key": "thumbnails/a.jpg",
            "media_type": "image/jpeg",
            "original_filename": "a.jpg",
            "content_sha256": "a" * 64,
            "canonical_width": 100,
            "canonical_height": 80,
        },
    )
