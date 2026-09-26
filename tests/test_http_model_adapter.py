from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from shelfsight_api.model_adapters import (
    MAX_RUNTIME_RESPONSE_BYTES,
    AdapterExecutionError,
    HttpModelAdapter,
)
from shelfsight_api.model_contract import DetectResponse, parse_model_request
from shelfsight_api.model_runtime import (
    CLIP_MODEL_ROOT_ENV,
    DETECTOR_RUNTIME_URL_ENV,
    RFDETR_CHECKPOINT_ENV,
    SAM3_CHECKPOINT_ENV,
    configured_model_job_definitions,
)
from shelfsight_api.model_service import ModelService, ResolvedModelImage

IMAGE_ID = uuid4()
IMAGE_BYTES = b"canonical image bytes"
IMAGE_SHA256 = "a" * 64


def detect_request() -> Any:
    return parse_model_request(
        {
            "request_id": "request-detect",
            "operation": "detect",
            "image": {
                "image_id": str(IMAGE_ID),
                "sha256": IMAGE_SHA256,
                "width": 100,
                "height": 80,
            },
            "model_role": "known_sku_detector",
        }
    )


@pytest.fixture
def runtime() -> Iterator[tuple[str, list[bytes]]]:
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            answer = json.dumps(
                {
                    "model_provenance": {
                        "model_id": "shelf-detector",
                        "model_version": "v1",
                        "artifact_sha256": "b" * 64,
                    },
                    "predictions": [
                        {
                            "proposal_id": "request-detect:1",
                            "geometry": {
                                "type": "axis_aligned_box",
                                "x": 10,
                                "y": 12,
                                "width": 20,
                                "height": 30,
                            },
                            "class_type": "product",
                            "score": 0.91,
                            "candidate_sku_id": None,
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", received
    server.shutdown()
    server.server_close()


def test_a_runtime_answer_passes_the_same_contract_checks(
    runtime: tuple[str, list[bytes]],
    tmp_path: Path,
) -> None:
    url, received = runtime
    image_path = tmp_path / "canonical.jpg"
    image_path.write_bytes(IMAGE_BYTES)
    service = ModelService(
        {"known_sku_detector": HttpModelAdapter(url)},
        lambda image: ResolvedModelImage(image_path, IMAGE_SHA256, 100, 80),
    )

    response = service.execute(detect_request().model_dump(mode="json"))

    assert isinstance(response, DetectResponse)
    assert response.predictions[0].score == 0.91
    assert response.model_provenance.artifact_sha256 == "b" * 64
    (body,) = received
    assert b'name="request"' in body and b'"request_id":"request-detect"' in body.replace(b" ", b"")
    assert b'name="image"' in body and IMAGE_BYTES in body


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (urllib.error.HTTPError("u", 400, "bad", {}, None), "model_runtime_rejected", False),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 503, "busy", {}, None), "model_runtime_failed", True),  # type: ignore[arg-type]
        (urllib.error.URLError("refused"), "model_runtime_unavailable", True),
    ],
)
def test_runtime_failures_map_to_retryable_or_final_errors(
    failure: Exception,
    code: str,
    retryable: bool,
) -> None:
    def opener(*_args: object, **_kwargs: object) -> Any:
        raise failure

    with pytest.raises(AdapterExecutionError) as error:
        HttpModelAdapter("http://runtime.local", opener=opener).execute(detect_request(), None)

    assert (error.value.code, error.value.retryable) == (code, retryable)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"not json", "invalid_model_response"),
        (b"[1, 2]", "invalid_model_response"),
        (b"x" * (MAX_RUNTIME_RESPONSE_BYTES + 1), "model_response_too_large"),
    ],
    ids=["not-json", "not-an-object", "too-large"],
)
def test_unusable_runtime_answers_are_refused(payload: bytes, code: str) -> None:
    def opener(*_args: object, **_kwargs: object) -> Any:
        return BytesIO(payload)

    with pytest.raises(AdapterExecutionError) as error:
        HttpModelAdapter("https://runtime.local", opener=opener).execute(detect_request(), None)

    assert error.value.code == code
    assert error.value.retryable is False


def test_runtime_url_must_be_http() -> None:
    with pytest.raises(ValueError, match="http or https"):
        HttpModelAdapter("file:///etc/passwd")


def test_worker_serves_detection_from_a_configured_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (RFDETR_CHECKPOINT_ENV, SAM3_CHECKPOINT_ENV, CLIP_MODEL_ROOT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(DETECTOR_RUNTIME_URL_ENV, "http://127.0.0.1:9")
    monkeypatch.setattr("shelfsight_api.model_runtime.get_engine", lambda: None)
    monkeypatch.setattr("shelfsight_api.model_runtime.get_media_root", lambda: tmp_path)

    assert set(configured_model_job_definitions()) == {"detect"}

    monkeypatch.setenv(RFDETR_CHECKPOINT_ENV, "detector.pth")
    with pytest.raises(ValueError, match="not both"):
        configured_model_job_definitions()
