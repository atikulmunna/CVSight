from __future__ import annotations

from collections import Counter
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import OwnerActor
from shelfsight_api.config import ConfigurationError, get_import_root, get_media_root
from shelfsight_api.database import get_engine
from shelfsight_api.image_ingest import DatasetVersionFrozenError, DatasetVersionNotFoundError
from shelfsight_api.labeled_import import LabeledImportError, read_labeled_dataset
from shelfsight_api.labeled_import_service import (
    class_summary,
    import_labeled_images,
    require_mapping,
    require_open_version,
    suggest_skus,
)
from shelfsight_api.media import MediaValidationError

router = APIRouter(prefix="/api")
PAGE_LIMIT = 100


class LabeledSourceRequest(BaseModel):
    format: Literal["yolo", "coco"]
    path: str = Field(min_length=1, max_length=1024)


class LabeledImportRequest(LabeledSourceRequest):
    class_skus: dict[str, UUID | None]
    annotation_state: Literal["verified", "proposed"] = "verified"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=PAGE_LIMIT, ge=1, le=PAGE_LIMIT)


class LabeledClassResponse(BaseModel):
    name: str
    boxes: int
    images: int
    suggested_sku_id: UUID | None


class LabeledPreviewResponse(BaseModel):
    format: Literal["yolo", "coco"]
    images: int
    boxes: int
    skipped_labels: int
    classes: list[LabeledClassResponse]
    source_splits: dict[str, int]


class LabeledImageOutcome(BaseModel):
    path: str
    outcome: Literal["imported", "completed", "skipped", "rejected"]
    code: str
    image_id: UUID | None
    boxes: int


class LabeledImportResponse(BaseModel):
    results: list[LabeledImageOutcome]
    next_offset: int | None
    total_images: int


@router.post(
    "/datasets/{dataset_id}/versions/{dataset_version_id}/labeled-imports/preview",
    response_model=LabeledPreviewResponse,
)
def preview_labeled_import(
    dataset_id: UUID,
    dataset_version_id: UUID,
    request: LabeledSourceRequest,
) -> LabeledPreviewResponse:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            require_open_version(connection, dataset_id, dataset_version_id)
        dataset = read_labeled_dataset(get_import_root(), request.format, request.path)
        with engine.connect() as connection:
            suggestions = suggest_skus(connection, dataset.class_names)
    except Exception as error:
        _raise_import_error(error)
        raise
    return LabeledPreviewResponse(
        format=dataset.format,
        images=len(dataset.images),
        boxes=sum(len(image.boxes) for image in dataset.images),
        skipped_labels=dataset.skipped_labels,
        classes=[
            LabeledClassResponse(**summary, suggested_sku_id=suggestions[summary["name"]])
            for summary in class_summary(dataset)
        ],
        source_splits=dict(Counter(image.source_split or "unsplit" for image in dataset.images)),
    )


@router.post(
    "/datasets/{dataset_id}/versions/{dataset_version_id}/labeled-imports",
    response_model=LabeledImportResponse,
)
def run_labeled_import(
    dataset_id: UUID,
    dataset_version_id: UUID,
    request: LabeledImportRequest,
    actor: OwnerActor,
) -> LabeledImportResponse:
    try:
        engine = get_engine()
        import_root = get_import_root()
        dataset = read_labeled_dataset(import_root, request.format, request.path)
        with engine.connect() as connection:
            require_open_version(connection, dataset_id, dataset_version_id)
            require_mapping(connection, dataset, request.class_skus)
        page = list(dataset.images[request.offset : request.offset + request.limit])
        results = import_labeled_images(
            engine,
            get_media_root(),
            import_root,
            dataset_id,
            dataset_version_id,
            page,
            request.class_skus,
            verified=request.annotation_state == "verified",
            actor=actor,
            dataset_format=request.format,
        )
    except Exception as error:
        _raise_import_error(error)
        raise
    following = request.offset + len(page)
    return LabeledImportResponse(
        results=[LabeledImageOutcome(**result) for result in results],
        next_offset=following if following < len(dataset.images) else None,
        total_images=len(dataset.images),
    )


def _raise_import_error(error: Exception) -> None:
    if isinstance(error, LabeledImportError):
        raise api_error(status.HTTP_400_BAD_REQUEST, error.code, str(error)) from error
    if isinstance(error, MediaValidationError):
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            str(getattr(error, "code", "invalid_path")),
            str(error),
        ) from error
    if isinstance(error, DatasetVersionNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND, "dataset_version_not_found", str(error)
        ) from error
    if isinstance(error, DatasetVersionFrozenError):
        raise api_error(status.HTTP_409_CONFLICT, "dataset_version_frozen", str(error)) from error
    if isinstance(error, (ConfigurationError, OSError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "import_unavailable",
            "labeled import storage is unavailable",
        ) from error
