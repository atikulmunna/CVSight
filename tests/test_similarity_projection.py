from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, func, insert, select, update

from shelfsight_api.data_model import create_annotation
from shelfsight_api.models import (
    dataset_versions,
    datasets,
    embeddings,
    images,
    jobs,
    sku_reference_images,
    skus,
)
from shelfsight_api.propagation_profile import T005_EXPLORATORY_PROPAGATION_PROFILE
from shelfsight_api.propagation_service import enqueue_propagation_embeddings
from shelfsight_api.recognition_profile import T005_RECOGNITION_PROFILE
from shelfsight_api.recognition_service import (
    enqueue_annotation_recognition_embedding,
    enqueue_gallery_recognition_embeddings,
)
from shelfsight_api.similarity_projection import (
    SimilarityRebuildBlockedError,
    rebuild_similarity_projections,
)


def test_similarity_rebuild_requeues_targets_without_losing_job_history(
    database_connection: Connection,
) -> None:
    annotation_id, reference_id = _seed_similarity_fixture(database_connection)
    original_job_ids = _complete_original_jobs(
        database_connection,
        annotation_id,
        reference_id,
    )
    _seed_embeddings(database_connection, annotation_id, reference_id)

    first = rebuild_similarity_projections(database_connection)

    assert first["recognition_reference_targets"] == 1
    assert first["recognition_annotation_targets"] == 1
    assert first["propagation_annotation_targets"] == 1
    assert first["deleted_embeddings"] == 3
    assert first["queued_new_jobs"] == 3
    assert first["queued_existing_jobs"] == 0
    assert database_connection.execute(
        select(func.count()).select_from(embeddings)
    ).scalar_one() == 0
    states = list(
        database_connection.execute(
            select(jobs.c.id, jobs.c.state).where(jobs.c.id.in_(original_job_ids))
        )
    )
    assert {state for _, state in states} == {"succeeded"}
    assert database_connection.execute(
        select(func.count()).select_from(jobs).where(jobs.c.state == "queued")
    ).scalar_one() == 3

    second = rebuild_similarity_projections(database_connection)

    assert second["queued_new_jobs"] == 0
    assert second["queued_existing_jobs"] == 3
    assert database_connection.execute(
        select(func.count()).select_from(jobs)
    ).scalar_one() == 6


def test_similarity_rebuild_refuses_an_active_embedding_worker(
    database_connection: Connection,
) -> None:
    annotation_id, reference_id = _seed_similarity_fixture(database_connection)
    original_job_ids = _complete_original_jobs(
        database_connection,
        annotation_id,
        reference_id,
    )
    _seed_embeddings(database_connection, annotation_id, reference_id)
    database_connection.execute(
        update(jobs)
        .where(jobs.c.id == original_job_ids[0])
        .values(
            state="running",
            claimed_by="active-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )

    with pytest.raises(SimilarityRebuildBlockedError, match="stop embedding workers"):
        rebuild_similarity_projections(database_connection)

    assert database_connection.execute(
        select(func.count()).select_from(embeddings)
    ).scalar_one() == 3


def _seed_similarity_fixture(connection: Connection) -> tuple[UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    image_id = uuid4()
    sku_id = uuid4()
    reference_id = uuid4()
    connection.execute(insert(datasets).values(id=dataset_id, name=f"rebuild-{dataset_id}"))
    connection.execute(
        insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
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
            content_sha256="a" * 64,
            canonical_width=100,
            canonical_height=80,
        )
    )
    connection.execute(insert(skus).values(id=sku_id, name="Recovery SKU"))
    connection.execute(
        insert(sku_reference_images).values(
            id=reference_id,
            sku_id=sku_id,
            original_media_key=f"references/original/{reference_id}.jpg",
            canonical_media_key=f"references/canonical/{reference_id}.jpg",
            thumbnail_media_key=f"references/thumbnails/{reference_id}.jpg",
            media_type="image/jpeg",
            original_filename="reference.jpg",
            content_sha256="b" * 64,
            width=40,
            height=60,
        )
    )
    annotation_id = create_annotation(
        connection,
        image_id,
        {
            "x": 10.0,
            "y": 10.0,
            "width": 30.0,
            "height": 40.0,
            "class_type": "product",
            "sku_id": sku_id,
            "lifecycle_state": "verified",
            "review_state": "accepted",
            "source": "human",
            "provenance": {"actor": "recovery-test"},
            "confidence": None,
            "occluded": False,
            "truncated": False,
            "shelf_row": 0,
        },
    )
    return annotation_id, reference_id


def _complete_original_jobs(
    connection: Connection,
    annotation_id: UUID,
    reference_id: UUID,
) -> list[UUID]:
    recognition, _ = enqueue_annotation_recognition_embedding(connection, annotation_id, 1)
    gallery = enqueue_gallery_recognition_embeddings(connection, [reference_id])[0]
    propagation = enqueue_propagation_embeddings(connection, [annotation_id])[0]
    job_ids = [
        UUID(str(recognition["id"])),
        UUID(str(gallery.job_id)),
        UUID(str(propagation.job_id)),
    ]
    connection.execute(
        update(jobs).where(jobs.c.id.in_(job_ids)).values(state="succeeded", result={})
    )
    return job_ids


def _seed_embeddings(
    connection: Connection,
    annotation_id: UUID,
    reference_id: UUID,
) -> None:
    recognition = T005_RECOGNITION_PROFILE.model_provenance
    propagation = T005_EXPLORATORY_PROPAGATION_PROFILE.model_provenance
    vector = [1.0, *([0.0] * (T005_RECOGNITION_PROFILE.dimension - 1))]
    connection.execute(
        insert(embeddings),
        [
            {
                "id": uuid4(),
                "purpose": "recognition",
                "subject_type": "sku_reference",
                "annotation_id": None,
                "annotation_revision": None,
                "reference_image_id": reference_id,
                "target_fingerprint": f"sha256:{'b' * 64}",
                "model_id": recognition.model_id,
                "model_version": recognition.model_version,
                "artifact_sha256": recognition.artifact_sha256,
                "configuration": {},
                "dimension": T005_RECOGNITION_PROFILE.dimension,
                "embedding": vector,
            },
            {
                "id": uuid4(),
                "purpose": "recognition",
                "subject_type": "annotation",
                "annotation_id": annotation_id,
                "annotation_revision": 1,
                "reference_image_id": None,
                "target_fingerprint": f"sha256:{'a' * 64}",
                "model_id": recognition.model_id,
                "model_version": recognition.model_version,
                "artifact_sha256": recognition.artifact_sha256,
                "configuration": {},
                "dimension": T005_RECOGNITION_PROFILE.dimension,
                "embedding": vector,
            },
            {
                "id": uuid4(),
                "purpose": "propagation",
                "subject_type": "annotation",
                "annotation_id": annotation_id,
                "annotation_revision": 1,
                "reference_image_id": None,
                "target_fingerprint": f"sha256:{'a' * 64}",
                "model_id": propagation.model_id,
                "model_version": propagation.model_version,
                "artifact_sha256": propagation.artifact_sha256,
                "configuration": {},
                "dimension": T005_EXPLORATORY_PROPAGATION_PROFILE.dimension,
                "embedding": vector,
            },
        ],
    )
