import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

UNKNOWN_GROUP = "unknown"


def count_share_of_shelf(groups: Sequence[str | None]) -> dict[str, Any]:
    if not groups:
        raise ValueError("at least one facing is required")
    normalized = [group if group else UNKNOWN_GROUP for group in groups]
    counts = Counter(normalized)
    total = len(normalized)
    return {
        "total_facings": total,
        "counts": dict(sorted(counts.items())),
        "shares": {
            group: count / total for group, count in sorted(counts.items())
        },
        "unknown_facings": counts[UNKNOWN_GROUP],
        "unknown_share": counts[UNKNOWN_GROUP] / total,
    }


def image_area_share_of_shelf(
    facings: Sequence[tuple[str | None, float]],
) -> dict[str, Any]:
    if not facings:
        raise ValueError("at least one facing is required")
    areas: defaultdict[str, float] = defaultdict(float)
    for group, area in facings:
        numeric_area = float(area)
        if not math.isfinite(numeric_area) or numeric_area <= 0:
            raise ValueError("facing areas must be finite and greater than zero")
        areas[group if group else UNKNOWN_GROUP] += numeric_area
    total_area = sum(areas.values())
    return {
        "total_image_area": total_area,
        "areas": dict(sorted(areas.items())),
        "shares": {
            group: area / total_area for group, area in sorted(areas.items())
        },
        "unknown_area_share": areas[UNKNOWN_GROUP] / total_area,
    }


def summarize_share_of_shelf(
    facings: Sequence[Mapping[str, Any]],
    *,
    observation_complete: bool,
    view: Literal["frontal", "mild_oblique", "severe_oblique"] | None = "frontal",
) -> dict[str, Any]:
    """Summarize scoped facing share without implying physical shelf width."""
    if not isinstance(observation_complete, bool):
        raise ValueError("observation_complete must be a boolean")
    if view not in {None, "frontal", "mild_oblique", "severe_oblique"}:
        raise ValueError("view must be frontal, mild_oblique, severe_oblique, or null")
    normalized = [_normalize_share_facing(facing) for facing in facings]
    annotation_ids = [facing["annotation_id"] for facing in normalized]
    if len(set(annotation_ids)) != len(annotation_ids):
        raise ValueError("facing annotation identifiers must be unique")

    status = _share_status(normalized, observation_complete)
    count_result = count_share_of_shelf(
        [facing["group_id"] for facing in normalized]
    )
    count_share = {
        "metric": "count_share_of_shelf",
        "status": status,
        "perspective_sensitive": False,
        **count_result,
    }
    area_share: dict[str, Any]
    if view in {None, "severe_oblique"}:
        area_share = {
            "metric": "image_area_share",
            "status": "unsupported",
            "reason": (
                "capture_view_not_declared"
                if view is None
                else "severe_oblique_view"
            ),
            "perspective_sensitive": True,
            "cross_image_comparable": False,
            "physical_shelf_share": False,
        }
    else:
        area_result = image_area_share_of_shelf(
            [
                (facing["group_id"], facing["bbox"][2] * facing["bbox"][3])
                for facing in normalized
            ]
        )
        area_share = {
            "metric": "image_area_share",
            "status": status,
            "reason": None,
            "perspective_sensitive": True,
            "cross_image_comparable": False,
            "physical_shelf_share": False,
            **area_result,
        }
    return {
        "scope_status": status,
        "count_share": count_share,
        "image_area_share": area_share,
    }


def compare_planogram(
    *,
    planogram: Mapping[str, Any] | None,
    fixture_id: str,
    captured_at: datetime,
    facings: Sequence[Mapping[str, Any]],
    observation_complete: bool,
    reviewed_slot_ids: Sequence[str],
) -> dict[str, Any]:
    """Compare reviewed fixture facings with one time-effective planogram."""
    _non_empty_identifier(fixture_id, "fixture_id")
    _aware_datetime(captured_at, "captured_at")
    if not isinstance(observation_complete, bool):
        raise ValueError("observation_complete must be a boolean")
    reviewed_slot_values = [
        _non_empty_identifier(slot_id, "reviewed slot")
        for slot_id in reviewed_slot_ids
    ]
    if len(set(reviewed_slot_values)) != len(reviewed_slot_values):
        raise ValueError("reviewed slot identifiers must be unique")
    reviewed_slots = set(reviewed_slot_values)
    if planogram is None:
        return _unsupported_planogram("no_planogram_reference")

    planogram_id = _non_empty_identifier(planogram.get("planogram_id"), "planogram_id")
    planogram_fixture = _non_empty_identifier(
        planogram.get("fixture_id"), "planogram fixture_id"
    )
    effective_from = _aware_datetime(
        planogram.get("effective_from"), "effective_from"
    )
    effective_to_value = planogram.get("effective_to")
    effective_to = (
        _aware_datetime(effective_to_value, "effective_to")
        if effective_to_value is not None
        else None
    )
    if effective_to is not None and effective_to <= effective_from:
        raise ValueError("effective_to must be later than effective_from")
    if planogram_fixture != fixture_id:
        return _unsupported_planogram("fixture_mismatch", planogram_id)
    if captured_at < effective_from or (
        effective_to is not None and captured_at >= effective_to
    ):
        return _unsupported_planogram("planogram_not_effective", planogram_id)

    slots_value = planogram.get("slots")
    if (
        not isinstance(slots_value, Sequence)
        or isinstance(slots_value, (str, bytes))
        or not slots_value
    ):
        raise ValueError("planogram requires at least one expected slot")
    slots = [_normalize_planogram_slot(slot) for slot in slots_value]
    slot_ids = [slot["slot_id"] for slot in slots]
    if len(set(slot_ids)) != len(slot_ids):
        raise ValueError("planogram slot identifiers must be unique")

    normalized_facings = [_normalize_planogram_facing(facing) for facing in facings]
    facing_ids = [facing["annotation_id"] for facing in normalized_facings]
    if len(set(facing_ids)) != len(facing_ids):
        raise ValueError("facing annotation identifiers must be unique")

    comparisons = [
        _compare_planogram_slot(
            slot,
            normalized_facings,
            observation_complete=observation_complete,
            slot_reviewed=slot["slot_id"] in reviewed_slots,
        )
        for slot in slots
    ]
    eligible = [
        comparison
        for comparison in comparisons
        if comparison["state"] != "unknown"
    ]
    compliant = sum(
        comparison["state"] == "present_compliant" for comparison in eligible
    )
    unknown_count = len(comparisons) - len(eligible)
    return {
        "status": "complete" if unknown_count == 0 else "partial",
        "reason": None,
        "planogram_id": planogram_id,
        "fixture_id": fixture_id,
        "eligible_expected_slots": len(eligible),
        "unknown_slots": unknown_count,
        "compliant_slots": compliant,
        "compliance_rate": compliant / len(eligible) if eligible else None,
        "slots": comparisons,
    }


def _normalize_share_facing(facing: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_facing(dict(facing))
    group_id = facing.get("group_id")
    if group_id is not None:
        group_id = _non_empty_identifier(group_id, "group_id")
    identity_state = facing.get(
        "identity_state", "known" if group_id is not None else "unknown"
    )
    if identity_state not in {"known", "unknown", "conflicted"}:
        raise ValueError("identity_state must be known, unknown, or conflicted")
    return {
        **normalized,
        "group_id": group_id,
        "identity_state": identity_state,
    }


def _share_status(
    facings: Sequence[Mapping[str, Any]], observation_complete: bool
) -> str:
    if not observation_complete:
        return "partial"
    if any(
        facing["review_state"] != "accepted"
        or facing["identity_state"] == "conflicted"
        for facing in facings
    ):
        return "provisional"
    return "complete"


def _normalize_planogram_slot(slot: object) -> dict[str, Any]:
    if not isinstance(slot, Mapping):
        raise ValueError("every planogram slot must be an object")
    shelf_row_id = slot.get("shelf_row_id")
    expected_facings = slot.get("expected_facings")
    if (
        isinstance(shelf_row_id, bool)
        or not isinstance(shelf_row_id, int)
        or shelf_row_id < 0
    ):
        raise ValueError("planogram shelf_row_id must be a non-negative integer")
    if (
        isinstance(expected_facings, bool)
        or not isinstance(expected_facings, int)
        or expected_facings <= 0
    ):
        raise ValueError("expected_facings must be a positive integer")
    return {
        "slot_id": _non_empty_identifier(slot.get("slot_id"), "slot_id"),
        "shelf_row_id": shelf_row_id,
        "sku_id": _non_empty_identifier(slot.get("sku_id"), "sku_id"),
        "expected_facings": expected_facings,
    }


def _normalize_planogram_facing(facing: Mapping[str, Any]) -> dict[str, Any]:
    if facing.get("class_type", "product") != "product":
        raise ValueError("planogram comparison accepts product facings only")
    sku_id = facing.get("sku_id")
    slot_id = facing.get("slot_id")
    if sku_id is not None:
        sku_id = _non_empty_identifier(sku_id, "facing sku_id")
    if slot_id is not None:
        slot_id = _non_empty_identifier(slot_id, "facing slot_id")
    review_state = facing.get("review_state", "unreviewed")
    if review_state not in {"unreviewed", "accepted", "flagged"}:
        raise ValueError("review_state must be unreviewed, accepted, or flagged")
    identity_state = facing.get(
        "identity_state", "known" if sku_id is not None else "unknown"
    )
    if identity_state not in {"known", "unknown", "conflicted"}:
        raise ValueError("identity_state must be known, unknown, or conflicted")
    if identity_state == "known" and sku_id is None:
        raise ValueError("a known facing requires sku_id")
    if identity_state == "unknown" and sku_id is not None:
        raise ValueError("an unknown facing cannot have sku_id")
    return {
        "annotation_id": _non_empty_identifier(
            facing.get("annotation_id"), "annotation_id"
        ),
        "sku_id": sku_id,
        "slot_id": slot_id,
        "review_state": review_state,
        "identity_state": identity_state,
    }


def _compare_planogram_slot(
    slot: Mapping[str, Any],
    facings: Sequence[Mapping[str, Any]],
    *,
    observation_complete: bool,
    slot_reviewed: bool,
) -> dict[str, Any]:
    slot_facings = [facing for facing in facings if facing["slot_id"] == slot["slot_id"]]
    target_in_slot = [
        facing
        for facing in slot_facings
        if _is_confirmed_sku(facing, slot["sku_id"])
    ]
    target_in_fixture = [
        facing for facing in facings if _is_confirmed_sku(facing, slot["sku_id"])
    ]
    evidence = {
        "observed_in_expected_slot": [
            facing["annotation_id"] for facing in target_in_slot
        ],
        "observed_in_fixture": [
            facing["annotation_id"] for facing in target_in_fixture
        ],
    }
    if not observation_complete:
        return _unknown_planogram_slot(slot, evidence, "incomplete_observation")
    if not slot_reviewed:
        return _unknown_planogram_slot(slot, evidence, "expected_slot_not_reviewed")

    observed_in_slot = len(target_in_slot)
    observed_in_fixture = len(target_in_fixture)
    expected = int(slot["expected_facings"])
    if observed_in_slot >= expected:
        state = "present_compliant"
    elif _contains_uncertain_identity(slot_facings):
        return _unknown_planogram_slot(slot, evidence, "slot_identity_incomplete")
    elif observed_in_slot > 0:
        state = "present_insufficient"
    elif observed_in_fixture > 0:
        state = "present_misplaced"
    elif _contains_uncertain_identity(facings):
        return _unknown_planogram_slot(slot, evidence, "fixture_identity_incomplete")
    else:
        state = "shelf_out_of_stock"
    return {
        **slot,
        "state": state,
        "missing_facings": max(expected - observed_in_slot, 0),
        "reason": None,
        "evidence": evidence,
    }


def _is_confirmed_sku(facing: Mapping[str, Any], sku_id: str) -> bool:
    return bool(
        facing["sku_id"] == sku_id
        and facing["identity_state"] == "known"
        and facing["review_state"] == "accepted"
    )


def _contains_uncertain_identity(facings: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        facing["review_state"] != "accepted" or facing["identity_state"] != "known"
        for facing in facings
    )


def _unknown_planogram_slot(
    slot: Mapping[str, Any], evidence: dict[str, list[str]], reason: str
) -> dict[str, Any]:
    return {
        **slot,
        "state": "unknown",
        "missing_facings": None,
        "reason": reason,
        "evidence": evidence,
    }


def _unsupported_planogram(
    reason: str, planogram_id: str | None = None
) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "reason": reason,
        "planogram_id": planogram_id,
        "eligible_expected_slots": 0,
        "unknown_slots": 0,
        "compliant_slots": 0,
        "compliance_rate": None,
        "slots": [],
    }


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _non_empty_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def visible_gap_fraction(
    row_left: float,
    row_width: float,
    gaps: Sequence[tuple[float, float]],
) -> float:
    values = [float(row_left), float(row_width)]
    if not all(math.isfinite(value) for value in values) or row_width <= 0:
        raise ValueError("row geometry must be finite with width greater than zero")
    row_right = row_left + row_width
    intervals = []
    for gap_left, gap_width in gaps:
        left = float(gap_left)
        width = float(gap_width)
        if not math.isfinite(left) or not math.isfinite(width) or width <= 0:
            raise ValueError("gap geometry must be finite with width greater than zero")
        clipped_left = max(row_left, left)
        clipped_right = min(row_right, left + width)
        if clipped_right > clipped_left:
            intervals.append((clipped_left, clipped_right))

    covered = 0.0
    current_left: float | None = None
    current_right: float | None = None
    for left, right in sorted(intervals):
        if current_left is None or current_right is None:
            current_left, current_right = left, right
        elif left <= current_right:
            current_right = max(current_right, right)
        else:
            covered += current_right - current_left
            current_left, current_right = left, right
    if current_left is not None and current_right is not None:
        covered += current_right - current_left
    return covered / row_width


def order_realogram(
    facings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = defaultdict(list)
    for facing in facings:
        annotation_id = facing.get("annotation_id")
        row_id = facing.get("shelf_row_id")
        bbox = facing.get("bbox")
        if not annotation_id or row_id is None:
            raise ValueError("every facing requires annotation_id and shelf_row_id")
        if not isinstance(bbox, Sequence) or len(bbox) != 4:
            raise ValueError("every facing requires a four-value bbox")
        left, top, width, height = (float(value) for value in bbox)
        if (
            not all(math.isfinite(value) for value in (left, top, width, height))
            or width <= 0
            or height <= 0
        ):
            raise ValueError("facing bbox must be finite with positive size")
        rows[row_id].append(
            {
                **facing,
                "bbox": [left, top, width, height],
                "_center_x": left + width / 2,
            }
        )

    ordered_rows = []
    for row_id in sorted(rows):
        ordered_facings = sorted(
            rows[row_id],
            key=lambda facing: (facing["_center_x"], facing["annotation_id"]),
        )
        ordered_rows.append(
            {
                "shelf_row_id": row_id,
                "facings": [
                    {
                        key: value
                        for key, value in facing.items()
                        if key != "_center_x"
                    }
                    for facing in ordered_facings
                ],
            }
        )
    return ordered_rows


def reconstruct_realogram(
    facings: Sequence[dict[str, Any]],
    *,
    observation_complete: bool,
    view: Literal["frontal", "mild_oblique", "severe_oblique"] = "frontal",
) -> dict[str, Any]:
    """Build a visible realogram while preserving review uncertainty."""
    if not facings:
        raise ValueError("at least one facing is required")
    if not isinstance(observation_complete, bool):
        raise ValueError("observation_complete must be a boolean")
    if view not in {"frontal", "mild_oblique", "severe_oblique"}:
        raise ValueError("view must be frontal, mild_oblique, or severe_oblique")
    normalized = [_normalize_facing(facing) for facing in facings]
    annotation_ids = [facing["annotation_id"] for facing in normalized]
    if len(set(annotation_ids)) != len(annotation_ids):
        raise ValueError("facing annotation identifiers must be unique")
    if view == "severe_oblique":
        return {
            "status": "unsupported",
            "reason": "severe_oblique_view",
            "assignment_source": None,
            "rows": [],
        }

    assigned_count = sum(
        facing["shelf_row_id"] is not None for facing in normalized
    )
    if assigned_count not in {0, len(normalized)}:
        raise ValueError("shelf row assignments must be complete or entirely automatic")

    assignment_source = "reviewed" if assigned_count else "automatic"
    if not assigned_count:
        normalized = _assign_automatic_rows(normalized)

    status = _realogram_status(
        normalized,
        observation_complete=observation_complete,
        assignment_source=assignment_source,
    )
    return {
        "status": status,
        "reason": None,
        "assignment_source": assignment_source,
        "rows": order_realogram(normalized),
    }


def _normalize_facing(facing: dict[str, Any]) -> dict[str, Any]:
    annotation_id = facing.get("annotation_id")
    bbox = facing.get("bbox")
    row_id = facing.get("shelf_row_id")
    if not isinstance(annotation_id, str) or not annotation_id:
        raise ValueError("every facing requires a non-empty annotation_id")
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)) or len(bbox) != 4:
        raise ValueError("every facing requires a four-value bbox")
    left, top, width, height = (float(value) for value in bbox)
    if (
        not all(math.isfinite(value) for value in (left, top, width, height))
        or width <= 0
        or height <= 0
    ):
        raise ValueError("facing bbox must be finite with positive size")
    if row_id is not None and (
        isinstance(row_id, bool) or not isinstance(row_id, int) or row_id < 0
    ):
        raise ValueError("shelf_row_id must be a non-negative integer or null")
    review_state = facing.get("review_state", "unreviewed")
    if review_state not in {"unreviewed", "accepted", "flagged"}:
        raise ValueError("review_state must be unreviewed, accepted, or flagged")
    if facing.get("class_type", "product") != "product":
        raise ValueError("realograms accept product facings only")
    return {
        **facing,
        "bbox": [left, top, width, height],
        "shelf_row_id": row_id,
        "review_state": review_state,
    }


def _assign_automatic_rows(facings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        facings,
        key=lambda facing: (
            facing["bbox"][1] + facing["bbox"][3] / 2,
            facing["bbox"][0],
            facing["annotation_id"],
        ),
    )
    for facing in ordered:
        center_y = facing["bbox"][1] + facing["bbox"][3] / 2
        height = facing["bbox"][3]
        candidates = [
            row
            for row in rows
            if abs(row["center_y"] - center_y)
            <= max(8.0, min(row["average_height"], height) * 0.55)
        ]
        if not candidates:
            rows.append(
                {
                    "center_y": center_y,
                    "average_height": height,
                    "facings": [facing],
                }
            )
            continue
        row = min(candidates, key=lambda candidate: abs(candidate["center_y"] - center_y))
        count = len(row["facings"])
        row["facings"].append(facing)
        row["center_y"] = (row["center_y"] * count + center_y) / (count + 1)
        row["average_height"] = (
            row["average_height"] * count + height
        ) / (count + 1)

    assigned: list[dict[str, Any]] = []
    for row_id, row in enumerate(sorted(rows, key=lambda value: value["center_y"])):
        assigned.extend(
            {**facing, "shelf_row_id": row_id} for facing in row["facings"]
        )
    return assigned


def _realogram_status(
    facings: Sequence[dict[str, Any]],
    *,
    observation_complete: bool,
    assignment_source: str,
) -> str:
    if not observation_complete:
        return "partial"
    if assignment_source == "automatic" or any(
        facing["review_state"] != "accepted" for facing in facings
    ):
        return "provisional"
    return "complete"


def classify_shelf_availability(
    *,
    expected_facings: int | None,
    observed_in_expected_slot: int,
    observed_in_fixture: int,
    expectation_authoritative: bool,
    observation_complete: bool,
    expected_slot_observed: bool,
) -> dict[str, int | str | None]:
    counts = (expected_facings, observed_in_expected_slot, observed_in_fixture)
    if any(
        value is not None and (isinstance(value, bool) or value < 0)
        for value in counts
    ):
        raise ValueError("facing counts must be non-negative integers")
    if any(value is not None and not isinstance(value, int) for value in counts):
        raise ValueError("facing counts must be non-negative integers")
    if observed_in_expected_slot > observed_in_fixture:
        raise ValueError("expected-slot observations cannot exceed fixture observations")

    missing_facings = (
        max(expected_facings - observed_in_expected_slot, 0)
        if expected_facings is not None and expectation_authoritative
        else None
    )
    if observed_in_fixture > 0:
        if (
            expected_facings is None
            or expected_facings == 0
            or not expectation_authoritative
        ):
            state = "present"
        elif observed_in_expected_slot == 0:
            state = "present_misplaced"
        elif observed_in_expected_slot < expected_facings:
            state = "present_insufficient"
        else:
            state = "present_compliant"
        return {"state": state, "missing_facings": missing_facings}

    if expected_facings is None or expected_facings == 0:
        return {"state": "not_observed", "missing_facings": missing_facings}
    if not (
        expectation_authoritative
        and observation_complete
        and expected_slot_observed
    ):
        return {"state": "possible_absence", "missing_facings": missing_facings}
    return {"state": "shelf_out_of_stock", "missing_facings": missing_facings}
