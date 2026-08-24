import pytest

from benchmark_tool.recognition import (
    calibrate_unknown_threshold,
    cosine_similarity,
    evaluate_rankings,
    normalize_embedding,
    rank_skus,
)


def test_normalize_embedding_and_cosine_similarity():
    assert normalize_embedding([3, 4]) == pytest.approx([0.6, 0.8])
    assert cosine_similarity([1, 0], [0, 2]) == pytest.approx(0)
    assert cosine_similarity([1, 1], [2, 2]) == pytest.approx(1)


def test_normalize_embedding_does_not_require_scalar_truth_testing():
    class Vector(list):
        def __bool__(self):
            raise ValueError("ambiguous truth value")

    assert normalize_embedding(Vector([3, 4])) == pytest.approx([0.6, 0.8])


@pytest.mark.parametrize(
    "embedding",
    ([], [0, 0], [float("nan"), 1], [float("inf"), 1]),
)
def test_normalize_embedding_rejects_invalid_values(embedding):
    with pytest.raises(ValueError):
        normalize_embedding(embedding)


def test_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="dimensions"):
        cosine_similarity([1, 0], [1, 0, 0])


def test_rank_skus_uses_best_exemplar_and_stable_sku_tie_break():
    gallery = {
        "sku_b": [[1, 0], [0, 1]],
        "sku_a": [[1, 0]],
        "sku_c": [[-1, 0]],
    }

    ranking = rank_skus([1, 0], gallery)

    assert ranking == [
        ("sku_a", pytest.approx(1)),
        ("sku_b", pytest.approx(1)),
        ("sku_c", pytest.approx(-1)),
    ]


@pytest.mark.parametrize(
    "gallery",
    ({}, {"": [[1, 0]]}, {"sku": []}, {"sku": [[1, 0, 0]]}),
)
def test_rank_skus_rejects_invalid_gallery(gallery):
    with pytest.raises(ValueError):
        rank_skus([1, 0], gallery)


def test_calibrate_unknown_threshold_uses_balanced_validation_accuracy():
    result = calibrate_unknown_threshold(
        known_scores=[0.8, 0.9],
        unknown_scores=[0.1, 0.2],
    )

    assert result["threshold"] == pytest.approx(0.5)
    assert result["balanced_accuracy"] == 1
    assert result["known_acceptance"] == 1
    assert result["unknown_rejection"] == 1


@pytest.mark.parametrize(
    ("known", "unknown"),
    (([], [0.1]), ([0.9], []), ([float("nan")], [0.1])),
)
def test_calibrate_unknown_threshold_rejects_invalid_scores(known, unknown):
    with pytest.raises(ValueError):
        calibrate_unknown_threshold(known, unknown)


def test_evaluate_rankings_reports_open_set_and_hard_pair_metrics():
    queries = [
        {
            "sku_id": "a",
            "is_unknown": False,
            "ranking": [("a", 0.9), ("b", 0.7)],
            "hard_pair_skus": ["b"],
        },
        {
            "sku_id": "b",
            "is_unknown": False,
            "ranking": [("a", 0.8), ("b", 0.7)],
            "hard_pair_skus": ["a"],
        },
        {
            "sku_id": "unknown",
            "is_unknown": True,
            "ranking": [("a", 0.6), ("b", 0.5)],
        },
    ]

    result = evaluate_rankings(queries, unknown_threshold=0.75, top_k=2)

    assert result["top_1_accuracy"] == pytest.approx(0.5)
    assert result["top_k_accuracy"] == 1
    assert result["known_false_unknown_rate"] == 0
    assert result["unknown_false_acceptance_rate"] == 0
    assert result["hard_pair_confusion_rate"] == pytest.approx(0.5)


def test_evaluate_rankings_rejects_invalid_options_and_empty_ranking():
    with pytest.raises(ValueError):
        evaluate_rankings([], unknown_threshold=float("nan"))
    with pytest.raises(ValueError):
        evaluate_rankings([], unknown_threshold=0.5, top_k=0)
    with pytest.raises(ValueError):
        evaluate_rankings(
            [{"sku_id": "a", "is_unknown": False, "ranking": []}],
            unknown_threshold=0.5,
        )
