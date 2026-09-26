from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import yaml
from PIL import Image, UnidentifiedImageError
from sqlalchemy import Connection, select

from shelfsight_api.geometry import pixel_crop_bounds
from shelfsight_api.media import resolve_media_path
from shelfsight_api.models import (
    dataset_snapshot_annotations,
    dataset_snapshot_images,
    dataset_snapshot_skus,
    dataset_snapshots,
)

MANIFEST_SCHEMA = "shelfsight-export-manifest/v1"
DETECTION_SCHEMA = "shelfsight-coco-detection/v1"
RECOGNITION_SCHEMA = "shelfsight-recognition-jsonl/v1"
YOLO_SCHEMA = "shelfsight-yolo-detection/v1"
COCO_CATEGORIES = (
    {"id": 1, "name": "product", "supercategory": "shelf"},
    {"id": 2, "name": "gap", "supercategory": "shelf"},
    {"id": 3, "name": "shelf_label", "supercategory": "shelf"},
)
CATEGORY_IDS = {category["name"]: category["id"] for category in COCO_CATEGORIES}
YOLO_FOLDERS = {"train": "train", "validation": "val", "test": "test"}
# YOLO classes are zero-based and follow the COCO category order.
YOLO_CLASS_INDEXES = {
    str(category["name"]): index for index, category in enumerate(COCO_CATEGORIES)
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ExportVersionNotFoundError(ValueError):
    """Raised when an export references a missing dataset version."""


class ExportVersionNotFrozenError(ValueError):
    """Raised when an export references a mutable dataset version."""


class ExportMediaError(ValueError):
    """Raised when canonical media cannot be exported safely."""


class InvalidExportArchiveError(ValueError):
    """Raised when a generated artifact fails its import contract."""


@dataclass(frozen=True)
class SnapshotData:
    dataset_id: UUID
    version_id: UUID
    snapshot_at: datetime
    schema_version: str
    content_sha256: str
    images: list[dict[str, Any]]
    annotations: list[dict[str, Any]]
    skus: dict[UUID, dict[str, Any]]


def build_detection_export(
    connection: Connection,
    media_root: Path,
    dataset_version_id: UUID,
) -> bytes:
    snapshot = _load_snapshot(connection, dataset_version_id)
    entries: dict[str, bytes] = {}
    coco_images: list[dict[str, Any]] = []
    image_numbers: dict[UUID, int] = {}

    for image_number, image in enumerate(snapshot.images, start=1):
        image_id = UUID(str(image["id"]))
        image_numbers[image_id] = image_number
        file_name = f"images/{image_id}.{_image_extension(image)}"
        entries[file_name] = _read_canonical_media(media_root, image)
        coco_images.append(
            {
                "id": image_number,
                "file_name": file_name,
                "width": image["canonical_width"],
                "height": image["canonical_height"],
                "shelfsight_image_id": str(image_id),
                "content_sha256": image["content_sha256"],
                "evaluation": _evaluation_metadata(image),
            }
        )

    accepted = [row for row in snapshot.annotations if _is_training_annotation(row)]
    coco_annotations = [
        _coco_annotation(index, row, image_numbers)
        for index, row in enumerate(accepted, start=1)
    ]
    coco = {
        "info": {
            "description": "ShelfSight class-agnostic detection export",
            "schema_version": DETECTION_SCHEMA,
            "dataset_version_id": str(snapshot.version_id),
        },
        "images": coco_images,
        "annotations": coco_annotations,
        "categories": list(COCO_CATEGORIES),
    }
    entries["annotations.coco.json"] = _json_bytes(coco)
    entries["manifest.json"] = _manifest_bytes(
        snapshot,
        "detection",
        DETECTION_SCHEMA,
        entries,
        {
            "images": len(coco_images),
            "annotations": len(coco_annotations),
            "excluded_annotations": len(snapshot.annotations) - len(accepted),
        },
        {
            "annotation_filter": "lifecycle_state=verified and review_state=accepted",
            "category_ids": CATEGORY_IDS,
            "product_category_is_sku_agnostic": True,
        },
    )
    return _zip_bytes(entries)


def build_yolo_export(
    connection: Connection,
    media_root: Path,
    dataset_version_id: UUID,
) -> bytes:
    """The detection export's images and verified boxes in the Ultralytics YOLO layout.

    Images keep their snapshot split as train, val, and test folders. Images without a
    split go to an unsplit folder that data.yaml leaves out, so no split is invented.
    """
    snapshot = _load_snapshot(connection, dataset_version_id)
    accepted = [row for row in snapshot.annotations if _is_training_annotation(row)]
    rows_by_image: dict[UUID, list[Mapping[str, Any]]] = defaultdict(list)
    for row in accepted:
        _validate_geometry(row)
        rows_by_image[UUID(str(row["image_id"]))].append(row)

    entries: dict[str, bytes] = {}
    folders: Counter[str] = Counter()
    for image in snapshot.images:
        image_id = UUID(str(image["id"]))
        folder = YOLO_FOLDERS.get(_evaluation_metadata(image).get("split", ""), "unsplit")
        folders[folder] += 1
        entries[f"images/{folder}/{image_id}.{_image_extension(image)}"] = (
            _read_canonical_media(media_root, image)
        )
        entries[f"labels/{folder}/{image_id}.txt"] = "".join(
            _yolo_line(row, image["canonical_width"], image["canonical_height"])
            for row in rows_by_image[image_id]
        ).encode("utf-8")

    data = {
        "path": ".",
        **{key: f"images/{key}" for key in ("train", "val", "test") if folders[key]},
        "names": {index: name for name, index in YOLO_CLASS_INDEXES.items()},
    }
    entries["data.yaml"] = yaml.safe_dump(data, sort_keys=False).encode("utf-8")
    entries["manifest.json"] = _manifest_bytes(
        snapshot,
        "yolo",
        YOLO_SCHEMA,
        entries,
        {
            "images": len(snapshot.images),
            "annotations": len(accepted),
            "excluded_annotations": len(snapshot.annotations) - len(accepted),
            "images_by_folder": dict(sorted(folders.items())),
        },
        {
            "annotation_filter": "lifecycle_state=verified and review_state=accepted",
            "class_indexes": YOLO_CLASS_INDEXES,
            "product_category_is_sku_agnostic": True,
            "split_source": "capture_metadata split; images without one are unsplit",
        },
    )
    return _zip_bytes(entries)


def _yolo_line(row: Mapping[str, Any], width: int, height: int) -> str:
    x, y = float(row["x"]), float(row["y"])
    box_width, box_height = float(row["width"]), float(row["height"])
    return (
        f"{YOLO_CLASS_INDEXES[str(row['class_type'])]} "
        f"{(x + box_width / 2) / width:.6f} {(y + box_height / 2) / height:.6f} "
        f"{box_width / width:.6f} {box_height / height:.6f}\n"
    )


def build_recognition_export(
    connection: Connection,
    media_root: Path,
    dataset_version_id: UUID,
) -> bytes:
    snapshot = _load_snapshot(connection, dataset_version_id)
    entries: dict[str, bytes] = {}
    samples: list[dict[str, Any]] = []
    product_rows = [
        row
        for row in snapshot.annotations
        if _is_training_annotation(row) and row["class_type"] == "product"
    ]
    image_by_id = {
        UUID(str(image["id"])): image
        for image in snapshot.images
    }
    open_images: dict[UUID, Image.Image] = {}
    try:
        for row in product_rows:
            image_id = UUID(str(row["image_id"]))
            source_image = open_images.get(image_id)
            if source_image is None:
                source_image = _open_canonical_image(
                    media_root,
                    image_by_id[image_id],
                )
                open_images[image_id] = source_image
            crop_bytes, pixel_box = _crop_png(source_image, row)
            crop_path = (
                f"crops/{row['annotation_id']}-r{row['annotation_revision']}.png"
            )
            entries[crop_path] = crop_bytes
            samples.append(
                _recognition_sample(
                    row,
                    image_by_id[image_id],
                    snapshot.skus,
                    crop_path,
                    crop_bytes,
                    pixel_box,
                )
            )
    finally:
        for source_image in open_images.values():
            source_image.close()

    entries["samples.jsonl"] = b"".join(_json_bytes(sample) for sample in samples)
    status_counts: dict[str, int] = {}
    for sample in samples:
        label_status = str(sample["label"]["status"])
        status_counts[label_status] = status_counts.get(label_status, 0) + 1
    entries["manifest.json"] = _manifest_bytes(
        snapshot,
        "recognition",
        RECOGNITION_SCHEMA,
        entries,
        {
            "samples": len(samples),
            "trainable_samples": sum(bool(sample["trainable"]) for sample in samples),
            "label_statuses": status_counts,
            "excluded_non_product_or_unreviewed": (
                len(snapshot.annotations) - len(product_rows)
            ),
        },
        {
            "annotation_filter": (
                "class_type=product, lifecycle_state=verified, "
                "and review_state=accepted"
            ),
            "crop_rounding": "floor left/top and ceil exclusive right/bottom",
            "unknown": "included with trainable=false",
            "deprecated": "included with trainable=false",
            "merged": "included with training_sku_id resolved to final target",
            "unassigned": "included with trainable=false",
        },
    )
    return _zip_bytes(entries)


def validate_export_archive(
    archive: bytes,
    expected_type: Literal["detection", "recognition"],
) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(archive), "r") as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise InvalidExportArchiveError("archive has missing or duplicate files")
            for name in names:
                _validate_archive_path(name)
            manifest = _json_object(bundle.read("manifest.json"))
            if (
                manifest.get("schema_version") != MANIFEST_SCHEMA
                or manifest.get("export_type") != expected_type
            ):
                raise InvalidExportArchiveError("archive manifest is incompatible")
            declared = manifest.get("files")
            if not isinstance(declared, list):
                raise InvalidExportArchiveError("archive file manifest is invalid")
            declared_names: set[str] = set()
            for item in declared:
                record = _require_record(item, "manifest file")
                path = record.get("path")
                checksum = record.get("sha256")
                size = record.get("size")
                if (
                    not isinstance(path, str)
                    or not isinstance(checksum, str)
                    or not isinstance(size, int)
                ):
                    raise InvalidExportArchiveError("archive checksum record is invalid")
                content = bundle.read(path)
                if len(content) != size or _sha256(content) != checksum:
                    raise InvalidExportArchiveError("archive checksum does not match")
                declared_names.add(path)
            if declared_names != set(names) - {"manifest.json"}:
                raise InvalidExportArchiveError("archive files do not match manifest")
            return manifest
    except (BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise InvalidExportArchiveError("archive cannot be imported") from error


def read_detection_export(archive: bytes) -> dict[str, Any]:
    validate_export_archive(archive, "detection")
    with ZipFile(BytesIO(archive), "r") as bundle:
        coco = _json_object(bundle.read("annotations.coco.json"))
    if not all(isinstance(coco.get(key), list) for key in ("images", "annotations", "categories")):
        raise InvalidExportArchiveError("COCO document is incomplete")
    return coco


def read_recognition_export(archive: bytes) -> list[dict[str, Any]]:
    validate_export_archive(archive, "recognition")
    with ZipFile(BytesIO(archive), "r") as bundle:
        content = bundle.read("samples.jsonl")
    samples: list[dict[str, Any]] = []
    for line in content.splitlines():
        if not line:
            continue
        samples.append(_json_object(line))
    return samples


def _load_snapshot(
    connection: Connection,
    dataset_version_id: UUID,
) -> SnapshotData:
    version = connection.execute(
        select(
            dataset_snapshots.c.dataset_id,
            dataset_snapshots.c.snapshot_at,
            dataset_snapshots.c.schema_version,
            dataset_snapshots.c.content_sha256,
        ).where(dataset_snapshots.c.dataset_version_id == dataset_version_id)
    ).one_or_none()
    if version is None:
        raise ExportVersionNotFrozenError("dataset version must be snapshotted")

    image_rows = connection.execute(
        select(
            dataset_snapshot_images.c.image_id.label("id"),
            dataset_snapshot_images.c.canonical_media_key,
            dataset_snapshot_images.c.media_type,
            dataset_snapshot_images.c.original_filename,
            dataset_snapshot_images.c.content_sha256,
            dataset_snapshot_images.c.canonical_width,
            dataset_snapshot_images.c.canonical_height,
            dataset_snapshot_images.c.capture_metadata,
        )
        .where(dataset_snapshot_images.c.dataset_version_id == dataset_version_id)
        .order_by(dataset_snapshot_images.c.image_id)
    ).mappings()
    annotation_rows = connection.execute(
        select(dataset_snapshot_annotations)
        .where(dataset_snapshot_annotations.c.dataset_version_id == dataset_version_id)
        .order_by(dataset_snapshot_annotations.c.annotation_id)
    ).mappings()
    sku_rows = connection.execute(
        select(dataset_snapshot_skus)
        .where(dataset_snapshot_skus.c.dataset_version_id == dataset_version_id)
        .order_by(dataset_snapshot_skus.c.sku_id)
    ).mappings()
    return SnapshotData(
        dataset_id=UUID(str(version.dataset_id)),
        version_id=dataset_version_id,
        snapshot_at=version.snapshot_at,
        schema_version=str(version.schema_version),
        content_sha256=str(version.content_sha256),
        images=[dict(row) for row in image_rows],
        annotations=[dict(row) for row in annotation_rows],
        skus={
            UUID(str(row["sku_id"])): {**dict(row), "id": row["sku_id"]}
            for row in sku_rows
        },
    )


def _coco_annotation(
    export_id: int,
    row: Mapping[str, Any],
    image_numbers: Mapping[UUID, int],
) -> dict[str, Any]:
    _validate_geometry(row)
    class_type = str(row["class_type"])
    bbox = [row["x"], row["y"], row["width"], row["height"]]
    return {
        "id": export_id,
        "image_id": image_numbers[UUID(str(row["image_id"]))],
        "category_id": CATEGORY_IDS[class_type],
        "bbox": bbox,
        "area": row["width"] * row["height"],
        "iscrowd": 0,
        "shelfsight_annotation_id": str(row["annotation_id"]),
        "shelfsight_annotation_revision": row["annotation_revision"],
        "attributes": {
            "occluded": row["occluded"],
            "truncated": row["truncated"],
            "shelf_row": row["shelf_row"],
            "source": row["source"],
        },
    }


def _evaluation_metadata(image: Mapping[str, Any]) -> dict[str, str]:
    capture_metadata = image.get("capture_metadata")
    if not isinstance(capture_metadata, dict):
        return {}
    provided = capture_metadata.get("provided", capture_metadata)
    if not isinstance(provided, dict):
        return {}
    keys = (
        "split",
        "near_duplicate_group",
        "capture_session_id",
        "store_id",
        "fixture_id",
    )
    return {
        key: value.strip()
        for key in keys
        if isinstance((value := provided.get(key)), str) and value.strip()
    }


def _recognition_sample(
    row: Mapping[str, Any],
    image: Mapping[str, Any],
    catalog: Mapping[UUID, dict[str, Any]],
    crop_path: str,
    crop_bytes: bytes,
    pixel_box: tuple[int, int, int, int],
) -> dict[str, Any]:
    source_sku_id = (
        UUID(str(row["sku_id"])) if row["sku_id"] is not None else None
    )
    label = _recognition_label(source_sku_id, catalog)
    return {
        "schema_version": RECOGNITION_SCHEMA,
        "sample_id": f"{row['annotation_id']}:r{row['annotation_revision']}",
        "crop_file": crop_path,
        "crop_sha256": _sha256(crop_bytes),
        "annotation_id": str(row["annotation_id"]),
        "annotation_revision": row["annotation_revision"],
        "image_id": str(row["image_id"]),
        "image_content_sha256": image["content_sha256"],
        "bbox": {
            "x": row["x"],
            "y": row["y"],
            "width": row["width"],
            "height": row["height"],
        },
        "pixel_crop": {
            "left": pixel_box[0],
            "top": pixel_box[1],
            "right_exclusive": pixel_box[2],
            "bottom_exclusive": pixel_box[3],
        },
        "source": row["source"],
        "provenance": row["provenance"],
        "attributes": {
            "confidence": row["confidence"],
            "occluded": row["occluded"],
            "truncated": row["truncated"],
            "shelf_row": row["shelf_row"],
        },
        "label": label,
        "trainable": label["trainable"],
    }


def _recognition_label(
    source_sku_id: UUID | None,
    catalog: Mapping[UUID, dict[str, Any]],
) -> dict[str, Any]:
    if source_sku_id is None:
        return {
            "status": "unassigned",
            "source_sku_id": None,
            "training_sku_id": None,
            "name": None,
            "trainable": False,
        }
    source = catalog[source_sku_id]
    if source["is_unknown"]:
        return {
            "status": "unknown",
            "source_sku_id": str(source_sku_id),
            "training_sku_id": None,
            "name": source["name"],
            "trainable": False,
        }
    if source["status"] == "deprecated":
        return {
            "status": "deprecated",
            "source_sku_id": str(source_sku_id),
            "training_sku_id": None,
            "name": source["name"],
            "trainable": False,
        }
    if source["status"] == "merged":
        target = _final_merge_target(source, catalog)
        is_trainable = target["status"] == "active" and not target["is_unknown"]
        return {
            "status": "merged" if is_trainable else "merged_to_untrainable",
            "source_sku_id": str(source_sku_id),
            "training_sku_id": str(target["id"]) if is_trainable else None,
            "name": source["name"],
            "training_name": target["name"],
            "trainable": is_trainable,
        }
    return {
        "status": "active",
        "source_sku_id": str(source_sku_id),
        "training_sku_id": str(source_sku_id),
        "name": source["name"],
        "trainable": True,
    }


def _final_merge_target(
    source: Mapping[str, Any],
    catalog: Mapping[UUID, dict[str, Any]],
) -> dict[str, Any]:
    current = source
    visited: set[UUID] = set()
    while current["status"] == "merged":
        current_id = UUID(str(current["id"]))
        if current_id in visited or current["merged_into_id"] is None:
            raise InvalidExportArchiveError("SKU merge chain is invalid")
        visited.add(current_id)
        current = catalog[UUID(str(current["merged_into_id"]))]
    return dict(current)


def _crop_png(
    source_image: Image.Image,
    row: Mapping[str, Any],
) -> tuple[bytes, tuple[int, int, int, int]]:
    _validate_geometry(row)
    left, top, right, bottom = pixel_crop_bounds(
        float(row["x"]),
        float(row["y"]),
        float(row["width"]),
        float(row["height"]),
    )
    if left < 0 or top < 0 or right > source_image.width or bottom > source_image.height:
        raise ExportMediaError("annotation crop is outside canonical image bounds")
    crop = source_image.crop((left, top, right, bottom))
    output = BytesIO()
    crop.save(output, format="PNG", compress_level=6, optimize=False)
    crop.close()
    return output.getvalue(), (left, top, right, bottom)


def _open_canonical_image(
    media_root: Path,
    image: Mapping[str, Any],
) -> Image.Image:
    content = _read_canonical_media(media_root, image)
    try:
        opened = Image.open(BytesIO(content))
        opened.load()
    except (OSError, UnidentifiedImageError) as error:
        raise ExportMediaError("canonical image cannot be decoded") from error
    if opened.size != (
        int(image["canonical_width"]),
        int(image["canonical_height"]),
    ):
        opened.close()
        raise ExportMediaError("canonical image dimensions do not match metadata")
    return opened


def _read_canonical_media(
    media_root: Path,
    image: Mapping[str, Any],
) -> bytes:
    path = resolve_media_path(media_root, str(image["canonical_media_key"]))
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ExportMediaError("canonical image is missing") from error
    if not content:
        raise ExportMediaError("canonical image is empty")
    return content


def _manifest_bytes(
    snapshot: SnapshotData,
    export_type: Literal["detection", "recognition", "yolo"],
    artifact_schema: str,
    entries: Mapping[str, bytes],
    counts: Mapping[str, Any],
    policies: Mapping[str, Any],
) -> bytes:
    files = [
        {
            "path": path,
            "sha256": _sha256(content),
            "size": len(content),
        }
        for path, content in sorted(entries.items())
    ]
    return _json_bytes(
        {
            "schema_version": MANIFEST_SCHEMA,
            "artifact_schema_version": artifact_schema,
            "export_type": export_type,
            "dataset_id": str(snapshot.dataset_id),
            "dataset_version_id": str(snapshot.version_id),
            "snapshot_at": snapshot.snapshot_at.isoformat(),
            "snapshot_schema_version": snapshot.schema_version,
            "snapshot_content_sha256": snapshot.content_sha256,
            "files": files,
            "counts": dict(counts),
            "policies": dict(policies),
        }
    )


def _zip_bytes(entries: Mapping[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as bundle:
        for path, content in sorted(entries.items()):
            info = ZipInfo(path, ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, content, compresslevel=9)
    return output.getvalue()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _json_object(content: bytes) -> dict[str, Any]:
    value = json.loads(content)
    return _require_record(value, "JSON document")


def _require_record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidExportArchiveError(f"{label} must be an object")
    return value


def _validate_archive_path(path: str) -> None:
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise InvalidExportArchiveError("archive contains an unsafe path")


def _validate_geometry(row: Mapping[str, Any]) -> None:
    values = (row["x"], row["y"], row["width"], row["height"])
    if (
        not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
        or float(row["x"]) < 0
        or float(row["y"]) < 0
        or float(row["width"]) <= 0
        or float(row["height"]) <= 0
    ):
        raise InvalidExportArchiveError("annotation geometry is invalid")
    if row["class_type"] not in CATEGORY_IDS:
        raise InvalidExportArchiveError("annotation class is invalid")


def _is_training_annotation(row: Mapping[str, Any]) -> bool:
    return bool(
        row["lifecycle_state"] == "verified"
        and row["review_state"] == "accepted"
    )


def _image_extension(image: Mapping[str, Any]) -> str:
    media_type = image["media_type"]
    if media_type == "image/jpeg":
        return "jpg"
    if media_type == "image/png":
        return "png"
    raise ExportMediaError("canonical image format is unsupported")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
