from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shelfsight_api.media import (
    CorruptImageError,
    ImageTooLargeError,
    MediaValidationError,
    UnsafePathError,
    UnsupportedImageError,
    parse_capture_metadata,
    prepare_image,
    read_limited,
    read_limited_file,
    remove_media_keys,
    resolve_import_path,
    resolve_media_path,
    write_media_bundle,
)


def image_bytes(
    image_format: str = "JPEG",
    *,
    size: tuple[int, int] = (40, 20),
    orientation: int | None = None,
) -> bytes:
    image = Image.new("RGB", size, "blue")
    output = BytesIO()
    if orientation is None:
        image.save(output, format=image_format)
    else:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(output, format=image_format, exif=exif)
    return output.getvalue()


def test_prepare_image_normalizes_orientation_and_builds_thumbnail() -> None:
    original = image_bytes(orientation=6)
    prepared = prepare_image(
        original,
        r"C:\incoming\shelf.jpg",
        {"store_id": "store-1"},
    )

    assert prepared.original_bytes == original
    assert (prepared.canonical_width, prepared.canonical_height) == (20, 40)
    assert prepared.original_filename == "shelf.jpg"
    assert prepared.media_type == "image/jpeg"
    assert prepared.capture_metadata["provided"] == {"store_id": "store-1"}
    assert prepared.capture_metadata["ingest"]["exif_orientation"] == 6
    assert prepared.capture_metadata["ingest"]["original_preserved"] is True
    assert prepared.capture_metadata["ingest"]["face_blurring"] == "not_applied"

    with Image.open(BytesIO(prepared.canonical_bytes)) as canonical:
        assert canonical.size == (20, 40)
        assert canonical.getexif().get(274) is None
    with Image.open(BytesIO(prepared.thumbnail_bytes)) as thumbnail:
        assert thumbnail.size == (20, 40)
        assert thumbnail.format == "JPEG"


def test_prepare_image_preserves_png_alpha_in_canonical_image() -> None:
    image = Image.new("RGBA", (12, 8), (0, 0, 255, 80))
    output = BytesIO()
    image.save(output, format="PNG")

    prepared = prepare_image(output.getvalue(), "transparent.png")

    with Image.open(BytesIO(prepared.canonical_bytes)) as canonical:
        assert canonical.mode == "RGBA"
        assert canonical.format == "PNG"


def test_prepare_image_rejects_corrupt_unsupported_and_oversized_images() -> None:
    with pytest.raises(CorruptImageError, match="cannot be decoded"):
        prepare_image(b"not an image", "broken.jpg")

    gif = BytesIO()
    Image.new("RGB", (2, 2)).save(gif, format="GIF")
    with pytest.raises(UnsupportedImageError, match="only JPEG and PNG"):
        prepare_image(gif.getvalue(), "image.gif")

    with pytest.raises(ImageTooLargeError, match="pixel limit"):
        prepare_image(image_bytes(size=(11, 10)), "large.jpg", max_pixels=100)


def test_bounded_read_rejects_empty_and_oversized_content(tmp_path: Path) -> None:
    with pytest.raises(CorruptImageError, match="empty"):
        read_limited(BytesIO())
    with pytest.raises(ImageTooLargeError, match="10-byte"):
        read_limited(BytesIO(b"12345678901"), max_bytes=10)

    image_path = tmp_path / "large.jpg"
    image_path.write_bytes(b"123456")
    with pytest.raises(ImageTooLargeError, match="5-byte"):
        read_limited_file(image_path, max_bytes=5)


def test_capture_metadata_requires_a_bounded_json_object() -> None:
    assert parse_capture_metadata('{"aisle": "A1"}') == {"aisle": "A1"}
    with pytest.raises(MediaValidationError, match="valid JSON"):
        parse_capture_metadata("{")
    with pytest.raises(MediaValidationError, match="JSON object"):
        parse_capture_metadata("[]")
    with pytest.raises(MediaValidationError, match="too large"):
        parse_capture_metadata('{"value":"' + ("x" * 17_000) + '"}')


def test_managed_storage_writes_once_and_supports_cleanup(tmp_path: Path) -> None:
    prepared = prepare_image(image_bytes(), "shelf.jpg")

    created = write_media_bundle(tmp_path, prepared)
    assert set(created) == {
        prepared.original_media_key,
        prepared.canonical_media_key,
        prepared.thumbnail_media_key,
    }
    assert write_media_bundle(tmp_path, prepared) == ()
    assert resolve_media_path(tmp_path, prepared.canonical_media_key).read_bytes()

    remove_media_keys(tmp_path, created)
    assert not resolve_media_path(tmp_path, prepared.original_media_key).exists()
    assert not resolve_media_path(tmp_path, prepared.canonical_media_key).exists()
    assert not resolve_media_path(tmp_path, prepared.thumbnail_media_key).exists()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.jpg",
        "/absolute.jpg",
        r"folder\image.jpg",
        "https://example.com/image.jpg",
        "C:/image.jpg",
    ],
)
def test_managed_and_import_paths_reject_unsafe_values(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    with pytest.raises(UnsafePathError):
        resolve_media_path(tmp_path, unsafe_path)
    with pytest.raises(UnsafePathError):
        resolve_import_path(tmp_path, unsafe_path)


def test_import_paths_are_confined_to_the_configured_root(tmp_path: Path) -> None:
    source = tmp_path / "batch" / "shelf.jpg"
    source.parent.mkdir()
    source.write_bytes(image_bytes())

    assert resolve_import_path(tmp_path, "batch/shelf.jpg") == source.resolve()
    with pytest.raises(CorruptImageError, match="does not exist"):
        resolve_import_path(tmp_path, "batch/missing.jpg")
