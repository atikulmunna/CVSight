from __future__ import annotations

from collections import Counter

import pytest

from shelfsight_api.export_split import (
    SplitCandidate,
    SplitRatioError,
    SplitRatios,
    assign_splits,
    normalize_split,
    parse_split_ratios,
)

RATIOS = SplitRatios(70, 20, 10)


def candidate(
    image_id: str, *boundaries: str, recorded: str | None = None
) -> SplitCandidate:
    return SplitCandidate(image_id, recorded, (f"content_sha256:{image_id}", *boundaries))


def test_unrelated_photos_follow_the_requested_ratios() -> None:
    candidates = [candidate(f"image-{index:03d}") for index in range(100)]

    splits = assign_splits(candidates, RATIOS)

    assert Counter(splits.values()) == {"train": 70, "valid": 20, "test": 10}


def test_related_photos_never_cross_splits() -> None:
    store_a = [candidate(f"a-{index}", "store_id:a") for index in range(6)]
    # c-0 shares a session with a-0, so it is tied to every store A photo.
    linked = [candidate("c-0", "capture_session_id:s1")]
    store_a[0] = candidate("a-0", "store_id:a", "capture_session_id:s1")
    others = [candidate(f"b-{index}") for index in range(14)]

    splits = assign_splits([*store_a, *linked, *others], RATIOS)

    assert len({splits[c.image_id] for c in [*store_a, *linked]}) == 1


def test_recorded_splits_are_kept_and_pull_related_photos_along() -> None:
    candidates = [
        candidate("imported-test", "store_id:x", recorded="test"),
        candidate("same-store", "store_id:x"),
        *[candidate(f"other-{index}") for index in range(8)],
    ]

    splits = assign_splits(candidates, RATIOS)

    assert splits["imported-test"] == "test"
    assert splits["same-store"] == "test"


def test_a_zero_percent_split_receives_nothing() -> None:
    candidates = [candidate(f"image-{index}") for index in range(10)]

    splits = assign_splits(candidates, SplitRatios(80, 20, 0))

    assert "test" not in splits.values()


def test_assignment_is_deterministic_regardless_of_input_order() -> None:
    candidates = [candidate(f"image-{index}") for index in range(30)]

    assert assign_splits(candidates, RATIOS) == assign_splits(candidates[::-1], RATIOS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("70,20,10", SplitRatios(70, 20, 10)), (" 80, 20, 0 ", SplitRatios(80, 20, 0))],
)
def test_split_ratios_parse(value: str, expected: SplitRatios) -> None:
    assert parse_split_ratios(value) == expected


@pytest.mark.parametrize("value", ["70,20", "70,20,20", "0,50,50", "70,20,x", "-10,60,50"])
def test_unusable_split_ratios_are_refused(value: str) -> None:
    with pytest.raises(SplitRatioError):
        parse_split_ratios(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Valid", "valid"), ("val", "valid"), ("validation", "valid"), ("TRAIN", "train"),
     ("unsplit", None), (None, None)],
)
def test_recorded_split_names_are_normalized(value: object, expected: str | None) -> None:
    assert normalize_split(value) == expected
