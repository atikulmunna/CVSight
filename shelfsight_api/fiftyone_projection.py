from __future__ import annotations

import argparse
import importlib
import math
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from shelfsight_api.config import get_media_root
from shelfsight_api.database import get_engine
from shelfsight_api.projection import (
    ProjectionDetection,
    ProjectionManifest,
    build_projection_manifest,
)

DATASET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FiftyOneUnavailableError(RuntimeError):
    """Raised when the optional projection runtime is unavailable."""


def rebuild_projection(
    manifest: ProjectionManifest,
    dataset_name: str,
    *,
    compute_similarity: bool = True,
    fiftyone_module: Any | None = None,
    brain_module: Any | None = None,
) -> dict[str, Any]:
    _validate_dataset_name(dataset_name)
    fiftyone = fiftyone_module or _import_optional("fiftyone")
    brain = brain_module or _import_optional("fiftyone.brain")
    staging_name = f"{dataset_name}.building-{uuid4().hex[:12]}"
    dataset = fiftyone.Dataset(staging_name, persistent=True, overwrite=True)
    try:
        samples = [_fiftyone_sample(fiftyone, sample) for sample in manifest.samples]
        if samples:
            dataset.add_samples(samples)
            dataset.add_dynamic_sample_fields(fields="product_crops")
        dataset.info = _dataset_info(manifest)
        dataset.description = "Read-only projection of an immutable CVSight snapshot"
        dataset.persistent = True
        dataset.save()
        _save_split_views(dataset, manifest)
        if compute_similarity and samples:
            _compute_similarity_views(brain, dataset, manifest)
        if dataset_name in fiftyone.list_datasets():
            fiftyone.delete_dataset(dataset_name)
        dataset.name = dataset_name
        dataset.save()
        return {
            "dataset_name": dataset_name,
            "dataset_version_id": str(manifest.dataset_version_id),
            "projection_schema_version": manifest.projection_schema_version,
            "sample_count": len(manifest.samples),
            "detection_count": sum(len(sample.detections) for sample in manifest.samples),
            "product_crop_count": sum(
                len(sample.product_crops) for sample in manifest.samples
            ),
            "similarity_computed": compute_similarity and bool(samples),
        }
    except Exception:
        if staging_name in fiftyone.list_datasets():
            fiftyone.delete_dataset(staging_name)
        raise


def delete_projection(dataset_name: str, *, fiftyone_module: Any | None = None) -> bool:
    _validate_dataset_name(dataset_name)
    fiftyone = fiftyone_module or _import_optional("fiftyone")
    if dataset_name not in fiftyone.list_datasets():
        return False
    fiftyone.delete_dataset(dataset_name)
    return True


def _fiftyone_sample(fiftyone: Any, sample: Any) -> Any:
    image_module = _import_optional("PIL.Image")
    with image_module.open(sample.filepath) as source:
        image = source.convert("RGB")
        image_embedding = _visual_embedding(image)
        crop_embeddings = {
            detection.annotation_id: _crop_embedding(image, detection.bounding_box)
            for detection in sample.product_crops
        }
    tags = [f"status:{sample.status}"]
    tags.append(_split_tag(sample.split))
    return fiftyone.Sample(
        filepath=str(sample.filepath),
        tags=tags,
        shelfsight_image_id=str(sample.image_id),
        source_content_sha256=sample.content_sha256,
        similarity_embedding=image_embedding,
        evaluation_split=sample.split,
        near_duplicate_group=sample.near_duplicate_group,
        capture_session_id=sample.capture_session_id,
        store_id=sample.store_id,
        fixture_id=sample.fixture_id,
        image_status=sample.status,
        ground_truth=fiftyone.Detections(
            detections=[_fiftyone_detection(fiftyone, value) for value in sample.detections]
        ),
        product_crops=fiftyone.Detections(
            detections=[
                _fiftyone_detection(
                    fiftyone,
                    value,
                    similarity_embedding=crop_embeddings[value.annotation_id],
                )
                for value in sample.product_crops
            ]
        ),
    )


def _fiftyone_detection(
    fiftyone: Any,
    detection: ProjectionDetection,
    *,
    similarity_embedding: list[float] | None = None,
) -> Any:
    values = {
        "label": detection.label,
        "bounding_box": list(detection.bounding_box),
        "confidence": detection.confidence,
        "shelfsight_annotation_id": str(detection.annotation_id),
        "annotation_revision": detection.annotation_revision,
        "sku_id": str(detection.sku_id) if detection.sku_id is not None else None,
        "sku_name": detection.sku_name,
        "lifecycle_state": detection.lifecycle_state,
        "review_state": detection.review_state,
        "source": detection.source,
        "occluded": detection.occluded,
        "truncated": detection.truncated,
        "shelf_row": detection.shelf_row,
        "provenance": detection.provenance,
    }
    if similarity_embedding is not None:
        values["similarity_embedding"] = similarity_embedding
    return fiftyone.Detection(**values)


def _visual_embedding(image: Any) -> list[float]:
    resized = image.resize((8, 8))
    values = [
        channel / 255
        for pixel in resized.get_flattened_data()
        for channel in pixel
    ]
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude == 0:
        return values
    return [value / magnitude for value in values]


def _crop_embedding(
    image: Any,
    bounding_box: tuple[float, float, float, float],
) -> list[float]:
    x, y, width, height = bounding_box
    left = round(x * image.width)
    top = round(y * image.height)
    right = round((x + width) * image.width)
    bottom = round((y + height) * image.height)
    return _visual_embedding(image.crop((left, top, right, bottom)))


def _dataset_info(manifest: ProjectionManifest) -> dict[str, Any]:
    artifacts: dict[str, list[dict[str, Any]]] = {
        "export": [],
        "model": [],
        "evaluation": [],
    }
    for artifact in manifest.artifacts:
        artifacts[str(artifact["artifact_type"])].append(
            {
                "artifact_key": str(artifact["artifact_key"]),
                "content_sha256": artifact["content_sha256"],
                "metadata": dict(artifact["metadata"]),
            }
        )
    return {
        "projection_schema_version": manifest.projection_schema_version,
        "source_dataset_id": str(manifest.dataset_id),
        "source_dataset_version_id": str(manifest.dataset_version_id),
        "source_schema_version": manifest.source_schema_version,
        "source_content_sha256": manifest.source_content_sha256,
        "read_only_projection": True,
        "artifacts": artifacts,
    }


def _save_split_views(dataset: Any, manifest: ProjectionManifest) -> None:
    for split in _projection_splits(manifest):
        dataset.save_view(
            _split_view_name("split", split),
            dataset.match_tags(_split_tag(split)),
        )


def _compute_similarity_views(
    brain: Any, dataset: Any, manifest: ProjectionManifest
) -> None:
    brain.compute_similarity(
        dataset,
        embeddings="similarity_embedding",
        brain_key="image_similarity",
    )
    if dataset.count("product_crops.detections"):
        brain.compute_similarity(
            dataset,
            patches_field="product_crops",
            embeddings="similarity_embedding",
            brain_key="product_crop_similarity",
        )
    for split in _projection_splits(manifest):
        split_view = dataset.match_tags(_split_tag(split))
        duplicate_index = brain.compute_near_duplicates(
            split_view,
            embeddings="similarity_embedding",
        )
        fields = {}
        if duplicate_index.neighbors_map:
            fields = {
                "type_field": "duplicate_type",
                "id_field": "duplicate_of",
                "dist_field": "duplicate_distance",
            }
        dataset.save_view(
            _split_view_name("near-duplicates", split),
            duplicate_index.duplicates_view(**fields),
        )


def _projection_splits(manifest: ProjectionManifest) -> list[str | None]:
    return sorted(
        {sample.split for sample in manifest.samples},
        key=lambda value: value or "",
    )


def _split_tag(split: str | None) -> str:
    return f"split:{split or 'unassigned'}"


def _split_view_name(prefix: str, split: str | None) -> str:
    return f"{prefix}-{split or 'unassigned'}"


def _import_optional(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as error:
        raise FiftyOneUnavailableError(
            "FiftyOne requires the optional Python 3.12 projection environment"
        ) from error


def _validate_dataset_name(dataset_name: str) -> None:
    if not DATASET_NAME_PATTERN.fullmatch(dataset_name):
        raise ValueError("dataset name is invalid")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the CVSight FiftyOne projection")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rebuild = subparsers.add_parser("rebuild", help="Rebuild from an immutable snapshot")
    rebuild.add_argument("dataset_version_id", type=UUID)
    rebuild.add_argument("--dataset-name", required=True)
    rebuild.add_argument(
        "--skip-similarity",
        action="store_true",
        help="Build fields only, without FiftyOne Brain indexes",
    )
    delete = subparsers.add_parser("delete", help="Delete only the rebuildable projection")
    delete.add_argument("--dataset-name", required=True)
    arguments = parser.parse_args()

    if arguments.command == "delete":
        deleted = delete_projection(arguments.dataset_name)
        print("projection deleted" if deleted else "projection not found")
        return

    with get_engine().connect() as connection:
        manifest = build_projection_manifest(
            connection,
            Path(get_media_root()),
            arguments.dataset_version_id,
        )
    result = rebuild_projection(
        manifest,
        arguments.dataset_name,
        compute_similarity=not arguments.skip_similarity,
    )
    print(
        "projection rebuilt: "
        f"{result['sample_count']} images, {result['product_crop_count']} product crops"
    )


if __name__ == "__main__":
    main()
