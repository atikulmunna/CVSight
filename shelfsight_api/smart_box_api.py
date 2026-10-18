from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.config import (
    ConfigurationError,
    SegmenterSettings,
    get_media_root,
    get_segmenter_settings,
)
from shelfsight_api.database import get_engine
from shelfsight_api.media import resolve_media_path
from shelfsight_api.smart_box_service import (
    SegmenterClient,
    SmartBoxError,
    smart_box_image,
)

router = APIRouter(prefix="/api")


class SmartBoxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: Annotated[FiniteFloat, Field(ge=0)]
    y: Annotated[FiniteFloat, Field(ge=0)]


class SmartBoxResponse(BaseModel):
    x: float
    y: float
    width: float
    height: float
    score: float


def segmenter_client(settings: SegmenterSettings) -> SegmenterClient:
    return SegmenterClient(settings)


@router.post("/images/{image_id}/smart-box", response_model=SmartBoxResponse)
def smart_box(image_id: UUID, request: SmartBoxRequest) -> SmartBoxResponse:
    """The box around the object at a point on a canonical image. Nothing is saved; the
    canvas creates the box through the annotation API like any drawn box."""
    try:
        settings = get_segmenter_settings()
    except ConfigurationError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "smart_box_misconfigured",
            "the smart box service settings are invalid",
        ) from error
    if settings is None:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "smart_box_unavailable",
            "no smart box service is configured",
        )
    try:
        # The connection closes before the call to the service, which can take seconds.
        with get_engine().connect() as connection:
            image = smart_box_image(connection, image_id)
        content = resolve_media_path(get_media_root(), image.canonical_media_key).read_bytes()
        box = segmenter_client(settings).box_at(image, content, request.x, request.y)
    except SmartBoxError as error:
        raise api_error(error.status_code, error.code, str(error)) from error
    except (ConfigurationError, OSError, ValueError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "image_unavailable",
            "the canonical image is unavailable",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            "the image service is unavailable",
        ) from error
    return SmartBoxResponse(
        x=box.x,
        y=box.y,
        width=box.width,
        height=box.height,
        score=box.score,
    )
