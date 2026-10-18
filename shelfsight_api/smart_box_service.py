from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select

from shelfsight_api.config import SegmenterSettings
from shelfsight_api.models import images

MAX_SEGMENTER_RESPONSE_BYTES = 4096


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
        if not (0 <= x < image.width and 0 <= y < image.height):
            raise SmartBoxError("point_outside_image", 422, "the point is outside the image")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            answer = self._segment(sha256, x, y)
        except _ImageNotLoaded:
            self._upload(sha256, content)
            answer = self._segment(sha256, x, y)
        return _validated_box(answer, image)

    def _segment(self, sha256: str, x: float, y: float) -> Any:
        body = json.dumps({"image_sha256": sha256, "x": x, "y": y}).encode("utf-8")
        return self._send("POST", "/v1/segment", body, "application/json")

    def _upload(self, sha256: str, content: bytes) -> None:
        self._send("PUT", f"/v1/images/{sha256}", content, "application/octet-stream")

    def _send(self, method: str, path: str, body: bytes, content_type: str) -> Any:
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
            with self._open(request, timeout=self._timeout) as response:
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


def _validated_box(answer: Any, image: SmartBoxImage) -> SmartBox:
    if not isinstance(answer, dict):
        raise _failed("the smart box service answer is malformed")
    values: list[float] = []
    for key in ("x", "y", "width", "height", "score"):
        value = answer.get(key)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise _failed("the smart box service answer is malformed")
        values.append(float(value))
    x, y, width, height, score = values
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


def _failed(message: str) -> SmartBoxError:
    return SmartBoxError("segmenter_failed", 502, message)
