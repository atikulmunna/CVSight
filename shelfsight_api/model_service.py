from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.image_ingest import ImageNotFoundError, get_image_record
from shelfsight_api.job_service import JobClaim
from shelfsight_api.media import MediaValidationError, resolve_media_path
from shelfsight_api.model_adapters import (
    AdapterExecutionError,
    ModelAdapter,
)
from shelfsight_api.model_contract import (
    DetectRequest,
    DetectResponse,
    EmbedRequest,
    EmbedResponse,
    ImageInput,
    ModelRequest,
    ModelResponse,
    RefineRequest,
    RefineResponse,
    RetrieveRequest,
    RetrieveResponse,
    parse_model_request,
    parse_model_response,
    validate_box_bounds,
)
from shelfsight_api.models import images, sku_reference_images
from shelfsight_api.worker import (
    JobDefinition,
    JobExecutionError,
    JobHandler,
    JobReporter,
    JobResultWriter,
)

MAX_MODEL_REQUEST_BYTES = 64 * 1024
MAX_MODEL_RESPONSE_BYTES = 256 * 1024
MAX_MODEL_IMAGE_BYTES = 50 * 1024 * 1024


class ModelServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ResolvedModelImage:
    path: Path
    sha256: str
    width: int
    height: int


ImageResolver = Callable[[ImageInput], ResolvedModelImage]


class ModelService:
    def __init__(
        self,
        adapters: Mapping[str, ModelAdapter],
        image_resolver: ImageResolver,
        *,
        timeouts: Mapping[str, float] | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._image_resolver = image_resolver
        self._timeouts = {
            "detect": 300.0,
            "refine": 30.0,
            "embed": 60.0,
            "retrieve": 5.0,
            **(timeouts or {}),
        }

    def execute(self, value: Any) -> ModelResponse:
        _validate_json_size(value, MAX_MODEL_REQUEST_BYTES, "model_request_too_large")
        try:
            request = parse_model_request(value)
        except ValidationError as error:
            raise ModelServiceError(
                "invalid_model_request",
                "model request is invalid",
                retryable=False,
            ) from error

        image = self._resolve_image(request)
        adapter = self._adapters.get(_adapter_key(request))
        if adapter is None:
            raise ModelServiceError(
                "model_unavailable",
                "no adapter is registered for this model role",
                retryable=False,
            )

        started = time.perf_counter()
        try:
            output = adapter.execute(request, image.path if image else None)
        except AdapterExecutionError as error:
            raise ModelServiceError(
                error.code,
                error.message,
                retryable=error.retryable,
            ) from error
        except Exception as error:
            raise ModelServiceError(
                "model_execution_failed",
                "model execution failed",
                retryable=True,
            ) from error
        elapsed_ms = (time.perf_counter() - started) * 1000
        timeout_seconds = self._timeouts[request.operation]
        if elapsed_ms > timeout_seconds * 1000:
            raise ModelServiceError(
                "model_timeout",
                "model operation exceeded its timeout",
                retryable=True,
            )

        response_value = {
            "request_id": request.request_id,
            "operation": request.operation,
            "input_fingerprint": _input_fingerprint(request),
            "configuration": request.configuration.model_dump(mode="json"),
            "timing_ms": {"total": elapsed_ms},
            **dict(output),
        }
        _validate_json_size(
            response_value,
            MAX_MODEL_RESPONSE_BYTES,
            "model_response_too_large",
        )
        try:
            response = parse_model_response(response_value)
            _validate_response_for_request(request, response)
            return response
        except (ValidationError, ValueError) as error:
            raise ModelServiceError(
                "invalid_model_response",
                "model returned an invalid response",
                retryable=False,
            ) from error

    def _resolve_image(
        self,
        request: ModelRequest,
    ) -> ResolvedModelImage | None:
        if isinstance(request, RetrieveRequest):
            return None
        try:
            image = self._image_resolver(request.image)
        except ModelServiceError:
            raise
        except Exception as error:
            raise ModelServiceError(
                "model_input_unavailable",
                "canonical model input is unavailable",
                retryable=True,
            ) from error
        if (
            image.sha256 != request.image.sha256
            or image.width != request.image.width
            or image.height != request.image.height
        ):
            raise ModelServiceError(
                "stale_input_fingerprint",
                "model input no longer matches the requested fingerprint",
                retryable=False,
            )
        return image


def database_image_resolver(engine: Engine, media_root: Path) -> ImageResolver:
    def resolve(image: ImageInput) -> ResolvedModelImage:
        try:
            record = get_image_record(engine, image.image_id)
            path = resolve_media_path(media_root, str(record["canonical_media_key"]))
            if not path.is_file() or path.stat().st_size > MAX_MODEL_IMAGE_BYTES:
                raise ModelServiceError(
                    "model_input_unavailable",
                    "canonical model input is unavailable",
                    retryable=True,
                )
            return ResolvedModelImage(
                path=path,
                sha256=str(record["content_sha256"]),
                width=int(record["canonical_width"]),
                height=int(record["canonical_height"]),
            )
        except ImageNotFoundError as error:
            raise ModelServiceError(
                "model_image_not_found",
                "model image does not exist",
                retryable=False,
            ) from error
        except (MediaValidationError, OSError, SQLAlchemyError) as error:
            raise ModelServiceError(
                "model_input_unavailable",
                "canonical model input is unavailable",
                retryable=True,
            ) from error

    return resolve


def database_embedding_image_resolver(
    engine: Engine,
    media_root: Path,
) -> ImageResolver:
    def resolve(image: ImageInput) -> ResolvedModelImage:
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    select(
                        images.c.canonical_media_key.label("media_key"),
                        images.c.content_sha256,
                        images.c.canonical_width.label("width"),
                        images.c.canonical_height.label("height"),
                    ).where(images.c.id == image.image_id)
                ).mappings().one_or_none()
                if row is None:
                    row = connection.execute(
                        select(
                            sku_reference_images.c.canonical_media_key.label("media_key"),
                            sku_reference_images.c.content_sha256,
                            sku_reference_images.c.width,
                            sku_reference_images.c.height,
                        ).where(sku_reference_images.c.id == image.image_id)
                    ).mappings().one_or_none()
            if row is None:
                raise ModelServiceError(
                    "model_image_not_found",
                    "model image does not exist",
                    retryable=False,
                )
            path = resolve_media_path(media_root, str(row["media_key"]))
            if not path.is_file() or path.stat().st_size > MAX_MODEL_IMAGE_BYTES:
                raise ModelServiceError(
                    "model_input_unavailable",
                    "canonical model input is unavailable",
                    retryable=True,
                )
            return ResolvedModelImage(
                path=path,
                sha256=str(row["content_sha256"]),
                width=int(row["width"]),
                height=int(row["height"]),
            )
        except ModelServiceError:
            raise
        except (MediaValidationError, OSError, SQLAlchemyError) as error:
            raise ModelServiceError(
                "model_input_unavailable",
                "canonical model input is unavailable",
                retryable=True,
            ) from error

    return resolve


def model_job_definitions(
    service: ModelService,
    result_writers: Mapping[str, JobResultWriter] | None = None,
) -> dict[str, JobDefinition]:
    def run(claim: JobClaim, _reporter: JobReporter) -> Mapping[str, Any]:
        try:
            response = service.execute(claim.payload)
            return response.model_dump(mode="json")
        except ModelServiceError as error:
            raise JobExecutionError(
                error.code,
                error.message,
                retryable=error.retryable,
            ) from error

    writers = result_writers or {}
    return {
        operation: JobDefinition(
            run=cast(JobHandler, run),
            write_result=writers.get(operation),
        )
        for operation in ("detect", "refine", "embed", "retrieve")
    }


def _adapter_key(request: ModelRequest) -> str:
    if isinstance(request, RetrieveRequest):
        return f"retrieve:{request.purpose}"
    return request.model_role


def _input_fingerprint(request: ModelRequest) -> str:
    if isinstance(request, RetrieveRequest):
        return f"embedding:{request.embedding_id}"
    if not isinstance(request, EmbedRequest):
        return f"sha256:{request.image.sha256}"
    value = {
        "image_sha256": request.image.sha256,
        "crop": request.crop.model_dump(mode="json"),
        "purpose": request.purpose,
        "target": (
            request.target.model_dump(mode="json")
            if request.target is not None
            else None
        ),
    }
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_response_for_request(
    request: ModelRequest,
    response: ModelResponse,
) -> None:
    if response.request_id != request.request_id or response.operation != request.operation:
        raise ValueError("response identity does not match its request")
    if isinstance(request, DetectRequest) and isinstance(response, DetectResponse):
        for prediction in response.predictions:
            validate_box_bounds(prediction.geometry, request.image)
        return
    if isinstance(request, RefineRequest) and isinstance(response, RefineResponse):
        validate_box_bounds(response.result.geometry, request.image)
        return
    if isinstance(request, EmbedRequest) and isinstance(response, EmbedResponse):
        if response.embedding.purpose != request.purpose:
            raise ValueError("embedding purpose does not match its request")
        return
    if isinstance(request, RetrieveRequest) and isinstance(response, RetrieveResponse):
        if (
            response.index_provenance.index_namespace != request.index_namespace
            or response.index_provenance.index_version != request.index_version
            or response.index_provenance.purpose != request.purpose
            or len(response.candidates) > request.top_k
        ):
            raise ValueError("retrieval response does not match its request")
        expected_subject = "sku" if request.purpose == "recognition" else "annotation"
        if any(
            candidate.subject_type != expected_subject
            for candidate in response.candidates
        ):
            raise ValueError("retrieval candidate type does not match its purpose")
        return
    raise ValueError("response type does not match its request")


def _validate_json_size(value: Any, limit: int, error_code: str) -> None:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ModelServiceError(
            "invalid_model_request",
            "model request is not finite JSON",
            retryable=False,
        ) from error
    if len(encoded) > limit:
        raise ModelServiceError(
            error_code,
            "model payload exceeds the size limit",
            retryable=False,
        )
