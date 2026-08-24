import importlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmark_tool.evaluation import intersection_over_union


def validate_options(
    checkpoint_path: Path, threshold: float, nms_iou: float = 0.5
) -> None:
    if not checkpoint_path.is_file():
        raise ValueError("RF-DETR checkpoint does not exist")
    if not 0 <= threshold <= 1:
        raise ValueError("RF-DETR threshold must be between 0 and 1")
    if not 0 < nms_iou <= 1:
        raise ValueError("RF-DETR NMS IoU must be greater than 0 and at most 1")


def convert_output(
    detections: Any, width: int, height: int
) -> list[dict[str, Any]]:
    class_names = detections.data["class_name"]
    predictions = []
    values = zip(
        detections.xyxy,
        detections.confidence,
        class_names,
        strict=True,
    )
    for box, score, class_name in values:
        left = min(max(float(box[0]), 0.0), float(width))
        top = min(max(float(box[1]), 0.0), float(height))
        right = min(max(float(box[2]), 0.0), float(width))
        bottom = min(max(float(box[3]), 0.0), float(height))
        if right <= left or bottom <= top:
            continue
        predictions.append(
            {
                "label": str(class_name),
                "bbox": [left, top, right - left, bottom - top],
                "score": float(score),
            }
        )
    return predictions


def slice_regions(width: int, height: int, overlap: float = 0.1) -> list[tuple[int, int, int, int]]:
    horizontal_overlap = round(width * overlap / 2)
    vertical_overlap = round(height * overlap / 2)
    middle_x = width // 2
    middle_y = height // 2
    x_ranges = ((0, middle_x + horizontal_overlap), (middle_x - horizontal_overlap, width))
    y_ranges = ((0, middle_y + vertical_overlap), (middle_y - vertical_overlap, height))
    return [
        (left, top, right, bottom)
        for top, bottom in y_ranges
        for left, right in x_ranges
    ]


def offset_predictions(
    predictions: list[dict[str, Any]], offset_x: int, offset_y: int
) -> list[dict[str, Any]]:
    return [
        {
            **prediction,
            "bbox": [
                prediction["bbox"][0] + offset_x,
                prediction["bbox"][1] + offset_y,
                prediction["bbox"][2],
                prediction["bbox"][3],
            ],
        }
        for prediction in predictions
    ]


def non_maximum_suppression(
    predictions: list[dict[str, Any]], iou_threshold: float
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for prediction in sorted(predictions, key=lambda item: item["score"], reverse=True):
        overlaps_kept = any(
            prediction["label"] == existing["label"]
            and intersection_over_union(prediction["bbox"], existing["bbox"]) >= iou_threshold
            for existing in kept
        )
        if not overlaps_kept:
            kept.append(prediction)
    return kept


def build_predictor(
    checkpoint_path: Path,
    threshold: float,
    optimized: bool,
    sliced: bool = False,
    nms_iou: float = 0.5,
) -> tuple[Callable[[Path, dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]:
    validate_options(checkpoint_path, threshold, nms_iou)

    torch = importlib.import_module("torch")
    rfdetr = importlib.import_module("rfdetr")
    model_type = rfdetr.RFDETRNano

    if not torch.cuda.is_available():
        raise RuntimeError("RF-DETR benchmark requires CUDA")

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = model_type(pretrain_weights=str(checkpoint_path))
    if optimized:
        model.optimize_for_inference(batch_size=1, dtype=torch.float16)
    torch.cuda.synchronize()
    runtime = {
        "model_load_and_optimize_ms": (time.perf_counter() - started) * 1000,
        "python": __import__("platform").python_version(),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device": torch.cuda.get_device_name(),
    }

    def predict(image_path: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
        predictions = predict_with_model(
            model,
            image_path,
            int(sample["width"]),
            int(sample["height"]),
            threshold,
            sliced=sliced,
            nms_iou=nms_iou,
        )
        torch.cuda.synchronize()
        runtime["peak_allocated_mib"] = torch.cuda.max_memory_allocated() / 1024**2
        runtime["peak_reserved_mib"] = torch.cuda.max_memory_reserved() / 1024**2
        return predictions

    return predict, runtime


def predict_with_model(
    model: Any,
    image_path: Path,
    width: int,
    height: int,
    threshold: float,
    *,
    sliced: bool,
    nms_iou: float = 0.5,
) -> list[dict[str, Any]]:
    from PIL import Image

    if sliced:
        predictions = []
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        for left, top, right, bottom in slice_regions(image.width, image.height):
            crop = image.crop((left, top, right, bottom))
            detections = model.predict(
                crop,
                threshold=threshold,
                include_source_image=False,
            )
            tile_predictions = convert_output(
                detections,
                right - left,
                bottom - top,
            )
            predictions.extend(offset_predictions(tile_predictions, left, top))
        return non_maximum_suppression(predictions, nms_iou)

    detections = model.predict(
        str(image_path),
        threshold=threshold,
        include_source_image=False,
    )
    return convert_output(detections, width, height)
