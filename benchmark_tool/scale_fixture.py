from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.engine import Connection

from shelfsight_api.data_model import create_annotation, snapshot_dataset_version
from shelfsight_api.models import (
    dataset_version_images,
    dataset_versions,
    datasets,
    embeddings,
    images,
    jobs,
    sku_reference_images,
    skus,
)
from shelfsight_api.recognition_profile import T005_RECOGNITION_PROFILE

IMAGE_COUNT = 100_000
SKU_COUNT = 2_000
DENSE_BOX_COUNT = 300
JOB_COUNT = 1_000
EXPORT_IMAGE_COUNT = 250
EXPORT_ANNOTATION_COUNT = 3_000
INSERT_CHUNK_SIZE = 5_000


@dataclass(frozen=True)
class ScaleFixture:
    dataset_id: UUID
    dataset_version_id: UUID
    image_ids: tuple[UUID, ...]
    dense_image_id: UUID
    query_annotation_id: UUID
    sku_ids: tuple[UUID, ...]
    export_dataset_version_id: UUID
    job_count: int


def seed_scale_fixture(engine: Engine, media_root: Path) -> tuple[ScaleFixture, dict[str, float]]:
    _require_empty_database(engine)
    media_key = _write_export_media(media_root)
    started = perf_counter()

    dataset_id = _id("metadata-dataset")
    version_id = _id("metadata-version")
    image_ids = tuple(_id(f"image-{index}") for index in range(IMAGE_COUNT))
    sku_ids = tuple(_id(f"sku-{index}") for index in range(SKU_COUNT))

    with engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name="T037 metadata scale"))
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        connection.execute(
            insert(skus),
            [_sku_row(index, sku_id) for index, sku_id in enumerate(sku_ids)],
        )

    for start in range(0, IMAGE_COUNT, INSERT_CHUNK_SIZE):
        end = min(start + INSERT_CHUNK_SIZE, IMAGE_COUNT)
        with engine.begin() as connection:
            connection.execute(
                insert(images),
                [_image_row(dataset_id, index, image_ids[index]) for index in range(start, end)],
            )
            connection.execute(
                insert(dataset_version_images),
                [
                    {
                        "dataset_id": dataset_id,
                        "dataset_version_id": version_id,
                        "image_id": image_ids[index],
                    }
                    for index in range(start, end)
                ],
            )

    dense_image_id = image_ids[0]
    with engine.begin() as connection:
        dense_annotation_ids = _seed_dense_annotations(
            connection,
            dense_image_id,
            sku_ids,
        )
        _seed_gallery(connection, sku_ids, media_key, dense_annotation_ids[0])
        _seed_jobs(connection)

    snapshot_started = perf_counter()
    export_version_id = _seed_export_snapshot(engine, sku_ids, media_key)
    snapshot_ms = (perf_counter() - snapshot_started) * 1_000
    with engine.begin() as connection:
        connection.execute(text("ANALYZE"))

    return (
        ScaleFixture(
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            image_ids=image_ids,
            dense_image_id=dense_image_id,
            query_annotation_id=dense_annotation_ids[0],
            sku_ids=sku_ids,
            export_dataset_version_id=export_version_id,
            job_count=JOB_COUNT,
        ),
        {
            "total_ms": (perf_counter() - started) * 1_000,
            "snapshot_ms": snapshot_ms,
        },
    )


def database_size_bytes(engine: Engine) -> int:
    with engine.connect() as connection:
        size = connection.execute(
            select(func.pg_database_size(func.current_database()))
        ).scalar_one()
        return int(size)


def _require_empty_database(engine: Engine) -> None:
    with engine.connect() as connection:
        counts = {
            "datasets": connection.execute(select(func.count()).select_from(datasets)).scalar_one(),
            "skus": connection.execute(
                select(func.count()).select_from(skus).where(skus.c.is_unknown.is_(False))
            ).scalar_one(),
            "jobs": connection.execute(select(func.count()).select_from(jobs)).scalar_one(),
        }
    if any(counts.values()):
        raise ValueError("scale benchmark requires an otherwise empty migrated database")


def _write_export_media(media_root: Path) -> str:
    media_key = "canonical/t037/performance-fixture.jpg"
    path = media_root / Path(media_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (38, 84, 80)).save(path, format="JPEG", quality=82)
    return media_key


def _sku_row(index: int, sku_id: UUID) -> dict[str, object]:
    return {
        "id": sku_id,
        "name": f"Scale Product {index:04d}",
        "upc": f"{index + 1:012d}",
        "category": f"Category {index % 20:02d}",
        "subcategory": f"Subcategory {index % 100:03d}",
        "brand": f"Brand {index % 200:03d}",
        "variant": f"Variant {index:04d}",
        "is_unknown": False,
        "status": "active",
    }


def _image_row(dataset_id: UUID, index: int, image_id: UUID) -> dict[str, object]:
    digest = f"{index + 1:064x}"
    return {
        "id": image_id,
        "dataset_id": dataset_id,
        "original_media_key": f"original/{digest[:2]}/{digest}.jpg",
        "canonical_media_key": f"canonical/{digest[:2]}/{digest}.jpg",
        "thumbnail_media_key": f"thumbnails/{digest[:2]}/{digest}.jpg",
        "media_type": "image/jpeg",
        "original_filename": f"scale-{index:06d}.jpg",
        "content_sha256": digest,
        "canonical_width": 1920,
        "canonical_height": 1080,
        "capture_metadata": {"provided": {"scale_index": index}},
        "status": "unlabeled",
    }


def _seed_dense_annotations(
    connection: Connection,
    image_id: UUID,
    sku_ids: tuple[UUID, ...],
) -> tuple[UUID, ...]:
    annotation_ids: list[UUID] = []
    for index in range(DENSE_BOX_COUNT):
        annotation_id = _id(f"dense-annotation-{index}")
        annotation_ids.append(annotation_id)
        column = index % 30
        row = index // 30
        create_annotation(
            connection,
            image_id,
            {
                "x": float(8 + column * 62),
                "y": float(8 + row * 100),
                "width": 54.0,
                "height": 88.0,
                "class_type": "product",
                "sku_id": sku_ids[index % len(sku_ids)],
                "lifecycle_state": "verified",
                "review_state": "accepted",
                "source": "human",
                "provenance": {"actor": "t037", "action": "scale_fixture"},
                "confidence": None,
                "occluded": False,
                "truncated": False,
                "shelf_row": row,
            },
            annotation_id=annotation_id,
        )
    return tuple(annotation_ids)


def _seed_gallery(
    connection: Connection,
    sku_ids: tuple[UUID, ...],
    media_key: str,
    query_annotation_id: UUID,
) -> None:
    profile = T005_RECOGNITION_PROFILE
    provenance = profile.model_provenance
    reference_ids = tuple(_id(f"reference-{index}") for index in range(SKU_COUNT))
    connection.execute(
        insert(sku_reference_images),
        [
            {
                "id": reference_id,
                "sku_id": sku_ids[index],
                "original_media_key": media_key,
                "canonical_media_key": media_key,
                "thumbnail_media_key": media_key,
                "media_type": "image/jpeg",
                "original_filename": f"reference-{index:04d}.jpg",
                "content_sha256": f"{index + 1:064x}",
                "width": 640,
                "height": 480,
            }
            for index, reference_id in enumerate(reference_ids)
        ],
    )
    embedding_rows = []
    for index, reference_id in enumerate(reference_ids):
        embedding_rows.append(
            {
                "id": _id(f"reference-embedding-{index}"),
                "purpose": "recognition",
                "subject_type": "sku_reference",
                "annotation_id": None,
                "annotation_revision": None,
                "reference_image_id": reference_id,
                "target_fingerprint": f"sha256:{index + 1:064x}",
                "model_id": provenance.model_id,
                "model_version": provenance.model_version,
                "artifact_sha256": provenance.artifact_sha256,
                "configuration": {"fixture": "t037"},
                "dimension": profile.dimension,
                "embedding": scale_vector(index, profile.dimension),
            }
        )
    connection.execute(insert(embeddings), embedding_rows)
    connection.execute(
        insert(embeddings).values(
            id=_id("query-embedding"),
            purpose="recognition",
            subject_type="annotation",
            annotation_id=query_annotation_id,
            annotation_revision=1,
            reference_image_id=None,
            target_fingerprint=f"sha256:{'f' * 64}",
            model_id=provenance.model_id,
            model_version=provenance.model_version,
            artifact_sha256=provenance.artifact_sha256,
            configuration={"fixture": "t037"},
            dimension=profile.dimension,
            embedding=scale_vector(0, profile.dimension),
        )
    )


def scale_vector(index: int, dimension: int) -> list[float]:
    if dimension < 2:
        raise ValueError("scale vector dimension must be at least 2")
    values = [0.0] * dimension
    first = index % dimension
    second = (index * 17 + 1) % dimension
    if second == first:
        second = (second + 1) % dimension
    values[first] = 0.8
    values[second] = 0.6
    return values


def _seed_jobs(connection: Connection) -> None:
    now = datetime.now(UTC)
    connection.execute(
        insert(jobs),
        [
            {
                "id": _id(f"job-{index}"),
                "job_type": "scale",
                "idempotency_key": f"t037-{index:04d}",
                "payload": {"index": index},
                "state": "queued",
                "progress_current": 0,
                "progress_total": 1,
                "attempt_count": 0,
                "max_attempts": 3,
                "available_at": now,
            }
            for index in range(JOB_COUNT)
        ],
    )


def _seed_export_snapshot(
    engine: Engine,
    sku_ids: tuple[UUID, ...],
    media_key: str,
) -> UUID:
    dataset_id = _id("export-dataset")
    version_id = _id("export-version")
    export_image_ids = tuple(_id(f"export-image-{index}") for index in range(EXPORT_IMAGE_COUNT))
    with engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name="T037 export scale"))
        connection.execute(
            insert(dataset_versions).values(id=version_id, dataset_id=dataset_id)
        )
        connection.execute(
            insert(images),
            [
                {
                    **_image_row(dataset_id, IMAGE_COUNT + index, image_id),
                    "original_media_key": media_key,
                    "canonical_media_key": media_key,
                    "thumbnail_media_key": media_key,
                    "canonical_width": 640,
                    "canonical_height": 480,
                    "status": "reviewed",
                }
                for index, image_id in enumerate(export_image_ids)
            ],
        )
        connection.execute(
            insert(dataset_version_images),
            [
                {
                    "dataset_id": dataset_id,
                    "dataset_version_id": version_id,
                    "image_id": image_id,
                }
                for image_id in export_image_ids
            ],
        )
        for index in range(EXPORT_ANNOTATION_COUNT):
            image_id = export_image_ids[index % len(export_image_ids)]
            box_index = index // len(export_image_ids)
            create_annotation(
                connection,
                image_id,
                {
                    "x": float(8 + (box_index % 6) * 100),
                    "y": float(8 + (box_index // 6) * 100),
                    "width": 80.0,
                    "height": 80.0,
                    "class_type": "product",
                    "sku_id": sku_ids[index % len(sku_ids)],
                    "lifecycle_state": "verified",
                    "review_state": "accepted",
                    "source": "human",
                    "provenance": {"actor": "t037", "action": "export_fixture"},
                    "confidence": None,
                    "occluded": False,
                    "truncated": False,
                    "shelf_row": box_index // 6,
                },
                annotation_id=_id(f"export-annotation-{index}"),
            )
        snapshot_dataset_version(connection, version_id)
    return version_id


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://cvsight.local/t037/{label}")
