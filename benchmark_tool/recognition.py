import math
from collections.abc import Mapping, Sequence
from typing import Any


def normalize_embedding(embedding: Sequence[float]) -> list[float]:
    if len(embedding) == 0:
        raise ValueError("embedding must not be empty")
    values = [float(value) for value in embedding]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude == 0:
        raise ValueError("embedding magnitude must be greater than zero")
    return [value / magnitude for value in values]


def cosine_similarity(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second):
        raise ValueError("embedding dimensions must match")
    first_normalized = normalize_embedding(first)
    second_normalized = normalize_embedding(second)
    return sum(
        first_value * second_value
        for first_value, second_value in zip(
            first_normalized,
            second_normalized,
            strict=True,
        )
    )


def rank_skus(
    query_embedding: Sequence[float],
    gallery: Mapping[str, Sequence[Sequence[float]]],
) -> list[tuple[str, float]]:
    if not gallery:
        raise ValueError("gallery must not be empty")
    query = normalize_embedding(query_embedding)
    ranked = []
    for sku_id, exemplars in gallery.items():
        if not sku_id:
            raise ValueError("gallery SKU identifiers must not be empty")
        if not exemplars:
            raise ValueError(f"gallery SKU {sku_id} has no exemplars")
        scores = [
            sum(
                query_value * exemplar_value
                for query_value, exemplar_value in zip(
                    query,
                    normalize_embedding(exemplar),
                    strict=True,
                )
            )
            for exemplar in exemplars
        ]
        ranked.append((sku_id, max(scores)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def calibrate_unknown_threshold(
    known_scores: Sequence[float],
    unknown_scores: Sequence[float],
) -> dict[str, float]:
    if not known_scores or not unknown_scores:
        raise ValueError("known and unknown validation scores must not be empty")
    scores = [float(score) for score in (*known_scores, *unknown_scores)]
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("validation scores must be finite")
    unique_scores = sorted(set(scores))
    candidates = [unique_scores[0] - 1e-12]
    candidates.extend(
        (left + right) / 2
        for left, right in zip(unique_scores, unique_scores[1:], strict=False)
    )
    candidates.append(unique_scores[-1] + 1e-12)

    best = None
    for threshold in candidates:
        known_acceptance = sum(score >= threshold for score in known_scores) / len(
            known_scores
        )
        unknown_rejection = sum(score < threshold for score in unknown_scores) / len(
            unknown_scores
        )
        balanced_accuracy = (known_acceptance + unknown_rejection) / 2
        candidate = {
            "threshold": threshold,
            "balanced_accuracy": balanced_accuracy,
            "known_acceptance": known_acceptance,
            "unknown_rejection": unknown_rejection,
        }
        comparison = (
            balanced_accuracy,
            unknown_rejection,
            known_acceptance,
            threshold,
        )
        if best is None or comparison > best[0]:
            best = (comparison, candidate)
    return best[1]


def evaluate_rankings(
    queries: Sequence[Mapping[str, Any]],
    unknown_threshold: float,
    top_k: int = 5,
) -> dict[str, int | float | None]:
    if not math.isfinite(unknown_threshold):
        raise ValueError("unknown threshold must be finite")
    if top_k < 1:
        raise ValueError("top_k must be at least one")

    known_count = 0
    unknown_count = 0
    top_1_correct = 0
    top_k_correct = 0
    known_false_unknown = 0
    unknown_false_acceptance = 0
    hard_pair_count = 0
    hard_pair_confusions = 0

    for query in queries:
        ranking = query["ranking"]
        if not ranking:
            raise ValueError("query ranking must not be empty")
        best_sku, best_score = ranking[0]
        predicted_sku = best_sku if best_score >= unknown_threshold else None
        if query["is_unknown"]:
            unknown_count += 1
            unknown_false_acceptance += predicted_sku is not None
            continue

        known_count += 1
        true_sku = query["sku_id"]
        top_1_correct += best_sku == true_sku
        top_k_correct += true_sku in [sku_id for sku_id, _ in ranking[:top_k]]
        known_false_unknown += predicted_sku is None

        hard_pair_skus = set(query.get("hard_pair_skus", ()))
        if hard_pair_skus:
            hard_pair_count += 1
            hard_pair_confusions += (
                predicted_sku is not None
                and predicted_sku != true_sku
                and predicted_sku in hard_pair_skus
            )

    return {
        "known_queries": known_count,
        "unknown_queries": unknown_count,
        "top_1_correct": top_1_correct,
        "top_1_accuracy": top_1_correct / known_count if known_count else None,
        "top_k": top_k,
        "top_k_correct": top_k_correct,
        "top_k_accuracy": top_k_correct / known_count if known_count else None,
        "known_false_unknown": known_false_unknown,
        "known_false_unknown_rate": (
            known_false_unknown / known_count if known_count else None
        ),
        "unknown_false_acceptance": unknown_false_acceptance,
        "unknown_false_acceptance_rate": (
            unknown_false_acceptance / unknown_count if unknown_count else None
        ),
        "hard_pair_queries": hard_pair_count,
        "hard_pair_confusions": hard_pair_confusions,
        "hard_pair_confusion_rate": (
            hard_pair_confusions / hard_pair_count if hard_pair_count else None
        ),
    }
