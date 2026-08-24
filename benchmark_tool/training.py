from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from shelfsight_api.export_service import read_detection_export, validate_export_archive

TRAINING_SCHEMA = "cvsight-rfdetr-training/v1"
RUN_SCHEMA = "cvsight-rfdetr-run/v1"
SPLIT_DIRECTORIES = {
    "train": "train",
    "validation": "valid",
    "test": "test",
}
BOUNDARY_KEYS = (
    "near_duplicate_group",
    "capture_session_id",
    "store_id",
    "fixture_id",
)


class TrainingInputError(ValueError):
    """Raised when immutable training input violates the pipeline contract."""


def prepare_training_dataset(
    archive_path: Path,
    output_dir: Path,
    checkpoint_path: Path,
    license_approvals_path: Path,
    *,
    code_version: str,
    seed: int = 1337,
    epochs: int = 5,
) -> dict[str, Any]:
    _validate_run_options(output_dir, checkpoint_path, code_version, seed, epochs)
    archive = archive_path.read_bytes()
    export_manifest = validate_export_archive(archive, "detection")
    _validate_snapshot_lineage(export_manifest)
    licenses = _load_license_approvals(license_approvals_path)
    coco = read_detection_export(archive)
    images = _training_images(coco)
    _validate_boundaries(images)
    annotations = _product_annotations(coco, images)
    split_counts = Counter(str(image["evaluation"]["split"]) for image in images)
    missing_splits = set(SPLIT_DIRECTORIES).difference(split_counts)
    if missing_splits:
        raise TrainingInputError(
            f"training export is missing required splits: {sorted(missing_splits)}"
        )

    output_dir.mkdir(parents=True)
    try:
        _write_split_datasets(archive, output_dir, images, annotations)
        checkpoint_sha256 = _sha256_file(checkpoint_path)
        manifest = {
            "schema_version": TRAINING_SCHEMA,
            "dataset_id": export_manifest["dataset_id"],
            "dataset_version_id": export_manifest["dataset_version_id"],
            "snapshot_at": export_manifest["snapshot_at"],
            "snapshot_schema_version": export_manifest["snapshot_schema_version"],
            "snapshot_content_sha256": export_manifest["snapshot_content_sha256"],
            "source_export_sha256": _sha256_bytes(archive),
            "code_version": code_version.strip(),
            "random_seed": seed,
            "model": "rf-detr-nano",
            "starting_checkpoint": {
                **licenses["starting_checkpoint"],
                "file_name": checkpoint_path.name,
                "sha256": checkpoint_sha256,
            },
            "dataset_licenses": licenses["datasets"],
            "training_config": _training_config(seed, epochs),
            "inference_strategy": {
                "default": "full_image",
                "dense_retry": "sliced_2x2",
                "confidence_threshold": 0.3,
                "slice_overlap": 0.1,
                "nms_iou": 0.5,
                "reason": "T004 measured slicing as useful only for dense high-recall retry",
            },
            "label_policy": {
                "class": "product",
                "filter": "verified and accepted in immutable source export",
                "teacher_labels_remain_distinguishable": True,
            },
            "counts": {
                "images_by_split": dict(sorted(split_counts.items())),
                "product_annotations_by_origin": dict(
                    sorted(Counter(_label_origin(row) for row in annotations).items())
                ),
            },
        }
        manifest["files"] = _directory_files(output_dir)
        _write_json(output_dir / "training-manifest.json", manifest)
        return manifest
    except Exception:
        _remove_created_tree(output_dir)
        raise


def run_rfdetr_training(
    dataset_dir: Path,
    checkpoint_path: Path,
    output_dir: Path,
    *,
    model_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    manifest = _read_json_object(dataset_dir / "training-manifest.json")
    if manifest.get("schema_version") != TRAINING_SCHEMA:
        raise TrainingInputError("training manifest is incompatible")
    expected_checkpoint = manifest.get("starting_checkpoint")
    if not isinstance(expected_checkpoint, dict):
        raise TrainingInputError("training manifest checkpoint is invalid")
    if _sha256_file(checkpoint_path) != expected_checkpoint.get("sha256"):
        raise TrainingInputError("starting checkpoint checksum does not match the manifest")
    if output_dir.exists():
        raise TrainingInputError("training output directory already exists")

    config = manifest.get("training_config")
    if not isinstance(config, dict):
        raise TrainingInputError("training configuration is invalid")
    output_dir.mkdir(parents=True)
    try:
        factory = model_factory or _rfdetr_factory()
        model = factory(str(checkpoint_path))
        model.train(
            dataset_dir=str(dataset_dir),
            output_dir=str(output_dir),
            epochs=config["epochs"],
            batch_size=config["batch_size"],
            grad_accum_steps=config["grad_accum_steps"],
            lr=config["learning_rate"],
            resolution=config["resolution"],
            amp_dtype=config["amp_dtype"],
            num_workers=config["num_workers"],
            multi_scale=False,
            expanded_scales=False,
            checkpoint_interval=1,
            eval_interval=1,
            use_ema=True,
            tensorboard=False,
            progress_bar="tqdm",
            run_test=False,
            seed=config["seed"],
            notes={
                "dataset_version_id": manifest["dataset_version_id"],
                "snapshot_content_sha256": manifest["snapshot_content_sha256"],
                "code_version": manifest["code_version"],
            },
        )
        artifacts = _directory_files(output_dir)
        if not any(item["path"].endswith(".pth") for item in artifacts):
            raise TrainingInputError("RF-DETR did not produce a model checkpoint")
        if not any(item["path"] == "metrics.csv" for item in artifacts):
            raise TrainingInputError("RF-DETR did not produce metrics.csv")
        run_manifest = {
            "schema_version": RUN_SCHEMA,
            "dataset_version_id": manifest["dataset_version_id"],
            "snapshot_content_sha256": manifest["snapshot_content_sha256"],
            "training_manifest_sha256": _sha256_file(
                dataset_dir / "training-manifest.json"
            ),
            "code_version": manifest["code_version"],
            "random_seed": config["seed"],
            "starting_checkpoint": expected_checkpoint,
            "training_config": config,
            "artifacts": artifacts,
        }
        _write_json(output_dir / "run-manifest.json", run_manifest)
        return run_manifest
    except Exception:
        _remove_created_tree(output_dir)
        raise


def _training_images(coco: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_images = coco.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise TrainingInputError("detection export contains no images")
    images = []
    for raw_image in raw_images:
        if not isinstance(raw_image, dict):
            raise TrainingInputError("detection export image is invalid")
        evaluation = raw_image.get("evaluation")
        split = evaluation.get("split") if isinstance(evaluation, dict) else None
        if split not in SPLIT_DIRECTORIES:
            raise TrainingInputError("every image requires a train, validation, or test split")
        file_name = raw_image.get("file_name")
        if not isinstance(file_name, str) or PurePosixPath(file_name).parent.as_posix() != "images":
            raise TrainingInputError("detection export image path is invalid")
        images.append(raw_image)
    return images


def _product_annotations(
    coco: Mapping[str, Any], images: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    product_ids = {
        row["id"]
        for row in coco.get("categories", [])
        if isinstance(row, dict) and row.get("name") == "product"
    }
    if len(product_ids) != 1:
        raise TrainingInputError("detection export requires one product category")
    image_ids = {image["id"] for image in images}
    raw_annotations = coco.get("annotations")
    if not isinstance(raw_annotations, list):
        raise TrainingInputError("detection export annotations are invalid")
    annotations = [
        row
        for row in raw_annotations
        if isinstance(row, dict) and row.get("category_id") in product_ids
    ]
    if any(row.get("image_id") not in image_ids for row in annotations):
        raise TrainingInputError("annotation references an unknown image")
    return annotations


def _validate_boundaries(images: list[dict[str, Any]]) -> None:
    boundaries: dict[tuple[str, str], set[str]] = {}
    for image in images:
        split = str(image["evaluation"]["split"])
        content_sha256 = image.get("content_sha256")
        if not isinstance(content_sha256, str) or len(content_sha256) != 64:
            raise TrainingInputError("image content checksum is invalid")
        boundaries.setdefault(("content_sha256", content_sha256), set()).add(split)
        evaluation = image["evaluation"]
        for key in BOUNDARY_KEYS:
            value = evaluation.get(key)
            if isinstance(value, str) and value.strip():
                boundaries.setdefault((key, value.strip()), set()).add(split)
    crossing = next((key for key, splits in boundaries.items() if len(splits) > 1), None)
    if crossing is not None:
        raise TrainingInputError(f"{crossing[0]} crosses evaluation splits")


def _write_split_datasets(
    archive: bytes,
    output_dir: Path,
    images: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> None:
    with ZipFile(BytesIO(archive)) as bundle:
        for split, split_directory in SPLIT_DIRECTORIES.items():
            target = output_dir / split_directory
            target.mkdir()
            split_images = [image for image in images if image["evaluation"]["split"] == split]
            split_image_ids = {image["id"] for image in split_images}
            exported_images = []
            for image in split_images:
                source_name = str(image["file_name"])
                destination_name = PurePosixPath(source_name).name
                (target / destination_name).write_bytes(bundle.read(source_name))
                exported_images.append({**image, "file_name": destination_name})
            split_annotations = [
                _training_annotation(row)
                for row in annotations
                if row["image_id"] in split_image_ids
            ]
            _write_json(
                target / "_annotations.coco.json",
                {
                    "info": {
                        "description": "CVSight class-agnostic product detector training",
                        "schema_version": TRAINING_SCHEMA,
                        "split": split,
                    },
                    "images": exported_images,
                    "annotations": split_annotations,
                    "categories": [
                        {"id": 1, "name": "product", "supercategory": "shelf"}
                    ],
                },
            )


def _training_annotation(annotation: dict[str, Any]) -> dict[str, Any]:
    attributes = annotation.get("attributes")
    source = attributes.get("source") if isinstance(attributes, dict) else None
    return {
        **annotation,
        "category_id": 1,
        "attributes": {
            **(attributes if isinstance(attributes, dict) else {}),
            "source": source,
            "label_origin": _label_origin(annotation),
        },
    }


def _label_origin(annotation: Mapping[str, Any]) -> str:
    attributes = annotation.get("attributes")
    source = attributes.get("source") if isinstance(attributes, dict) else None
    return {
        "human": "human_ground_truth",
        "model": "teacher_generated",
        "propagated": "propagated_label",
        "imported": "imported_ground_truth",
    }.get(str(source), "unknown_origin")


def _training_config(seed: int, epochs: int) -> dict[str, Any]:
    return {
        "epochs": epochs,
        "batch_size": 2,
        "grad_accum_steps": 8,
        "learning_rate": 0.0001,
        "resolution": 384,
        "amp_dtype": "bf16",
        "num_workers": 2,
        "seed": seed,
    }


def _load_license_approvals(path: Path) -> dict[str, Any]:
    value = _read_json_object(path)
    datasets = value.get("datasets")
    checkpoint = value.get("starting_checkpoint")
    if not isinstance(datasets, list) or not datasets or not isinstance(checkpoint, dict):
        raise TrainingInputError("license approvals require datasets and a starting checkpoint")
    records = [*datasets, checkpoint]
    for record in records:
        if not isinstance(record, dict) or record.get("approved") is not True:
            raise TrainingInputError("every dataset and checkpoint license must be approved")
        for key in ("name", "license", "source"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise TrainingInputError(f"license approval requires {key}")
    return {"datasets": datasets, "starting_checkpoint": checkpoint}


def _validate_snapshot_lineage(manifest: Mapping[str, Any]) -> None:
    required = (
        "dataset_id",
        "dataset_version_id",
        "snapshot_at",
        "snapshot_schema_version",
        "snapshot_content_sha256",
    )
    if any(not isinstance(manifest.get(key), str) or not manifest[key] for key in required):
        raise TrainingInputError("detection export lacks immutable snapshot lineage")
    checksum = str(manifest["snapshot_content_sha256"])
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise TrainingInputError("snapshot content checksum is invalid")


def _validate_run_options(
    output_dir: Path,
    checkpoint_path: Path,
    code_version: str,
    seed: int,
    epochs: int,
) -> None:
    if output_dir.exists():
        raise TrainingInputError("training dataset output directory already exists")
    if not checkpoint_path.is_file():
        raise TrainingInputError("starting checkpoint does not exist")
    if not code_version.strip():
        raise TrainingInputError("code version is required")
    if seed < 0:
        raise TrainingInputError("random seed must be non-negative")
    if epochs <= 0:
        raise TrainingInputError("epochs must be positive")


def _rfdetr_factory() -> Callable[[str], Any]:
    model_type = importlib.import_module("rfdetr").RFDETRNano
    return lambda checkpoint: model_type(pretrain_weights=checkpoint)


def _directory_files(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrainingInputError(f"cannot read {path.name}") from error
    if not isinstance(value, dict):
        raise TrainingInputError(f"{path.name} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _remove_created_tree(root: Path) -> None:
    if not root.exists():
        return
    shutil.rmtree(root)
