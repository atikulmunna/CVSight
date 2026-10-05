"""Assign train, valid, and test splits for dataset exports without leaking related photos."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

SPLITS = ("train", "valid", "test")
# Photos sharing any of these values stay in one split, the boundary the training
# preparation enforces, so a near-identical shelf photo is never tested on its own twin.
SPLIT_BOUNDARY_KEYS = ("near_duplicate_group", "capture_session_id", "store_id", "fixture_id")
_SPLIT_NAMES = {
    "train": "train",
    "training": "train",
    "valid": "valid",
    "val": "valid",
    "validation": "valid",
    "test": "test",
    "testing": "test",
}


class SplitRatioError(ValueError):
    """Raised when requested split percentages cannot be used."""


@dataclass(frozen=True)
class SplitRatios:
    train: int
    valid: int
    test: int

    def as_dict(self) -> dict[str, int]:
        return {"train": self.train, "valid": self.valid, "test": self.test}


@dataclass(frozen=True)
class SplitCandidate:
    image_id: str
    recorded_split: str | None
    # Values such as "store_id:17" or "content_sha256:ab12..." tying photos together.
    boundaries: tuple[str, ...]


def parse_split_ratios(text: str) -> SplitRatios:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SplitRatioError("split must be three whole percentages, such as 70,20,10")
    train, valid, test = (int(part) for part in parts)
    if train + valid + test != 100 or train == 0:
        raise SplitRatioError("split percentages must add up to 100 and include training")
    return SplitRatios(train, valid, test)


def normalize_split(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _SPLIT_NAMES.get(value.strip().lower())


def assign_splits(candidates: Sequence[SplitCandidate], ratios: SplitRatios) -> dict[str, str]:
    """Map each image id to train, valid, or test.

    A split already recorded for an image is kept, and related photos follow it. Other
    related photos move as one group, and each group goes to the split furthest below
    its share of all images, largest groups first so the ratios come out close.
    """
    assigned: dict[str, str] = {}
    pending: list[list[SplitCandidate]] = []
    for group in _related_groups(candidates):
        recorded = sorted({c.recorded_split for c in group if c.recorded_split is not None})
        if not recorded:
            pending.append(group)
            continue
        for candidate in group:
            assigned[candidate.image_id] = candidate.recorded_split or recorded[0]

    targets = {
        split: len(candidates) * percent / 100
        for split, percent in ratios.as_dict().items()
        if percent > 0
    }
    counts = Counter(assigned.values())
    for group in sorted(pending, key=_group_order):
        split = max(targets, key=lambda name: targets[name] - counts[name])
        for candidate in group:
            assigned[candidate.image_id] = split
        counts[split] += len(group)
    return assigned


def _related_groups(candidates: Sequence[SplitCandidate]) -> list[list[SplitCandidate]]:
    parent = list(range(len(candidates)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    first_with: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        for boundary in candidate.boundaries:
            other = first_with.setdefault(boundary, index)
            parent[root(index)] = root(other)
    groups: dict[int, list[SplitCandidate]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(root(index), []).append(candidate)
    return list(groups.values())


def _group_order(group: list[SplitCandidate]) -> tuple[int, str]:
    # Largest first for close ratios; a hash of the ids breaks ties without favoring
    # the order photos happened to be uploaded in.
    ids = ",".join(sorted(candidate.image_id for candidate in group))
    return -len(group), hashlib.sha256(ids.encode("utf-8")).hexdigest()
