from __future__ import annotations

import hashlib
import hmac
import io
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_PROMPT_POINTS = 16
MAX_POLYGON_POINTS = 1_000
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

T = TypeVar("T")


@dataclass(frozen=True)
class SegmentedBox:
    x: float
    y: float
    width: float
    height: float
    score: float


@dataclass(frozen=True)
class PromptPoint:
    x: float
    y: float
    # True for a click on the object, False for a click on something it must leave out.
    include: bool


@dataclass(frozen=True)
class SegmentedPolygon:
    # At most MAX_POLYGON_POINTS [x, y] corners, in pixels.
    points: list[tuple[float, float]]
    score: float


class Segmenter(Protocol):
    model_id: str
    model_version: str

    # The expensive image encoding, done once per photo.
    def encode(self, image: Image.Image) -> Any: ...

    # The box around the object at one positive click, or None when nothing is there.
    def segment(self, encoded: Any, x: float, y: float) -> SegmentedBox | None: ...

    # The outline of the object the clicks describe, or None when nothing is there.
    def outline(self, encoded: Any, points: Sequence[PromptPoint]) -> SegmentedPolygon | None: ...


class SegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_sha256: str = Field(pattern=SHA256_PATTERN.pattern)
    x: FiniteFloat = Field(ge=0)
    y: FiniteFloat = Field(ge=0)


class PromptPointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: FiniteFloat = Field(ge=0)
    y: FiniteFloat = Field(ge=0)
    include: bool


class PolygonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_sha256: str = Field(pattern=SHA256_PATTERN.pattern)
    points: list[PromptPointRequest] = Field(min_length=1, max_length=MAX_PROMPT_POINTS)


@dataclass(frozen=True)
class _Encoded:
    state: Any
    width: int
    height: int


def create_app(segmenter: Segmenter, token: str, cache_size: int = 8) -> FastAPI:
    """Serve clicks against photos the caller uploads once, keyed by their SHA-256.

    Every request must carry the bearer token: on a shared cluster the port is reachable
    by other users of the node's network.
    """
    if len(token) < 32:
        raise ValueError("the token must be at least 32 characters")
    if cache_size < 1:
        raise ValueError("the cache must hold at least one photo")

    app = FastAPI(
        title="CVSight SAM 3 segmenter",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    cache: OrderedDict[str, _Encoded] = OrderedDict()
    # One GPU serves every request, so model calls run one at a time.
    model_lock = threading.Lock()

    def locked(function: Callable[..., T], *args: Any) -> T:
        with model_lock:
            return function(*args)

    def authorize(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
            raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized")

    @app.get("/v1/health")
    def health(request: Request) -> dict[str, str]:
        authorize(request)
        return {"model_id": segmenter.model_id, "model_version": segmenter.model_version}

    @app.put("/v1/images/{image_sha256}", status_code=status.HTTP_204_NO_CONTENT)
    async def load_image(image_sha256: str, request: Request) -> Response:
        # The token is checked before the body is read, so strangers cannot fill memory.
        authorize(request)
        if not SHA256_PATTERN.fullmatch(image_sha256):
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_checksum")
        body = await _read_limited(request)
        if hashlib.sha256(body).hexdigest() != image_sha256:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "checksum_mismatch")
        image = _decode(body)
        state = await run_in_threadpool(locked, segmenter.encode, image)
        cache[image_sha256] = _Encoded(state, image.width, image.height)
        cache.move_to_end(image_sha256)
        while len(cache) > cache_size:
            cache.popitem(last=False)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def cached(image_sha256: str) -> _Encoded:
        encoded = cache.get(image_sha256)
        if encoded is None:
            raise _error(status.HTTP_404_NOT_FOUND, "image_not_loaded")
        cache.move_to_end(image_sha256)
        return encoded

    def require_inside(encoded: _Encoded, x: float, y: float) -> None:
        if x >= encoded.width or y >= encoded.height:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "point_outside_image")

    @app.post("/v1/segment")
    def segment(body: SegmentRequest, request: Request) -> dict[str, float]:
        authorize(request)
        encoded = cached(body.image_sha256)
        require_inside(encoded, body.x, body.y)
        box = locked(segmenter.segment, encoded.state, body.x, body.y)
        if box is None:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_object")
        return {
            "x": box.x,
            "y": box.y,
            "width": box.width,
            "height": box.height,
            "score": box.score,
        }

    @app.post("/v1/polygon")
    def polygon(body: PolygonRequest, request: Request) -> dict[str, Any]:
        authorize(request)
        encoded = cached(body.image_sha256)
        for point in body.points:
            require_inside(encoded, point.x, point.y)
        if not any(point.include for point in body.points):
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_included_point")
        points = [PromptPoint(point.x, point.y, point.include) for point in body.points]
        outline = locked(segmenter.outline, encoded.state, points)
        if outline is None:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_object")
        return {
            "polygon": [[x, y] for x, y in outline.points],
            "score": outline.score,
        }

    return app


async def _read_limited(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise _error(status.HTTP_413_CONTENT_TOO_LARGE, "image_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(body: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(body)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "image_too_large")
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_image") from error


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})
