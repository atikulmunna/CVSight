from datetime import UTC, datetime

import pytest

from benchmark_tool.analytics import (
    classify_shelf_availability,
    compare_planogram,
    count_share_of_shelf,
    image_area_share_of_shelf,
    order_realogram,
    reconstruct_realogram,
    summarize_share_of_shelf,
    visible_gap_fraction,
)


def test_count_share_of_shelf_preserves_unknown_bucket():
    result = count_share_of_shelf(["brand_a", "brand_a", "brand_b", None])

    assert result == {
        "total_facings": 4,
        "counts": {"brand_a": 2, "brand_b": 1, "unknown": 1},
        "shares": {"brand_a": 0.5, "brand_b": 0.25, "unknown": 0.25},
        "unknown_facings": 1,
        "unknown_share": 0.25,
    }


def test_count_share_of_shelf_rejects_empty_scope():
    with pytest.raises(ValueError):
        count_share_of_shelf([])


def test_image_area_share_of_shelf_includes_unknown_area():
    result = image_area_share_of_shelf(
        [("brand_a", 100), ("brand_b", 50), (None, 50)]
    )

    assert result["total_image_area"] == 200
    assert result["shares"] == {
        "brand_a": pytest.approx(0.5),
        "brand_b": pytest.approx(0.25),
        "unknown": pytest.approx(0.25),
    }


@pytest.mark.parametrize(
    "facings",
    ([], [("brand", 0)], [("brand", -1)], [("brand", float("nan"))]),
)
def test_image_area_share_of_shelf_rejects_invalid_input(facings):
    with pytest.raises(ValueError):
        image_area_share_of_shelf(facings)


def test_share_summary_labels_count_and_diagnostic_area_metrics():
    result = summarize_share_of_shelf(
        [
            {
                "annotation_id": "known",
                "bbox": [0, 0, 10, 10],
                "group_id": "brand-a",
                "identity_state": "known",
                "review_state": "accepted",
            },
            {
                "annotation_id": "unknown",
                "bbox": [10, 0, 20, 10],
                "group_id": None,
                "identity_state": "unknown",
                "review_state": "accepted",
            },
        ],
        observation_complete=True,
        view="mild_oblique",
    )

    assert result["scope_status"] == "complete"
    assert result["count_share"]["shares"] == {
        "brand-a": 0.5,
        "unknown": 0.5,
    }
    assert result["count_share"]["perspective_sensitive"] is False
    assert result["image_area_share"]["shares"] == {
        "brand-a": pytest.approx(1 / 3),
        "unknown": pytest.approx(2 / 3),
    }
    assert result["image_area_share"]["perspective_sensitive"] is True
    assert result["image_area_share"]["cross_image_comparable"] is False
    assert result["image_area_share"]["physical_shelf_share"] is False


def test_share_summary_rejects_area_for_severe_oblique_view():
    result = summarize_share_of_shelf(
        [
            {
                "annotation_id": "facing",
                "bbox": [0, 0, 10, 10],
                "group_id": "brand-a",
                "review_state": "accepted",
            }
        ],
        observation_complete=True,
        view="severe_oblique",
    )

    assert result["count_share"]["status"] == "complete"
    assert result["image_area_share"] == {
        "metric": "image_area_share",
        "status": "unsupported",
        "reason": "severe_oblique_view",
        "perspective_sensitive": True,
        "cross_image_comparable": False,
        "physical_shelf_share": False,
    }


def test_share_summary_requires_declared_view_for_area_metric():
    result = summarize_share_of_shelf(
        [
            {
                "annotation_id": "facing",
                "bbox": [0, 0, 10, 10],
                "group_id": "brand-a",
                "review_state": "accepted",
            }
        ],
        observation_complete=True,
        view=None,
    )

    assert result["count_share"]["status"] == "complete"
    assert result["image_area_share"]["status"] == "unsupported"
    assert result["image_area_share"]["reason"] == "capture_view_not_declared"


@pytest.mark.parametrize(
    ("observation_complete", "review_state", "identity_state", "expected"),
    (
        (False, "accepted", "known", "partial"),
        (True, "unreviewed", "known", "provisional"),
        (True, "accepted", "conflicted", "provisional"),
    ),
)
def test_share_summary_preserves_scope_uncertainty(
    observation_complete, review_state, identity_state, expected
):
    result = summarize_share_of_shelf(
        [
            {
                "annotation_id": "facing",
                "bbox": [0, 0, 10, 10],
                "group_id": "brand-a",
                "review_state": review_state,
                "identity_state": identity_state,
            }
        ],
        observation_complete=observation_complete,
    )

    assert result["scope_status"] == expected
    assert result["count_share"]["status"] == expected
    assert result["image_area_share"]["status"] == expected


def test_share_summary_rejects_duplicate_facing_identifiers():
    facing = {
        "annotation_id": "duplicate",
        "bbox": [0, 0, 10, 10],
        "group_id": "brand-a",
        "review_state": "accepted",
    }

    with pytest.raises(ValueError, match="identifiers must be unique"):
        summarize_share_of_shelf(
            [facing, {**facing, "bbox": [20, 0, 10, 10]}],
            observation_complete=True,
        )


@pytest.mark.parametrize(
    "arguments",
    (
        {"observation_complete": 1},
        {"observation_complete": True, "view": "fisheye"},
    ),
)
def test_share_summary_rejects_invalid_scope(arguments):
    with pytest.raises(ValueError):
        summarize_share_of_shelf(
            [
                {
                    "annotation_id": "facing",
                    "bbox": [0, 0, 10, 10],
                    "group_id": "brand-a",
                }
            ],
            **arguments,
        )


def test_visible_gap_fraction_clips_and_merges_overlapping_gaps():
    result = visible_gap_fraction(
        row_left=10,
        row_width=100,
        gaps=[(0, 20), (15, 20), (90, 30), (200, 10)],
    )

    assert result == pytest.approx(0.45)


@pytest.mark.parametrize(
    ("row_left", "row_width", "gaps"),
    (
        (0, 0, []),
        (float("nan"), 10, []),
        (0, 10, [(1, 0)]),
        (0, 10, [(float("nan"), 1)]),
    ),
)
def test_visible_gap_fraction_rejects_invalid_geometry(
    row_left,
    row_width,
    gaps,
):
    with pytest.raises(ValueError):
        visible_gap_fraction(row_left, row_width, gaps)


def test_order_realogram_groups_rows_and_orders_by_center():
    facings = [
        {
            "annotation_id": "right",
            "shelf_row_id": 2,
            "bbox": [40, 10, 10, 20],
            "sku_id": "b",
        },
        {
            "annotation_id": "left",
            "shelf_row_id": 2,
            "bbox": [10, 10, 20, 20],
            "sku_id": None,
        },
        {
            "annotation_id": "top",
            "shelf_row_id": 1,
            "bbox": [30, 0, 10, 10],
            "sku_id": "a",
        },
    ]

    result = order_realogram(facings)

    assert [row["shelf_row_id"] for row in result] == [1, 2]
    assert [
        facing["annotation_id"] for facing in result[1]["facings"]
    ] == ["left", "right"]
    assert result[1]["facings"][0]["sku_id"] is None


@pytest.mark.parametrize(
    "facing",
    (
        {"annotation_id": "", "shelf_row_id": 1, "bbox": [0, 0, 1, 1]},
        {"annotation_id": "a", "shelf_row_id": None, "bbox": [0, 0, 1, 1]},
        {"annotation_id": "a", "shelf_row_id": 1, "bbox": [0, 0, 0, 1]},
        {"annotation_id": "a", "shelf_row_id": 1, "bbox": [0, 0, 1]},
    ),
)
def test_order_realogram_rejects_incomplete_facings(facing):
    with pytest.raises(ValueError):
        order_realogram([facing])


def test_reconstruct_realogram_clusters_rows_and_preserves_unknown_identity():
    result = reconstruct_realogram(
        [
            {
                "annotation_id": "bottom-right",
                "bbox": [45, 82, 12, 28],
                "sku_id": "sku-b",
                "review_state": "accepted",
            },
            {
                "annotation_id": "top-right",
                "bbox": [50, 12, 10, 30],
                "sku_id": "sku-a",
                "review_state": "accepted",
            },
            {
                "annotation_id": "bottom-left",
                "bbox": [10, 80, 20, 30],
                "sku_id": None,
                "review_state": "accepted",
            },
            {
                "annotation_id": "top-left",
                "bbox": [5, 10, 18, 32],
                "sku_id": "sku-a",
                "review_state": "accepted",
            },
        ],
        observation_complete=True,
    )

    assert result["status"] == "provisional"
    assert result["assignment_source"] == "automatic"
    assert [
        [facing["annotation_id"] for facing in row["facings"]]
        for row in result["rows"]
    ] == [["top-left", "top-right"], ["bottom-left", "bottom-right"]]
    assert result["rows"][1]["facings"][0]["sku_id"] is None


def test_reconstruct_realogram_uses_reviewed_manual_rows_for_complete_result():
    result = reconstruct_realogram(
        [
            {
                "annotation_id": "right",
                "shelf_row_id": 1,
                "bbox": [30, 20, 10, 10],
                "review_state": "accepted",
            },
            {
                "annotation_id": "left",
                "shelf_row_id": 1,
                "bbox": [10, 20, 10, 10],
                "review_state": "accepted",
            },
        ],
        observation_complete=True,
        view="mild_oblique",
    )

    assert result["status"] == "complete"
    assert result["assignment_source"] == "reviewed"
    assert [
        facing["annotation_id"] for facing in result["rows"][0]["facings"]
    ] == ["left", "right"]


def test_reconstruct_realogram_marks_incomplete_observation_partial():
    result = reconstruct_realogram(
        [
            {
                "annotation_id": "visible-facing",
                "shelf_row_id": 0,
                "bbox": [10, 10, 10, 10],
                "review_state": "accepted",
            }
        ],
        observation_complete=False,
    )

    assert result["status"] == "partial"
    assert len(result["rows"]) == 1


def test_reconstruct_realogram_splits_distant_rows_deterministically():
    facings = [
        {"annotation_id": "lower", "bbox": [20, 40, 10, 10]},
        {"annotation_id": "upper-b", "bbox": [20, 0, 10, 10]},
        {"annotation_id": "upper-a", "bbox": [0, 0, 10, 10]},
    ]

    forward = reconstruct_realogram(facings, observation_complete=True)
    reverse = reconstruct_realogram(
        list(reversed(facings)), observation_complete=True
    )

    assert forward == reverse
    assert [
        [facing["annotation_id"] for facing in row["facings"]]
        for row in forward["rows"]
    ] == [["upper-a", "upper-b"], ["lower"]]


def test_reconstruct_realogram_rejects_severe_oblique_view_explicitly():
    result = reconstruct_realogram(
        [{"annotation_id": "facing", "bbox": [10, 10, 10, 10]}],
        observation_complete=True,
        view="severe_oblique",
    )

    assert result == {
        "status": "unsupported",
        "reason": "severe_oblique_view",
        "assignment_source": None,
        "rows": [],
    }


@pytest.mark.parametrize(
    "facings",
    (
        [],
        [
            {
                "annotation_id": "assigned",
                "shelf_row_id": 0,
                "bbox": [0, 0, 10, 10],
            },
            {
                "annotation_id": "automatic",
                "shelf_row_id": None,
                "bbox": [20, 0, 10, 10],
            },
        ],
        [{"annotation_id": "bad-row", "shelf_row_id": -1, "bbox": [0, 0, 1, 1]}],
        [
            {"annotation_id": "duplicate", "bbox": [0, 0, 1, 1]},
            {"annotation_id": "duplicate", "bbox": [2, 0, 1, 1]},
        ],
        [
            {
                "annotation_id": "gap",
                "class_type": "gap",
                "bbox": [0, 0, 1, 1],
            }
        ],
    ),
)
def test_reconstruct_realogram_rejects_incomplete_or_invalid_inputs(facings):
    with pytest.raises(ValueError):
        reconstruct_realogram(facings, observation_complete=True)


@pytest.mark.parametrize(
    "arguments",
    (
        {"observation_complete": 1},
        {"observation_complete": True, "view": "fisheye"},
    ),
)
def test_reconstruct_realogram_rejects_invalid_scope(arguments):
    with pytest.raises(ValueError):
        reconstruct_realogram(
            [{"annotation_id": "facing", "bbox": [0, 0, 1, 1]}],
            **arguments,
        )


def test_planogram_comparison_reports_compliance_and_noncompliance_states():
    result = compare_planogram(
        planogram=_planogram(),
        fixture_id="fixture-1",
        captured_at=datetime(2026, 1, 15, tzinfo=UTC),
        facings=[
            _planogram_facing("a-1", "sku-a", "slot-a"),
            _planogram_facing("a-2", "sku-a", "slot-a"),
            _planogram_facing("b-1", "sku-b", "slot-b"),
            _planogram_facing("c-elsewhere", "sku-c", "other-slot"),
        ],
        observation_complete=True,
        reviewed_slot_ids=["slot-a", "slot-b", "slot-c", "slot-d"],
    )

    assert result["status"] == "complete"
    assert result["eligible_expected_slots"] == 4
    assert result["unknown_slots"] == 0
    assert result["compliant_slots"] == 1
    assert result["compliance_rate"] == 0.25
    assert {
        slot["slot_id"]: (slot["state"], slot["missing_facings"])
        for slot in result["slots"]
    } == {
        "slot-a": ("present_compliant", 0),
        "slot-b": ("present_insufficient", 2),
        "slot-c": ("present_misplaced", 1),
        "slot-d": ("shelf_out_of_stock", 2),
    }


def test_planogram_comparison_excludes_unknown_slot_from_denominator():
    result = compare_planogram(
        planogram={
            **_planogram(),
            "slots": [_planogram()["slots"][0]],
        },
        fixture_id="fixture-1",
        captured_at=datetime(2026, 1, 15, tzinfo=UTC),
        facings=[
            {
                "annotation_id": "unknown-facing",
                "sku_id": None,
                "slot_id": "slot-a",
                "review_state": "accepted",
                "identity_state": "unknown",
            }
        ],
        observation_complete=True,
        reviewed_slot_ids=["slot-a"],
    )

    assert result["status"] == "partial"
    assert result["eligible_expected_slots"] == 0
    assert result["unknown_slots"] == 1
    assert result["compliance_rate"] is None
    assert result["slots"][0]["state"] == "unknown"
    assert result["slots"][0]["reason"] == "slot_identity_incomplete"


def test_planogram_comparison_is_disabled_without_effective_reference():
    missing = compare_planogram(
        planogram=None,
        fixture_id="fixture-1",
        captured_at=datetime(2026, 1, 15, tzinfo=UTC),
        facings=[],
        observation_complete=True,
        reviewed_slot_ids=[],
    )
    mismatched = compare_planogram(
        planogram={**_planogram(), "fixture_id": "other-fixture"},
        fixture_id="fixture-1",
        captured_at=datetime(2026, 1, 15, tzinfo=UTC),
        facings=[],
        observation_complete=True,
        reviewed_slot_ids=[],
    )
    expired = compare_planogram(
        planogram=_planogram(),
        fixture_id="fixture-1",
        captured_at=datetime(2027, 1, 1, tzinfo=UTC),
        facings=[],
        observation_complete=True,
        reviewed_slot_ids=[],
    )

    assert missing["status"] == "unsupported"
    assert missing["reason"] == "no_planogram_reference"
    assert mismatched["reason"] == "fixture_mismatch"
    assert expired["reason"] == "planogram_not_effective"
    assert expired["compliance_rate"] is None


@pytest.mark.parametrize(
    "changes",
    (
        {"effective_from": datetime(2026, 1, 1)},
        {"effective_to": datetime(2025, 12, 31, tzinfo=UTC)},
        {"slots": []},
        {
            "slots": [
                {
                    "slot_id": "duplicate",
                    "shelf_row_id": 0,
                    "sku_id": "sku-a",
                    "expected_facings": 1,
                },
                {
                    "slot_id": "duplicate",
                    "shelf_row_id": 0,
                    "sku_id": "sku-b",
                    "expected_facings": 1,
                },
            ]
        },
    ),
)
def test_planogram_comparison_rejects_invalid_reference(changes):
    with pytest.raises(ValueError):
        compare_planogram(
            planogram={**_planogram(), **changes},
            fixture_id="fixture-1",
            captured_at=datetime(2026, 1, 15, tzinfo=UTC),
            facings=[],
            observation_complete=True,
            reviewed_slot_ids=[],
        )


@pytest.mark.parametrize(
    "facings",
    (
        [
            {
                "annotation_id": "duplicate",
                "sku_id": "sku-a",
                "slot_id": "slot-a",
                "review_state": "accepted",
                "identity_state": "known",
            },
            {
                "annotation_id": "duplicate",
                "sku_id": "sku-a",
                "slot_id": "slot-a",
                "review_state": "accepted",
                "identity_state": "known",
            },
        ],
        [
            {
                "annotation_id": "unknown-with-sku",
                "sku_id": "sku-a",
                "slot_id": "slot-a",
                "review_state": "accepted",
                "identity_state": "unknown",
            }
        ],
        [
            {
                "annotation_id": "gap",
                "sku_id": "sku-a",
                "slot_id": "slot-a",
                "review_state": "accepted",
                "identity_state": "known",
                "class_type": "gap",
            }
        ],
    ),
)
def test_planogram_comparison_rejects_invalid_facings(facings):
    with pytest.raises(ValueError):
        compare_planogram(
            planogram={
                **_planogram(),
                "slots": [_planogram()["slots"][0]],
            },
            fixture_id="fixture-1",
            captured_at=datetime(2026, 1, 15, tzinfo=UTC),
            facings=facings,
            observation_complete=True,
            reviewed_slot_ids=["slot-a"],
        )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            {
                "expected_facings": None,
                "observed_in_expected_slot": 0,
                "observed_in_fixture": 0,
                "expectation_authoritative": False,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "not_observed", "missing_facings": None},
        ),
        (
            {
                "expected_facings": 2,
                "observed_in_expected_slot": 0,
                "observed_in_fixture": 0,
                "expectation_authoritative": False,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "possible_absence", "missing_facings": None},
        ),
        (
            {
                "expected_facings": 2,
                "observed_in_expected_slot": 0,
                "observed_in_fixture": 0,
                "expectation_authoritative": True,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "shelf_out_of_stock", "missing_facings": 2},
        ),
        (
            {
                "expected_facings": 3,
                "observed_in_expected_slot": 1,
                "observed_in_fixture": 1,
                "expectation_authoritative": True,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "present_insufficient", "missing_facings": 2},
        ),
        (
            {
                "expected_facings": 2,
                "observed_in_expected_slot": 0,
                "observed_in_fixture": 1,
                "expectation_authoritative": True,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "present_misplaced", "missing_facings": 2},
        ),
        (
            {
                "expected_facings": 2,
                "observed_in_expected_slot": 1,
                "observed_in_fixture": 1,
                "expectation_authoritative": False,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "present", "missing_facings": None},
        ),
        (
            {
                "expected_facings": 2,
                "observed_in_expected_slot": 2,
                "observed_in_fixture": 2,
                "expectation_authoritative": True,
                "observation_complete": True,
                "expected_slot_observed": True,
            },
            {"state": "present_compliant", "missing_facings": 0},
        ),
    ),
)
def test_classify_shelf_availability(arguments, expected):
    assert classify_shelf_availability(**arguments) == expected


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "expected_facings": -1,
            "observed_in_expected_slot": 0,
            "observed_in_fixture": 0,
        },
        {
            "expected_facings": 1.5,
            "observed_in_expected_slot": 0,
            "observed_in_fixture": 0,
        },
        {
            "expected_facings": 1,
            "observed_in_expected_slot": 2,
            "observed_in_fixture": 1,
        },
        {
            "expected_facings": True,
            "observed_in_expected_slot": 0,
            "observed_in_fixture": 0,
        },
    ),
)
def test_classify_shelf_availability_rejects_invalid_counts(arguments):
    with pytest.raises(ValueError):
        classify_shelf_availability(
            expectation_authoritative=True,
            observation_complete=True,
            expected_slot_observed=True,
            **arguments,
        )


def _planogram():
    return {
        "planogram_id": "planogram-1",
        "fixture_id": "fixture-1",
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "effective_to": datetime(2026, 2, 1, tzinfo=UTC),
        "slots": [
            {
                "slot_id": "slot-a",
                "shelf_row_id": 0,
                "sku_id": "sku-a",
                "expected_facings": 2,
            },
            {
                "slot_id": "slot-b",
                "shelf_row_id": 0,
                "sku_id": "sku-b",
                "expected_facings": 3,
            },
            {
                "slot_id": "slot-c",
                "shelf_row_id": 1,
                "sku_id": "sku-c",
                "expected_facings": 1,
            },
            {
                "slot_id": "slot-d",
                "shelf_row_id": 1,
                "sku_id": "sku-d",
                "expected_facings": 2,
            },
        ],
    }


def _planogram_facing(annotation_id, sku_id, slot_id):
    return {
        "annotation_id": annotation_id,
        "sku_id": sku_id,
        "slot_id": slot_id,
        "review_state": "accepted",
        "identity_state": "known",
    }
