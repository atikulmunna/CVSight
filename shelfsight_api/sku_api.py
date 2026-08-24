from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import OwnerActor
from shelfsight_api.config import ConfigurationError, get_media_root
from shelfsight_api.database import get_engine
from shelfsight_api.media import (
    MediaValidationError,
    prepare_image,
    read_limited,
    resolve_media_path,
)
from shelfsight_api.sku_reference_service import (
    DuplicateSkuReferenceError,
    SkuReferenceNotFoundError,
    get_sku_reference,
    ingest_sku_reference_image,
    reference_media_variant,
)
from shelfsight_api.sku_service import (
    InvalidSkuStateError,
    SkuNotFoundError,
    create_sku,
    deprecate_sku,
    get_sku,
    list_skus,
    merge_skus,
    update_sku,
)

router = APIRouter(prefix="/api")

class SkuFields(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    upc: str | None = Field(
        default=None,
        pattern=r"^(\d{8}|\d{12}|\d{13}|\d{14})$",
    )
    category: str | None = Field(default=None, max_length=255)
    subcategory: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    variant: str | None = Field(default=None, max_length=255)


class MergeSkuRequest(BaseModel):
    target_sku_id: UUID


class ReferenceImageResponse(BaseModel):
    id: UUID
    sku_id: UUID
    original_filename: str
    media_type: Literal["image/jpeg", "image/png"]
    content_sha256: str
    width: int
    height: int
    created_at: datetime
    original_url: str
    canonical_url: str
    thumbnail_url: str


class SkuResponse(BaseModel):
    id: UUID
    name: str
    upc: str | None
    category: str | None
    subcategory: str | None
    brand: str | None
    variant: str | None
    is_unknown: bool
    status: Literal["active", "deprecated", "merged"]
    merged_into_id: UUID | None
    created_at: datetime
    updated_at: datetime
    reference_images: list[ReferenceImageResponse]


class SkuListResponse(BaseModel):
    skus: list[SkuResponse]
    total: int
    limit: int
    offset: int


class MergeSkuResponse(BaseModel):
    source: SkuResponse
    target: SkuResponse
    repointed_annotations: int


@router.get("/skus", response_model=SkuListResponse)
def catalog(
    query: Annotated[str | None, Query(max_length=200)] = None,
    sku_status: Annotated[
        Literal["active", "deprecated", "merged", "all"],
        Query(alias="status"),
    ] = "active",
    limit: Annotated[int, Query(ge=1, le=2500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SkuListResponse:
    try:
        with get_engine().connect() as connection:
            rows, total = list_skus(
                connection,
                query=query,
                status=None if sku_status == "all" else sku_status,
                limit=limit,
                offset=offset,
            )
        return SkuListResponse(
            skus=[_sku_response(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.get("/skus/{sku_id}", response_model=SkuResponse)
def sku(sku_id: UUID) -> SkuResponse:
    try:
        with get_engine().connect() as connection:
            return _sku_response(get_sku(connection, sku_id))
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.post("/skus", response_model=SkuResponse, status_code=status.HTTP_201_CREATED)
def create_catalog_sku(
    request: SkuFields,
    actor: OwnerActor,
) -> SkuResponse:
    del actor
    try:
        with get_engine().begin() as connection:
            row = create_sku(connection, request.model_dump())
        return _sku_response(row)
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.put("/skus/{sku_id}", response_model=SkuResponse)
def update_catalog_sku(
    sku_id: UUID,
    request: SkuFields,
    actor: OwnerActor,
) -> SkuResponse:
    del actor
    try:
        with get_engine().begin() as connection:
            row = update_sku(connection, sku_id, request.model_dump())
        return _sku_response(row)
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.post("/skus/{sku_id}/deprecate", response_model=SkuResponse)
def deprecate_catalog_sku(
    sku_id: UUID,
    actor: OwnerActor,
) -> SkuResponse:
    del actor
    try:
        with get_engine().begin() as connection:
            row = deprecate_sku(connection, sku_id)
        return _sku_response(row)
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.post("/skus/{sku_id}/merge", response_model=MergeSkuResponse)
def merge_catalog_sku(
    sku_id: UUID,
    request: MergeSkuRequest,
    actor: OwnerActor,
) -> MergeSkuResponse:
    try:
        with get_engine().begin() as connection:
            source, repointed = merge_skus(
                connection,
                sku_id,
                request.target_sku_id,
                actor,
            )
            target = get_sku(connection, request.target_sku_id)
        return MergeSkuResponse(
            source=_sku_response(source),
            target=_sku_response(target),
            repointed_annotations=repointed,
        )
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.post(
    "/skus/{sku_id}/reference-images",
    response_model=ReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_sku_reference_image(
    sku_id: UUID,
    file: Annotated[UploadFile, File()],
    actor: OwnerActor,
) -> ReferenceImageResponse:
    del actor
    try:
        prepared = prepare_image(
            read_limited(file.file),
            file.filename or "upload",
        )
        engine = get_engine()
        ingested = ingest_sku_reference_image(
            engine,
            get_media_root(),
            sku_id,
            prepared,
        )
        return _reference_response(get_sku_reference(engine, ingested.id))
    except Exception as error:
        _raise_sku_error(error)
        raise


@router.get("/sku-reference-images/{reference_id}/media/{variant}")
def sku_reference_media(
    reference_id: UUID,
    variant: Literal["original", "canonical", "thumbnail"],
) -> FileResponse:
    try:
        record = get_sku_reference(get_engine(), reference_id)
        media_key, media_type = reference_media_variant(record, variant)
        path = resolve_media_path(get_media_root(), media_key)
        if not path.is_file():
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                "media_missing",
                "managed reference image file does not exist",
            )
        return FileResponse(path, media_type=media_type)
    except HTTPException:
        raise
    except Exception as error:
        _raise_sku_error(error)
        raise


def _sku_response(row: dict[str, Any]) -> SkuResponse:
    references = [
        _reference_response(reference)
        for reference in row.get("reference_images", [])
        if isinstance(reference, dict)
    ]
    values = {**row, "reference_images": references}
    return SkuResponse.model_validate(values)


def _reference_response(row: dict[str, Any]) -> ReferenceImageResponse:
    reference_id = UUID(str(row["id"]))
    return ReferenceImageResponse.model_validate({**row, **_reference_urls(reference_id)})


def _reference_urls(reference_id: UUID) -> dict[str, str]:
    prefix = f"/api/sku-reference-images/{reference_id}/media"
    return {
        "original_url": f"{prefix}/original",
        "canonical_url": f"{prefix}/canonical",
        "thumbnail_url": f"{prefix}/thumbnail",
    }


def _raise_sku_error(error: Exception) -> None:
    if isinstance(error, SkuNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "sku_not_found",
            "SKU does not exist",
        ) from error
    if isinstance(error, SkuReferenceNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "sku_reference_not_found",
            "SKU reference image does not exist",
        ) from error
    if isinstance(error, InvalidSkuStateError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "invalid_sku_state",
            str(error),
        ) from error
    if isinstance(error, DuplicateSkuReferenceError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "duplicate_reference_image",
            "SKU already has this reference image",
        ) from error
    if isinstance(error, MediaValidationError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            error.code,
            str(error),
        ) from error
    if isinstance(error, ConfigurationError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "managed storage is not configured",
        ) from error
    if isinstance(error, IntegrityError):
        code = (
            "duplicate_upc"
            if _constraint_name(error) == "uq_skus_upc"
            else "catalog_constraint_violation"
        )
        message = (
            "UPC already belongs to another SKU"
            if code == "duplicate_upc"
            else "SKU violates a catalog constraint"
        )
        raise api_error(
            status.HTTP_409_CONFLICT,
            code,
            message,
        ) from error
    if isinstance(error, (OSError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "catalog_unavailable",
            "SKU catalog is unavailable",
        ) from error


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return str(value) if value is not None else None
