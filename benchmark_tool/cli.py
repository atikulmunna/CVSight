import argparse
import json
from pathlib import Path
from typing import Any

from benchmark_tool.runner import fingerprint, run_benchmark, write_result


def fake_predict(_image_path: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one deterministic proposal for end-to-end harness validation."""
    width = sample["width"]
    height = sample["height"]
    return [
        {
            "label": "product",
            "bbox": [width * 0.25, height * 0.25, width * 0.5, height * 0.5],
            "score": 0.5,
        }
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ShelfSight benchmark harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the deterministic fake model")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--hardware", type=Path)

    sam_parser = subparsers.add_parser("run-sam", help="Run the SAM image model")
    sam_parser.add_argument("--manifest", type=Path, required=True)
    sam_parser.add_argument("--output", type=Path, required=True)
    sam_parser.add_argument("--checkpoint", type=Path, required=True)
    sam_parser.add_argument("--source-commit", required=True)
    sam_parser.add_argument("--prompt", default="product")
    sam_parser.add_argument("--threshold", type=float, default=0.5)
    sam_parser.add_argument("--hardware", type=Path)

    rfdetr_parser = subparsers.add_parser("run-rfdetr", help="Run RF-DETR Nano")
    rfdetr_parser.add_argument("--manifest", type=Path, required=True)
    rfdetr_parser.add_argument("--output", type=Path, required=True)
    rfdetr_parser.add_argument("--checkpoint", type=Path, required=True)
    rfdetr_parser.add_argument("--version", required=True)
    rfdetr_parser.add_argument("--threshold", type=float, default=0.5)
    rfdetr_parser.add_argument("--optimized", action="store_true")
    rfdetr_parser.add_argument("--sliced", action="store_true")
    rfdetr_parser.add_argument("--nms-iou", type=float, default=0.5)
    rfdetr_parser.add_argument("--hardware", type=Path)

    prepare_parser = subparsers.add_parser(
        "prepare-rfdetr-training",
        help="Build a leakage-safe RF-DETR dataset from an immutable export",
    )
    prepare_parser.add_argument("--export", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--checkpoint", type=Path, required=True)
    prepare_parser.add_argument("--licenses", type=Path, required=True)
    prepare_parser.add_argument("--code-version", required=True)
    prepare_parser.add_argument("--seed", type=int, default=1337)
    prepare_parser.add_argument("--epochs", type=int, default=5)

    train_parser = subparsers.add_parser(
        "train-rfdetr",
        help="Train RF-DETR from a prepared immutable dataset",
    )
    train_parser.add_argument("--dataset", type=Path, required=True)
    train_parser.add_argument("--checkpoint", type=Path, required=True)
    train_parser.add_argument("--output", type=Path, required=True)

    predict_parser = subparsers.add_parser(
        "predict-rfdetr-training",
        help="Run fixed full and sliced inference on the frozen test split",
    )
    predict_parser.add_argument("--dataset", type=Path, required=True)
    predict_parser.add_argument("--checkpoint", type=Path, required=True)
    predict_parser.add_argument("--output", type=Path, required=True)

    evaluate_parser = subparsers.add_parser(
        "evaluate-rfdetr-training",
        help="Evaluate full and sliced RF-DETR predictions on the frozen test split",
    )
    evaluate_parser.add_argument("--dataset", type=Path, required=True)
    evaluate_parser.add_argument("--predictions", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    return parser


def load_hardware(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("hardware input must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        hardware = load_hardware(getattr(args, "hardware", None))
        if args.command == "run":
            result = run_benchmark(
                manifest_path=args.manifest,
                predict=fake_predict,
                model_name="fake",
                model_version="1",
                model_config={"proposal": "centered_half_image"},
                hardware=hardware,
            )
        elif args.command == "run-sam":
            from benchmark_tool.sam_image import build_predictor

            predict, runtime = build_predictor(args.checkpoint, args.prompt, args.threshold)
            result = run_benchmark(
                manifest_path=args.manifest,
                predict=predict,
                model_name="sam-image",
                model_version=args.source_commit,
                model_config={
                    "checkpoint": args.checkpoint.name,
                    "checkpoint_sha256": fingerprint(args.checkpoint),
                    "prompt": args.prompt,
                    "threshold": args.threshold,
                    "precision": "bfloat16",
                    "runtime": runtime,
                },
                hardware=hardware,
            )
        elif args.command == "run-rfdetr":
            from benchmark_tool.rfdetr import build_predictor

            predict, runtime = build_predictor(
                args.checkpoint,
                args.threshold,
                args.optimized,
                args.sliced,
                args.nms_iou,
            )
            result = run_benchmark(
                manifest_path=args.manifest,
                predict=predict,
                model_name="rf-detr-nano",
                model_version=args.version,
                model_config={
                    "checkpoint": args.checkpoint.name,
                    "checkpoint_sha256": fingerprint(args.checkpoint),
                    "threshold": args.threshold,
                    "optimized": args.optimized,
                    "sliced": args.sliced,
                    "slice_grid": "2x2" if args.sliced else None,
                    "slice_overlap": 0.1 if args.sliced else None,
                    "nms_iou": args.nms_iou if args.sliced else None,
                    "resolution": 384,
                    "num_queries": 300,
                    "num_select": 300,
                    "runtime": runtime,
                },
                hardware=hardware,
            )
        elif args.command == "prepare-rfdetr-training":
            from benchmark_tool.training import prepare_training_dataset

            manifest = prepare_training_dataset(
                args.export,
                args.output,
                args.checkpoint,
                args.licenses,
                code_version=args.code_version,
                seed=args.seed,
                epochs=args.epochs,
            )
            print(
                f"prepared={manifest['dataset_version_id']} "
                f"images={sum(manifest['counts']['images_by_split'].values())}"
            )
            return 0
        elif args.command == "train-rfdetr":
            from benchmark_tool.training import run_rfdetr_training

            manifest = run_rfdetr_training(
                args.dataset,
                args.checkpoint,
                args.output,
            )
            print(f"artifacts={len(manifest['artifacts'])}")
            return 0
        elif args.command == "predict-rfdetr-training":
            from benchmark_tool.training_evaluation import generate_test_predictions

            predictions = generate_test_predictions(
                args.dataset,
                args.checkpoint,
                args.output,
            )
            print(
                "full_predictions="
                f"{len(predictions['modes']['full_image']['predictions'])} "
                "sliced_predictions="
                f"{len(predictions['modes']['sliced_2x2']['predictions'])}"
            )
            return 0
        else:
            from benchmark_tool.training_evaluation import (
                evaluate_training_predictions,
                write_evaluation_report,
            )

            report = evaluate_training_predictions(args.dataset, args.predictions)
            write_evaluation_report(args.output, report)
            print(
                "full_recall="
                f"{report['modes']['full_image']['product_recall_at_iou_50']} "
                "sliced_recall="
                f"{report['modes']['sliced_2x2']['product_recall_at_iou_50']}"
            )
            return 0
        write_result(args.output, result)
    except (ImportError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"benchmark failed: {error}")
        return 1

    summary = result["summary"]
    print(
        f"processed={summary['total']} "
        f"succeeded={summary['succeeded']} "
        f"failed={summary['failed']}"
    )
    return 0
