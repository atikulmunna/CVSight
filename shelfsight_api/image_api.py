from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import AnnotationUser, OwnerUser
from shelfsight_api.config import ConfigurationError, get_import_root, get_media_root
from shelfsight_api.database import get_engine
from shelfsight_api.image_ingest import (
    DatasetVersionFrozenError,
    DatasetVersionNotFoundError,
    DuplicateImageError,
    ImageNotFoundError,
    ImageReviewBlockedError,
    IngestedImage,
    get_image_record,
    ingest_prepared_image,
    list_version_images,
    mark_image_reviewed,
    media_variant,
)
from shelfsight_api.media import (
    MAX_MANIFEST_ITEMS,
    CorruptImageError,
    ImageTooLargeError,
    MediaValidationError,
    UnsupportedImageError,
    parse_capture_metadata,
    prepare_image,
    read_limited,
    read_limited_file,
    resolve_import_path,
    resolve_media_path,
    validate_capture_metadata,
)

router = APIRouter(prefix="/api")


class ImageResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    original_filename: str
    media_type: str
    content_sha256: str
    width: int
    height: int
    status: str
    capture_metadata: dict[str, Any]
    original_url: str
    canonical_url: str
    thumbnail_url: str


class ManifestItem(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    capture_metadata: dict[str, Any] = Field(default_factory=dict)


class ManifestRequest(BaseModel):
    items: list[ManifestItem] = Field(min_length=1, max_length=MAX_MANIFEST_ITEMS)


class ManifestOutcome(BaseModel):
    path: str
    outcome: Literal["imported", "duplicate", "rejected"]
    code: str
    image_id: UUID | None = None


class ManifestResponse(BaseModel):
    results: list[ManifestOutcome]


class ImageReviewResponse(BaseModel):
    id: UUID
    status: Literal["reviewed"]


ImageStatus = Literal["unlabeled", "pre_labeled", "in_progress", "labeled", "reviewed"]


class ImageSummaryResponse(BaseModel):
    id: UUID
    original_filename: str
    media_type: str
    width: int
    height: int
    status: ImageStatus
    created_at: datetime
    thumbnail_url: str


class ImageListResponse(BaseModel):
    images: list[ImageSummaryResponse]
    total: int
    limit: int
    offset: int


@router.get(
    "/datasets/{dataset_id}/versions/{dataset_version_id}/images",
    response_model=ImageListResponse,
)
def images_index(
    dataset_id: UUID,
    dataset_version_id: UUID,
    image_status: Annotated[ImageStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 60,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ImageListResponse:
    try:
        result = list_version_images(
            get_engine(),
            dataset_id,
            dataset_version_id,
            image_status,
            limit,
            offset,
        )
        return ImageListResponse(
            images=[_summary_response(image) for image in result["images"]],
            total=int(result["total"]),
            limit=limit,
            offset=offset,
        )
    except Exception as error:
        _raise_http_error(error)
        raise


@router.post(
    "/datasets/{dataset_id}/versions/{dataset_version_id}/images",
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_image(
    dataset_id: UUID,
    dataset_version_id: UUID,
    file: Annotated[UploadFile, File()],
    user: OwnerUser,
    capture_metadata: Annotated[str | None, Form()] = None,
) -> ImageResponse:
    del user
    try:
        metadata = parse_capture_metadata(capture_metadata)
        image_bytes = read_limited(file.file)
        prepared = prepare_image(image_bytes, file.filename or "upload", metadata)
        ingested = ingest_prepared_image(
            get_engine(),
            get_media_root(),
            dataset_id,
            dataset_version_id,
            prepared,
        )
        return _ingested_response(ingested)
    except Exception as error:
        _raise_http_error(error)
        raise


@router.post(
    "/datasets/{dataset_id}/versions/{dataset_version_id}/images/import",
    response_model=ManifestResponse,
)
def import_manifest(
    dataset_id: UUID,
    dataset_version_id: UUID,
    manifest: ManifestRequest,
    user: OwnerUser,
) -> ManifestResponse:
    del user
    try:
        import_root = get_import_root()
        media_root = get_media_root()
        engine = get_engine()
    except ConfigurationError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "managed storage is not configured",
        ) from error

    results: list[ManifestOutcome] = []
    for item in manifest.items:
        try:
            source_path = resolve_import_path(import_root, item.path)
            prepared = prepare_image(
                read_limited_file(source_path),
                source_path.name,
                validate_capture_metadata(item.capture_metadata),
            )
            ingested = ingest_prepared_image(
                engine,
                media_root,
                dataset_id,
                dataset_version_id,
                prepared,
            )
            results.append(
                ManifestOutcome(
                    path=item.path,
                    outcome="imported",
                    code="image_imported",
                    image_id=ingested.id,
                )
            )
        except DuplicateImageError as error:
            results.append(
                ManifestOutcome(
                    path=item.path,
                    outcome="duplicate",
                    code="duplicate_image",
                    image_id=error.image_id,
                )
            )
        except (
            MediaValidationError,
            DatasetVersionNotFoundError,
            DatasetVersionFrozenError,
        ) as error:
            results.append(
                ManifestOutcome(
                    path=item.path,
                    outcome="rejected",
                    code=_error_code(error),
                )
            )
        except (OSError, SQLAlchemyError):
            results.append(
                ManifestOutcome(
                    path=item.path,
                    outcome="rejected",
                    code="service_unavailable",
                )
            )
    return ManifestResponse(results=results)


@router.get("/images/{image_id}", response_model=ImageResponse)
def image_metadata(image_id: UUID) -> ImageResponse:
    try:
        return _record_response(get_image_record(get_engine(), image_id))
    except Exception as error:
        _raise_http_error(error)
        raise


@router.post("/images/{image_id}/reviewed", response_model=ImageReviewResponse)
def review_image(image_id: UUID, user: AnnotationUser) -> ImageReviewResponse:
    del user
    try:
        return ImageReviewResponse.model_validate(
            mark_image_reviewed(get_engine(), image_id)
        )
    except ImageReviewBlockedError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "image_review_blocked",
            str(error),
        ) from error
    except Exception as error:
        _raise_http_error(error)
        raise


@router.get("/images/{image_id}/media/{variant}")
def image_media(
    image_id: UUID,
    variant: Literal["original", "canonical", "thumbnail"],
) -> FileResponse:
    try:
        record = get_image_record(get_engine(), image_id)
        media_key, media_type = media_variant(record, variant)
        path = resolve_media_path(get_media_root(), media_key)
        if not path.is_file():
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                "media_missing",
                "managed image file does not exist",
            )
        return FileResponse(path, media_type=media_type)
    except HTTPException:
        raise
    except Exception as error:
        _raise_http_error(error)
        raise


def _ingested_response(ingested: IngestedImage) -> ImageResponse:
    prepared = ingested.prepared
    return ImageResponse(
        id=ingested.id,
        dataset_id=ingested.dataset_id,
        original_filename=prepared.original_filename,
        media_type=prepared.media_type,
        content_sha256=prepared.content_sha256,
        width=prepared.canonical_width,
        height=prepared.canonical_height,
        status="unlabeled",
        capture_metadata=prepared.capture_metadata,
        **_media_urls(ingested.id),
    )


def _record_response(record: dict[str, Any]) -> ImageResponse:
    image_id = UUID(str(record["id"]))
    return ImageResponse(
        id=image_id,
        dataset_id=UUID(str(record["dataset_id"])),
        original_filename=str(record["original_filename"]),
        media_type=str(record["media_type"]),
        content_sha256=str(record["content_sha256"]),
        width=int(record["canonical_width"]),
        height=int(record["canonical_height"]),
        status=str(record["status"]),
        capture_metadata=dict(record["capture_metadata"]),
        **_media_urls(image_id),
    )


def _summary_response(record: dict[str, Any]) -> ImageSummaryResponse:
    image_id = UUID(str(record["id"]))
    return ImageSummaryResponse(
        id=image_id,
        original_filename=str(record["original_filename"]),
        media_type=str(record["media_type"]),
        width=int(record["canonical_width"]),
        height=int(record["canonical_height"]),
        status=cast(ImageStatus, str(record["status"])),
        created_at=cast(datetime, record["created_at"]),
        thumbnail_url=_media_urls(image_id)["thumbnail_url"],
    )


def _media_urls(image_id: UUID) -> dict[str, str]:
    prefix = f"/api/images/{image_id}/media"
    return {
        "original_url": f"{prefix}/original",
        "canonical_url": f"{prefix}/canonical",
        "thumbnail_url": f"{prefix}/thumbnail",
    }


def _raise_http_error(error: Exception) -> None:
    if isinstance(error, DuplicateImageError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            error_code="duplicate_image",
            message="dataset already contains this exact image",
            image_id=str(error.image_id),
        ) from error
    if isinstance(error, DatasetVersionFrozenError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_version_frozen",
            "dataset version is already snapshotted",
        ) from error
    if isinstance(error, (DatasetVersionNotFoundError, ImageNotFoundError)):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            _error_code(error),
            str(error),
        ) from error
    if isinstance(error, ImageTooLargeError):
        raise api_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            error.code,
            str(error),
        ) from error
    if isinstance(error, UnsupportedImageError):
        raise api_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            error.code,
            str(error),
        ) from error
    if isinstance(error, (CorruptImageError, MediaValidationError)):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            _error_code(error),
            str(error),
        ) from error
    if isinstance(error, (ConfigurationError, OSError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "image service is unavailable",
        ) from error


def _error_code(error: Exception) -> str:
    if isinstance(error, DatasetVersionNotFoundError):
        return "dataset_version_not_found"
    if isinstance(error, DatasetVersionFrozenError):
        return "dataset_version_frozen"
    if isinstance(error, ImageNotFoundError):
        return "image_not_found"
    return str(getattr(error, "code", "invalid_image"))
