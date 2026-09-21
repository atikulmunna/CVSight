from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import OwnerUser
from shelfsight_api.data_model import (
    DatasetSnapshotNotFoundError,
    DatasetVersionFrozenError,
    DatasetVersionNotFoundError,
    SnapshotArtifactConflictError,
    get_dataset_snapshot,
    register_snapshot_artifact,
    snapshot_dataset_version,
)
from shelfsight_api.database import get_engine
from shelfsight_api.dataset_service import (
    DatasetNotFoundError,
    DatasetOpenVersionExistsError,
    DatasetVersionProgressNotFoundError,
    create_dataset_with_open_version,
    create_next_dataset_version,
    get_dataset_version_progress,
    list_dataset_versions,
    list_datasets,
)

router = APIRouter(prefix="/api")


class CatalogImportSku(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    upc: Annotated[str | None, Field(pattern=r"^(\d{8}|\d{12}|\d{13}|\d{14})$")] = None
    category: Annotated[str | None, Field(max_length=255)] = None
    subcategory: Annotated[str | None, Field(max_length=255)] = None
    brand: Annotated[str | None, Field(max_length=255)] = None
    variant: Annotated[str | None, Field(max_length=255)] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("upc", "category", "subcategory", "brand", "variant")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CreateDatasetRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(max_length=4000)] = None
    catalog: Annotated[list[CatalogImportSku], Field(max_length=2500)] = Field(
        default_factory=list
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_catalog_upcs(self) -> CreateDatasetRequest:
        upcs = [sku.upc for sku in self.catalog if sku.upc is not None]
        if len(upcs) != len(set(upcs)):
            raise ValueError("catalog contains duplicate UPC values")
        return self


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    open_version_id: UUID
    imported_skus: int


class DatasetSummaryResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    open_version_id: UUID | None
    latest_version_id: UUID
    image_count: int


class ProgressImagesResponse(BaseModel):
    total: int


class ProgressAnnotationsResponse(BaseModel):
    total: int
    decided: int


class ProgressIdentityResponse(BaseModel):
    accepted_products: int
    known_products: int
    unknown_products: int
    unassigned_products: int


class ProgressQaResponse(BaseModel):
    reviewed_images: int
    flagged_annotations: int


class DatasetVersionProgressResponse(BaseModel):
    dataset_version_id: UUID
    images: ProgressImagesResponse
    annotations: ProgressAnnotationsResponse
    identity: ProgressIdentityResponse
    qa: ProgressQaResponse


class VersionReviewSignoffResponse(BaseModel):
    signed_by: str
    signed_at: datetime
    reviewed_annotation_count: int
    risk_item_count: int


class DatasetVersionSummaryResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    parent_version_id: UUID | None
    created_at: datetime
    snapshot_at: datetime | None
    status: Literal["working", "released", "frozen"]
    image_count: int
    review_signoff: VersionReviewSignoffResponse | None
    export_types: list[Literal["detection", "recognition"]]


class WorkingVersionResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    parent_version_id: UUID
    created_at: datetime
    image_count: int


class SnapshotResponse(BaseModel):
    dataset_version_id: UUID
    status: Literal["snapshotted"]
    captured_annotations: int
    captured_images: int
    captured_skus: int
    captured_sku_references: int
    schema_version: str
    content_sha256: str


class SnapshotManifestResponse(BaseModel):
    dataset_id: UUID
    dataset_version_id: UUID
    parent_version_id: UUID | None
    schema_version: str
    content_sha256: str
    snapshot_at: datetime
    images: list[dict[str, Any]]
    annotations: list[dict[str, Any]]
    skus: list[dict[str, Any]]
    sku_references: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]


class CreateSnapshotArtifactRequest(BaseModel):
    artifact_type: Literal["model", "evaluation"]
    artifact_key: Annotated[
        str,
        Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$"),
    ]
    content_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must contain finite JSON values") from error
        if len(encoded) > 16 * 1024:
            raise ValueError("metadata exceeds the size limit")
        return value


class SnapshotArtifactResponse(BaseModel):
    id: UUID
    dataset_version_id: UUID
    artifact_type: Literal["export", "model", "evaluation"]
    artifact_key: str
    content_sha256: str | None
    metadata: dict[str, Any]
    created_at: datetime


@router.get("/datasets", response_model=list[DatasetSummaryResponse])
def datasets_index() -> list[DatasetSummaryResponse]:
    try:
        with get_engine().connect() as connection:
            projects = list_datasets(connection)
        return [DatasetSummaryResponse.model_validate(project) for project in projects]
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.post(
    "/datasets",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dataset(request: CreateDatasetRequest, user: OwnerUser) -> DatasetResponse:
    del user
    try:
        with get_engine().begin() as connection:
            created = create_dataset_with_open_version(
                connection,
                request.name,
                request.description,
                [sku.model_dump() for sku in request.catalog],
            )
        return DatasetResponse.model_validate(created)
    except IntegrityError as error:
        constraint = _constraint_name(error)
        if constraint == "uq_skus_upc":
            raise api_error(
                status.HTTP_409_CONFLICT,
                "duplicate_upc",
                "a catalog UPC already exists",
            ) from error
        code = (
            "dataset_name_conflict"
            if constraint == "uq_datasets_name"
            else "catalog_constraint_violation"
        )
        message = (
            "a dataset with this name already exists"
            if constraint == "uq_datasets_name"
            else "catalog import violates a catalog constraint"
        )
        raise api_error(
            status.HTTP_409_CONFLICT,
            code,
            message,
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.get(
    "/datasets/{dataset_id}/versions",
    response_model=list[DatasetVersionSummaryResponse],
)
def dataset_versions_index(dataset_id: UUID) -> list[DatasetVersionSummaryResponse]:
    try:
        with get_engine().connect() as connection:
            versions = list_dataset_versions(connection, dataset_id)
        return [DatasetVersionSummaryResponse.model_validate(version) for version in versions]
    except DatasetNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_not_found",
            "dataset does not exist",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=WorkingVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_working_version(
    dataset_id: UUID,
    user: OwnerUser,
) -> WorkingVersionResponse:
    del user
    try:
        with get_engine().begin() as connection:
            version = create_next_dataset_version(connection, dataset_id)
        return WorkingVersionResponse.model_validate(version)
    except DatasetNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_not_found",
            "dataset does not exist",
        ) from error
    except DatasetOpenVersionExistsError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_open_version_exists",
            "dataset already has a working version",
        ) from error
    except IntegrityError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_open_version_exists",
            "dataset already has a working version",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.get(
    "/dataset-versions/{dataset_version_id}/progress",
    response_model=DatasetVersionProgressResponse,
)
def dataset_version_progress(
    dataset_version_id: UUID,
) -> DatasetVersionProgressResponse:
    try:
        with get_engine().connect() as connection:
            progress = get_dataset_version_progress(connection, dataset_version_id)
        return DatasetVersionProgressResponse.model_validate(progress)
    except DatasetVersionProgressNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_version_not_found",
            "dataset version does not exist",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.post(
    "/dataset-versions/{dataset_version_id}/snapshot",
    response_model=SnapshotResponse,
)
def snapshot_version(dataset_version_id: UUID, user: OwnerUser) -> SnapshotResponse:
    del user
    try:
        with get_engine().begin() as connection:
            captured = snapshot_dataset_version(connection, dataset_version_id)
            snapshot = get_dataset_snapshot(connection, dataset_version_id)
        return SnapshotResponse(
            dataset_version_id=dataset_version_id,
            status="snapshotted",
            captured_annotations=captured,
            captured_images=len(snapshot["images"]),
            captured_skus=len(snapshot["skus"]),
            captured_sku_references=len(snapshot["sku_references"]),
            schema_version=str(snapshot["schema_version"]),
            content_sha256=str(snapshot["content_sha256"]),
        )
    except DatasetVersionNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_version_not_found",
            "dataset version does not exist",
        ) from error
    except DatasetVersionFrozenError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_version_frozen",
            "dataset version is already snapshotted",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.get(
    "/dataset-versions/{dataset_version_id}/snapshot",
    response_model=SnapshotManifestResponse,
)
def dataset_snapshot(dataset_version_id: UUID) -> SnapshotManifestResponse:
    try:
        with get_engine().connect() as connection:
            snapshot = get_dataset_snapshot(connection, dataset_version_id)
        return SnapshotManifestResponse.model_validate(snapshot)
    except DatasetSnapshotNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_snapshot_not_found",
            "dataset snapshot does not exist",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


@router.post(
    "/dataset-versions/{dataset_version_id}/artifacts",
    response_model=SnapshotArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_snapshot_artifact(
    dataset_version_id: UUID,
    request: CreateSnapshotArtifactRequest,
    user: OwnerUser,
) -> SnapshotArtifactResponse:
    del user
    try:
        with get_engine().begin() as connection:
            artifact = register_snapshot_artifact(
                connection,
                dataset_version_id,
                request.artifact_type,
                request.artifact_key,
                content_sha256=request.content_sha256,
                metadata=request.metadata,
            )
        return SnapshotArtifactResponse.model_validate(artifact)
    except DatasetSnapshotNotFoundError as error:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_snapshot_not_found",
            "dataset snapshot does not exist",
        ) from error
    except SnapshotArtifactConflictError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "snapshot_artifact_conflict",
            "artifact identity is already bound to different lineage",
        ) from error
    except (IntegrityError, SQLAlchemyError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dataset_service_unavailable",
            "dataset service is unavailable",
        ) from error


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return str(value) if value is not None else None
