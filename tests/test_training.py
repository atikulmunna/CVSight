from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from benchmark_tool.training import (
    RUN_SCHEMA,
    TRAINING_SCHEMA,
    TrainingInputError,
    prepare_training_dataset,
    run_rfdetr_training,
)
from benchmark_tool.training_evaluation import (
    EVALUATION_SCHEMA,
    PREDICTION_SCHEMA,
    evaluate_training_predictions,
    generate_test_predictions,
)


def test_prepares_reproducible_leakage_safe_class_agnostic_dataset(
    tmp_path: Path,
) -> None:
    archive_path = _write_export(tmp_path / "snapshot.zip")
    checkpoint = tmp_path / "rf-detr-nano.pth"
    checkpoint.write_bytes(b"approved checkpoint")
    licenses = _write_licenses(tmp_path / "licenses.json")

    first = prepare_training_dataset(
        archive_path,
        tmp_path / "prepared-first",
        checkpoint,
        licenses,
        code_version="commit-123",
        seed=41,
        epochs=2,
    )
    second = prepare_training_dataset(
        archive_path,
        tmp_path / "prepared-second",
        checkpoint,
        licenses,
        code_version="commit-123",
        seed=41,
        epochs=2,
    )

    assert first == second
    assert _tree_hashes(tmp_path / "prepared-first") == _tree_hashes(
        tmp_path / "prepared-second"
    )
    assert first["schema_version"] == TRAINING_SCHEMA
    assert first["counts"] == {
        "images_by_split": {"test": 1, "train": 1, "validation": 1},
        "product_annotations_by_origin": {
            "human_ground_truth": 1,
            "propagated_label": 1,
            "teacher_generated": 1,
        },
    }
    assert first["starting_checkpoint"]["license"] == "Apache-2.0"
    assert first["training_config"]["seed"] == 41
    assert first["inference_strategy"]["default"] == "full_image"
    assert first["inference_strategy"]["dense_retry"] == "sliced_2x2"

    train_coco = _read_json(
        tmp_path / "prepared-first" / "train" / "_annotations.coco.json"
    )
    test_coco = _read_json(
        tmp_path / "prepared-first" / "test" / "_annotations.coco.json"
    )
    assert [row["id"] for row in train_coco["images"]] == [1]
    assert [row["id"] for row in test_coco["images"]] == [3]
    assert {row["category_id"] for row in train_coco["annotations"]} == {1}
    assert train_coco["annotations"][0]["attributes"]["label_origin"] == (
        "human_ground_truth"
    )
    assert test_coco["annotations"][0]["attributes"]["label_origin"] == (
        "propagated_label"
    )


def test_preparation_rejects_split_leakage_and_unapproved_licenses(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "rf-detr-nano.pth"
    checkpoint.write_bytes(b"checkpoint")
    licenses = _write_licenses(tmp_path / "licenses.json")

    with pytest.raises(TrainingInputError, match="capture_session_id crosses"):
        prepare_training_dataset(
            _write_export(tmp_path / "leaking.zip", crossing_group=True),
            tmp_path / "leaking-output",
            checkpoint,
            licenses,
            code_version="commit-123",
        )
    assert not (tmp_path / "leaking-output").exists()

    value = _read_json(licenses)
    value["datasets"][0]["approved"] = False
    licenses.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrainingInputError, match="must be approved"):
        prepare_training_dataset(
            _write_export(tmp_path / "valid.zip"),
            tmp_path / "unapproved-output",
            checkpoint,
            licenses,
            code_version="commit-123",
        )


def test_training_uses_manifest_configuration_and_checksums_artifacts(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "rf-detr-nano.pth"
    checkpoint.write_bytes(b"checkpoint")
    prepared = tmp_path / "prepared"
    prepare_training_dataset(
        _write_export(tmp_path / "snapshot.zip"),
        prepared,
        checkpoint,
        _write_licenses(tmp_path / "licenses.json"),
        code_version="commit-456",
        seed=7,
        epochs=3,
    )
    calls: list[dict[str, Any]] = []

    class FakeModel:
        def train(self, **kwargs: Any) -> None:
            calls.append(kwargs)
            output = Path(kwargs["output_dir"])
            (output / "checkpoint_best_total.pth").write_bytes(b"trained")
            (output / "metrics.csv").write_text(
                "epoch,val/mAP_50,val/mAP_50_95,val/recall\n2,0.7,0.5,0.8\n",
                encoding="utf-8",
            )

    run = run_rfdetr_training(
        prepared,
        checkpoint,
        tmp_path / "run",
        model_factory=lambda _checkpoint: FakeModel(),
    )

    assert run["schema_version"] == RUN_SCHEMA
    assert run["code_version"] == "commit-456"
    assert run["random_seed"] == 7
    assert {artifact["path"] for artifact in run["artifacts"]} == {
        "checkpoint_best_total.pth",
        "metrics.csv",
    }
    assert calls[0]["epochs"] == 3
    assert calls[0]["seed"] == 7
    assert calls[0]["resolution"] == 384
    assert calls[0]["run_test"] is False
    assert calls[0]["notes"]["dataset_version_id"] == "version-1"

    changed_checkpoint = tmp_path / "changed.pth"
    changed_checkpoint.write_bytes(b"different")
    with pytest.raises(TrainingInputError, match="checksum"):
        run_rfdetr_training(
            prepared,
            changed_checkpoint,
            tmp_path / "invalid-run",
            model_factory=lambda _checkpoint: FakeModel(),
        )


def test_evaluation_reports_product_map_duplicates_and_dense_failures(
    tmp_path: Path,
) -> None:
    dataset_dir = _write_evaluation_dataset(tmp_path / "dataset")
    predictions_path = tmp_path / "predictions.json"
    exact_predictions = [
        {"image_id": 30, "bbox": [float(index), 0.0, 0.8, 1.0], "score": 1 - index / 100}
        for index in range(50)
    ]
    predictions_path.write_text(
        json.dumps(
            {
                "schema_version": PREDICTION_SCHEMA,
                "dataset_version_id": "version-1",
                "model_artifact_sha256": "f" * 64,
                "modes": {
                    "full_image": {
                        "confidence_threshold": 0.3,
                        "predictions": exact_predictions,
                    },
                    "sliced_2x2": {
                        "confidence_threshold": 0.3,
                        "predictions": [
                            *exact_predictions[:-1],
                            {**exact_predictions[0], "score": 0.01},
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_training_predictions(dataset_dir, predictions_path)

    assert report["schema_version"] == EVALUATION_SCHEMA
    assert report["modes"]["full_image"]["product_recall_at_iou_50"] == 1
    assert report["modes"]["full_image"]["map_50_95"] == 1
    sliced = report["modes"]["sliced_2x2"]
    assert sliced["product_recall_at_iou_50"] == 49 / 50
    assert sliced["duplicate_proposals_at_iou_50"] == 1
    assert sliced["dense_scenes"]["failures"] == [
        {"image_id": 30, "ground_truth": 50, "matched": 49, "missed": 1}
    ]

    value = _read_json(predictions_path)
    value["modes"]["full_image"]["predictions"][0]["image_id"] = 10
    predictions_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrainingInputError, match="non-test image"):
        evaluate_training_predictions(dataset_dir, predictions_path)


def test_prediction_run_uses_fixed_full_and_sliced_test_strategy(tmp_path: Path) -> None:
    checkpoint = tmp_path / "rf-detr-nano.pth"
    checkpoint.write_bytes(b"checkpoint")
    prepared = tmp_path / "prepared"
    prepare_training_dataset(
        _write_export(tmp_path / "snapshot.zip"),
        prepared,
        checkpoint,
        _write_licenses(tmp_path / "licenses.json"),
        code_version="commit-789",
    )
    predict_calls = []

    class FakeModel:
        def predict(self, image: Any, **kwargs: Any) -> Any:
            predict_calls.append((image, kwargs))
            return SimpleNamespace(
                xyxy=[[1.0, 2.0, 11.0, 14.0]],
                confidence=[0.9],
                data={"class_name": ["product"]},
            )

    output = tmp_path / "predictions.json"
    document = generate_test_predictions(
        prepared,
        checkpoint,
        output,
        model_factory=lambda _checkpoint: FakeModel(),
    )

    assert document["schema_version"] == PREDICTION_SCHEMA
    assert document["model_artifact_sha256"] == _sha256(checkpoint.read_bytes())
    assert document["modes"]["full_image"]["confidence_threshold"] == 0.3
    assert len(document["modes"]["full_image"]["predictions"]) == 1
    assert len(document["modes"]["sliced_2x2"]["predictions"]) == 4
    assert len(predict_calls) == 5
    assert all(call[1]["threshold"] == 0.3 for call in predict_calls)


def _write_export(path: Path, *, crossing_group: bool = False) -> Path:
    image_rows = [
        _image(1, "train", "a", "train-session"),
        _image(2, "validation", "b", "validation-session"),
        _image(3, "test", "c", "train-session" if crossing_group else "test-session"),
    ]
    coco = {
        "info": {
            "schema_version": "shelfsight-coco-detection/v1",
            "dataset_version_id": "version-1",
        },
        "images": image_rows,
        "annotations": [
            _annotation(1, 1, 1, "human"),
            _annotation(2, 2, 1, "model"),
            _annotation(3, 3, 1, "propagated"),
            _annotation(4, 1, 2, "human"),
        ],
        "categories": [
            {"id": 1, "name": "product", "supercategory": "shelf"},
            {"id": 2, "name": "gap", "supercategory": "shelf"},
        ],
    }
    entries = {
        "annotations.coco.json": _json_bytes(coco),
        "images/1.jpg": _jpeg_bytes("#264653"),
        "images/2.jpg": _jpeg_bytes("#2a9d8f"),
        "images/3.jpg": _jpeg_bytes("#e9c46a"),
    }
    manifest = {
        "schema_version": "shelfsight-export-manifest/v1",
        "artifact_schema_version": "shelfsight-coco-detection/v1",
        "export_type": "detection",
        "dataset_id": "dataset-1",
        "dataset_version_id": "version-1",
        "snapshot_at": "2026-08-09T12:00:00+00:00",
        "snapshot_schema_version": "shelfsight-dataset-snapshot/v2",
        "snapshot_content_sha256": "d" * 64,
        "files": [
            {"path": name, "sha256": _sha256(content), "size": len(content)}
            for name, content in sorted(entries.items())
        ],
        "counts": {"images": 3, "annotations": 4},
        "policies": {},
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as bundle:
        for name, content in sorted({**entries, "manifest.json": _json_bytes(manifest)}.items()):
            bundle.writestr(name, content)
    path.write_bytes(output.getvalue())
    return path


def _image(
    image_id: int,
    split: str,
    checksum_character: str,
    capture_session_id: str,
) -> dict[str, Any]:
    return {
        "id": image_id,
        "file_name": f"images/{image_id}.jpg",
        "width": 100,
        "height": 80,
        "shelfsight_image_id": f"image-{image_id}",
        "content_sha256": checksum_character * 64,
        "evaluation": {
            "split": split,
            "capture_session_id": capture_session_id,
        },
    }


def _annotation(
    annotation_id: int,
    image_id: int,
    category_id: int,
    source: str,
) -> dict[str, Any]:
    return {
        "id": annotation_id,
        "image_id": image_id,
        "category_id": category_id,
        "bbox": [1.0, 2.0, 10.0, 12.0],
        "area": 120.0,
        "iscrowd": 0,
        "attributes": {"source": source},
    }


def _write_licenses(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "name": "Reviewed Shelf Dataset",
                        "license": "CC BY 4.0",
                        "source": "https://example.test/dataset",
                        "approved": True,
                    }
                ],
                "starting_checkpoint": {
                    "name": "RF-DETR Nano",
                    "license": "Apache-2.0",
                    "source": "https://github.com/roboflow/rf-detr",
                    "approved": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_evaluation_dataset(root: Path) -> Path:
    (root / "test").mkdir(parents=True)
    (root / "training-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": TRAINING_SCHEMA,
                "dataset_version_id": "version-1",
                "snapshot_content_sha256": "d" * 64,
                "source_export_sha256": "e" * 64,
                "code_version": "commit-123",
                "random_seed": 41,
            }
        ),
        encoding="utf-8",
    )
    annotations = [
        {
            "id": index + 1,
            "image_id": 30,
            "category_id": 1,
            "bbox": [float(index), 0.0, 0.8, 1.0],
        }
        for index in range(50)
    ]
    (root / "test" / "_annotations.coco.json").write_text(
        json.dumps(
            {
                "images": [{"id": 30, "file_name": "test.jpg"}],
                "annotations": annotations,
                "categories": [{"id": 1, "name": "product"}],
            }
        ),
        encoding="utf-8",
    )
    return root


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _jpeg_bytes(color: str) -> bytes:
    output = BytesIO()
    with Image.new("RGB", (100, 80), color) as image:
        image.save(output, format="JPEG")
    return output.getvalue()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
