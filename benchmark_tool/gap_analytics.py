from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from benchmark_tool.analytics import (
    classify_shelf_availability,
    visible_gap_fraction,
)
from benchmark_tool.evaluation import evaluate_detections


def derive_gap_proposals(
    *,
    shelf_row_id: int,
    row_bbox: Sequence[float],
    facings: Sequence[Mapping[str, Any]],
    minimum_gap_width: float,
) -> list[dict[str, Any]]:
    """Derive review-only horizontal gap proposals from reviewed facings."""
    row_left, row_top, row_width, row_height = _bbox(row_bbox, "row")
    if isinstance(shelf_row_id, bool) or shelf_row_id < 0:
        raise ValueError("shelf_row_id must be a non-negative integer")
    if not isinstance(shelf_row_id, int):
        raise ValueError("shelf_row_id must be a non-negative integer")
    gap_width = float(minimum_gap_width)
    if not math.isfinite(gap_width) or gap_width <= 0:
        raise ValueError("minimum_gap_width must be finite and greater than zero")

    intervals: list[tuple[float, float]] = []
    evidence_ids: list[str] = []
    row_right = row_left + row_width
    for facing in facings:
        annotation_id = _identifier(facing.get("annotation_id"), "annotation_id")
        if facing.get("class_type", "product") != "product":
            raise ValueError("gap derivation accepts product facings only")
        if facing.get("review_state") != "accepted":
            raise ValueError("gap derivation requires reviewed product facings")
        if facing.get("shelf_row_id") != shelf_row_id:
            raise ValueError("every facing must belong to the requested shelf row")
        left, _, width, _ = _bbox_value(facing.get("bbox"), "facing")
        clipped_left = max(row_left, left)
        clipped_right = min(row_right, left + width)
        if clipped_right > clipped_left:
            intervals.append((clipped_left, clipped_right))
        evidence_ids.append(annotation_id)

    proposals = []
    for index, (left, right) in enumerate(
        _complement_intervals(row_left, row_right, intervals),
        start=1,
    ):
        width = right - left
        if width < gap_width:
            continue
        proposals.append(
            {
                "proposal_id": f"derived-gap:{shelf_row_id}:{index}",
                "class_type": "gap",
                "bbox": [left, row_top, width, row_height],
                "shelf_row_id": shelf_row_id,
                "sku_id": None,
                "source": "geometry",
                "review_state": "unreviewed",
                "derived_from_annotation_ids": sorted(set(evidence_ids)),
            }
        )
    return proposals


def summarize_visible_gaps(
    *,
    rows: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    observation_complete: bool,
) -> dict[str, Any]:
    """Associate gap evidence with rows without treating proposals as facts."""
    if not rows:
        raise ValueError("at least one shelf row is required")
    if not isinstance(observation_complete, bool):
        raise ValueError("observation_complete must be a boolean")

    normalized_rows = [_normalize_row(row) for row in rows]
    row_ids = [row["shelf_row_id"] for row in normalized_rows]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("shelf row identifiers must be unique")
    rows_by_id = {row["shelf_row_id"]: row for row in normalized_rows}
    normalized_gaps = [_normalize_gap(gap, rows_by_id) for gap in gaps]
    gap_ids = [gap["annotation_id"] for gap in normalized_gaps]
    if len(set(gap_ids)) != len(gap_ids):
        raise ValueError("gap annotation identifiers must be unique")

    summaries = []
    for row in sorted(normalized_rows, key=lambda value: value["shelf_row_id"]):
        row_gaps = [
            gap
            for gap in normalized_gaps
            if gap["shelf_row_id"] == row["shelf_row_id"]
        ]
        accepted = [gap for gap in row_gaps if gap["review_state"] == "accepted"]
        proposed = [gap for gap in row_gaps if gap["review_state"] != "accepted"]
        row_left, _, row_width, _ = row["bbox"]
        fraction = visible_gap_fraction(
            row_left,
            row_width,
            [(gap["bbox"][0], gap["bbox"][2]) for gap in accepted],
        )
        summaries.append(
            {
                "shelf_row_id": row["shelf_row_id"],
                "status": _gap_status(
                    observation_complete=observation_complete,
                    row=row,
                    proposed=proposed,
                ),
                "visible_gap_fraction": fraction,
                "accepted_gap_ids": [gap["annotation_id"] for gap in accepted],
                "proposed_gap_ids": [gap["annotation_id"] for gap in proposed],
            }
        )
    return {
        "status": _overall_gap_status(summaries),
        "rows": summaries,
    }


def analyze_sku_availability(
    *,
    sku_id: str,
    expected_facings: int | None,
    observed_in_expected_slot: Sequence[str],
    observed_in_fixture: Sequence[str],
    expectation_authoritative: bool,
    observation_complete: bool,
    expected_slot_observed: bool,
    all_evidence_reviewed: bool,
    observation_evidence_id: str,
    expectation_evidence_id: str | None = None,
    expected_slot_evidence_id: str | None = None,
    gap_evidence_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Return an evidence-bearing availability state with no invented probability."""
    sku = _identifier(sku_id, "sku_id")
    observation_id = _identifier(observation_evidence_id, "observation_evidence_id")
    slot_annotations = _identifiers(
        observed_in_expected_slot,
        "observed_in_expected_slot",
    )
    fixture_annotations = _identifiers(
        observed_in_fixture,
        "observed_in_fixture",
    )
    gap_ids = _identifiers(gap_evidence_ids, "gap_evidence_ids")
    if not set(slot_annotations).issubset(fixture_annotations):
        raise ValueError("expected-slot observations must be fixture observations")
    for value, name in (
        (expectation_authoritative, "expectation_authoritative"),
        (observation_complete, "observation_complete"),
        (expected_slot_observed, "expected_slot_observed"),
        (all_evidence_reviewed, "all_evidence_reviewed"),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
    expectation_id = _optional_identifier(
        expectation_evidence_id,
        "expectation_evidence_id",
    )
    slot_id = _optional_identifier(
        expected_slot_evidence_id,
        "expected_slot_evidence_id",
    )
    if expectation_authoritative and expectation_id is None:
        raise ValueError("authoritative expectations require expectation evidence")
    if expected_slot_observed and slot_id is None:
        raise ValueError("an observed expected slot requires slot evidence")

    classification = classify_shelf_availability(
        expected_facings=expected_facings,
        observed_in_expected_slot=len(slot_annotations),
        observed_in_fixture=len(fixture_annotations),
        expectation_authoritative=expectation_authoritative,
        observation_complete=observation_complete and all_evidence_reviewed,
        expected_slot_observed=expected_slot_observed and all_evidence_reviewed,
    )
    uncertainty = _availability_uncertainty(
        state=str(classification["state"]),
        all_evidence_reviewed=all_evidence_reviewed,
        observation_complete=observation_complete,
    )
    return {
        "sku_id": sku,
        **classification,
        "uncertainty_status": uncertainty,
        "confidence": {
            "status": uncertainty,
            "probability": None,
            "calibrated": False,
        },
        "evidence": {
            "observation_id": observation_id,
            "expectation_id": expectation_id,
            "expected_slot_id": slot_id,
            "observed_in_expected_slot": slot_annotations,
            "observed_in_fixture": fixture_annotations,
            "gap_ids": gap_ids,
        },
    }


def evaluate_gap_predictions(
    ground_truth: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    """Evaluate gap predictions only against accepted reviewed ground truth."""
    return _evaluate_gap_split(
        ground_truth,
        predictions,
        evaluation_split="test",
        iou_threshold=iou_threshold,
    )


def select_gap_score_threshold(
    ground_truth: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    """Select a score threshold on validation data by maximum F1."""
    if not ground_truth:
        raise ValueError("gap threshold selection requires reviewed validation truth")

    scores = {_prediction_score(prediction) for prediction in predictions}
    thresholds = sorted(scores | {1.0}, reverse=True)
    candidates: list[dict[str, Any]] = []
    for threshold in thresholds:
        filtered = [
            prediction
            for prediction in predictions
            if _prediction_score(prediction) >= threshold
        ]
        metrics = _evaluate_gap_split(
            ground_truth,
            filtered,
            evaluation_split="validation",
            iou_threshold=iou_threshold,
        )
        precision = metrics["precision"]
        recall = metrics["recall"]
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else 0.0
        )
        candidates.append({"threshold": threshold, "f1": f1, "metrics": metrics})

    selected = max(
        candidates,
        key=lambda candidate: (
            candidate["f1"],
            candidate["metrics"]["precision"] or 0.0,
            candidate["metrics"]["recall"] or 0.0,
            candidate["threshold"],
        ),
    )
    return {
        **selected,
        "candidate_count": len(candidates),
        "selection_rule": "maximum F1, then precision, recall, and higher threshold",
    }


def _evaluate_gap_split(
    ground_truth: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    evaluation_split: str,
    iou_threshold: float,
) -> dict[str, Any]:
    reviewed_ground_truth: dict[str, list[dict[str, Any]]] = {}
    for gap in ground_truth:
        if gap.get("class_type", "gap") != "gap":
            raise ValueError("gap evaluation accepts gap ground truth only")
        if gap.get("review_state") != "accepted":
            raise ValueError("gap ground truth must be accepted by a reviewer")
        if gap.get("evaluation_split") != evaluation_split:
            raise ValueError(
                f"gap ground truth must belong to the frozen {evaluation_split} split"
            )
        image_id = _identifier(gap.get("image_id"), "image_id")
        reviewed_ground_truth.setdefault(image_id, []).append(
            {"label": "gap", "bbox": list(_bbox_value(gap.get("bbox"), "gap"))}
        )
    normalized_predictions: dict[str, list[dict[str, Any]]] = {}
    for gap in predictions:
        if gap.get("class_type", "gap") != "gap":
            raise ValueError("gap evaluation accepts gap predictions only")
        if gap.get("evaluation_split") != evaluation_split:
            raise ValueError(
                f"gap predictions must belong to the frozen {evaluation_split} split"
            )
        image_id = _identifier(gap.get("image_id"), "image_id")
        score = _prediction_score(gap)
        normalized_predictions.setdefault(image_id, []).append(
            {
                "label": "gap",
                "bbox": list(_bbox_value(gap.get("bbox"), "gap")),
                "score": score,
            }
        )
    totals = {
        "matched": 0,
        "ground_truth": 0,
        "predictions": 0,
        "duplicate_proposals": 0,
    }
    for image_id in sorted(set(reviewed_ground_truth) | set(normalized_predictions)):
        metrics = evaluate_detections(
            reviewed_ground_truth.get(image_id, []),
            normalized_predictions.get(image_id, []),
            iou_threshold=iou_threshold,
        )
        for name in totals:
            value = metrics[name]
            if not isinstance(value, int):
                raise RuntimeError("detection evaluator returned an invalid count")
            totals[name] += value
    ground_truth_count = totals["ground_truth"]
    prediction_count = totals["predictions"]
    return {
        **totals,
        "recall": (
            totals["matched"] / ground_truth_count if ground_truth_count else None
        ),
        "precision": (
            totals["matched"] / prediction_count if prediction_count else None
        ),
        "duplicate_rate": (
            totals["duplicate_proposals"] / prediction_count
            if prediction_count
            else None
        ),
        "gate_status": (
            "measured"
            if reviewed_ground_truth
            else "unavailable_no_reviewed_ground_truth"
        ),
    }


def _prediction_score(prediction: Mapping[str, Any]) -> float:
    score = float(prediction.get("score", 1.0))
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("gap prediction score must be between zero and one")
    return score


def _bbox(value: Sequence[float], subject: str) -> tuple[float, float, float, float]:
    return _bbox_value(value, subject)


def _bbox_value(value: object, subject: str) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ValueError(f"{subject} bbox must contain four values")
    left, top, width, height = (float(item) for item in value)
    if (
        not all(math.isfinite(item) for item in (left, top, width, height))
        or width <= 0
        or height <= 0
    ):
        raise ValueError(f"{subject} bbox must be finite with positive size")
    return left, top, width, height


def _complement_intervals(
    left: float,
    right: float,
    occupied: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(occupied):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    gaps = []
    cursor = left
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < right:
        gaps.append((cursor, right))
    return gaps


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    row_id = row.get("shelf_row_id")
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id < 0:
        raise ValueError("shelf_row_id must be a non-negative integer")
    review_state = row.get("review_state")
    if review_state not in {"unreviewed", "accepted", "flagged"}:
        raise ValueError("row review_state must be unreviewed, accepted, or flagged")
    row_complete = row.get("observation_complete", True)
    occluded = row.get("occluded", False)
    if not isinstance(row_complete, bool) or not isinstance(occluded, bool):
        raise ValueError("row completeness and occlusion must be boolean")
    return {
        "shelf_row_id": row_id,
        "bbox": list(_bbox_value(row.get("bbox"), "row")),
        "review_state": review_state,
        "observation_complete": row_complete,
        "occluded": occluded,
    }


def _normalize_gap(
    gap: Mapping[str, Any],
    rows_by_id: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    annotation_id = _identifier(gap.get("annotation_id"), "annotation_id")
    if gap.get("class_type", "gap") != "gap":
        raise ValueError("visible gap analysis accepts gap annotations only")
    if gap.get("sku_id") is not None:
        raise ValueError("gap annotations cannot name a SKU")
    row_id = gap.get("shelf_row_id")
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id not in rows_by_id:
        raise ValueError("every gap must reference an existing shelf row")
    review_state = gap.get("review_state")
    if review_state not in {"unreviewed", "accepted", "flagged"}:
        raise ValueError("gap review_state must be unreviewed, accepted, or flagged")
    bbox = _bbox_value(gap.get("bbox"), "gap")
    if not _boxes_intersect(bbox, tuple(rows_by_id[row_id]["bbox"])):
        raise ValueError("gap geometry must intersect its shelf row")
    return {
        "annotation_id": annotation_id,
        "class_type": "gap",
        "bbox": list(bbox),
        "shelf_row_id": row_id,
        "sku_id": None,
        "review_state": review_state,
    }


def _boxes_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    first_left, first_top, first_width, first_height = first
    second_left, second_top, second_width, second_height = second
    return (
        max(first_left, second_left) < min(first_left + first_width, second_left + second_width)
        and max(first_top, second_top)
        < min(first_top + first_height, second_top + second_height)
    )


def _gap_status(
    *,
    observation_complete: bool,
    row: Mapping[str, Any],
    proposed: Sequence[Mapping[str, Any]],
) -> str:
    if not observation_complete or not row["observation_complete"] or row["occluded"]:
        return "partial"
    if row["review_state"] != "accepted" or proposed:
        return "provisional"
    return "complete"


def _overall_gap_status(rows: Sequence[Mapping[str, Any]]) -> str:
    statuses = {row["status"] for row in rows}
    if "partial" in statuses:
        return "partial"
    if "provisional" in statuses:
        return "provisional"
    return "complete"


def _availability_uncertainty(
    *,
    state: str,
    all_evidence_reviewed: bool,
    observation_complete: bool,
) -> str:
    if state == "not_observed":
        return "unknown"
    if state == "possible_absence" or not all_evidence_reviewed or not observation_complete:
        return "provisional"
    return "confirmed"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_identifier(value: object, name: str) -> str | None:
    return None if value is None else _identifier(value, name)


def _identifiers(values: Sequence[str], name: str) -> list[str]:
    identifiers = [_identifier(value, name) for value in values]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{name} must not contain duplicates")
    return sorted(identifiers)
