from __future__ import annotations

import pytest

from benchmark_tool.gap_analytics import (
    analyze_sku_availability,
    derive_gap_proposals,
    evaluate_gap_predictions,
    select_gap_score_threshold,
    summarize_visible_gaps,
)


def test_derive_gap_proposals_returns_unreviewed_geometry_without_sku() -> None:
    proposals = derive_gap_proposals(
        shelf_row_id=0,
        row_bbox=[0, 10, 100, 20],
        facings=[
            _facing("left", 10, 20),
            _facing("right", 60, 20),
        ],
        minimum_gap_width=10,
    )

    assert [proposal["bbox"] for proposal in proposals] == [
        [0.0, 10.0, 10.0, 20.0],
        [30.0, 10.0, 30.0, 20.0],
        [80.0, 10.0, 20.0, 20.0],
    ]
    assert all(proposal["sku_id"] is None for proposal in proposals)
    assert all(proposal["review_state"] == "unreviewed" for proposal in proposals)
    assert proposals[0]["derived_from_annotation_ids"] == ["left", "right"]


def test_derive_gap_proposals_merges_overlap_and_ignores_small_intervals() -> None:
    proposals = derive_gap_proposals(
        shelf_row_id=0,
        row_bbox=[0, 0, 100, 20],
        facings=[
            _facing("first", -5, 30),
            _facing("overlap", 20, 30),
            _facing("last", 55, 45),
        ],
        minimum_gap_width=6,
    )

    assert proposals == []


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "shelf_row_id": -1,
            "row_bbox": [0, 0, 10, 10],
            "facings": [],
            "minimum_gap_width": 1,
        },
        {
            "shelf_row_id": 0,
            "row_bbox": [0, 0, 0, 10],
            "facings": [],
            "minimum_gap_width": 1,
        },
        {
            "shelf_row_id": 0,
            "row_bbox": [0, 0, 10, 10],
            "facings": [],
            "minimum_gap_width": 0,
        },
        {
            "shelf_row_id": 0,
            "row_bbox": [0, 0, 10, 10],
            "facings": [
                {
                    "annotation_id": "unreviewed",
                    "class_type": "product",
                    "bbox": [0, 10, 5, 20],
                    "shelf_row_id": 0,
                    "review_state": "unreviewed",
                }
            ],
            "minimum_gap_width": 1,
        },
    ),
)
def test_derive_gap_proposals_rejects_invalid_or_unreviewed_input(arguments) -> None:
    with pytest.raises(ValueError):
        derive_gap_proposals(**arguments)


def test_summarize_visible_gaps_uses_only_reviewed_gap_geometry() -> None:
    result = summarize_visible_gaps(
        rows=[_row(0), _row(1)],
        gaps=[
            _gap("accepted", 0, 10, 20, "accepted"),
            _gap("proposal", 0, 40, 30, "unreviewed"),
            _gap("other-row", 1, 0, 10, "accepted"),
        ],
        observation_complete=True,
    )

    assert result["status"] == "provisional"
    assert result["rows"][0] == {
        "shelf_row_id": 0,
        "status": "provisional",
        "visible_gap_fraction": pytest.approx(0.2),
        "accepted_gap_ids": ["accepted"],
        "proposed_gap_ids": ["proposal"],
    }
    assert result["rows"][1]["status"] == "complete"
    assert result["rows"][1]["visible_gap_fraction"] == pytest.approx(0.1)


def test_summarize_visible_gaps_marks_occluded_row_partial() -> None:
    result = summarize_visible_gaps(
        rows=[{**_row(0), "occluded": True}],
        gaps=[],
        observation_complete=True,
    )

    assert result["status"] == "partial"
    assert result["rows"][0]["status"] == "partial"


@pytest.mark.parametrize(
    "gaps",
    (
        [
            {
                "annotation_id": "sku-gap",
                "class_type": "gap",
                "bbox": [0, 0, 10, 20],
                "shelf_row_id": 0,
                "sku_id": "sku-a",
                "review_state": "accepted",
            }
        ],
        [
            {
                "annotation_id": "missing-row",
                "class_type": "gap",
                "bbox": [0, 90, 10, 20],
                "shelf_row_id": 3,
                "sku_id": None,
                "review_state": "accepted",
            }
        ],
        [
            {
                "annotation_id": "outside",
                "class_type": "gap",
                "bbox": [0, 100, 10, 20],
                "shelf_row_id": 0,
                "sku_id": None,
                "review_state": "accepted",
            }
        ],
        [
            {
                "annotation_id": "duplicate",
                "class_type": "gap",
                "bbox": [0, 0, 10, 20],
                "shelf_row_id": 0,
                "sku_id": None,
                "review_state": "accepted",
            },
            {
                "annotation_id": "duplicate",
                "class_type": "gap",
                "bbox": [20, 0, 10, 20],
                "shelf_row_id": 0,
                "sku_id": None,
                "review_state": "accepted",
            },
        ],
    ),
)
def test_summarize_visible_gaps_rejects_invalid_evidence(gaps) -> None:
    with pytest.raises(ValueError):
        summarize_visible_gaps(
            rows=[_row(0)],
            gaps=gaps,
            observation_complete=True,
        )


def test_shelf_out_of_stock_requires_and_returns_exact_evidence() -> None:
    result = analyze_sku_availability(
        sku_id="sku-a",
        expected_facings=2,
        observed_in_expected_slot=[],
        observed_in_fixture=[],
        expectation_authoritative=True,
        observation_complete=True,
        expected_slot_observed=True,
        all_evidence_reviewed=True,
        observation_evidence_id="observation-1",
        expectation_evidence_id="planogram-7",
        expected_slot_evidence_id="slot-2",
        gap_evidence_ids=["gap-2", "gap-1"],
    )

    assert result["state"] == "shelf_out_of_stock"
    assert result["missing_facings"] == 2
    assert result["uncertainty_status"] == "confirmed"
    assert result["confidence"] == {
        "status": "confirmed",
        "probability": None,
        "calibrated": False,
    }
    assert result["evidence"] == {
        "observation_id": "observation-1",
        "expectation_id": "planogram-7",
        "expected_slot_id": "slot-2",
        "observed_in_expected_slot": [],
        "observed_in_fixture": [],
        "gap_ids": ["gap-1", "gap-2"],
    }


def test_unreviewed_absence_stays_provisional() -> None:
    result = analyze_sku_availability(
        sku_id="sku-a",
        expected_facings=1,
        observed_in_expected_slot=[],
        observed_in_fixture=[],
        expectation_authoritative=True,
        observation_complete=True,
        expected_slot_observed=True,
        all_evidence_reviewed=False,
        observation_evidence_id="observation-1",
        expectation_evidence_id="planogram-7",
        expected_slot_evidence_id="slot-2",
    )

    assert result["state"] == "possible_absence"
    assert result["uncertainty_status"] == "provisional"


def test_gap_without_expectation_never_names_missing_sku() -> None:
    result = analyze_sku_availability(
        sku_id="sku-a",
        expected_facings=None,
        observed_in_expected_slot=[],
        observed_in_fixture=[],
        expectation_authoritative=False,
        observation_complete=True,
        expected_slot_observed=False,
        all_evidence_reviewed=True,
        observation_evidence_id="observation-1",
        gap_evidence_ids=["gap-1"],
    )

    assert result["state"] == "not_observed"
    assert result["missing_facings"] is None
    assert result["uncertainty_status"] == "unknown"


def test_present_elsewhere_is_misplaced_with_observation_evidence() -> None:
    result = analyze_sku_availability(
        sku_id="sku-a",
        expected_facings=2,
        observed_in_expected_slot=[],
        observed_in_fixture=["facing-4"],
        expectation_authoritative=True,
        observation_complete=True,
        expected_slot_observed=True,
        all_evidence_reviewed=True,
        observation_evidence_id="observation-1",
        expectation_evidence_id="planogram-7",
        expected_slot_evidence_id="slot-2",
    )

    assert result["state"] == "present_misplaced"
    assert result["uncertainty_status"] == "confirmed"
    assert result["evidence"]["observed_in_fixture"] == ["facing-4"]


@pytest.mark.parametrize(
    "changes",
    (
        {"expectation_evidence_id": None},
        {"expected_slot_evidence_id": None},
        {"observed_in_expected_slot": ["not-in-fixture"]},
        {"gap_evidence_ids": ["duplicate", "duplicate"]},
    ),
)
def test_availability_rejects_missing_or_inconsistent_evidence(changes) -> None:
    arguments = {
        "sku_id": "sku-a",
        "expected_facings": 1,
        "observed_in_expected_slot": [],
        "observed_in_fixture": [],
        "expectation_authoritative": True,
        "observation_complete": True,
        "expected_slot_observed": True,
        "all_evidence_reviewed": True,
        "observation_evidence_id": "observation-1",
        "expectation_evidence_id": "planogram-7",
        "expected_slot_evidence_id": "slot-2",
        "gap_evidence_ids": [],
    }
    arguments.update(changes)

    with pytest.raises(ValueError):
        analyze_sku_availability(**arguments)


def test_gap_evaluation_reports_reviewed_precision_and_recall() -> None:
    result = evaluate_gap_predictions(
        ground_truth=[
            {
                "class_type": "gap",
                "review_state": "accepted",
                "image_id": "image-1",
                "evaluation_split": "test",
                "bbox": [0, 0, 10, 10],
            }
        ],
        predictions=[
            {
                "class_type": "gap",
                "image_id": "image-1",
                "evaluation_split": "test",
                "bbox": [0, 0, 10, 10],
                "score": 0.9,
            },
            {
                "class_type": "gap",
                "image_id": "image-1",
                "evaluation_split": "test",
                "bbox": [20, 0, 10, 10],
                "score": 0.8,
            },
        ],
    )

    assert result["gate_status"] == "measured"
    assert result["recall"] == 1
    assert result["precision"] == 0.5


def test_gap_evaluation_exposes_missing_reviewed_ground_truth() -> None:
    result = evaluate_gap_predictions([], [])

    assert result["gate_status"] == "unavailable_no_reviewed_ground_truth"
    assert result["recall"] is None
    assert result["precision"] is None


def test_gap_evaluation_rejects_unreviewed_truth() -> None:
    with pytest.raises(ValueError):
        evaluate_gap_predictions(
            [
                {
                    "class_type": "gap",
                    "review_state": "unreviewed",
                    "image_id": "image-1",
                    "evaluation_split": "test",
                    "bbox": [0, 0, 1, 1],
                }
            ],
            [],
        )


def test_gap_threshold_selection_uses_validation_f1() -> None:
    result = select_gap_score_threshold(
        ground_truth=[
            {
                "class_type": "gap",
                "review_state": "accepted",
                "image_id": "image-1",
                "evaluation_split": "validation",
                "bbox": [0, 0, 10, 10],
            }
        ],
        predictions=[
            {
                "class_type": "gap",
                "image_id": "image-1",
                "evaluation_split": "validation",
                "bbox": [20, 0, 10, 10],
                "score": 0.9,
            },
            {
                "class_type": "gap",
                "image_id": "image-1",
                "evaluation_split": "validation",
                "bbox": [0, 0, 10, 10],
                "score": 0.8,
            },
            {
                "class_type": "gap",
                "image_id": "image-2",
                "evaluation_split": "validation",
                "bbox": [0, 0, 10, 10],
                "score": 0.7,
            },
        ],
    )

    assert result["threshold"] == 0.8
    assert result["f1"] == pytest.approx(2 / 3)
    assert result["metrics"]["precision"] == 0.5
    assert result["metrics"]["recall"] == 1


def test_gap_threshold_selection_requires_validation_truth() -> None:
    with pytest.raises(
        ValueError,
        match="gap threshold selection requires reviewed validation truth",
    ):
        select_gap_score_threshold([], [])


def test_gap_threshold_selection_rejects_test_records() -> None:
    with pytest.raises(
        ValueError,
        match="gap ground truth must belong to the frozen validation split",
    ):
        select_gap_score_threshold(
            [
                {
                    "class_type": "gap",
                    "review_state": "accepted",
                    "image_id": "image-1",
                    "evaluation_split": "test",
                    "bbox": [0, 0, 10, 10],
                }
            ],
            [],
        )


def test_gap_evaluation_never_matches_across_images() -> None:
    result = evaluate_gap_predictions(
        [
            {
                "class_type": "gap",
                "review_state": "accepted",
                "image_id": "truth-image",
                "evaluation_split": "test",
                "bbox": [0, 0, 10, 10],
            }
        ],
        [
            {
                "class_type": "gap",
                "image_id": "other-image",
                "evaluation_split": "test",
                "bbox": [0, 0, 10, 10],
                "score": 0.9,
            }
        ],
    )

    assert result["matched"] == 0
    assert result["recall"] == 0
    assert result["precision"] == 0


@pytest.mark.parametrize(
    "gap",
    (
        {
            "class_type": "gap",
            "image_id": "image-1",
            "evaluation_split": "validation",
            "bbox": [0, 0, 1, 1],
            "score": 0.5,
        },
        {
            "class_type": "gap",
            "image_id": "image-1",
            "evaluation_split": "test",
            "bbox": [0, 0, 0, 1],
            "score": 0.5,
        },
        {
            "class_type": "gap",
            "image_id": "image-1",
            "evaluation_split": "test",
            "bbox": [0, 0, 1, 1],
            "score": 2,
        },
    ),
)
def test_gap_evaluation_rejects_unsafe_or_invalid_predictions(gap) -> None:
    with pytest.raises(ValueError):
        evaluate_gap_predictions([], [gap])


def _facing(annotation_id: str, left: float, width: float) -> dict[str, object]:
    return {
        "annotation_id": annotation_id,
        "class_type": "product",
        "bbox": [left, 10, width, 20],
        "shelf_row_id": 0,
        "review_state": "accepted",
    }


def _row(shelf_row_id: int) -> dict[str, object]:
    return {
        "shelf_row_id": shelf_row_id,
        "bbox": [0, shelf_row_id * 30, 100, 20],
        "review_state": "accepted",
        "observation_complete": True,
        "occluded": False,
    }


def _gap(
    annotation_id: str,
    shelf_row_id: int,
    left: float,
    width: float,
    review_state: str,
    *,
    sku_id: str | None = None,
    top: float | None = None,
) -> dict[str, object]:
    return {
        "annotation_id": annotation_id,
        "class_type": "gap",
        "bbox": [left, shelf_row_id * 30 if top is None else top, width, 20],
        "shelf_row_id": shelf_row_id,
        "sku_id": sku_id,
        "review_state": review_state,
    }
