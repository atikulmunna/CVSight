from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from benchmark_tool.gap_dataset import (
    GapDatasetRecord,
    assign_capture_groups,
    derive_gap_candidates,
    estimate_interior_gap_score,
    parse_source_identity,
    read_normalized_polygon_boxes,
    select_gap_benchmark,
)


def test_parse_source_identity_collapses_retake_variants() -> None:
    identity = parse_source_identity(
        "Skin_Cleansing_retake_3_20260802_124535_18a7afa1_jpg.rf."
        "b222c1ec857cac95bf14df1feea3f3a2"
    )

    assert identity == {
        "family": "Skin_Cleansing",
        "captured_at": datetime(2026, 8, 2, 12, 45, 35),
        "source_id": "18a7afa1",
        "roboflow_id": "b222c1ec857cac95bf14df1feea3f3a2",
    }


def test_parse_source_identity_rejects_unknown_names() -> None:
    with pytest.raises(ValueError):
        parse_source_identity("renamed-image")


def test_polygon_boxes_are_validated(tmp_path: Path) -> None:
    label = tmp_path / "label.txt"
    label.write_text("2 0.1 0.2 0.3 0.2 0.3 0.6 0.1 0.6\n", encoding="utf-8")

    box = read_normalized_polygon_boxes(label)[0]
    assert box == pytest.approx([0.1, 0.2, 0.2, 0.4])

    label.write_text("2 0.1 0.2 1.2 0.2 0.3 0.6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bounds"):
        read_normalized_polygon_boxes(label)


def test_gap_score_uses_interior_space_relative_to_product_width() -> None:
    boxes = [
        [0.1, 0.1, 0.1, 0.2],
        [0.2, 0.1, 0.1, 0.2],
        [0.6, 0.1, 0.1, 0.2],
    ]

    assert estimate_interior_gap_score(boxes) == pytest.approx(3.0)
    assert estimate_interior_gap_score(boxes[:1]) == 0


def test_gap_candidates_are_row_assigned_and_exclude_outer_space() -> None:
    candidates = derive_gap_candidates(
        [
            [0.1, 0.1, 0.1, 0.2],
            [0.2, 0.1, 0.1, 0.2],
            [0.6, 0.1, 0.1, 0.2],
            [0.1, 0.6, 0.1, 0.2],
            [0.25, 0.6, 0.1, 0.2],
        ]
    )

    assert candidates == [
        {
            "bbox": pytest.approx([0.3, 0.1, 0.3, 0.2]),
            "shelf_row_id": 0,
            "score": pytest.approx(1.0),
        },
        {
            "bbox": pytest.approx([0.2, 0.6, 0.05, 0.2]),
            "shelf_row_id": 1,
            "score": pytest.approx(1 / 6),
        },
    ]


@pytest.mark.parametrize(
    "boxes",
    (
        [[0.1, 0.2, 0.3]],
        [[0.9, 0.2, 0.2, 0.3]],
        [[0.1, 0.2, float("nan"), 0.3]],
    ),
)
def test_gap_candidates_reject_invalid_normalized_boxes(boxes) -> None:
    with pytest.raises(ValueError, match="normalized boxes"):
        derive_gap_candidates(boxes)


def test_capture_groups_join_temporal_and_perceptual_neighbors() -> None:
    start = datetime(2026, 8, 1, 10, 0, 0)
    records = [
        _record("a", start, difference_hash=0),
        _record("b", start + timedelta(seconds=100), difference_hash=2**255),
        _record("c", start + timedelta(hours=1), difference_hash=0),
        _record("d", start + timedelta(hours=2), difference_hash=(2**256 - 1)),
    ]

    grouped = assign_capture_groups(records, maximum_hash_distance=0)

    assert grouped[0].capture_group == grouped[1].capture_group
    assert grouped[0].capture_group == grouped[2].capture_group
    assert grouped[3].capture_group != grouped[0].capture_group


def test_selection_is_exact_deterministic_and_leakage_safe() -> None:
    records = [
        GapDatasetRecord(
            **{
                **_record(str(index), datetime(2026, 8, 1, 10, index)).__dict__,
                "capture_group": f"group-{index}",
                "gap_score": float(index),
            }
        )
        for index in range(12)
    ]

    first = select_gap_benchmark(records, validation_count=4, test_count=6)
    second = select_gap_benchmark(list(reversed(records)), validation_count=4, test_count=6)

    assert first == second
    assert sum(record.split == "validation" for record in first) == 4
    assert sum(record.split == "test" for record in first) == 6
    assert len({record.capture_group for record in first}) == 10


def test_selection_requires_independent_groups() -> None:
    records = [
        GapDatasetRecord(
            **{
                **_record("a", datetime(2026, 8, 1, 10)).__dict__,
                "capture_group": "same",
            }
        ),
        GapDatasetRecord(
            **{
                **_record("b", datetime(2026, 8, 1, 11)).__dict__,
                "capture_group": "same",
            }
        ),
    ]

    with pytest.raises(ValueError, match="independent"):
        select_gap_benchmark(records, validation_count=1, test_count=1)


def _record(
    identifier: str,
    captured_at: datetime,
    *,
    difference_hash: int = 0,
) -> GapDatasetRecord:
    return GapDatasetRecord(
        image_path=Path(f"{identifier}.jpg"),
        label_path=Path(f"{identifier}.txt"),
        family="Skin_Care",
        captured_at=captured_at,
        source_id=identifier,
        content_sha256=identifier * 64,
        difference_hash=difference_hash,
        width=100,
        height=100,
        annotation_count=2,
        gap_score=1.0,
    )
