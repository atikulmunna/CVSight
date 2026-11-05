from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from shelfsight_api import annotation_crop
from shelfsight_api.annotation_crop import CropSource, render_crop
from shelfsight_api.media import MediaMissingError, open_canonical_image

PHOTO_SIZE = (400, 300)
MEDIA_KEY = "canonical/ab/photo.png"


def _source(x: float = 100.0) -> CropSource:
    return CropSource(MEDIA_KEY, PHOTO_SIZE, (x, 50.0, 200.0, 150.0))


def _write_photo(media_root: Path) -> None:
    path = media_root / MEDIA_KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", PHOTO_SIZE, "white").save(path)


@pytest.fixture
def photo_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    real_open = open_canonical_image

    def counting_open(media_root: Path, media_key: str, size: tuple[int, int]) -> Any:
        opened.append(media_key)
        return real_open(media_root, media_key, size)

    monkeypatch.setattr(annotation_crop, "open_canonical_image", counting_open)
    return opened


def test_the_same_crop_decodes_the_photo_once(tmp_path: Path, photo_opens: list[str]) -> None:
    _write_photo(tmp_path)

    first = render_crop(tmp_path, _source())
    second = render_crop(tmp_path, _source())

    assert first == second
    assert len(photo_opens) == 1


def test_another_box_on_the_same_photo_is_rendered_again(
    tmp_path: Path, photo_opens: list[str]
) -> None:
    _write_photo(tmp_path)

    render_crop(tmp_path, _source(x=100.0))
    render_crop(tmp_path, _source(x=120.0))

    assert len(photo_opens) == 2


def test_a_failed_crop_is_not_remembered(tmp_path: Path, photo_opens: list[str]) -> None:
    with pytest.raises(MediaMissingError):
        render_crop(tmp_path, _source())

    _write_photo(tmp_path)

    assert render_crop(tmp_path, _source())
    assert len(photo_opens) == 2
