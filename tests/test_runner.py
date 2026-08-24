import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmark_tool.cli import main
from benchmark_tool.runner import run_benchmark, validate_predictions

FIELDNAMES = ["image_id", "relative_path", "sha256", "width", "height", "split"]


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def valid_row(image_id: str, relative_path: str) -> dict[str, str]:
    return {
        "image_id": image_id,
        "relative_path": relative_path,
        "sha256": "abc",
        "width": "100",
        "height": "80",
        "split": "test",
    }


def test_run_benchmark_isolates_invalid_inputs_and_model_failures(tmp_path: Path) -> None:
    first_image = tmp_path / "first.jpg"
    failed_image = tmp_path / "failed.jpg"
    first_image.write_bytes(b"first")
    failed_image.write_bytes(b"failed")
    manifest = tmp_path / "manifest.csv"
    write_manifest(
        manifest,
        [
            valid_row("first", first_image.name),
            valid_row("missing", "missing.jpg"),
            valid_row("failed", failed_image.name),
        ],
    )

    def predict(_path: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
        if sample["image_id"] == "failed":
            raise RuntimeError("private model detail")
        return [{"label": "product", "bbox": [0, 0, 10, 10], "score": 0.75}]

    ticks = iter([0, 2_000_000, 3_000_000])
    result = run_benchmark(
        manifest,
        predict,
        model_name="fake",
        model_version="test",
        model_config={},
        clock_ns=lambda: next(ticks),
    )

    assert result["summary"]["total"] == 3
    assert result["summary"]["succeeded"] == 1
    assert result["summary"]["failed"] == 2
    assert result["results"][0]["latency_ms"] == 2
    assert result["results"][1]["error_type"] == "invalid_manifest"
    assert result["results"][2]["message"] == "RuntimeError"
    assert "private model detail" not in json.dumps(result)
    assert result["run"]["dataset_fingerprint"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()


def test_validate_predictions_rejects_boxes_outside_image() -> None:
    predictions = [{"label": "product", "bbox": [90, 0, 20, 10], "score": 0.5}]

    try:
        validate_predictions(predictions, width=100, height=80)
    except ValueError as error:
        assert str(error) == "model returned a bounding box outside the image"
    else:
        raise AssertionError("expected invalid model output to fail")


def test_cli_runs_fake_model_and_writes_result(tmp_path: Path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    manifest = tmp_path / "manifest.csv"
    output = tmp_path / "output" / "result.json"
    hardware = tmp_path / "hardware.json"
    hardware.write_text('{"gpu": "test"}', encoding="utf-8")
    write_manifest(manifest, [valid_row("image", image.name)])

    exit_code = main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--hardware",
            str(hardware),
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["summary"]["succeeded"] == 1
    assert result["run"]["hardware"] == {"gpu": "test"}
    assert result["results"][0]["predictions"] == [
        {"label": "product", "bbox": [25.0, 20.0, 50.0, 40.0], "score": 0.5}
    ]
