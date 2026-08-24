import math
from typing import Any

import pytest

from benchmark_tool.scale import GATES, evaluate_gates
from benchmark_tool.scale_fixture import scale_vector


def _passing_report() -> dict[str, Any]:
    latency = {"p95_ms": 1.0}
    return {
        "api": {
            "image_metadata": latency.copy(),
            "dense_annotations": latency.copy(),
            "catalog_full": latency.copy(),
            "catalog_search": latency.copy(),
            "failure_paths": latency.copy(),
            "unexpected_responses": 0,
        },
        "jobs": {"throughput_per_second": 100.0, "p95_ms": 1.0, "errors": 0},
        "similarity": {"p95_ms": 1.0, "errors": 0},
        "exports": {
            "detection": latency.copy(),
            "recognition": latency.copy(),
            "errors": 0,
        },
        "storage": {"seed_growth_bytes": 1, "workload_growth_bytes": 1},
        "memory": {"traced_peak_bytes": 1, "retained_growth_bytes": 1},
    }


def test_scale_vector_is_deterministic_and_normalized() -> None:
    first = scale_vector(31, 512)

    assert first == scale_vector(31, 512)
    assert first != scale_vector(32, 512)
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)


def test_scale_vector_rejects_an_invalid_dimension() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        scale_vector(0, 1)


def test_evaluate_gates_accepts_a_passing_report() -> None:
    gates = evaluate_gates(_passing_report())

    assert len(gates) == len(GATES) + 4
    assert all(gate["passed"] for gate in gates)


def test_evaluate_gates_reports_latency_and_error_failures() -> None:
    report = _passing_report()
    report["api"]["catalog_search"]["p95_ms"] = GATES["api.catalog_search.p95_ms"] + 1
    report["exports"]["errors"] = 1

    failures = {
        gate["name"] for gate in evaluate_gates(report) if not gate["passed"]
    }

    assert failures == {"api.catalog_search.p95_ms", "exports.errors"}
