from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import secrets
import socket
import sys
import threading
import time
import tracemalloc
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx
import uvicorn
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from benchmark_tool.evaluation import summarize_latencies
from benchmark_tool.scale_fixture import (
    DENSE_BOX_COUNT,
    EXPORT_ANNOTATION_COUNT,
    EXPORT_IMAGE_COUNT,
    IMAGE_COUNT,
    JOB_COUNT,
    SKU_COUNT,
    ScaleFixture,
    database_size_bytes,
    seed_scale_fixture,
)
from shelfsight_api.auth_service import hash_password
from shelfsight_api.database import get_engine
from shelfsight_api.export_service import (
    ExportVersionNotFrozenError,
    build_detection_export,
    build_recognition_export,
    validate_export_archive,
)
from shelfsight_api.job_service import claim_next_job, complete_job
from shelfsight_api.models import jobs
from shelfsight_api.recognition_service import get_annotation_sku_candidates

REPORT_SCHEMA = "cvsight-scale-report/v1"

GATES = {
    "api.image_metadata.p95_ms": 100.0,
    "api.dense_annotations.p95_ms": 500.0,
    "api.catalog_full.p95_ms": 750.0,
    "api.catalog_search.p95_ms": 250.0,
    "api.failure_paths.p95_ms": 250.0,
    "jobs.minimum_throughput_per_second": 50.0,
    "jobs.p95_ms": 150.0,
    "similarity.p95_ms": 250.0,
    "exports.detection.p95_ms": 3_000.0,
    "exports.recognition.p95_ms": 12_000.0,
    "storage.maximum_seed_bytes": 320 * 1024 * 1024,
    "storage.maximum_workload_growth_bytes": 32 * 1024 * 1024,
    "memory.maximum_peak_bytes": 512 * 1024 * 1024,
    "memory.maximum_retained_growth_bytes": 64 * 1024 * 1024,
}


@dataclass(frozen=True)
class HttpOutcome:
    latency_ms: float
    status_code: int
    count: int | None


def build_report(engine: Engine, media_root: Path) -> dict[str, Any]:
    size_before = database_size_bytes(engine)
    fixture, seed = seed_scale_fixture(engine, media_root)
    size_after_seed = database_size_bytes(engine)

    server, server_thread, base_url, password = _start_api_server()
    try:
        api = _measure_api(base_url, password, fixture)
        tracemalloc.start()
        gc.collect()
        memory_start, _ = tracemalloc.get_traced_memory()
        jobs_result = _measure_jobs(engine, fixture)
        similarity = _measure_similarity(engine, fixture)
        exports = _measure_exports(engine, media_root, fixture)
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        if tracemalloc.is_tracing():
            gc.collect()
            memory_end, memory_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

    size_final = database_size_bytes(engine)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "logical_cpu_count": os.cpu_count(),
            "postgresql": _database_version(engine),
        },
        "fixture": {
            "image_metadata_records": IMAGE_COUNT,
            "catalog_skus": SKU_COUNT,
            "dense_image_boxes": DENSE_BOX_COUNT,
            "queued_jobs": JOB_COUNT,
            "gallery_embeddings": SKU_COUNT,
            "export_images": EXPORT_IMAGE_COUNT,
            "export_annotations": EXPORT_ANNOTATION_COUNT,
        },
        "seed": seed,
        "api": api,
        "jobs": jobs_result,
        "similarity": similarity,
        "exports": exports,
        "storage": {
            "database_bytes_before": size_before,
            "database_bytes_after_seed": size_after_seed,
            "database_bytes_final": size_final,
            "seed_growth_bytes": size_after_seed - size_before,
            "workload_growth_bytes": size_final - size_after_seed,
            "bytes_per_metadata_image": round(
                (size_after_seed - size_before) / IMAGE_COUNT,
                3,
            ),
        },
        "memory": {
            "traced_start_bytes": memory_start,
            "traced_end_bytes": memory_end,
            "traced_peak_bytes": memory_peak,
            "retained_growth_bytes": max(0, memory_end - memory_start),
        },
    }
    report["gates"] = evaluate_gates(report)
    report["passed"] = all(gate["passed"] for gate in report["gates"])
    return report


def evaluate_gates(report: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "api.image_metadata.p95_ms": report["api"]["image_metadata"]["p95_ms"],
        "api.dense_annotations.p95_ms": report["api"]["dense_annotations"]["p95_ms"],
        "api.catalog_full.p95_ms": report["api"]["catalog_full"]["p95_ms"],
        "api.catalog_search.p95_ms": report["api"]["catalog_search"]["p95_ms"],
        "api.failure_paths.p95_ms": report["api"]["failure_paths"]["p95_ms"],
        "jobs.minimum_throughput_per_second": report["jobs"]["throughput_per_second"],
        "jobs.p95_ms": report["jobs"]["p95_ms"],
        "similarity.p95_ms": report["similarity"]["p95_ms"],
        "exports.detection.p95_ms": report["exports"]["detection"]["p95_ms"],
        "exports.recognition.p95_ms": report["exports"]["recognition"]["p95_ms"],
        "storage.maximum_seed_bytes": report["storage"]["seed_growth_bytes"],
        "storage.maximum_workload_growth_bytes": report["storage"]["workload_growth_bytes"],
        "memory.maximum_peak_bytes": report["memory"]["traced_peak_bytes"],
        "memory.maximum_retained_growth_bytes": report["memory"]["retained_growth_bytes"],
    }
    zero_error_checks = {
        "api.errors": report["api"]["unexpected_responses"],
        "jobs.errors": report["jobs"]["errors"],
        "similarity.errors": report["similarity"]["errors"],
        "exports.errors": report["exports"]["errors"],
    }
    results = []
    for name, threshold in GATES.items():
        measured = float(values[name])
        minimum = "minimum" in name
        passed = measured >= threshold if minimum else measured <= threshold
        results.append(
            {
                "name": name,
                "measured": measured,
                "operator": ">=" if minimum else "<=",
                "threshold": threshold,
                "passed": passed,
            }
        )
    results.extend(
        {
            "name": name,
            "measured": int(value),
            "operator": "==",
            "threshold": 0,
            "passed": value == 0,
        }
        for name, value in zero_error_checks.items()
    )
    return results


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _start_api_server() -> tuple[uvicorn.Server, threading.Thread, str, str]:
    from shelfsight_api.app import app

    password = secrets.token_urlsafe(24)
    os.environ["SHELFSIGHT_AUTH_USERS"] = json.dumps(
        [
            {
                "username": "scale-owner",
                "role": "owner",
                "password_hash": hash_password(password),
            }
        ]
    )
    os.environ["SHELFSIGHT_SESSION_COOKIE_SECURE"] = "false"
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="t037-api", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("scale benchmark API did not start")
    return server, thread, f"http://127.0.0.1:{port}", password


def _measure_api(base_url: str, password: str, fixture: ScaleFixture) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(base_url=base_url, timeout=30, limits=limits) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "scale-owner", "password": password},
        )
        login.raise_for_status()
        metadata_urls = [
            f"/api/images/{fixture.image_ids[(index * 7919) % len(fixture.image_ids)]}"
            for index in range(400)
        ]
        metadata = _measure_http(client, metadata_urls, 200, concurrency=8)
        dense = _measure_http(
            client,
            [f"/api/images/{fixture.dense_image_id}/annotations"] * 40,
            200,
            concurrency=4,
            count_field="annotations",
            expected_count=DENSE_BOX_COUNT,
        )
        # The full catalog response is the largest payload measured here, so it needs
        # enough samples for a p95 to mean anything. With 20 samples the estimator
        # lands on the second-slowest request and one hiccup defines the result: the
        # same unchanged build measured between 410 ms and 777 ms across five runs
        # against a 750 ms gate.
        catalog_full = _measure_http(
            client,
            ["/api/skus?limit=2500"] * 100,
            200,
            concurrency=4,
            count_field="skus",
            expected_count=SKU_COUNT + 1,
        )
        catalog_search = _measure_http(
            client,
            [f"/api/skus?query=Scale%20Product%20{index:04d}&limit=25" for index in range(100)],
            200,
            concurrency=8,
        )
        missing_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
        failures = _measure_http(
            client,
            [f"/api/images/{missing_id}"] * 25 + ["/api/skus?limit=0"] * 25,
            {404, 422},
            concurrency=8,
        )
    unexpected = sum(
        int(metric["unexpected_responses"])
        for metric in (metadata, dense, catalog_full, catalog_search, failures)
    )
    return {
        "concurrency": 8,
        "image_metadata": metadata,
        "dense_annotations": dense,
        "catalog_full": catalog_full,
        "catalog_search": catalog_search,
        "failure_paths": failures,
        "unexpected_responses": unexpected,
    }


def _measure_http(
    client: httpx.Client,
    urls: list[str],
    expected_status: int | set[int],
    *,
    concurrency: int,
    count_field: str | None = None,
    expected_count: int | None = None,
) -> dict[str, Any]:
    statuses = {expected_status} if isinstance(expected_status, int) else expected_status

    def request(url: str) -> HttpOutcome:
        started = perf_counter()
        response = client.get(url)
        elapsed = (perf_counter() - started) * 1_000
        count = None
        if count_field is not None and response.status_code in statuses:
            value = response.json().get(count_field)
            count = len(value) if isinstance(value, list) else None
        return HttpOutcome(elapsed, response.status_code, count)

    for url in urls[: min(5, len(urls))]:
        request(url)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcomes = list(executor.map(request, urls))
    unexpected = sum(
        (outcome.status_code not in statuses)
        or (expected_count is not None and outcome.count != expected_count)
        for outcome in outcomes
    )
    return {
        **summarize_latencies([outcome.latency_ms for outcome in outcomes]),
        "unexpected_responses": unexpected,
    }


def _measure_jobs(engine: Engine, fixture: ScaleFixture) -> dict[str, Any]:
    started = perf_counter()

    def worker(worker_index: int) -> list[float]:
        latencies: list[float] = []
        while True:
            item_started = perf_counter()
            with engine.begin() as connection:
                claim = claim_next_job(
                    connection,
                    f"scale-worker-{worker_index}",
                    lease_seconds=60,
                    job_types={"scale"},
                )
                if claim is None:
                    break
                complete_job(connection, claim, {"processed": True})
            latencies.append((perf_counter() - item_started) * 1_000)
        return latencies

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(worker, range(4)))
    elapsed = perf_counter() - started
    latencies = [latency for worker_latencies in results for latency in worker_latencies]
    with engine.connect() as connection:
        succeeded = connection.execute(
            select(func.count()).select_from(jobs).where(jobs.c.state == "succeeded")
        ).scalar_one()
    errors = abs(fixture.job_count - int(succeeded))
    return {
        **summarize_latencies(latencies),
        "workers": 4,
        "completed": int(succeeded),
        "throughput_per_second": round(int(succeeded) / elapsed, 3),
        "errors": errors,
    }


def _measure_similarity(engine: Engine, fixture: ScaleFixture) -> dict[str, Any]:
    def query(_: int) -> tuple[float, bool]:
        started = perf_counter()
        with engine.connect() as connection:
            result = get_annotation_sku_candidates(
                connection,
                fixture.query_annotation_id,
                1,
                9,
            )
        return (perf_counter() - started) * 1_000, len(result["candidates"]) == 9

    for index in range(5):
        query(index)
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(query, range(160)))
    return {
        **summarize_latencies([latency for latency, _ in outcomes]),
        "concurrency": 8,
        "gallery_size": SKU_COUNT,
        "errors": sum(not valid for _, valid in outcomes),
    }


def _measure_exports(
    engine: Engine,
    media_root: Path,
    fixture: ScaleFixture,
) -> dict[str, Any]:
    errors = 0

    def detection() -> int:
        with engine.connect() as connection:
            archive = build_detection_export(
                connection,
                media_root,
                fixture.export_dataset_version_id,
            )
        validate_export_archive(archive, "detection")
        return len(archive)

    def recognition() -> int:
        with engine.connect() as connection:
            archive = build_recognition_export(
                connection,
                media_root,
                fixture.export_dataset_version_id,
            )
        validate_export_archive(archive, "recognition")
        return len(archive)

    detection_metric = _measure_callable(detection, samples=5)
    recognition_metric = _measure_callable(recognition, samples=3)
    try:
        with engine.connect() as connection:
            build_detection_export(connection, media_root, fixture.dataset_version_id)
        errors += 1
    except ExportVersionNotFrozenError:
        pass
    return {
        "detection": detection_metric,
        "recognition": recognition_metric,
        "errors": errors,
    }


def _measure_callable(
    operation: Callable[[], int],
    *,
    samples: int,
) -> dict[str, Any]:
    latencies: list[float] = []
    sizes: list[int] = []
    for _ in range(samples):
        started = perf_counter()
        sizes.append(operation())
        latencies.append((perf_counter() - started) * 1_000)
    return {
        **summarize_latencies(latencies),
        "artifact_bytes": max(sizes),
    }


def _database_version(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(select(func.version())).scalar_one())


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CVSight release scale benchmark")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.environ.get("SHELFSIGHT_DATABASE_URL", "").strip()
    media_root_value = os.environ.get("SHELFSIGHT_MEDIA_ROOT", "").strip()
    if not database_url or not media_root_value:
        print("SHELFSIGHT_DATABASE_URL and SHELFSIGHT_MEDIA_ROOT are required")
        return 1
    get_engine.cache_clear()
    engine = get_engine()
    try:
        report = build_report(engine, Path(media_root_value).resolve())
        write_report(args.output, report)
    except (OSError, RuntimeError, ValueError, SQLAlchemyError, httpx.HTTPError) as error:
        print(f"scale benchmark failed ({type(error).__name__})")
        return 1
    finally:
        engine.dispose()
        get_engine.cache_clear()
    failed = [gate["name"] for gate in report["gates"] if not gate["passed"]]
    print(
        f"passed={report['passed']} images={IMAGE_COUNT} skus={SKU_COUNT} "
        f"failed_gates={','.join(failed) if failed else 'none'}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
