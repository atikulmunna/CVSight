"""Score detector predictions against ground-truth boxes.

Shared by the RF-DETR training evaluator and the Models page evaluation, so a model is
measured the same way however it is registered. Boxes are [x, y, width, height] lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

EVALUATION_SCHEMA = "cvsight-detector-evaluation/v1"
IOU_THRESHOLDS = tuple(0.5 + index * 0.05 for index in range(10))
DENSE_IMAGE_THRESHOLD = 50
# Adjacent facings often share a few edge pixels; from this IoU on, two products
# genuinely hide part of each other, which is where detectors merge or drop boxes.
OVERLAP_IOU_THRESHOLD = 0.1


def intersection_over_union(first: Sequence[float], second: Sequence[float]) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second

    intersection_left = max(first_x, second_x)
    intersection_top = max(first_y, second_y)
    intersection_right = min(first_x + first_width, second_x + second_width)
    intersection_bottom = min(first_y + first_height, second_y + second_height)
    intersection_width = max(0.0, intersection_right - intersection_left)
    intersection_height = max(0.0, intersection_bottom - intersection_top)
    intersection_area = intersection_width * intersection_height

    first_area = max(0.0, first_width) * max(0.0, first_height)
    second_area = max(0.0, second_width) * max(0.0, second_height)
    union_area = first_area + second_area - intersection_area
    if union_area <= 0:
        return 0.0
    return intersection_area / union_area


def detector_metrics(
    ground_truth: dict[int, list[list[float]]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    matched_indexes, duplicates = _match(ground_truth, predictions, 0.5)
    matched = sum(len(indexes) for indexes in matched_indexes.values())
    ground_truth_count = sum(len(boxes) for boxes in ground_truth.values())
    prediction_count = len(predictions)
    average_precisions = [
        _average_precision(ground_truth, predictions, iou_threshold)
        for iou_threshold in IOU_THRESHOLDS
    ]
    dense_image_ids = {
        image_id for image_id, boxes in ground_truth.items() if len(boxes) >= DENSE_IMAGE_THRESHOLD
    }
    dense_ground_truth = {
        image_id: boxes for image_id, boxes in ground_truth.items() if image_id in dense_image_ids
    }
    dense_predictions = [
        prediction for prediction in predictions if prediction["image_id"] in dense_image_ids
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
        "duplicate_rate_at_iou_50": (duplicates / prediction_count if prediction_count else None),
        "map_50": average_precisions[0],
        # Every threshold is unmeasurable together, when there is no ground truth.
        "map_50_95": (
            sum(precision for precision in average_precisions if precision is not None)
            / len(average_precisions)
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
        "overlapping_products": _overlapping_recall(ground_truth, matched_indexes),
    }


def _overlapping_recall(
    ground_truth: Mapping[int, list[list[float]]],
    matched_indexes: Mapping[int, set[int]],
) -> dict[str, Any]:
    overlapping = [
        (image_id, index)
        for image_id, boxes in ground_truth.items()
        for index in range(len(boxes))
        if _overlaps_another(boxes, index)
    ]
    matched = sum(1 for image_id, index in overlapping if index in matched_indexes[image_id])
    return {
        "ground_truth": len(overlapping),
        "matched_at_iou_50": matched,
        "recall_at_iou_50": matched / len(overlapping) if overlapping else None,
    }


def _overlaps_another(boxes: Sequence[list[float]], index: int) -> bool:
    return any(
        intersection_over_union(boxes[index], box) >= OVERLAP_IOU_THRESHOLD
        for other, box in enumerate(boxes)
        if other != index
    )


def _match_counts(
    ground_truth: Mapping[int, list[list[float]]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float,
) -> tuple[int, int]:
    matched_indexes, duplicates = _match(ground_truth, predictions, iou_threshold)
    return sum(len(indexes) for indexes in matched_indexes.values()), duplicates


def _match(
    ground_truth: Mapping[int, list[list[float]]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float,
) -> tuple[dict[int, set[int]], int]:
    """Ground-truth indexes matched per image, and predictions that only repeat a match."""
    unmatched = {image_id: set(range(len(boxes))) for image_id, boxes in ground_truth.items()}
    matched: dict[int, set[int]] = {image_id: set() for image_id in ground_truth}
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
            matched[image_id].add(best_index)
        elif any(
            intersection_over_union(prediction["bbox"], box) >= iou_threshold for box in boxes
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
    for true_positive, false_positive in zip(true_positives, false_positives, strict=True):
        matched += true_positive
        rejected += false_positive
        recalls.append(matched / ground_truth_count)
        precisions.append(matched / (matched + rejected))
    return (
        sum(
            max(
                (
                    precision
                    for recall, precision in zip(recalls, precisions, strict=True)
                    if recall >= target
                ),
                default=0.0,
            )
            for target in (index / 100 for index in range(101))
        )
        / 101
    )


def _ordered(predictions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(predictions, key=lambda row: (-row["score"], row["order"]))
