from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from shelfsight_api.geometry import pixel_crop_bounds
from shelfsight_api.model_contract import (
    DetectRequest,
    EmbedRequest,
    ModelProvenance,
    ModelRequest,
    RefineRequest,
    RetrieveRequest,
)


class ModelAdapter(Protocol):
    def execute(
        self,
        request: ModelRequest,
        image_path: Path | None,
    ) -> Mapping[str, Any]: ...


class AdapterExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class FakeModelAdapter:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds
        self.provenance = ModelProvenance(
            model_id="fake-model",
            model_version="contract-001",
            artifact_sha256="f" * 64,
        )

    def execute(
        self,
        request: ModelRequest,
        image_path: Path | None,
    ) -> Mapping[str, Any]:
        if self.delay_seconds:
            import time

            time.sleep(self.delay_seconds)
        if isinstance(request, DetectRequest):
            return {
                "model_provenance": self.provenance.model_dump(mode="json"),
                "predictions": [
                    {
                        "proposal_id": f"{request.request_id}:1",
                        "geometry": {
                            "type": "axis_aligned_box",
                            "x": 0,
                            "y": 0,
                            "width": min(20, request.image.width),
                            "height": min(20, request.image.height),
                        },
                        "class_type": "product",
                        "score": 0.9,
                        "candidate_sku_id": None,
                    }
                ],
            }
        if isinstance(request, RefineRequest):
            return {
                "model_provenance": self.provenance.model_dump(mode="json"),
                "result": {
                    "geometry": request.box_hint.model_dump(mode="json"),
                    "score": 0.95,
                },
            }
        if isinstance(request, EmbedRequest):
            embedding_id = uuid5(NAMESPACE_URL, request.request_id)
            return {
                "model_provenance": self.provenance.model_dump(mode="json"),
                "embedding": {
                    "embedding_id": str(embedding_id),
                    "purpose": request.purpose,
                    "dimension": 4,
                    "normalized": True,
                    "values": [0.5, -0.5, 0.5, -0.5],
                },
            }
        if isinstance(request, RetrieveRequest):
            subject_id = uuid5(NAMESPACE_URL, f"{request.index_namespace}:subject")
            return {
                "index_provenance": {
                    "index_namespace": request.index_namespace,
                    "index_version": request.index_version,
                    "purpose": request.purpose,
                },
                "candidates": [
                    {
                        "rank": 1,
                        "subject_type": (
                            "sku" if request.purpose == "recognition" else "annotation"
                        ),
                        "subject_id": str(subject_id),
                        "score": 0.9,
                    }
                ],
                "unknown_decision": {
                    "is_unknown": False,
                    "threshold": 0.5,
                    "calibration_version": request.configuration.calibration_version,
                    "automatic_confirmation": False,
                },
            }
        raise AdapterExecutionError(
            "unsupported_operation",
            "adapter does not support this operation",
            retryable=False,
        )


RfdetrPredictor = Callable[
    [Path, DetectRequest],
    Sequence[Mapping[str, Any]],
]

BoxRefinerPredictor = Callable[
    [Path, RefineRequest],
    Sequence[Mapping[str, Any]],
]
ImageEmbedderPredictor = Callable[
    [Path, EmbedRequest],
    Sequence[float],
]


class RfdetrAdapter:
    def __init__(
        self,
        predictor: RfdetrPredictor,
        provenance: ModelProvenance,
    ) -> None:
        self._predictor = predictor
        self._provenance = provenance

    def execute(
        self,
        request: ModelRequest,
        image_path: Path | None,
    ) -> Mapping[str, Any]:
        if not isinstance(request, DetectRequest):
            raise AdapterExecutionError(
                "unsupported_operation",
                "RF-DETR supports detect requests only",
                retryable=False,
            )
        if image_path is None:
            raise AdapterExecutionError(
                "model_input_unavailable",
                "canonical image is unavailable",
                retryable=True,
            )
        raw_predictions = self._predictor(image_path, request)
        predictions = [
            _rfdetr_prediction(request.request_id, index, prediction)
            for index, prediction in enumerate(raw_predictions)
        ]
        return {
            "model_provenance": self._provenance.model_dump(mode="json"),
            "predictions": predictions,
        }


def load_rfdetr_adapter(
    checkpoint_path: Path,
    *,
    model_version: str,
) -> RfdetrAdapter:
    if not checkpoint_path.is_file():
        raise AdapterExecutionError(
            "model_artifact_missing",
            "RF-DETR model artifact is unavailable",
            retryable=False,
        )
    provenance = ModelProvenance(
        model_id="rfdetr-nano-known-sku",
        model_version=model_version,
        artifact_sha256=_sha256(checkpoint_path),
    )
    try:
        torch = importlib.import_module("torch")
        rfdetr = importlib.import_module("rfdetr")
    except ImportError as error:
        raise AdapterExecutionError(
            "model_runtime_unavailable",
            "RF-DETR runtime is unavailable",
            retryable=False,
        ) from error
    if not torch.cuda.is_available():
        raise AdapterExecutionError(
            "model_device_unavailable",
            "RF-DETR requires an available CUDA device",
            retryable=True,
        )

    from benchmark_tool.rfdetr import predict_with_model

    model = rfdetr.RFDETRNano(pretrain_weights=str(checkpoint_path))
    model.optimize_for_inference(batch_size=1, dtype=torch.float16)

    def predict(path: Path, request: DetectRequest) -> Sequence[Mapping[str, Any]]:
        predictions = predict_with_model(
            model,
            path,
            request.image.width,
            request.image.height,
            request.configuration.confidence_threshold,
            sliced=request.configuration.inference_strategy == "dense_retry",
            nms_iou=0.5,
        )
        torch.cuda.synchronize()
        return predictions

    return RfdetrAdapter(
        predict,
        provenance,
    )


class Sam3BoxRefinerAdapter:
    def __init__(
        self,
        predictor: BoxRefinerPredictor,
        provenance: ModelProvenance,
    ) -> None:
        self._predictor = predictor
        self._provenance = provenance

    def execute(
        self,
        request: ModelRequest,
        image_path: Path | None,
    ) -> Mapping[str, Any]:
        if not isinstance(request, RefineRequest):
            raise AdapterExecutionError(
                "unsupported_operation",
                "SAM supports refine requests only",
                retryable=False,
            )
        if image_path is None:
            raise AdapterExecutionError(
                "model_input_unavailable",
                "canonical image is unavailable",
                retryable=True,
            )
        candidates = self._predictor(image_path, request)
        selected = _select_refinement(request, candidates)
        if selected is None:
            raise AdapterExecutionError(
                "refinement_not_found",
                "no box refinement was found",
                retryable=False,
            )
        return {
            "model_provenance": self._provenance.model_dump(mode="json"),
            "result": {
                "geometry": {
                    "type": "axis_aligned_box",
                    "x": selected["bbox"][0],
                    "y": selected["bbox"][1],
                    "width": selected["bbox"][2],
                    "height": selected["bbox"][3],
                },
                "score": selected["score"],
            },
        }


def load_sam3_box_refiner_adapter(
    checkpoint_path: Path,
    *,
    model_version: str,
) -> Sam3BoxRefinerAdapter:
    if not checkpoint_path.is_file():
        raise AdapterExecutionError(
            "model_artifact_missing",
            "SAM model artifact is unavailable",
            retryable=False,
        )
    provenance = ModelProvenance(
        model_id="sam3-box-refiner",
        model_version=model_version,
        artifact_sha256=_sha256(checkpoint_path),
    )
    try:
        torch = importlib.import_module("torch")
        pil_image = importlib.import_module("PIL.Image")
        processor_module = importlib.import_module("sam3.model.sam3_image_processor")
        builder_module = importlib.import_module("sam3.model_builder")
    except ImportError as error:
        raise AdapterExecutionError(
            "model_runtime_unavailable",
            "SAM runtime is unavailable",
            retryable=False,
        ) from error
    if not torch.cuda.is_available():
        raise AdapterExecutionError(
            "model_device_unavailable",
            "SAM requires an available CUDA device",
            retryable=True,
        )

    model = builder_module.build_sam3_image_model(checkpoint_path=str(checkpoint_path))
    processor = processor_module.Sam3Processor(model, confidence_threshold=0.5)

    def predict(path: Path, request: RefineRequest) -> Sequence[Mapping[str, Any]]:
        with pil_image.open(path) as source_image:
            image = source_image.convert("RGB")
        hint = request.box_hint
        normalized_box = [
            (hint.x + hint.width / 2) / request.image.width,
            (hint.y + hint.height / 2) / request.image.height,
            hint.width / request.image.width,
            hint.height / request.image.height,
        ]
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state = processor.set_image(image)
            output = processor.add_geometric_prompt(
                box=normalized_box,
                label=True,
                state=state,
            )
            torch.cuda.synchronize()
        boxes = output["boxes"].detach().cpu().tolist()
        scores = output["scores"].detach().cpu().tolist()
        return [
            {
                "bbox": [
                    box[0],
                    box[1],
                    box[2] - box[0],
                    box[3] - box[1],
                ],
                "score": score,
            }
            for box, score in zip(boxes, scores, strict=True)
        ]

    return Sam3BoxRefinerAdapter(predict, provenance)


class ClipImageEmbedderAdapter:
    def __init__(
        self,
        predictor: ImageEmbedderPredictor,
        provenance: ModelProvenance,
    ) -> None:
        self._predictor = predictor
        self._provenance = provenance

    def execute(
        self,
        request: ModelRequest,
        image_path: Path | None,
    ) -> Mapping[str, Any]:
        if not isinstance(request, EmbedRequest):
            raise AdapterExecutionError(
                "unsupported_operation",
                "CLIP supports image embedding requests only",
                retryable=False,
            )
        if image_path is None:
            raise AdapterExecutionError(
                "model_input_unavailable",
                "canonical image is unavailable",
                retryable=True,
            )
        values = [float(value) for value in self._predictor(image_path, request)]
        return {
            "model_provenance": self._provenance.model_dump(mode="json"),
            "embedding": {
                "embedding_id": str(uuid5(NAMESPACE_URL, request.request_id)),
                "purpose": request.purpose,
                "dimension": len(values),
                "normalized": True,
                "values": values,
            },
        }


def load_clip_image_embedder_adapter(
    model_root: Path,
    provenance: ModelProvenance,
) -> ClipImageEmbedderAdapter:
    weights_path = model_root / "pytorch_model.bin"
    if (
        not model_root.is_dir()
        or not (model_root / "config.json").is_file()
        or not (model_root / "preprocessor_config.json").is_file()
        or not weights_path.is_file()
        or _sha256(weights_path) != provenance.artifact_sha256
    ):
        raise AdapterExecutionError(
            "model_artifact_missing",
            "CLIP model artifact is unavailable or does not match its profile",
            retryable=False,
        )
    try:
        torch = importlib.import_module("torch")
        pil_image = importlib.import_module("PIL.Image")
        transformers = importlib.import_module("transformers")
    except ImportError as error:
        raise AdapterExecutionError(
            "model_runtime_unavailable",
            "CLIP runtime is unavailable",
            retryable=False,
        ) from error
    if not torch.cuda.is_available():
        raise AdapterExecutionError(
            "model_device_unavailable",
            "CLIP requires an available CUDA device",
            retryable=True,
        )

    processor = transformers.CLIPImageProcessor.from_pretrained(
        model_root,
        local_files_only=True,
    )
    model = transformers.CLIPVisionModelWithProjection.from_pretrained(
        model_root,
        local_files_only=True,
    ).to("cuda")
    model.eval()

    def predict(path: Path, request: EmbedRequest) -> Sequence[float]:
        crop = request.crop
        bounds = pixel_crop_bounds(crop.x, crop.y, crop.width, crop.height)
        with pil_image.open(path) as source_image:
            image = source_image.convert("RGB").crop(bounds)
        try:
            inputs = processor(images=[image], return_tensors="pt")
        finally:
            image.close()
        pixel_values = inputs["pixel_values"].to("cuda")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            features = model(pixel_values=pixel_values).image_embeds
            normalized = torch.nn.functional.normalize(features.float(), dim=1)
        torch.cuda.synchronize()
        values = normalized[0].cpu().tolist()
        return [float(value) for value in values]

    return ClipImageEmbedderAdapter(predict, provenance)


def _rfdetr_prediction(
    request_id: str,
    index: int,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    bbox = value.get("bbox")
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
        bbox = []
    candidate = value.get("candidate_sku_id")
    candidate_sku_id: str | None = None
    if candidate is not None:
        candidate_sku_id = str(candidate)
    return {
        "proposal_id": f"{request_id}:{index + 1}",
        "geometry": {
            "type": "axis_aligned_box",
            "x": bbox[0] if len(bbox) == 4 else None,
            "y": bbox[1] if len(bbox) == 4 else None,
            "width": bbox[2] if len(bbox) == 4 else None,
            "height": bbox[3] if len(bbox) == 4 else None,
        },
        "class_type": "product",
        "score": value.get("score"),
        "candidate_sku_id": candidate_sku_id,
    }


def _select_refinement(
    request: RefineRequest,
    candidates: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    valid = [candidate for candidate in candidates if _valid_refinement(candidate, request)]
    if not valid:
        return None
    hint = [
        request.box_hint.x,
        request.box_hint.y,
        request.box_hint.width,
        request.box_hint.height,
    ]
    return max(
        valid,
        key=lambda candidate: (
            _box_iou(hint, candidate["bbox"]),
            float(candidate["score"]),
        ),
    )


def _valid_refinement(
    candidate: Mapping[str, Any],
    request: RefineRequest,
) -> bool:
    bbox = candidate.get("bbox")
    score = candidate.get("score")
    if (
        not isinstance(bbox, Sequence)
        or isinstance(bbox, (str, bytes))
        or len(bbox) != 4
        or not all(isinstance(value, int | float) for value in bbox)
        or not isinstance(score, int | float)
    ):
        return False
    x, y, width, height = (float(value) for value in bbox)
    return (
        x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and x + width <= request.image.width
        and y + height <= request.image.height
        and 0 <= float(score) <= 1
    )


def _box_iou(left: Sequence[float], right: Sequence[float]) -> float:
    left_x2 = left[0] + left[2]
    left_y2 = left[1] + left[3]
    right_x2 = right[0] + right[2]
    right_y2 = right[1] + right[3]
    intersection_width = max(0.0, min(left_x2, right_x2) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left_y2, right_y2) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    union = left[2] * left[3] + right[2] * right[3] - intersection
    return intersection / union if union > 0 else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
