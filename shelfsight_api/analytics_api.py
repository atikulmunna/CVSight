from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.analytics_service import (
    AnalyticsVersionNotFoundError,
    AnalyticsVersionNotFrozenError,
    build_analytics_csv,
    build_analytics_json,
    build_snapshot_analytics,
)
from shelfsight_api.api_errors import api_error
from shelfsight_api.data_model import register_snapshot_artifact
from shelfsight_api.database import get_engine

router = APIRouter(prefix="/api")


class AnalyticsResponse(BaseModel):
    schema_version: Literal["shelfsight-analytics-response/v1"]
    dataset_id: UUID
    dataset_version_id: UUID
    snapshot: dict[str, Any]
    formula_version: str
    result_sha256: str
    generated_at: datetime
    model: dict[str, Any]
    group_by: Literal["sku"]
    group_labels: dict[str, str]
    summary: dict[str, int]
    planogram: dict[str, Any]
    images: list[dict[str, Any]]


@router.get(
    "/dataset-versions/{dataset_version_id}/analytics",
    response_model=AnalyticsResponse,
)
def dataset_analytics(dataset_version_id: UUID) -> AnalyticsResponse:
    try:
        with get_engine().connect() as connection:
            report = build_snapshot_analytics(connection, dataset_version_id)
        return AnalyticsResponse.model_validate(report)
    except Exception as error:
        _raise_analytics_error(error)
        raise


@router.get("/dataset-versions/{dataset_version_id}/analytics/export")
def export_dataset_analytics(
    dataset_version_id: UUID,
    export_format: Literal["json", "csv"] = Query(alias="format"),
) -> Response:
    try:
        with get_engine().begin() as connection:
            report = build_snapshot_analytics(connection, dataset_version_id)
            content = (
                build_analytics_json(report)
                if export_format == "json"
                else build_analytics_csv(report)
            )
            content_sha256 = hashlib.sha256(content).hexdigest()
            register_snapshot_artifact(
                connection,
                dataset_version_id,
                "export",
                f"{dataset_version_id}:analytics:{export_format}:{content_sha256}",
                content_sha256=content_sha256,
                metadata={
                    "export_type": "analytics",
                    "format": export_format,
                    "formula_version": report["formula_version"],
                    "result_sha256": report["result_sha256"],
                },
            )
        extension = "json" if export_format == "json" else "csv"
        media_type = "application/json" if export_format == "json" else "text/csv"
        file_name = f"cvsight-{dataset_version_id}-analytics.{extension}"
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
        )
    except Exception as error:
        _raise_analytics_error(error)
        raise


def _raise_analytics_error(error: Exception) -> None:
    if isinstance(error, AnalyticsVersionNotFoundError):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "dataset_version_not_found",
            "dataset version does not exist",
        ) from error
    if isinstance(error, AnalyticsVersionNotFrozenError):
        raise api_error(
            status.HTTP_409_CONFLICT,
            "dataset_version_not_frozen",
            "dataset version must be snapshotted before analytics are final",
        ) from error
    if isinstance(error, ValueError):
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_analytics_snapshot",
            str(error),
        ) from error
    if isinstance(error, SQLAlchemyError):
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "analytics_unavailable",
            "analytics service is unavailable",
        ) from error
