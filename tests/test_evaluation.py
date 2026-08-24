import pytest

from benchmark_tool.evaluation import (
    evaluate_detections,
    intersection_over_union,
    summarize_latencies,
)


def test_intersection_over_union() -> None:
    assert intersection_over_union([0, 0, 10, 10], [5, 5, 10, 10]) == pytest.approx(25 / 175)
    assert intersection_over_union([0, 0, 1, 1], [2, 2, 1, 1]) == 0


def test_evaluate_detections_counts_matches_and_duplicates() -> None:
    ground_truth = [{"label": "product", "bbox": [0, 0, 10, 10]}]
    predictions = [
        {"label": "product", "bbox": [0, 0, 10, 10], "score": 0.9},
        {"label": "product", "bbox": [1, 1, 9, 9], "score": 0.8},
        {"label": "gap", "bbox": [0, 0, 10, 10], "score": 0.7},
    ]

    result = evaluate_detections(ground_truth, predictions)

    assert result == {
        "matched": 1,
        "ground_truth": 1,
        "predictions": 3,
        "recall": 1.0,
        "precision": 1 / 3,
        "duplicate_proposals": 1,
        "duplicate_rate": 1 / 3,
    }

def test_evaluate_detections_handles_empty_inputs() -> None:
    result = evaluate_detections([], [])

    assert result["recall"] is None
    assert result["precision"] is None
    assert result["duplicate_rate"] is None


def test_evaluate_detections_rejects_invalid_threshold() -> None:
    with pytest.raises(ValueError, match="IoU threshold"):
        evaluate_detections([], [], iou_threshold=0)


def test_summarize_latencies_uses_nearest_rank_percentiles() -> None:
    assert summarize_latencies([5.0, 1.0, 3.0, 2.0, 4.0]) == {
        "count": 5,
        "p50_ms": 3.0,
        "p95_ms": 5.0,
        "max_ms": 5.0,
    }
    assert summarize_latencies([]) == {
        "count": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
