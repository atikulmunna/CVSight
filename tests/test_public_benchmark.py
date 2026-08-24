import json
import re
from pathlib import Path
from typing import Any

PUBLIC_BENCHMARK = Path(__file__).parents[1] / "docs" / "benchmarks" / "release-0.1.0.json"
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
SECRET_KEY_PARTS = ("password", "secret", "token", "credential")


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        keys = list(value)
        return keys + [nested for item in value.values() for nested in _walk_keys(item)]
    if isinstance(value, list):
        return [nested for item in value for nested in _walk_keys(item)]
    return []


def test_public_benchmark_is_valid_and_sanitized() -> None:
    raw = PUBLIC_BENCHMARK.read_text(encoding="utf-8")
    report = json.loads(raw)

    assert report["schema"] == "cvsight-public-benchmark/v1"
    assert report["release"] == "0.1.0"
    assert re.fullmatch(r"[0-9a-f]{40}", report["source_commit"])
    assert report["private_data_included"] is False
    assert report["release_gates_passed"] is True
    assert all(
        report[section]["passed"]
        for section in (
            "install",
            "functional_checks",
            "scale",
            "canvas",
            "recovery",
            "security",
            "licenses",
        )
    )
    assert report["scale"]["configured_gates_passed"] == 18
    assert "C:\\" not in raw
    assert "benchmark-local" not in raw
    assert UUID_PATTERN.search(raw) is None
    assert not any(
        part in key.lower() for key in _walk_keys(report) for part in SECRET_KEY_PARTS
    )


def test_exploratory_models_are_not_release_accuracy_claims() -> None:
    report = json.loads(PUBLIC_BENCHMARK.read_text(encoding="utf-8"))
    models = report["exploratory_models"]

    assert models["release_accuracy_evidence"] is False
    assert models["clip_recognition"]["status"] == "rejected_for_automatic_assignment"
    assert models["clip_propagation"]["status"] == "rejected_for_automatic_propagation"
