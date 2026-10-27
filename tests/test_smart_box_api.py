from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, insert

from runtimes.sam3_segmenter.service import (
    PromptPoint,
    Region,
    SegmentedBox,
    SegmentedPolygon,
    create_app,
)
from shelfsight_api.app import app
from shelfsight_api.config import (
    SEGMENTER_TOKEN_ENV,
    SEGMENTER_URL_ENV,
    ConfigurationError,
    SegmenterSettings,
    get_segmenter_settings,
)
from shelfsight_api.models import datasets, images
from shelfsight_api.smart_box_service import (
    Exemplar,
    SegmenterClient,
    SmartBoxError,
    SmartBoxImage,
)
from shelfsight_api.smart_box_service import PromptPoint as ClickPoint

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
TRIANGLE = [[10, 12], [30, 12], [20, 42]]


def polygon_json(**fields: Any) -> bytes:
    return json.dumps({"polygon": TRIANGLE, "score": 0.7, **fields}).encode()


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


def test_asks_for_an_outline_with_every_click() -> None:
    opener, sent = scripted(polygon_json())
    clicks = [ClickPoint(15, 20, include=True), ClickPoint(40, 50, include=False)]

    outline = SegmenterClient(SETTINGS, opener=opener).polygon_at(IMAGE, b"photo", clicks)

    assert outline.points == [(10, 12), (30, 12), (20, 42)]
    assert outline.score == 0.7
    assert sent[0].full_url == "http://segmenter.test/v1/polygon"
    assert json.loads(sent[0].data or b"") == {
        "image_sha256": hashlib.sha256(b"photo").hexdigest(),
        "points": [
            {"x": 15, "y": 20, "include": True},
            {"x": 40, "y": 50, "include": False},
        ],
    }


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (http_error(404), "segmenter_outdated"),
        (polygon_json(polygon=TRIANGLE[:2]), "segmenter_failed"),
        (polygon_json(polygon=[*TRIANGLE[:2], [101, 12]]), "segmenter_failed"),
        (polygon_json(polygon=[*TRIANGLE[:2], [20]]), "segmenter_failed"),
        (polygon_json(polygon=[*TRIANGLE[:2], [20, None]]), "segmenter_failed"),
        (polygon_json(score=1.5), "segmenter_failed"),
        (polygon_json(polygon={"x": 1}), "segmenter_failed"),
    ],
)
def test_refuses_an_outline_it_cannot_trust(answer: Any, code: str) -> None:
    opener, _ = scripted(answer)

    with pytest.raises(SmartBoxError) as raised:
        SegmenterClient(SETTINGS, opener=opener).polygon_at(
            IMAGE, b"photo", [ClickPoint(15, 20, include=True)]
        )

    assert (raised.value.code, raised.value.status_code) == (code, 502)


def test_prepares_a_photo_by_uploading_only_what_the_service_lacks() -> None:
    opener, sent = scripted(b"", http_error(404, {"detail": {"code": "image_not_loaded"}}), b"")
    segmenter = SegmenterClient(SETTINGS, opener=opener)

    segmenter.prepare(b"held")
    segmenter.prepare(b"photo")

    checksum = hashlib.sha256(b"photo").hexdigest()
    assert [request.get_method() for request in sent] == ["GET", "GET", "PUT"]
    assert sent[0].full_url.endswith(hashlib.sha256(b"held").hexdigest())
    assert sent[2].full_url == f"http://segmenter.test/v1/images/{checksum}"
    assert sent[2].data == b"photo"


def test_finds_objects_by_phrase_and_example_and_checks_every_outline() -> None:
    opener, sent = scripted(
        json.dumps({"objects": [{"polygon": TRIANGLE, "score": 0.8}]}).encode(),
        json.dumps({"objects": [{"polygon": TRIANGLE[:2], "score": 0.8}]}).encode(),
    )
    segmenter = SegmenterClient(SETTINGS, opener=opener)
    example = Exemplar(10, 12, 20, 30)

    found = segmenter.find_all(IMAGE, b"photo", "bottle", [example], 0.4)
    with pytest.raises(SmartBoxError) as malformed:
        segmenter.find_all(IMAGE, b"photo", None, [example], 0.5)
    with pytest.raises(SmartBoxError) as outside:
        segmenter.find_all(IMAGE, b"photo", None, [Exemplar(90, 12, 20, 30)], 0.5)

    assert [(item.points, item.score) for item in found] == [([(10, 12), (30, 12), (20, 42)], 0.8)]
    assert sent[0].full_url == "http://segmenter.test/v1/find"
    assert json.loads(sent[0].data or b"") == {
        "image_sha256": hashlib.sha256(b"photo").hexdigest(),
        "text": "bottle",
        "exemplars": [{"x": 10, "y": 12, "width": 20, "height": 30}],
        "threshold": 0.4,
    }
    assert malformed.value.code == "segmenter_failed"
    assert outside.value.code == "exemplar_outside_image"
    assert len(sent) == 2


def test_refuses_a_point_outside_the_image_without_calling_the_service() -> None:
    opener, sent = scripted()

    with pytest.raises(SmartBoxError) as raised:
        SegmenterClient(SETTINGS, opener=opener).box_at(IMAGE, b"photo", 100, 20)
    with pytest.raises(SmartBoxError) as outline_raised:
        SegmenterClient(SETTINGS, opener=opener).polygon_at(
            IMAGE, b"photo", [ClickPoint(15, 20, include=True), ClickPoint(15, 80, include=False)]
        )

    assert raised.value.code == "point_outside_image"
    assert outline_raised.value.code == "point_outside_image"
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

    def find(
        self,
        encoded: Any,
        text: str | None,
        exemplars: Sequence[Region],
        threshold: float,
    ) -> list[SegmentedPolygon]:
        # One object per example box, and one more for a phrase.
        squares = [(box.x + 30, box.y) for box in exemplars] + ([(5, 5)] if text else [])
        return [
            SegmentedPolygon(points=[(x, y), (x + 10, y), (x + 10, y + 10), (x, y + 10)], score=0.7)
            for x, y in squares
        ]

    def outline(self, encoded: Any, points: Sequence[PromptPoint]) -> SegmentedPolygon | None:
        # Each excluded click trims the right edge, so the test sees every click arrive.
        first = points[0]
        right = first.x + 10 - 2 * sum(not point.include for point in points)
        return SegmentedPolygon(
            points=[(first.x - 10, first.y - 10), (right, first.y - 10), (first.x, first.y + 10)],
            score=0.6,
        )


def connect_runtime(monkeypatch: pytest.MonkeyPatch, segmenter: FakeSegmenter) -> None:
    """Points the API at the real service app, run in process around a fake SAM."""
    runtime = TestClient(create_app(segmenter, TOKEN))
    monkeypatch.setenv(SEGMENTER_URL_ENV, "http://segmenter.test")
    monkeypatch.setenv(SEGMENTER_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        "shelfsight_api.smart_box_api.segmenter_client",
        lambda settings: SegmenterClient(settings, opener=runtime_opener(runtime)),
    )


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
    connect_runtime(monkeypatch, segmenter)
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


def test_outlines_the_object_under_clicks_through_the_service(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_runtime(monkeypatch, FakeSegmenter())
    url = f"/api/images/{photo}/smart-polygon"
    included = {"x": 40, "y": 30, "include": True}
    excluded = {"x": 48, "y": 30, "include": False}

    single = client.post(url, json={"points": [included]})
    refined = client.post(url, json={"points": [included, excluded]})
    only_excluded = client.post(url, json={"points": [excluded]})
    too_many = client.post(url, json={"points": [included] * 17})
    outside = client.post(url, json={"points": [included, {**excluded, "y": 80}]})

    assert single.status_code == 200
    assert single.json() == {"polygon": [[30, 20], [50, 20], [40, 40]], "score": 0.6}
    assert refined.json()["polygon"][1] == [48, 20]
    assert only_excluded.status_code == 422
    assert too_many.status_code == 422
    assert outside.json()["detail"]["code"] == "point_outside_image"


def test_finds_objects_on_a_photo_through_the_service(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_runtime(monkeypatch, FakeSegmenter())
    url = f"/api/images/{photo}/smart-find"
    example = {"x": 10, "y": 20, "width": 15, "height": 25}

    found = client.post(url, json={"text": " bottle ", "exemplars": [example]})
    no_prompt = client.post(url, json={"text": "  "})
    outside = client.post(url, json={"exemplars": [{**example, "x": 90}]})

    assert found.status_code == 200
    assert found.json() == {
        "objects": [
            {"polygon": [[40, 20], [50, 20], [50, 30], [40, 30]], "score": 0.7},
            {"polygon": [[5, 5], [15, 5], [15, 15], [5, 15]], "score": 0.7},
        ]
    }
    assert no_prompt.status_code == 422
    assert outside.json()["detail"]["code"] == "exemplar_outside_image"


def test_prepares_a_photo_once_ahead_of_the_first_click(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segmenter = FakeSegmenter()
    connect_runtime(monkeypatch, segmenter)

    first = client.post(f"/api/images/{photo}/smart-prepare")
    again = client.post(f"/api/images/{photo}/smart-prepare")
    click = client.post(f"/api/images/{photo}/smart-box", json={"x": 40, "y": 30})
    missing = client.post(f"/api/images/{uuid4()}/smart-prepare")

    assert (first.status_code, again.status_code, click.status_code) == (204, 204, 200)
    assert segmenter.encoded == 1
    assert missing.json()["detail"]["code"] == "image_not_found"


def test_says_when_no_smart_box_service_is_configured(
    photo: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SEGMENTER_URL_ENV, raising=False)

    response = client.post(f"/api/images/{photo}/smart-box", json={"x": 1, "y": 1})
    outline = client.post(
        f"/api/images/{photo}/smart-polygon",
        json={"points": [{"x": 1, "y": 1, "include": True}]},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "smart_box_unavailable"
    assert outline.json()["detail"]["code"] == "smart_box_unavailable"
