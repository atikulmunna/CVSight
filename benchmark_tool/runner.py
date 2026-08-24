import csv
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark_tool.evaluation import summarize_latencies

PredictFunction = Callable[[Path, dict[str, Any]], list[dict[str, Any]]]
REQUIRED_COLUMNS = {"image_id", "relative_path", "sha256", "width", "height", "split"}


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> tuple[list[dict[str, Any]], str]:
    manifest_root = path.resolve().parent
    records: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"manifest is missing required columns: {missing}")

        for row_number, row in enumerate(reader, start=2):
            try:
                records.append(parse_manifest_row(row, row_number, manifest_root))
            except ValueError as error:
                records.append(
                    {
                        "image_id": row.get("image_id") or f"row_{row_number}",
                        "status": "error",
                        "error_type": "invalid_manifest",
                        "message": str(error),
                    }
                )
    return records, fingerprint(path)


def parse_manifest_row(
    row: dict[str, str], row_number: int, manifest_root: Path
) -> dict[str, Any]:
    image_id = (row.get("image_id") or "").strip()
    if not image_id:
        raise ValueError(f"row {row_number} has no image ID")

    relative_path = Path((row.get("relative_path") or "").strip())
    if not relative_path.as_posix() or relative_path.is_absolute():
        raise ValueError(f"row {row_number} has an invalid relative path")
    image_path = (manifest_root / relative_path).resolve()
    if not image_path.is_relative_to(manifest_root):
        raise ValueError(f"row {row_number} escapes the manifest directory")
    if not image_path.is_file():
        raise ValueError(f"row {row_number} image does not exist")

    try:
        width = int(row["width"])
        height = int(row["height"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"row {row_number} has invalid image dimensions") from error
    if width <= 0 or height <= 0:
        raise ValueError(f"row {row_number} has invalid image dimensions")

    return {
        "image_id": image_id,
        "image_path": image_path,
        "sha256": row["sha256"],
        "width": width,
        "height": height,
        "split": row["split"],
    }


def validate_predictions(
    predictions: Sequence[dict[str, Any]], width: int, height: int
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for prediction in predictions:
        label = prediction.get("label")
        bbox = prediction.get("bbox")
        score = prediction.get("score")
        if not isinstance(label, str) or not label:
            raise ValueError("model returned an invalid label")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("model returned an invalid bounding box")
        if not all(isinstance(value, int | float) and math.isfinite(value) for value in bbox):
            raise ValueError("model returned a non-finite bounding box")
        x, y, box_width, box_height = (float(value) for value in bbox)
        if x < 0 or y < 0 or box_width <= 0 or box_height <= 0:
            raise ValueError("model returned an invalid bounding box")
        if x + box_width > width or y + box_height > height:
            raise ValueError("model returned a bounding box outside the image")
        if not isinstance(score, int | float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("model returned an invalid score")
        validated.append({"label": label, "bbox": [x, y, box_width, box_height], "score": score})
    return validated


def run_benchmark(
    manifest_path: Path,
    predict: PredictFunction,
    model_name: str,
    model_version: str,
    model_config: dict[str, Any],
    hardware: dict[str, Any] | None = None,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    records, dataset_fingerprint = read_manifest(manifest_path)
    started_at = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []

    for record in records:
        if record.get("status") == "error":
            results.append(record)
            continue

        start = clock_ns()
        try:
            predictions = predict(record["image_path"], record)
            predictions = validate_predictions(predictions, record["width"], record["height"])
        except Exception as error:  # noqa: BLE001
            results.append(
                {
                    "image_id": record["image_id"],
                    "status": "error",
                    "error_type": "model_error",
                    "message": type(error).__name__,
                }
            )
            continue
        elapsed_ms = (clock_ns() - start) / 1_000_000
        results.append(
            {
                "image_id": record["image_id"],
                "status": "ok",
                "latency_ms": elapsed_ms,
                "predictions": predictions,
            }
        )

    latencies = [result["latency_ms"] for result in results if result["status"] == "ok"]
    succeeded = len(latencies)
    return {
        "schema_version": 1,
        "run": {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "dataset_fingerprint": dataset_fingerprint,
            "model": {"name": model_name, "version": model_version, "config": model_config},
            "hardware": hardware or {},
        },
        "summary": {
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "latency": summarize_latencies(latencies),
        },
        "results": results,
    }


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(result, file, indent=2)
            file.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
