from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from shelfsight_api.model_adapters import (
    FakeModelAdapter,
    ModelAdapter,
    ModelProvenance,
    RfdetrAdapter,
)
from shelfsight_api.model_contract import (
    DetectResponse,
    EmbedResponse,
    RefineResponse,
    RetrieveResponse,
)
from shelfsight_api.model_runtime import (
    CLIP_MODEL_ROOT_ENV,
    RFDETR_CHECKPOINT_ENV,
    RFDETR_VERSION_ENV,
    SAM3_CHECKPOINT_ENV,
    SAM3_VERSION_ENV,
    configured_model_job_definitions,
)
from shelfsight_api.model_service import (
    ModelService,
    ModelServiceError,
    ResolvedModelImage,
)

IMAGE_ID = uuid4()
IMAGE_SHA256 = "a" * 64


def image_request(operation: str, **values: Any) -> dict[str, Any]:
    return {
        "request_id": f"request-{operation}",
        "operation": operation,
        "image": {
            "image_id": str(IMAGE_ID),
            "sha256": IMAGE_SHA256,
            "width": 100,
            "height": 80,
        },
        "model_role": {
            "detect": "known_sku_detector",
            "refine": "box_refiner",
            "embed": "crop_embedder",
        }[operation],
        **values,
    }


def resolver(tmp_path: Path):
    image_path = tmp_path / "canonical.png"
    image_path.write_bytes(b"contract-image")

    def resolve(_image: object) -> ResolvedModelImage:
        return ResolvedModelImage(
            path=image_path,
            sha256=IMAGE_SHA256,
            width=100,
            height=80,
        )

    return resolve


@pytest.mark.parametrize(
    "adapter",
    [
        FakeModelAdapter(),
        RfdetrAdapter(
            lambda _path, _request: [
                {
                    "bbox": [10.0, 12.0, 30.0, 40.0],
                    "score": 0.88,
                }
            ],
            ModelProvenance(
                model_id="rfdetr-nano-known-sku",
                model_version="1.8.3-one-epoch",
                artifact_sha256="b" * 64,
            ),
        ),
    ],
    ids=["fake", "selected-rfdetr"],
)
def test_fake_and_selected_detector_pass_the_same_contract(
    adapter: ModelAdapter,
    tmp_path: Path,
) -> None:
    service = ModelService(
        {"known_sku_detector": adapter},
        resolver(tmp_path),
    )

    response = service.execute(
        image_request(
            "detect",
            configuration={
                "confidence_threshold": 0.3,
                "inference_strategy": "full_image",
            },
        )
    )

    assert isinstance(response, DetectResponse)
    assert response.request_id == "request-detect"
    assert response.input_fingerprint == f"sha256:{IMAGE_SHA256}"
    assert response.configuration.confidence_threshold == 0.3
    assert response.model_provenance.model_version
    assert response.timing_ms.total >= 0
    assert response.predictions


def test_fake_adapter_implements_refine_embed_and_retrieve_contracts(
    tmp_path: Path,
) -> None:
    fake = FakeModelAdapter()
    service = ModelService(
        {
            "box_refiner": fake,
            "crop_embedder": fake,
            "retrieve:recognition": fake,
        },
        resolver(tmp_path),
    )
    box = {
        "type": "axis_aligned_box",
        "x": 10,
        "y": 12,
        "width": 30,
        "height": 40,
    }

    refined = service.execute(
        image_request(
            "refine",
            box_hint=box,
            configuration={"return_geometry": "axis_aligned_box"},
        )
    )
    embedded = service.execute(
        image_request(
            "embed",
            crop=box,
            purpose="recognition",
            configuration={"normalize": True},
        )
    )
    retrieved = service.execute(
        {
            "request_id": "request-retrieve",
            "operation": "retrieve",
            "embedding_id": str(uuid4()),
            "purpose": "recognition",
            "index_namespace": "recognition:clip-vit-b32:baseline-001",
            "index_version": "gallery-001",
            "top_k": 5,
            "configuration": {
                "distance": "cosine",
                "calibration_version": "validation-001",
            },
        }
    )

    assert isinstance(refined, RefineResponse)
    assert isinstance(embedded, EmbedResponse)
    assert embedded.embedding.dimension == len(embedded.embedding.values)
    assert embedded.input_fingerprint.startswith("sha256:")
    assert isinstance(retrieved, RetrieveResponse)
    assert retrieved.input_fingerprint.startswith("embedding:")
    assert retrieved.unknown_decision.automatic_confirmation is False


def test_invalid_geometry_stale_inputs_and_oversized_requests_fail_stably(
    tmp_path: Path,
) -> None:
    service = ModelService(
        {"box_refiner": FakeModelAdapter()},
        resolver(tmp_path),
    )

    with pytest.raises(ModelServiceError, match="invalid") as invalid:
        service.execute(
            image_request(
                "refine",
                box_hint={
                    "type": "axis_aligned_box",
                    "x": 90,
                    "y": 10,
                    "width": 20,
                    "height": 20,
                },
                configuration={"return_geometry": "axis_aligned_box"},
            )
        )
    assert invalid.value.code == "invalid_model_request"
    assert invalid.value.retryable is False

    stale_service = ModelService(
        {"known_sku_detector": FakeModelAdapter()},
        lambda _image: ResolvedModelImage(
            path=tmp_path / "image.png",
            sha256="b" * 64,
            width=100,
            height=80,
        ),
    )
    with pytest.raises(ModelServiceError) as stale:
        stale_service.execute(image_request("detect"))
    assert stale.value.code == "stale_input_fingerprint"

    with pytest.raises(ModelServiceError) as oversized:
        service.execute({"padding": "x" * (65 * 1024)})
    assert oversized.value.code == "model_request_too_large"


def test_timeout_and_adapter_failures_have_deterministic_safe_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([10.0, 11.0])
    monkeypatch.setattr(
        "shelfsight_api.model_service.time.perf_counter",
        lambda: next(times),
    )
    timed = ModelService(
        {"known_sku_detector": FakeModelAdapter()},
        resolver(tmp_path),
        timeouts={"detect": 0.1},
    )
    with pytest.raises(ModelServiceError) as timeout:
        timed.execute(image_request("detect"))
    assert timeout.value.code == "model_timeout"
    assert timeout.value.retryable is True
    monkeypatch.undo()

    class BrokenAdapter:
        def execute(
            self,
            _request: object,
            _image_path: Path | None,
        ) -> Mapping[str, Any]:
            raise RuntimeError("C:\\private\\checkpoint secret")

    broken = ModelService(
        {"known_sku_detector": BrokenAdapter()},
        resolver(tmp_path),
    )
    with pytest.raises(ModelServiceError) as failure:
        broken.execute(image_request("detect"))
    assert failure.value.code == "model_execution_failed"
    assert "private" not in failure.value.message
    assert "checkpoint" not in failure.value.message


def test_invalid_adapter_output_is_rejected_before_persistence(tmp_path: Path) -> None:
    class InvalidAdapter:
        def execute(
            self,
            _request: object,
            _image_path: Path | None,
        ) -> Mapping[str, Any]:
            return {
                "model_provenance": {
                    "model_id": "broken",
                    "model_version": "broken-001",
                    "artifact_sha256": "c" * 64,
                },
                "predictions": [
                    {
                        "proposal_id": "outside",
                        "geometry": {
                            "type": "axis_aligned_box",
                            "x": 95,
                            "y": 10,
                            "width": 20,
                            "height": 20,
                        },
                        "class_type": "product",
                        "score": 0.9,
                        "candidate_sku_id": None,
                    }
                ],
            }

    service = ModelService(
        {"known_sku_detector": InvalidAdapter()},
        resolver(tmp_path),
    )
    with pytest.raises(ModelServiceError) as invalid:
        service.execute(image_request("detect"))
    assert invalid.value.code == "invalid_model_response"
    assert invalid.value.retryable is False


def test_frontend_has_no_concrete_model_branching() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "src"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in frontend.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    ).lower()

    assert "rfdetr" not in source
    assert "rf-detr" not in source
    assert "sam3" not in source
    assert "clip-vit" not in source


def test_worker_registers_models_only_from_operator_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RFDETR_CHECKPOINT_ENV, raising=False)
    monkeypatch.delenv(RFDETR_VERSION_ENV, raising=False)
    monkeypatch.delenv(SAM3_CHECKPOINT_ENV, raising=False)
    monkeypatch.delenv(SAM3_VERSION_ENV, raising=False)
    monkeypatch.delenv(CLIP_MODEL_ROOT_ENV, raising=False)
    assert configured_model_job_definitions() == {}

    monkeypatch.setenv(RFDETR_CHECKPOINT_ENV, "configured-checkpoint.pth")
    with pytest.raises(ValueError, match=RFDETR_VERSION_ENV):
        configured_model_job_definitions()

    monkeypatch.delenv(RFDETR_CHECKPOINT_ENV)
    monkeypatch.setenv(SAM3_CHECKPOINT_ENV, "configured-sam.pt")
    with pytest.raises(ValueError, match=SAM3_VERSION_ENV):
        configured_model_job_definitions()

    monkeypatch.setenv(RFDETR_CHECKPOINT_ENV, "configured-checkpoint.pth")
    monkeypatch.setenv(RFDETR_VERSION_ENV, "detector-test")
    monkeypatch.setenv(SAM3_VERSION_ENV, "refiner-test")
    with pytest.raises(ValueError, match="separate worker processes"):
        configured_model_job_definitions()
