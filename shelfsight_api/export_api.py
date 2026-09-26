from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.config import ConfigurationError, get_media_root
from shelfsight_api.data_model import register_snapshot_artifact
from shelfsight_api.database import get_engine
from shelfsight_api.export_service import (
    ExportMediaError,
    ExportVersionNotFoundError,
    ExportVersionNotFrozenError,
    build_detection_export,
    build_recognition_export,
    build_yolo_export,
)

router = APIRouter(prefix="/api")


@router.get("/dataset-versions/{dataset_version_id}/exports/{export_type}")
def export_dataset_version(
    dataset_version_id: UUID,
    export_type: Literal["detection", "recognition", "yolo"],
) -> Response:
    try:
        with get_engine().begin() as connection:
            if export_type == "detection":
                build = build_detection_export
            elif export_type == "recognition":
                build = build_recognition_export
            else:
                build = build_yolo_export
            archive = build(connection, get_media_root(), dataset_version_id)
            archive_sha256 = hashlib.sha256(archive).hexdigest()
            register_snapshot_artifact(
                connection,
                dataset_version_id,
                "export",
                f"{dataset_version_id}:{export_type}:{archive_sha256}",
                content_sha256=archive_sha256,
                metadata={"export_type": export_type},
            )
        file_name = f"shelfsight-{dataset_version_id}-{export_type}.zip"
        return Response(
            archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
        )
    except Exception as error:
        _raise_export_error(error)
        raise


def _raise_export_error(error: Exception) -> None:
    if isinstance(error, ExportVersionNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_version_not_found",
            "dataset version does not exist",
        ) from error
    if isinstance(error, ExportVersionNotFrozenError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_version_not_frozen",
            "dataset version must be snapshotted before export",
        ) from error
    if isinstance(error, ExportMediaError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_export_media",
            str(error),
        ) from error
    if isinstance(error, (ConfigurationError, OSError, SQLAlchemyError)):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "export_unavailable",
            "export service is unavailable",
        ) from error
