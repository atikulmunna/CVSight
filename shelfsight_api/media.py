from __future__ import annotations

import hashlib
import json
import os
import tempfile
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 120_000_000
MAX_MANIFEST_ITEMS = 250
MAX_CAPTURE_METADATA_BYTES = 16 * 1024
THUMBNAIL_MAX_SIZE = 512
READ_CHUNK_SIZE = 1024 * 1024

SUPPORTED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
}


class MediaValidationError(ValueError):
    code = "invalid_image"


class ImageTooLargeError(MediaValidationError):
    code = "image_too_large"


class CorruptImageError(MediaValidationError):
    code = "corrupt_image"


class UnsupportedImageError(MediaValidationError):
    code = "unsupported_image"


class UnsafePathError(MediaValidationError):
    code = "unsafe_path"


@dataclass(frozen=True)
class PreparedImage:
    original_bytes: bytes
    canonical_bytes: bytes
    thumbnail_bytes: bytes
    content_sha256: str
    original_media_key: str
    canonical_media_key: str
    thumbnail_media_key: str
    original_filename: str
    media_type: str
    canonical_width: int
    canonical_height: int
    capture_metadata: dict[str, Any]


def read_limited(stream: BinaryIO, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := stream.read(READ_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise ImageTooLargeError(f"image exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    if total == 0:
        raise CorruptImageError("image is empty")
    return b"".join(chunks)


def read_limited_file(path: Path, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            raise ImageTooLargeError(f"image exceeds the {max_bytes}-byte limit")
        with path.open("rb") as stream:
            return read_limited(stream, max_bytes)
    except MediaValidationError:
        raise
    except OSError as error:
        raise CorruptImageError("image cannot be read") from error


def parse_capture_metadata(value: str | None) -> dict[str, Any]:
    if value is None or not value.strip():
        return {}
    if len(value.encode("utf-8")) > MAX_CAPTURE_METADATA_BYTES:
        raise MediaValidationError("capture metadata is too large")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise MediaValidationError("capture metadata must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise MediaValidationError("capture metadata must be a JSON object")
    return validate_capture_metadata(parsed)


def validate_capture_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CAPTURE_METADATA_BYTES:
        raise MediaValidationError("capture metadata is too large")
    return dict(value)


def prepare_image(
    image_bytes: bytes,
    original_filename: str,
    capture_metadata: Mapping[str, Any] | None = None,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
    thumbnail_size: int = THUMBNAIL_MAX_SIZE,
) -> PreparedImage:
    digest = hashlib.sha256(image_bytes).hexdigest()
    safe_name = sanitize_filename(original_filename)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as source:
                image_format = source.format
                if image_format not in SUPPORTED_FORMATS:
                    raise UnsupportedImageError("only JPEG and PNG images are supported")
                original_width, original_height = source.size
                _validate_pixel_count(original_width, original_height, max_pixels)
                orientation = source.getexif().get(274)
                source.load()
                canonical = ImageOps.exif_transpose(source).copy()
    except MediaValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageTooLargeError("decoded image exceeds the safe pixel limit") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise CorruptImageError("image cannot be decoded") from error

    canonical_width, canonical_height = canonical.size
    _validate_pixel_count(canonical_width, canonical_height, max_pixels)
    extension, media_type = SUPPORTED_FORMATS[image_format]
    canonical = _canonical_mode(canonical, image_format)
    canonical_bytes = _encode_canonical(canonical, image_format)
    thumbnail_bytes = _encode_thumbnail(canonical, thumbnail_size)
    prefix = digest[:2]

    return PreparedImage(
        original_bytes=image_bytes,
        canonical_bytes=canonical_bytes,
        thumbnail_bytes=thumbnail_bytes,
        content_sha256=digest,
        original_media_key=f"original/{prefix}/{digest}.{extension}",
        canonical_media_key=f"canonical/{prefix}/{digest}.{extension}",
        thumbnail_media_key=f"thumbnails/{prefix}/{digest}.jpg",
        original_filename=safe_name,
        media_type=media_type,
        canonical_width=canonical_width,
        canonical_height=canonical_height,
        capture_metadata={
            "provided": dict(capture_metadata or {}),
            "ingest": {
                "original_format": image_format,
                "original_width": original_width,
                "original_height": original_height,
                "exif_orientation": orientation,
                "orientation_normalized": True,
                "original_preserved": True,
                "face_blurring": "not_applied",
            },
        },
    )


def write_media_bundle(media_root: Path, image: PreparedImage) -> tuple[str, ...]:
    created: list[str] = []
    files = (
        (image.original_media_key, image.original_bytes),
        (image.canonical_media_key, image.canonical_bytes),
        (image.thumbnail_media_key, image.thumbnail_bytes),
    )
    try:
        for media_key, content in files:
            if _write_once(resolve_media_path(media_root, media_key), content):
                created.append(media_key)
    except OSError:
        remove_media_keys(media_root, created)
        raise
    return tuple(created)


def remove_media_keys(media_root: Path, media_keys: list[str] | tuple[str, ...]) -> None:
    for media_key in media_keys:
        path = resolve_media_path(media_root, media_key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def resolve_media_path(media_root: Path, media_key: str) -> Path:
    relative = _safe_relative_path(media_key)
    root = media_root.resolve()
    resolved = root.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(root):
        raise UnsafePathError("media key leaves the managed root")
    return resolved


def resolve_import_path(import_root: Path, relative_path: str) -> Path:
    resolved = resolve_import_location(import_root, relative_path)
    if not resolved.is_file():
        raise CorruptImageError("import image does not exist")
    return resolved


def resolve_import_location(import_root: Path, relative_path: str) -> Path:
    """A file or folder path kept inside the import root; it need not exist yet."""
    relative = _safe_relative_path(relative_path)
    root = import_root.resolve()
    resolved = root.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(root):
        raise UnsafePathError("import path leaves the configured root")
    return resolved


def sanitize_filename(filename: str) -> str:
    safe_name = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
    if not safe_name:
        return "upload"
    return safe_name[:255]


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or "://" in value or ":" in value:
        raise UnsafePathError("path must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafePathError("path must stay inside its configured root")
    return path


def _validate_pixel_count(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImageTooLargeError(f"decoded image exceeds the {max_pixels}-pixel limit")


def _canonical_mode(image: Image.Image, image_format: str) -> Image.Image:
    if image_format == "PNG" and ("A" in image.getbands() or "transparency" in image.info):
        return image.convert("RGBA")
    return image.convert("RGB")


def _encode_canonical(image: Image.Image, image_format: str) -> bytes:
    output = BytesIO()
    if image_format == "JPEG":
        image.save(output, format="JPEG", quality=95, subsampling=0, optimize=False)
    else:
        image.save(output, format="PNG", compress_level=6, optimize=False)
    return output.getvalue()


def _encode_thumbnail(image: Image.Image, max_size: int) -> bytes:
    thumbnail = image.copy()
    thumbnail.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    if thumbnail.mode != "RGB":
        background = Image.new("RGB", thumbnail.size, "white")
        if "A" in thumbnail.getbands():
            background.paste(thumbnail, mask=thumbnail.getchannel("A"))
        else:
            background.paste(thumbnail)
        thumbnail = background
    output = BytesIO()
    thumbnail.save(output, format="JPEG", quality=85, optimize=False)
    return output.getvalue()


def _write_once(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
