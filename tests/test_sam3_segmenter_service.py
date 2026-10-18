from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from runtimes.sam3_segmenter import __main__ as runtime_main
from runtimes.sam3_segmenter import service
from runtimes.sam3_segmenter.service import SegmentedBox, create_app

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeSegmenter:
    model_id = "fake-sam"
    model_version = "test"

    def __init__(self) -> None:
        self.encoded: list[tuple[int, int]] = []

    def encode(self, image: Image.Image) -> Any:
        self.encoded.append(image.size)
        return image.size

    def segment(self, encoded: Any, x: float, y: float) -> SegmentedBox | None:
        if x < 5:
            return None
        return SegmentedBox(x=x - 4, y=y - 4, width=8, height=8, score=0.9)


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
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401]
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
