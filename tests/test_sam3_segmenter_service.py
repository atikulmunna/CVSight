from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from runtimes.sam3_segmenter import __main__ as runtime_main
from runtimes.sam3_segmenter import service
from runtimes.sam3_segmenter.service import (
    PromptPoint,
    Region,
    SegmentedBox,
    SegmentedPolygon,
    create_app,
)

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeSegmenter:
    model_id = "fake-sam"
    model_version = "test"

    def __init__(self) -> None:
        self.encoded: list[tuple[int, int]] = []
        self.prompts: list[list[PromptPoint]] = []
        self.finds: list[tuple[str | None, list[Region], float]] = []

    def encode(self, image: Image.Image) -> Any:
        self.encoded.append(image.size)
        return image.size

    def segment(self, encoded: Any, x: float, y: float) -> SegmentedBox | None:
        if x < 5:
            return None
        return SegmentedBox(x=x - 4, y=y - 4, width=8, height=8, score=0.9)

    def find(
        self,
        encoded: Any,
        text: str | None,
        exemplars: Sequence[Region],
        threshold: float,
    ) -> list[SegmentedPolygon]:
        self.finds.append((text, list(exemplars), threshold))
        found = SegmentedPolygon(points=[(1, 1), (5, 1), (3, 6)], score=0.9)
        return [found, found] if text else [found]

    def outline(self, encoded: Any, points: Sequence[PromptPoint]) -> SegmentedPolygon | None:
        self.prompts.append(list(points))
        first = points[0]
        if first.x < 5:
            return None
        return SegmentedPolygon(
            points=[(first.x - 4, first.y - 4), (first.x + 4, first.y - 4), (first.x, first.y + 4)],
            score=0.8,
        )


def png(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


def upload(client: TestClient, body: bytes) -> tuple[str, int]:
    sha = hashlib.sha256(body).hexdigest()
    return sha, client.put(f"/v1/images/{sha}", content=body, headers=AUTH).status_code


def click(client: TestClient, sha: str, x: float, y: float, **extra: Any) -> Any:
    return client.post(
        "/v1/segment",
        json={"image_sha256": sha, "x": x, "y": y, **extra},
        headers=AUTH,
    )


def outline(client: TestClient, sha: str, points: list[dict[str, Any]]) -> Any:
    return client.post(
        "/v1/polygon",
        json={"image_sha256": sha, "points": points},
        headers=AUTH,
    )


@pytest.fixture
def segmenter() -> FakeSegmenter:
    return FakeSegmenter()


@pytest.fixture
def client(segmenter: FakeSegmenter) -> TestClient:
    return TestClient(create_app(segmenter, TOKEN, cache_size=1))


def test_encodes_a_photo_once_and_answers_each_click_with_a_box(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    sha, status = upload(client, png())

    first = click(client, sha, 20, 15)
    second = click(client, sha, 30, 20)

    assert status == 204
    assert first.status_code == 200
    assert first.json() == {"x": 16, "y": 11, "width": 8, "height": 8, "score": 0.9}
    assert second.json()["x"] == 26
    assert segmenter.encoded == [(40, 30)]
    assert client.get("/v1/health", headers=AUTH).json() == {
        "model_id": "fake-sam",
        "model_version": "test",
    }


def test_answers_included_and_excluded_clicks_with_an_outline(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    sha, _ = upload(client, png())

    response = outline(
        client,
        sha,
        [{"x": 20, "y": 15, "include": True}, {"x": 30, "y": 20, "include": False}],
    )

    assert response.status_code == 200
    assert response.json() == {"polygon": [[16, 11], [24, 11], [20, 19]], "score": 0.8}
    assert segmenter.prompts == [
        [PromptPoint(20, 15, include=True), PromptPoint(30, 20, include=False)]
    ]


def test_refuses_outline_clicks_it_cannot_answer(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    sha, _ = upload(client, png())
    included = {"x": 20, "y": 15, "include": True}

    assert outline(client, "0" * 64, [included]).json() == {"detail": {"code": "image_not_loaded"}}
    assert outline(client, sha, [{**included, "include": False}]).json() == {
        "detail": {"code": "no_included_point"}
    }
    assert outline(client, sha, [included, {"x": 20, "y": 30, "include": False}]).json() == {
        "detail": {"code": "point_outside_image"}
    }
    assert outline(client, sha, [{"x": 2, "y": 15, "include": True}]).json() == {
        "detail": {"code": "no_object"}
    }
    assert outline(client, sha, []).status_code == 422
    assert outline(client, sha, [included] * 17).status_code == 422
    assert outline(client, sha, [{"x": 20, "y": 15}]).status_code == 422
    assert segmenter.prompts == [[PromptPoint(2, 15, include=True)]]


def test_finds_every_object_matching_a_phrase_or_example_boxes(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    sha, _ = upload(client, png())
    example = {"x": 2, "y": 2, "width": 10, "height": 8}

    by_text = client.post("/v1/find", json={"image_sha256": sha, "text": " bottle "}, headers=AUTH)
    by_example = client.post(
        "/v1/find",
        json={"image_sha256": sha, "exemplars": [example], "threshold": 0.3},
        headers=AUTH,
    )

    found = {"polygon": [[1, 1], [5, 1], [3, 6]], "score": 0.9}
    assert by_text.json() == {"objects": [found, found]}
    assert by_example.json() == {"objects": [found]}
    assert segmenter.finds == [("bottle", [], 0.5), (None, [Region(2, 2, 10, 8)], 0.3)]


def test_refuses_a_find_without_a_prompt_or_with_an_example_off_the_photo(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    sha, _ = upload(client, png())

    def find(**body: Any) -> Any:
        return client.post("/v1/find", json={"image_sha256": sha, **body}, headers=AUTH)

    small = {"x": 1, "y": 1, "width": 2, "height": 2}
    assert find().status_code == 422
    assert find(text="   ").status_code == 422
    assert find(exemplars=[{"x": 35, "y": 2, "width": 10, "height": 8}]).json() == {
        "detail": {"code": "exemplar_outside_image"}
    }
    assert find(text="bottle", threshold=1.5).status_code == 422
    assert find(exemplars=[small] * 9).status_code == 422
    assert segmenter.finds == []


def test_says_whether_it_holds_a_photo(client: TestClient) -> None:
    body = png()
    sha = hashlib.sha256(body).hexdigest()

    before = client.get(f"/v1/images/{sha}", headers=AUTH)
    upload(client, body)
    after = client.get(f"/v1/images/{sha}", headers=AUTH)

    assert before.status_code == 404
    assert before.json() == {"detail": {"code": "image_not_loaded"}}
    assert after.status_code == 204
    assert client.get(f"/v1/images/{sha}").status_code == 401


def test_rejects_every_request_without_the_token(
    client: TestClient,
    segmenter: FakeSegmenter,
) -> None:
    body = png()
    sha = hashlib.sha256(body).hexdigest()
    wrong = {"Authorization": "Bearer " + "x" * 40}

    responses = [
        client.get("/v1/health"),
        client.get("/v1/health", headers=wrong),
        client.put(f"/v1/images/{sha}", content=body, headers=wrong),
        client.post("/v1/segment", json={"image_sha256": sha, "x": 1, "y": 1}),
        client.post(
            "/v1/polygon",
            json={"image_sha256": sha, "points": [{"x": 1, "y": 1, "include": True}]},
        ),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401]
    assert responses[0].json() == {"detail": {"code": "unauthorized"}}
    assert segmenter.encoded == []


def test_asks_for_a_photo_it_does_not_hold_and_keeps_only_the_recent_ones(
    client: TestClient,
) -> None:
    first_sha, _ = upload(client, png(40, 30))
    second_sha, _ = upload(client, png(50, 30))

    assert click(client, "0" * 64, 10, 10).json() == {"detail": {"code": "image_not_loaded"}}
    assert click(client, first_sha, 10, 10).status_code == 404
    assert click(client, second_sha, 10, 10).status_code == 200


def test_rejects_wrong_checksums_invalid_images_and_clicks_outside_the_photo(
    client: TestClient,
) -> None:
    body = png()
    junk = b"not an image"
    sha, _ = upload(client, body)

    mismatch = client.put(f"/v1/images/{'0' * 64}", content=body, headers=AUTH)
    malformed = client.put("/v1/images/not-a-checksum", content=body, headers=AUTH)
    invalid = client.put(
        f"/v1/images/{hashlib.sha256(junk).hexdigest()}",
        content=junk,
        headers=AUTH,
    )

    assert mismatch.json() == {"detail": {"code": "checksum_mismatch"}}
    assert malformed.json() == {"detail": {"code": "invalid_checksum"}}
    assert invalid.json() == {"detail": {"code": "invalid_image"}}
    assert click(client, sha, 40, 10).json() == {"detail": {"code": "point_outside_image"}}
    assert click(client, sha, -1, 10).status_code == 422
    assert click(client, sha, 10, 10, positive=False).status_code == 422
    assert click(client, sha, 2, 10).json() == {"detail": {"code": "no_object"}}


def test_refuses_an_image_over_the_size_limit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 10)
    body = png()

    response = client.put(
        f"/v1/images/{hashlib.sha256(body).hexdigest()}",
        content=body,
        headers=AUTH,
    )

    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "image_too_large"}}


def test_requires_a_long_token_and_a_usable_cache(segmenter: FakeSegmenter) -> None:
    with pytest.raises(ValueError, match="32 characters"):
        create_app(segmenter, "short")
    with pytest.raises(ValueError, match="at least one photo"):
        create_app(segmenter, TOKEN, cache_size=0)


def test_command_refuses_to_start_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(runtime_main.TOKEN_ENV, "too-short")

    with pytest.raises(SystemExit):
        runtime_main.main(["--checkpoint", "sam3.pt"])

    assert runtime_main.TOKEN_ENV in capsys.readouterr().err
