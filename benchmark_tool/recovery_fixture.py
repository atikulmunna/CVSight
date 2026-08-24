from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import Connection, Engine, func, insert, select, update

from shelfsight_api.data_model import (
    create_annotation,
    register_snapshot_artifact,
    snapshot_dataset_version,
)
from shelfsight_api.database import get_engine
from shelfsight_api.dataset_service import create_dataset_with_open_version
from shelfsight_api.image_ingest import ingest_prepared_image
from shelfsight_api.job_service import (
    claim_next_job,
    enqueue_job,
    get_job,
    recover_expired_jobs,
)
from shelfsight_api.media import prepare_image, resolve_media_path
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_snapshots,
    datasets,
    embeddings,
    images,
    jobs,
    sku_reference_images,
    skus,
)
from shelfsight_api.projection import build_projection_manifest
from shelfsight_api.propagation_profile import T005_EXPLORATORY_PROPAGATION_PROFILE
from shelfsight_api.propagation_service import enqueue_propagation_embeddings
from shelfsight_api.recognition_profile import T005_RECOGNITION_PROFILE
from shelfsight_api.recognition_service import (
    enqueue_annotation_recognition_embedding,
    enqueue_gallery_recognition_embeddings,
)
from shelfsight_api.sku_reference_service import ingest_sku_reference_image

STATE_SCHEMA = "cvsight-recovery-fixture/v1"


def seed_fixture(engine: Engine, media_root: Path) -> dict[str, Any]:
    _require_empty_database(engine)
    with engine.begin() as connection:
        dataset = create_dataset_with_open_version(
            connection,
            "T038 recovery fixture",
            "Disposable backup and restore evidence",
        )
        sku_id = uuid4()
        connection.execute(
            insert(skus).values(
                id=sku_id,
                name="Recovery Fixture SKU",
                brand="CVSight",
                variant="T038",
            )
        )

    dataset_id = UUID(str(dataset["id"]))
    version_id = UUID(str(dataset["open_version_id"]))
    image_prepared = prepare_image(
        _png_bytes("#287f78"),
        "recovery-shelf.png",
        {
            "split": "test",
            "near_duplicate_group": "t038-recovery",
            "capture_session_id": "t038",
            "store_id": "recovery-store",
            "fixture_id": "recovery-fixture",
        },
    )
    image = ingest_prepared_image(
        engine,
        media_root,
        dataset_id,
        version_id,
        image_prepared,
    )
    reference = ingest_sku_reference_image(
        engine,
        media_root,
        sku_id,
        prepare_image(_png_bytes("#38bda9"), "recovery-reference.png"),
    )

    with engine.begin() as connection:
        annotation_id = create_annotation(
            connection,
            image.id,
            {
                "x": 12.0,
                "y": 10.0,
                "width": 42.0,
                "height": 54.0,
                "class_type": "product",
                "sku_id": sku_id,
                "lifecycle_state": "verified",
                "review_state": "accepted",
                "source": "human",
                "provenance": {"actor": "t038", "action": "recovery_fixture"},
                "confidence": None,
                "occluded": False,
                "truncated": False,
                "shelf_row": 0,
            },
        )
        connection.execute(
            update(images).where(images.c.id == image.id).values(status="reviewed")
        )
        snapshot_dataset_version(connection, version_id)
        snapshot = connection.execute(
            select(dataset_snapshots.c.content_sha256).where(
                dataset_snapshots.c.dataset_version_id == version_id
            )
        ).scalar_one()
        register_snapshot_artifact(
            connection,
            version_id,
            "evaluation",
            "t038/recovery-fixture.json",
            content_sha256="d" * 64,
            metadata={"purpose": "recovery_drill"},
        )
        embedding_job_ids = _complete_embedding_jobs(
            connection,
            annotation_id,
            reference.id,
        )
        _insert_embeddings(connection, annotation_id, reference.id)
        recovery_job, _ = enqueue_job(
            connection,
            "recovery_probe",
            "t038-expired-worker",
            {"fixture": "t038"},
            max_attempts=2,
        )
        claim = claim_next_job(
            connection,
            "interrupted-worker",
            lease_seconds=1,
            now=datetime.now(UTC),
            job_types={"recovery_probe"},
        )
        if claim is None or claim.id != recovery_job["id"]:
            raise RuntimeError("recovery fixture job was not claimed")
        connection.execute(
            update(jobs)
            .where(jobs.c.id == claim.id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))
        )

    identity = {
        "dataset_id": str(dataset_id),
        "dataset_version_id": str(version_id),
        "image_id": str(image.id),
        "annotation_id": str(annotation_id),
        "sku_id": str(sku_id),
        "reference_image_id": str(reference.id),
        "recovery_job_id": str(recovery_job["id"]),
        "embedding_job_ids": [str(value) for value in embedding_job_ids],
        "snapshot_content_sha256": str(snapshot),
    }
    return {
        "schema_version": STATE_SCHEMA,
        "identity": identity,
        "fingerprint": fixture_fingerprint(engine, media_root, identity),
    }


def fixture_fingerprint(
    engine: Engine,
    media_root: Path,
    identity: dict[str, Any],
) -> dict[str, Any]:
    version_id = UUID(identity["dataset_version_id"])
    annotation_id = UUID(identity["annotation_id"])
    recovery_job_id = UUID(identity["recovery_job_id"])
    with engine.connect() as connection:
        current = connection.execute(
            select(
                annotation_records.c.current_revision,
                annotation_revisions.c.sku_id,
                annotation_revisions.c.review_state,
                annotation_revisions.c.lifecycle_state,
            )
            .join(
                annotation_revisions,
                (annotation_revisions.c.annotation_id == annotation_records.c.id)
                & (
                    annotation_revisions.c.revision
                    == annotation_records.c.current_revision
                ),
            )
            .where(annotation_records.c.id == annotation_id)
        ).mappings().one()
        snapshot_hash = connection.execute(
            select(dataset_snapshots.c.content_sha256).where(
                dataset_snapshots.c.dataset_version_id == version_id
            )
        ).scalar_one()
        media_keys = sorted(
            {
                str(value)
                for row in connection.execute(
                    select(
                        images.c.original_media_key,
                        images.c.canonical_media_key,
                        images.c.thumbnail_media_key,
                    ).where(images.c.dataset_id == UUID(identity["dataset_id"]))
                )
                for value in row
            }
            | {
                str(value)
                for row in connection.execute(
                    select(
                        sku_reference_images.c.original_media_key,
                        sku_reference_images.c.canonical_media_key,
                        sku_reference_images.c.thumbnail_media_key,
                    ).where(
                        sku_reference_images.c.id
                        == UUID(identity["reference_image_id"])
                    )
                )
                for value in row
            }
        )
        embedding_rows = [
            {
                "purpose": row["purpose"],
                "subject_type": row["subject_type"],
                "annotation_id": (
                    str(row["annotation_id"]) if row["annotation_id"] else None
                ),
                "annotation_revision": row["annotation_revision"],
                "reference_image_id": (
                    str(row["reference_image_id"])
                    if row["reference_image_id"]
                    else None
                ),
                "model_id": row["model_id"],
                "model_version": row["model_version"],
                "embedding": [round(float(value), 8) for value in row["embedding"]],
            }
            for row in connection.execute(
                select(embeddings).order_by(
                    embeddings.c.purpose,
                    embeddings.c.subject_type,
                    embeddings.c.id,
                )
            ).mappings()
        ]
        job = get_job(connection, recovery_job_id)
        counts = {
            "datasets": connection.execute(
                select(func.count()).select_from(datasets)
            ).scalar_one(),
            "images": connection.execute(
                select(func.count()).select_from(images)
            ).scalar_one(),
            "annotations": connection.execute(
                select(func.count()).select_from(annotation_records)
            ).scalar_one(),
            "sku_references": connection.execute(
                select(func.count()).select_from(sku_reference_images)
            ).scalar_one(),
            "embeddings": len(embedding_rows),
        }
        projection_hash = _projection_hash(connection, media_root, version_id)

    media = {
        key: _sha256_file(resolve_media_path(media_root, key))
        for key in media_keys
    }
    return {
        "counts": counts,
        "snapshot_content_sha256": str(snapshot_hash),
        "annotation": {
            "current_revision": current["current_revision"],
            "sku_id": str(current["sku_id"]),
            "review_state": current["review_state"],
            "lifecycle_state": current["lifecycle_state"],
        },
        "media": media,
        "embeddings_sha256": _json_sha256(embedding_rows),
        "projection_sha256": projection_hash,
        "recovery_job_state": job["state"],
    }


def verify_fixture(engine: Engine, media_root: Path, state: dict[str, Any]) -> None:
    if state.get("schema_version") != STATE_SCHEMA:
        raise ValueError("recovery fixture state is incompatible")
    actual = fixture_fingerprint(engine, media_root, state["identity"])
    if actual != state.get("fingerprint"):
        raise ValueError("restored fixture does not match its recovery fingerprint")


def recover_fixture_job(engine: Engine, state: dict[str, Any]) -> dict[str, Any]:
    job_id = UUID(state["identity"]["recovery_job_id"])
    with engine.begin() as connection:
        recovered = recover_expired_jobs(connection)
        job = get_job(connection, job_id)
    if recovered < 1 or job["state"] != "queued":
        raise RuntimeError("expired fixture job was not recovered")
    return {
        "recovered_jobs": recovered,
        "fixture_job_state": job["state"],
        "error_code": job["error_code"],
    }


def _complete_embedding_jobs(
    connection: Connection,
    annotation_id: UUID,
    reference_id: UUID,
) -> list[UUID]:
    annotation_job, _ = enqueue_annotation_recognition_embedding(
        connection,
        annotation_id,
        1,
    )
    gallery = enqueue_gallery_recognition_embeddings(connection, [reference_id])[0]
    propagation = enqueue_propagation_embeddings(connection, [annotation_id])[0]
    job_ids = [
        UUID(str(annotation_job["id"])),
        UUID(str(gallery.job_id)),
        UUID(str(propagation.job_id)),
    ]
    connection.execute(
        update(jobs)
        .where(jobs.c.id.in_(job_ids))
        .values(state="succeeded", result={"fixture": True})
    )
    return job_ids


def _insert_embeddings(
    connection: Connection,
    annotation_id: UUID,
    reference_id: UUID,
) -> None:
    recognition = T005_RECOGNITION_PROFILE.model_provenance
    propagation = T005_EXPLORATORY_PROPAGATION_PROFILE.model_provenance
    recognition_vector = _unit_vector(T005_RECOGNITION_PROFILE.dimension)
    propagation_vector = _unit_vector(T005_EXPLORATORY_PROPAGATION_PROFILE.dimension)
    connection.execute(
        insert(embeddings),
        [
            _embedding_row(
                "recognition",
                "sku_reference",
                recognition,
                recognition_vector,
                reference_id=reference_id,
                fingerprint="b" * 64,
            ),
            _embedding_row(
                "recognition",
                "annotation",
                recognition,
                recognition_vector,
                annotation_id=annotation_id,
                fingerprint="a" * 64,
            ),
            _embedding_row(
                "propagation",
                "annotation",
                propagation,
                propagation_vector,
                annotation_id=annotation_id,
                fingerprint="a" * 64,
            ),
        ],
    )


def _embedding_row(
    purpose: str,
    subject_type: str,
    provenance: Any,
    vector: list[float],
    *,
    annotation_id: UUID | None = None,
    reference_id: UUID | None = None,
    fingerprint: str,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "purpose": purpose,
        "subject_type": subject_type,
        "annotation_id": annotation_id,
        "annotation_revision": 1 if annotation_id else None,
        "reference_image_id": reference_id,
        "target_fingerprint": f"sha256:{fingerprint}",
        "model_id": provenance.model_id,
        "model_version": provenance.model_version,
        "artifact_sha256": provenance.artifact_sha256,
        "configuration": {"fixture": "t038"},
        "dimension": len(vector),
        "embedding": vector,
    }


def _unit_vector(dimension: int) -> list[float]:
    return [1.0, *([0.0] * (dimension - 1))]


def _projection_hash(
    connection: Connection,
    media_root: Path,
    version_id: UUID,
) -> str:
    manifest = build_projection_manifest(connection, media_root, version_id)
    value = {
        "schema": manifest.projection_schema_version,
        "source": manifest.source_content_sha256,
        "samples": [
            {
                "image_id": str(sample.image_id),
                "content_sha256": sample.content_sha256,
                "split": sample.split,
                "detections": [
                    {
                        "annotation_id": str(detection.annotation_id),
                        "revision": detection.annotation_revision,
                        "box": detection.bounding_box,
                        "sku_id": str(detection.sku_id),
                    }
                    for detection in sample.detections
                ],
            }
            for sample in manifest.samples
        ],
    }
    return _json_sha256(value)


def _png_bytes(color: str) -> bytes:
    output = BytesIO()
    with Image.new("RGB", (96, 72), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    content = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _require_empty_database(engine: Engine) -> None:
    with engine.connect() as connection:
        dataset_count = connection.execute(
            select(func.count()).select_from(datasets)
        ).scalar_one()
        sku_count = connection.execute(
            select(func.count()).select_from(skus).where(skus.c.is_unknown.is_(False))
        ).scalar_one()
    if dataset_count or sku_count:
        raise ValueError("recovery fixture requires an empty migrated database")


def _read_state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("recovery fixture state must be an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the T038 recovery fixture")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--state", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--state", type=Path, required=True)
    recover = commands.add_parser("recover-job")
    recover.add_argument("--state", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    engine = get_engine()
    try:
        if arguments.command == "seed":
            state = seed_fixture(engine, Path(os.environ["SHELFSIGHT_MEDIA_ROOT"]))
            arguments.state.parent.mkdir(parents=True, exist_ok=True)
            arguments.state.write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = {"seeded": True, **state["identity"]}
        else:
            state = _read_state(arguments.state)
            if arguments.command == "verify":
                verify_fixture(engine, Path(os.environ["SHELFSIGHT_MEDIA_ROOT"]), state)
                result = {"verified": True, **state["identity"]}
            else:
                result = recover_fixture_job(engine, state)
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"recovery fixture failed: {error}")
        return 1
    finally:
        engine.dispose()
        get_engine.cache_clear()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
