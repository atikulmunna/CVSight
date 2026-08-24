from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmark_tool.evaluation import intersection_over_union
from benchmark_tool.rfdetr import predict_with_model
from benchmark_tool.training import TRAINING_SCHEMA, TrainingInputError

PREDICTION_SCHEMA = "cvsight-detector-predictions/v1"
EVALUATION_SCHEMA = "cvsight-detector-evaluation/v1"
REQUIRED_MODES = ("full_image", "sliced_2x2")
IOU_THRESHOLDS = tuple(0.5 + index * 0.05 for index in range(10))
DENSE_IMAGE_THRESHOLD = 50


def generate_test_predictions(
    dataset_dir: Path,
    checkpoint_path: Path,
    output_path: Path,
    *,
    model_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    training_manifest = _read_json(dataset_dir / "training-manifest.json")
    if training_manifest.get("schema_version") != TRAINING_SCHEMA:
        raise TrainingInputError("training manifest is incompatible")
    if not checkpoint_path.is_file():
        raise TrainingInputError("trained checkpoint does not exist")
    strategy = training_manifest.get("inference_strategy")
    if not isinstance(strategy, dict):
        raise TrainingInputError("inference strategy is invalid")
    threshold = strategy.get("confidence_threshold")
    nms_iou = strategy.get("nms_iou")
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise TrainingInputError("inference confidence threshold is invalid")
    if not isinstance(nms_iou, (int, float)) or not 0 < nms_iou <= 1:
        raise TrainingInputError("inference NMS IoU is invalid")

    coco = _read_json(dataset_dir / "test" / "_annotations.coco.json")
    images = coco.get("images")
    if not isinstance(images, list):
        raise TrainingInputError("test COCO images are invalid")
    factory = model_factory or _prediction_model_factory()
    model = factory(str(checkpoint_path))
    modes: dict[str, dict[str, Any]] = {}
    for mode, sliced in (("full_image", False), ("sliced_2x2", True)):
        mode_predictions = []
        for image in sorted(images, key=lambda row: int(row["id"])):
            image_path = dataset_dir / "test" / str(image["file_name"])
            if not image_path.is_file():
                raise TrainingInputError("test image is missing")
            predictions = predict_with_model(
                model,
                image_path,
                int(image["width"]),
                int(image["height"]),
                float(threshold),
                sliced=sliced,
                nms_iou=float(nms_iou),
            )
            mode_predictions.extend(
                {
                    "image_id": int(image["id"]),
                    "bbox": prediction["bbox"],
                    "score": prediction["score"],
                }
                for prediction in predictions
            )
        modes[mode] = {
            "confidence_threshold": float(threshold),
            "predictions": mode_predictions,
        }
    document = {
        "schema_version": PREDICTION_SCHEMA,
        "dataset_version_id": training_manifest["dataset_version_id"],
        "model_artifact_sha256": _sha256_file(checkpoint_path),
        "modes": modes,
    }
    write_evaluation_report(output_path, document)
    return document


def evaluate_training_predictions(
    dataset_dir: Path,
    predictions_path: Path,
) -> dict[str, Any]:
    training_manifest = _read_json(dataset_dir / "training-manifest.json")
    if training_manifest.get("schema_version") != TRAINING_SCHEMA:
        raise TrainingInputError("training manifest is incompatible")
    coco = _read_json(dataset_dir / "test" / "_annotations.coco.json")
    predictions_document = _read_json(predictions_path)
    _validate_prediction_lineage(training_manifest, predictions_document)
    ground_truth = _ground_truth(coco)

    modes = predictions_document.get("modes")
    if not isinstance(modes, dict):
        raise TrainingInputError("prediction modes are invalid")
    results = {}
    for mode in REQUIRED_MODES:
        mode_document = modes.get(mode)
        if not isinstance(mode_document, dict):
            raise TrainingInputError(f"predictions require {mode}")
        threshold = mode_document.get("confidence_threshold")
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise TrainingInputError(f"{mode} confidence threshold is invalid")
        predictions = _predictions(mode_document.get("predictions"), ground_truth)
        results[mode] = {
            "confidence_threshold": float(threshold),
            **_metrics(ground_truth, predictions),
        }

    return {
        "schema_version": EVALUATION_SCHEMA,
        "dataset_version_id": training_manifest["dataset_version_id"],
        "snapshot_content_sha256": training_manifest["snapshot_content_sha256"],
        "source_export_sha256": training_manifest["source_export_sha256"],
        "code_version": training_manifest["code_version"],
        "random_seed": training_manifest["random_seed"],
        "model_artifact_sha256": predictions_document["model_artifact_sha256"],
        "predictions_sha256": _sha256_file(predictions_path),
        "iou_thresholds": list(IOU_THRESHOLDS),
        "dense_image_threshold": DENSE_IMAGE_THRESHOLD,
        "modes": results,
    }


def write_evaluation_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _metrics(
    ground_truth: dict[int, list[list[float]]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    matched, duplicates = _match_counts(ground_truth, predictions, 0.5)
    ground_truth_count = sum(len(boxes) for boxes in ground_truth.values())
    prediction_count = len(predictions)
    average_precisions = [
        _average_precision(ground_truth, predictions, iou_threshold)
        for iou_threshold in IOU_THRESHOLDS
    ]
    dense_image_ids = {
        image_id
        for image_id, boxes in ground_truth.items()
        if len(boxes) >= DENSE_IMAGE_THRESHOLD
    }
    dense_ground_truth = {
        image_id: boxes
        for image_id, boxes in ground_truth.items()
        if image_id in dense_image_ids
    }
    dense_predictions = [
        prediction
        for prediction in predictions
        if prediction["image_id"] in dense_image_ids
    ]
    dense_matched, _ = _match_counts(dense_ground_truth, dense_predictions, 0.5)
    dense_count = sum(len(boxes) for boxes in dense_ground_truth.values())
    dense_failures = []
    for image_id in sorted(dense_image_ids):
        image_ground_truth = {image_id: ground_truth[image_id]}
        image_predictions = [
            prediction for prediction in predictions if prediction["image_id"] == image_id
        ]
        image_matched, _ = _match_counts(image_ground_truth, image_predictions, 0.5)
        if image_matched < len(ground_truth[image_id]):
            dense_failures.append(
                {
                    "image_id": image_id,
                    "ground_truth": len(ground_truth[image_id]),
                    "matched": image_matched,
                    "missed": len(ground_truth[image_id]) - image_matched,
                }
            )
    return {
        "ground_truth": ground_truth_count,
        "predictions": prediction_count,
        "matched_at_iou_50": matched,
        "product_recall_at_iou_50": matched / ground_truth_count if ground_truth_count else None,
        "precision_at_iou_50": matched / prediction_count if prediction_count else None,
        "duplicate_proposals_at_iou_50": duplicates,
        "duplicate_rate_at_iou_50": (
            duplicates / prediction_count if prediction_count else None
        ),
        "map_50": average_precisions[0],
        "map_50_95": (
            sum(average_precisions) / len(average_precisions)
            if average_precisions and average_precisions[0] is not None
            else None
        ),
        "dense_scenes": {
            "images": len(dense_image_ids),
            "ground_truth": dense_count,
            "matched_at_iou_50": dense_matched,
            "recall_at_iou_50": dense_matched / dense_count if dense_count else None,
            "failures": dense_failures,
        },
    }


def _ground_truth(coco: Mapping[str, Any]) -> dict[int, list[list[float]]]:
    images = coco.get("images")
    annotations = coco.get("annotations")
    if not isinstance(images, list) or not isinstance(annotations, list):
        raise TrainingInputError("test COCO data is invalid")
    image_ids = {
        image["id"] for image in images if isinstance(image, dict) and "id" in image
    }
    ground_truth: dict[int, list[list[float]]] = {int(image_id): [] for image_id in image_ids}
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("image_id") not in image_ids:
            raise TrainingInputError("test annotation references an unknown image")
        ground_truth[int(annotation["image_id"])].append(
            _box(annotation.get("bbox"), "test annotation")
        )
    return ground_truth


def _predictions(
    value: Any,
    ground_truth: Mapping[int, list[list[float]]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TrainingInputError("mode predictions must be a list")
    predictions = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TrainingInputError("prediction must be an object")
        image_id = item.get("image_id")
        score = item.get("score")
        if not isinstance(image_id, int) or image_id not in ground_truth:
            raise TrainingInputError("prediction references a non-test image")
        if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise TrainingInputError("prediction score is invalid")
        predictions.append(
            {
                "image_id": image_id,
                "bbox": _box(item.get("bbox"), "prediction"),
                "score": float(score),
                "order": index,
            }
        )
    return predictions


def _match_counts(
    ground_truth: Mapping[int, list[list[float]]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float,
) -> tuple[int, int]:
    unmatched = {image_id: set(range(len(boxes))) for image_id, boxes in ground_truth.items()}
    matched = 0
    duplicates = 0
    for prediction in _ordered(predictions):
        image_id = prediction["image_id"]
        boxes = ground_truth[image_id]
        candidates = [
            (intersection_over_union(prediction["bbox"], boxes[index]), index)
            for index in unmatched[image_id]
        ]
        best_iou, best_index = max(candidates, default=(0.0, -1))
        if best_iou >= iou_threshold:
            unmatched[image_id].remove(best_index)
            matched += 1
        elif any(
            intersection_over_union(prediction["bbox"], box) >= iou_threshold
            for box in boxes
        ):
            duplicates += 1
    return matched, duplicates


def _average_precision(
    ground_truth: Mapping[int, list[list[float]]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float,
) -> float | None:
    ground_truth_count = sum(len(boxes) for boxes in ground_truth.values())
    if not ground_truth_count:
        return None
    unmatched = {image_id: set(range(len(boxes))) for image_id, boxes in ground_truth.items()}
    true_positives = []
    false_positives = []
    for prediction in _ordered(predictions):
        image_id = prediction["image_id"]
        candidates = [
            (
                intersection_over_union(prediction["bbox"], ground_truth[image_id][index]),
                index,
            )
            for index in unmatched[image_id]
        ]
        best_iou, best_index = max(candidates, default=(0.0, -1))
        is_match = best_iou >= iou_threshold
        if is_match:
            unmatched[image_id].remove(best_index)
        true_positives.append(1 if is_match else 0)
        false_positives.append(0 if is_match else 1)

    recalls = []
    precisions = []
    matched = 0
    rejected = 0
    for true_positive, false_positive in zip(
        true_positives, false_positives, strict=True
    ):
        matched += true_positive
        rejected += false_positive
        recalls.append(matched / ground_truth_count)
        precisions.append(matched / (matched + rejected))
    return sum(
        max(
            (
                precision
                for recall, precision in zip(recalls, precisions, strict=True)
                if recall >= target
            ),
            default=0.0,
        )
        for target in (index / 100 for index in range(101))
    ) / 101


def _ordered(predictions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(predictions, key=lambda row: (-row["score"], row["order"]))


def _box(value: Any, label: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, (int, float)) and math.isfinite(item) for item in value)
    ):
        raise TrainingInputError(f"{label} box is invalid")
    box = [float(item) for item in value]
    if box[0] < 0 or box[1] < 0 or box[2] <= 0 or box[3] <= 0:
        raise TrainingInputError(f"{label} box is invalid")
    return box


def _validate_prediction_lineage(
    training_manifest: Mapping[str, Any],
    predictions: Mapping[str, Any],
) -> None:
    if predictions.get("schema_version") != PREDICTION_SCHEMA:
        raise TrainingInputError("prediction document is incompatible")
    if predictions.get("dataset_version_id") != training_manifest.get("dataset_version_id"):
        raise TrainingInputError("predictions target a different dataset version")
    checksum = predictions.get("model_artifact_sha256")
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
    ):
        raise TrainingInputError("model artifact checksum is invalid")


def _prediction_model_factory() -> Callable[[str], Any]:
    model_type = importlib.import_module("rfdetr").RFDETRNano
    return lambda checkpoint: model_type(pretrain_weights=checkpoint)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrainingInputError(f"cannot read {path.name}") from error
    if not isinstance(value, dict):
        raise TrainingInputError(f"{path.name} must contain a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
