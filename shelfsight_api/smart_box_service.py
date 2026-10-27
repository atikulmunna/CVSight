from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select

from shelfsight_api.config import SegmenterSettings
from shelfsight_api.models import MAX_POLYGON_POINTS, images

# A find answers with up to 300 outlines of up to 1,000 points, about 25 bytes a point.
MAX_SEGMENTER_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_FOUND_OBJECTS = 300
# A status check answers quickly or not at all; the smart routes keep the long timeout.
STATUS_TIMEOUT_SECONDS = 5.0


class SmartBoxError(RuntimeError):
    """A smart box request that could not be answered, with a stable code."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class SmartBoxImage:
    canonical_media_key: str
    width: int
    height: int


@dataclass(frozen=True)
class SmartBox:
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
class SmartPolygon:
    points: list[tuple[float, float]]
    score: float


@dataclass(frozen=True)
class Exemplar:
    """An example box, in pixels, of the objects to find."""

    x: float
    y: float
    width: float
    height: float


def smart_box_image(connection: Connection, image_id: UUID) -> SmartBoxImage:
    row = connection.execute(
        select(
            images.c.canonical_media_key,
            images.c.canonical_width,
            images.c.canonical_height,
        ).where(images.c.id == image_id)
    ).one_or_none()
    if row is None:
        raise SmartBoxError("image_not_found", 404, "image does not exist")
    return SmartBoxImage(row.canonical_media_key, row.canonical_width, row.canonical_height)


class SegmenterClient:
    """Talks to the SAM segmenter service in runtimes/sam3_segmenter.

    The service keeps each photo's encoding by checksum, so a photo is uploaded only when
    the service says it does not hold it; later clicks on that photo send just the point.
    """

    def __init__(
        self,
        settings: SegmenterSettings,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._settings = settings
        self._open = opener
        self._timeout = timeout_seconds

    def box_at(self, image: SmartBoxImage, content: bytes, x: float, y: float) -> SmartBox:
        """The box around the object at a point, given the photo's canonical bytes."""
        _require_inside(image, x, y)
        return _validated_box(self._ask(content, "/v1/segment", {"x": x, "y": y}), image)

    def polygon_at(
        self, image: SmartBoxImage, content: bytes, points: Sequence[PromptPoint]
    ) -> SmartPolygon:
        """The outline of the object that included points lie on and excluded ones do not."""
        for point in points:
            _require_inside(image, point.x, point.y)
        prompt = {
            "points": [{"x": point.x, "y": point.y, "include": point.include} for point in points]
        }
        return _validated_polygon(self._ask(content, "/v1/polygon", prompt), image)

    def check(self) -> None:
        """Raises a SmartBoxError unless the service answers with the configured token."""
        self._send("GET", "/v1/health", None, "application/json", STATUS_TIMEOUT_SECONDS)

    def prepare(self, content: bytes) -> None:
        """Sends the photo unless the service already holds it, so the first click on the
        photo does not wait for the upload and SAM's encoding."""
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            self._send("GET", f"/v1/images/{sha256}", None, "application/json")
        except _ImageNotLoaded:
            self._upload(sha256, content)

    def find_all(
        self,
        image: SmartBoxImage,
        content: bytes,
        text: str | None,
        exemplars: Sequence[Exemplar],
        threshold: float,
    ) -> list[SmartPolygon]:
        """The outlines of every object matching a phrase, example boxes, or both."""
        for exemplar in exemplars:
            if exemplar.x + exemplar.width > image.width or (
                exemplar.y + exemplar.height > image.height
            ):
                raise SmartBoxError(
                    "exemplar_outside_image", 422, "an example box is outside the image"
                )
        prompt = {
            "text": text,
            "exemplars": [
                {"x": item.x, "y": item.y, "width": item.width, "height": item.height}
                for item in exemplars
            ],
            "threshold": threshold,
        }
        answer = self._ask(content, "/v1/find", prompt)
        objects = answer.get("objects") if isinstance(answer, dict) else None
        if not isinstance(objects, list) or len(objects) > MAX_FOUND_OBJECTS:
            raise _failed("the smart box service answer is malformed")
        return [_validated_polygon(item, image) for item in objects]

    def _ask(self, content: bytes, path: str, prompt: dict[str, Any]) -> Any:
        sha256 = hashlib.sha256(content).hexdigest()
        body = json.dumps({"image_sha256": sha256, **prompt}).encode("utf-8")
        try:
            return self._send("POST", path, body, "application/json")
        except _ImageNotLoaded:
            self._upload(sha256, content)
            return self._send("POST", path, body, "application/json")

    def _upload(self, sha256: str, content: bytes) -> None:
        self._send("PUT", f"/v1/images/{sha256}", content, "application/octet-stream")

    def _send(
        self,
        method: str,
        path: str,
        body: bytes | None,
        content_type: str,
        timeout_seconds: float | None = None,
    ) -> Any:
        request = urllib.request.Request(
            self._settings.url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._settings.token}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )
        try:
            with self._open(request, timeout=timeout_seconds or self._timeout) as response:
                payload = response.read(MAX_SEGMENTER_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise _http_error(error) from error
        except (urllib.error.URLError, OSError) as error:
            raise SmartBoxError(
                "segmenter_unavailable",
                503,
                "the smart box service is unreachable",
            ) from error
        if not payload:
            return None
        if len(payload) > MAX_SEGMENTER_RESPONSE_BYTES:
            raise _failed("the smart box service answer is too large")
        try:
            return json.loads(payload)
        except ValueError as error:
            raise _failed("the smart box service answer is not JSON") from error


class _ImageNotLoaded(Exception):
    """The service does not hold the photo yet."""


def _http_error(error: urllib.error.HTTPError) -> Exception:
    code = _error_code(error)
    if error.code == 404 and code == "image_not_loaded":
        return _ImageNotLoaded()
    if error.code == 404 and code is None:
        return SmartBoxError(
            "segmenter_outdated",
            502,
            "the smart box service does not offer this tool; update it",
        )
    if error.code == 422 and code == "no_object":
        return SmartBoxError("no_object", 422, "no object was found at that point")
    if error.code == 401:
        return SmartBoxError(
            "segmenter_rejected",
            502,
            "the smart box service refused the configured token",
        )
    return _failed(f"the smart box service answered HTTP {error.code}")


def _error_code(error: urllib.error.HTTPError) -> str | None:
    try:
        body = json.loads(error.read(MAX_SEGMENTER_RESPONSE_BYTES))
    except (ValueError, OSError):
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None


def _require_inside(image: SmartBoxImage, x: float, y: float) -> None:
    if not (0 <= x < image.width and 0 <= y < image.height):
        raise SmartBoxError("point_outside_image", 422, "the point is outside the image")


def _validated_box(answer: Any, image: SmartBoxImage) -> SmartBox:
    if not isinstance(answer, dict):
        raise _failed("the smart box service answer is malformed")
    x, y, width, height, score = (
        _finite(answer.get(key)) for key in ("x", "y", "width", "height", "score")
    )
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > image.width
        or y + height > image.height
        or not 0 <= score <= 1
    ):
        raise _failed("the smart box lies outside the image")
    return SmartBox(x=x, y=y, width=width, height=height, score=score)


def _validated_polygon(answer: Any, image: SmartBoxImage) -> SmartPolygon:
    if not isinstance(answer, dict):
        raise _failed("the smart box service answer is malformed")
    polygon = answer.get("polygon")
    score = _finite(answer.get("score"))
    if not isinstance(polygon, list) or not 3 <= len(polygon) <= MAX_POLYGON_POINTS:
        raise _failed("the smart polygon has too few or too many points")
    points = [_polygon_point(point) for point in polygon]
    if not all(0 <= x <= image.width and 0 <= y <= image.height for x, y in points):
        raise _failed("the smart polygon lies outside the image")
    if not 0 <= score <= 1:
        raise _failed("the smart box service answer is malformed")
    return SmartPolygon(points=points, score=score)


def _polygon_point(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise _failed("the smart polygon has a malformed point")
    return _finite(value[0]), _finite(value[1])


def _finite(value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise _failed("the smart box service answer is malformed")
    return float(value)


def _failed(message: str) -> SmartBoxError:
    return SmartBoxError("segmenter_failed", 502, message)
