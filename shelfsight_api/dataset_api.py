from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, field_validator
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
from shelfsight_api.dataset_service import create_dataset_with_open_version

router = APIRouter(prefix="/api")


class CreateDatasetRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(max_length=4000)] = None

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


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    open_version_id: UUID


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
            )
        return DatasetResponse.model_validate(created)
    except IntegrityError as error:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_name_conflict",
            "a dataset with this name already exists",
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
