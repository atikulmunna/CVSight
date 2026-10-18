from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, insert

from runtimes.sam3_segmenter.service import SegmentedBox, create_app
from shelfsight_api.app import app
from shelfsight_api.config import (
    SEGMENTER_TOKEN_ENV,
    SEGMENTER_URL_ENV,
    ConfigurationError,
    SegmenterSettings,
    get_segmenter_settings,
)
from shelfsight_api.models import datasets, images
from shelfsight_api.smart_box_service import SegmenterClient, SmartBoxError, SmartBoxImage

client = TestClient(app)
TOKEN = "s" * 40
SETTINGS = SegmenterSettings(url="http://segmenter.test", token=TOKEN)
IMAGE = SmartBoxImage(canonical_media_key="canonical/x.png", width=100, height=80)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def http_error(code: int, body: Any = None) -> urllib.error.HTTPError:
    content = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError(
        "http://segmenter.test", code, "error", hdrs=None, fp=io.BytesIO(content)
    )


def scripted(*answers: Any) -> tuple[Callable[..., Any], list[urllib.request.Request]]:
    """An opener that replays answers in order: bytes are bodies, exceptions are raised."""
    remaining = list(answers)
    sent: list[urllib.request.Request] = []

    def open_request(request: urllib.request.Request, timeout: float) -> FakeResponse:
        sent.append(request)
        answer = remaining.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return FakeResponse(answer)

    return open_request, sent


def box_json(**fields: Any) -> bytes:
    box = {"x": 10, "y": 12, "width": 20, "height": 30, "score": 0.8, **fields}
    return json.dumps(box).encode()


BOX = box_json()


def test_settings_are_optional_but_must_be_complete_when_given() -> None:
    assert get_segmenter_settings({}) is None
    assert get_segmenter_settings(
        {SEGMENTER_URL_ENV: "http://127.0.0.1:8093/", SEGMENTER_TOKEN_ENV: TOKEN}
    ) == SegmenterSettings(url="http://127.0.0.1:8093", token=TOKEN)
    with pytest.raises(ConfigurationError, match=SEGMENTER_URL_ENV):
        get_segmenter_settings({SEGMENTER_URL_ENV: "ftp://host", SEGMENTER_TOKEN_ENV: TOKEN})
    with pytest.raises(ConfigurationError, match=SEGMENTER_TOKEN_ENV):
        get_segmenter_settings({SEGMENTER_URL_ENV: "http://host", SEGMENTER_TOKEN_ENV: "short"})


def test_uploads_a_photo_only_when_the_service_asks_for_it() -> None:
    opener, sent = scripted(
        http_error(404, {"detail": {"code": "image_not_loaded"}}),
        b"",
        BOX,
        BOX,
    )
    segmenter = SegmenterClient(SETTINGS, opener=opener)

    first = segmenter.box_at(IMAGE, b"photo", 15, 20)
    second = segmenter.box_at(IMAGE, b"photo", 16, 21)

    checksum = hashlib.sha256(b"photo").hexdigest()
    assert (first.x, first.y, first.width, first.height, first.score) == (10, 12, 20, 30, 0.8)
    assert second == first
    assert [(request.get_method(), request.full_url) for request in sent] == [
        ("POST", "http://segmenter.test/v1/segment"),
        ("PUT", f"http://segmenter.test/v1/images/{checksum}"),
        ("POST", "http://segmenter.test/v1/segment"),
        ("POST", "http://segmenter.test/v1/segment"),
    ]
    assert sent[1].data == b"photo"
    assert sent[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert json.loads(sent[0].data or b"") == {"image_sha256": checksum, "x": 15, "y": 20}


@pytest.mark.parametrize(
    ("answer", "code", "status_code"),
    [
        (urllib.error.URLError("refused"), "segmenter_unavailable", 503),
        (http_error(401, {"detail": {"code": "unauthorized"}}), "segmenter_rejected", 502),
        (http_error(422, {"detail": {"code": "no_object"}}), "no_object", 422),
        (http_error(500), "segmenter_failed", 502),
        (b"not json", "segmenter_failed", 502),
        (box_json(x=90, width=20), "segmenter_failed", 502),
        (box_json(score=True), "segmenter_failed", 502),
    ],
)
def test_maps_service_failures_to_stable_errors(answer: Any, code: str, status_code: int) -> None:
    opener, _ = scripted(answer)

    with pytest.raises(SmartBoxError) as raised:
        SegmenterClient(SETTINGS, opener=opener).box_at(IMAGE, b"photo", 15, 20)

    assert (raised.value.code, raised.value.status_code) == (code, status_code)


def test_refuses_a_point_outside_the_image_without_calling_the_service() -> None:
    opener, sent = scripted()

    with pytest.raises(SmartBoxError) as raised:
        SegmenterClient(SETTINGS, opener=opener).box_at(IMAGE, b"photo", 100, 20)

    assert raised.value.code == "point_outside_image"
    assert sent == []


class FakeSegmenter:
    model_id = "fake-sam"
    model_version = "test"

    def __init__(self) -> None:
        self.encoded = 0

    def encode(self, image: Image.Image) -> Any:
        self.encoded += 1
        return image.size

    def segment(self, encoded: Any, x: float, y: float) -> SegmentedBox | None:
        return SegmentedBox(x=x - 5, y=y - 5, width=10, height=10, score=0.75)


def runtime_opener(runtime: TestClient) -> Callable[..., Any]:
    """Sends the API's requests to the real service app instead of over the network."""

    def open_request(request: urllib.request.Request, timeout: float) -> FakeResponse:
        response = runtime.request(
            request.get_method(),
            urllib.parse.urlsplit(request.full_url).path,
            content=request.data,
            headers=dict(request.header_items()),
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status_code,
                "error",
                hdrs=None,
                fp=io.BytesIO(response.content),
            )
        return FakeResponse(response.content)

    return open_request


@pytest.fixture
def photo(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[UUID]:
    dataset_id = uuid4()
    image_id = uuid4()
    media_key = f"canonical/{image_id.hex}.png"
    (tmp_path / "canonical").mkdir()
    Image.new("RGB", (100, 80), "white").save(tmp_path / media_key, "PNG")
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}"))
        connection.execute(
            insert(images).values(
                id=image_id,
                dataset_id=dataset_id,
                original_media_key=f"original/{image_id}.png",
                canonical_media_key=media_key,
                thumbnail_media_key=f"thumbnails/{image_id}.jpg",
                media_type="image/png",
                original_filename=f"{image_id}.png",
                content_sha256=image_id.hex * 2,
                canonical_width=100,
                canonical_height=80,
            )
        )
    monkeypatch.setattr("shelfsight_api.smart_box_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.smart_box_api.get_media_root", lambda: tmp_path)

    yield image_id

    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))


def test_finds_the_box_under_a_click_through_the_service(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segmenter = FakeSegmenter()
    runtime = TestClient(create_app(segmenter, TOKEN))
    monkeypatch.setenv(SEGMENTER_URL_ENV, "http://segmenter.test")
    monkeypatch.setenv(SEGMENTER_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        "shelfsight_api.smart_box_api.segmenter_client",
        lambda settings: SegmenterClient(settings, opener=runtime_opener(runtime)),
    )
    annotator = {"X-ShelfSight-Actor": "annotator:test"}
    url = f"/api/images/{photo}/smart-box"

    first = client.post(url, json={"x": 40, "y": 30}, headers=annotator)
    second = client.post(url, json={"x": 60, "y": 30}, headers=annotator)
    outside = client.post(url, json={"x": 100, "y": 30})
    missing = client.post(f"/api/images/{uuid4()}/smart-box", json={"x": 1, "y": 1})

    assert first.status_code == 200
    assert first.json() == {"x": 35, "y": 25, "width": 10, "height": 10, "score": 0.75}
    assert second.json()["x"] == 55
    assert segmenter.encoded == 1
    assert outside.json()["detail"]["code"] == "point_outside_image"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "image_not_found"


def test_says_when_no_smart_box_service_is_configured(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SEGMENTER_URL_ENV, raising=False)

    response = client.post(f"/api/images/{photo}/smart-box", json={"x": 1, "y": 1})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "smart_box_unavailable"
