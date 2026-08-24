import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def validate_options(checkpoint_path: Path, prompt: str, threshold: float) -> None:
    if not checkpoint_path.is_file():
        raise ValueError("SAM checkpoint does not exist")
    if not prompt.strip():
        raise ValueError("SAM prompt must not be empty")
    if not 0 <= threshold <= 1:
        raise ValueError("SAM threshold must be between 0 and 1")


def convert_output(
    output: dict[str, Any], label: str, width: int, height: int
) -> list[dict[str, Any]]:
    boxes = output["boxes"].detach().cpu().tolist()
    scores = output["scores"].detach().cpu().tolist()
    predictions = []
    for box, score in zip(boxes, scores, strict=True):
        left = min(max(float(box[0]), 0.0), float(width))
        top = min(max(float(box[1]), 0.0), float(height))
        right = min(max(float(box[2]), 0.0), float(width))
        bottom = min(max(float(box[3]), 0.0), float(height))
        if right <= left or bottom <= top:
            continue
        predictions.append(
            {
                "label": label,
                "bbox": [left, top, right - left, bottom - top],
                "score": float(score),
            }
        )
    return predictions


def build_predictor(
    checkpoint_path: Path, prompt: str, threshold: float
) -> tuple[Callable[[Path, dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]:
    validate_options(checkpoint_path, prompt, threshold)

    import torch
    from PIL import Image
    from sam3.model.sam3_image_processor import Sam3Processor
    from sam3.model_builder import build_sam3_image_model

    if not torch.cuda.is_available():
        raise RuntimeError("SAM image benchmark requires CUDA")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    started = time.perf_counter()
    model = build_sam3_image_model(checkpoint_path=str(checkpoint_path))
    model.eval()
    torch.cuda.synchronize()
    processor = Sam3Processor(model, confidence_threshold=threshold)
    runtime = {
        "model_load_ms": (time.perf_counter() - started) * 1000,
        "python": __import__("platform").python_version(),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device": torch.cuda.get_device_name(),
    }

    def predict(image_path: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state = processor.set_image(image)
            output = processor.set_text_prompt(prompt=prompt, state=state)
        torch.cuda.synchronize()
        runtime["peak_allocated_mib"] = torch.cuda.max_memory_allocated() / 1024**2
        runtime["peak_reserved_mib"] = torch.cuda.max_memory_reserved() / 1024**2
        return convert_output(output, "product", sample["width"], sample["height"])

    return predict, runtime
