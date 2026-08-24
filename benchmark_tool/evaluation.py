import math
from collections.abc import Sequence
from typing import Any


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


def evaluate_detections(
    ground_truth: Sequence[dict[str, Any]],
    predictions: Sequence[dict[str, Any]],
    iou_threshold: float = 0.5,
) -> dict[str, int | float | None]:
    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be greater than 0 and at most 1")

    ordered_predictions = sorted(
        enumerate(predictions),
        key=lambda item: (-float(item[1]["score"]), item[0]),
    )
    unmatched_ground_truth = set(range(len(ground_truth)))
    matched_ground_truth: set[int] = set()
    unmatched_predictions: list[dict[str, Any]] = []

    for _, prediction in ordered_predictions:
        candidates = [
            (intersection_over_union(prediction["bbox"], ground_truth[index]["bbox"]), index)
            for index in unmatched_ground_truth
            if prediction["label"] == ground_truth[index]["label"]
        ]
        best_iou, best_index = max(candidates, default=(0.0, -1))
        if best_iou >= iou_threshold:
            unmatched_ground_truth.remove(best_index)
            matched_ground_truth.add(best_index)
        else:
            unmatched_predictions.append(prediction)

    duplicates = sum(
        any(
            prediction["label"] == ground_truth[index]["label"]
            and intersection_over_union(prediction["bbox"], ground_truth[index]["bbox"])
            >= iou_threshold
            for index in matched_ground_truth
        )
        for prediction in unmatched_predictions
    )
    matched = len(matched_ground_truth)
    prediction_count = len(predictions)
    ground_truth_count = len(ground_truth)
    return {
        "matched": matched,
        "ground_truth": ground_truth_count,
        "predictions": prediction_count,
        "recall": matched / ground_truth_count if ground_truth_count else None,
        "precision": matched / prediction_count if prediction_count else None,
        "duplicate_proposals": duplicates,
        "duplicate_rate": duplicates / prediction_count if prediction_count else None,
    }

def percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    if not 0 < probability <= 1:
        raise ValueError("probability must be greater than 0 and at most 1")
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def summarize_latencies(values: Sequence[float]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }
