from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, delete, select

from shelfsight_api.database import get_engine
from shelfsight_api.job_service import enqueue_job
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    embeddings,
    jobs,
    sku_reference_images,
    skus,
)
from shelfsight_api.propagation_service import enqueue_propagation_embeddings
from shelfsight_api.recognition_service import (
    enqueue_annotation_recognition_embedding,
    enqueue_gallery_recognition_embeddings,
)

QUEUE_CHUNK_SIZE = 250


class SimilarityRebuildBlockedError(RuntimeError):
    """Raised when an embedding worker is active during a rebuild."""


def rebuild_similarity_projections(connection: Connection) -> dict[str, Any]:
    running = connection.execute(
        select(jobs.c.id).where(jobs.c.job_type == "embed", jobs.c.state == "running").limit(1)
    ).first()
    if running is not None:
        raise SimilarityRebuildBlockedError(
            "stop embedding workers before rebuilding similarity projections"
        )

    rebuild_id = uuid4()
    reference_ids = list(
        connection.execute(
            select(sku_reference_images.c.id)
            .join(skus, skus.c.id == sku_reference_images.c.sku_id)
            .where(skus.c.status == "active", skus.c.is_unknown.is_(False))
            .order_by(sku_reference_images.c.id)
        ).scalars()
    )
    annotation_rows = list(
        connection.execute(
            select(
                annotation_records.c.id,
                annotation_records.c.current_revision,
                annotation_revisions.c.sku_id,
                skus.c.is_unknown,
            )
            .join(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision == annotation_records.c.current_revision,
                ),
            )
            .outerjoin(skus, skus.c.id == annotation_revisions.c.sku_id)
            .where(
                annotation_revisions.c.class_type == "product",
                annotation_revisions.c.lifecycle_state == "verified",
                annotation_revisions.c.review_state == "accepted",
            )
            .order_by(annotation_records.c.id)
        ).mappings()
    )

    source_job_ids: list[UUID] = []
    for chunk in _chunks(reference_ids):
        gallery_outcomes = enqueue_gallery_recognition_embeddings(connection, chunk)
        source_job_ids.extend(_accepted_job_ids(gallery_outcomes))
    for row in annotation_rows:
        job, _ = enqueue_annotation_recognition_embedding(
            connection,
            UUID(str(row["id"])),
            int(row["current_revision"]),
        )
        source_job_ids.append(UUID(str(job["id"])))
    propagation_ids = [
        UUID(str(row["id"]))
        for row in annotation_rows
        if row["sku_id"] is None or not bool(row["is_unknown"])
    ]
    for chunk in _chunks(propagation_ids):
        propagation_outcomes = enqueue_propagation_embeddings(connection, chunk)
        source_job_ids.extend(_accepted_job_ids(propagation_outcomes))

    queued_payloads = {
        _payload_key(payload)
        for payload in connection.execute(
            select(jobs.c.payload).where(
                jobs.c.job_type == "embed",
                jobs.c.state == "queued",
            )
        ).scalars()
    }
    queued_existing = 0
    queued_new = 0
    for source_job_id in source_job_ids:
        source = connection.execute(
            select(jobs).where(jobs.c.id == source_job_id)
        ).mappings().one()
        payload_key = _payload_key(source["payload"])
        if source["state"] == "queued" or payload_key in queued_payloads:
            queued_existing += 1
            continue
        _, created = enqueue_job(
            connection,
            "embed",
            f"projection-rebuild:{rebuild_id}:{source_job_id}",
            source["payload"],
            dataset_version_id=source["dataset_version_id"],
            max_attempts=3,
            progress_total=source["progress_total"],
        )
        queued_new += int(created)
        queued_payloads.add(payload_key)

    deleted_embeddings = connection.execute(delete(embeddings)).rowcount
    return {
        "rebuild_id": str(rebuild_id),
        "recognition_reference_targets": len(reference_ids),
        "recognition_annotation_targets": len(annotation_rows),
        "propagation_annotation_targets": len(propagation_ids),
        "deleted_embeddings": deleted_embeddings,
        "queued_existing_jobs": queued_existing,
        "queued_new_jobs": queued_new,
    }


def _chunks(values: list[UUID]) -> Iterable[list[UUID]]:
    for start in range(0, len(values), QUEUE_CHUNK_SIZE):
        yield values[start : start + QUEUE_CHUNK_SIZE]


def _accepted_job_ids(outcomes: Iterable[Any]) -> list[UUID]:
    job_ids: list[UUID] = []
    for outcome in outcomes:
        if outcome.job_id is None:
            raise RuntimeError("eligible similarity target was rejected")
        job_ids.append(UUID(str(outcome.job_id)))
    return job_ids


def _payload_key(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def main() -> int:
    engine = get_engine()
    try:
        with engine.begin() as connection:
            result = rebuild_similarity_projections(connection)
    except SimilarityRebuildBlockedError as error:
        print(f"similarity rebuild blocked: {error}")
        return 1
    finally:
        engine.dispose()
        get_engine.cache_clear()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
