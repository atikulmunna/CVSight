from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
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
    PromptPoint,
    SegmenterClient,
    SmartBoxError,
    SmartBoxImage,
    smart_box_image,
)

MAX_PROMPT_POINTS = 16

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


class SmartPolygonPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: Annotated[FiniteFloat, Field(ge=0)]
    y: Annotated[FiniteFloat, Field(ge=0)]
    include: bool


class SmartPolygonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[SmartPolygonPoint] = Field(min_length=1, max_length=MAX_PROMPT_POINTS)

    @model_validator(mode="after")
    def _requires_an_included_point(self) -> SmartPolygonRequest:
        if not any(point.include for point in self.points):
            raise ValueError("at least one point must lie on the object")
        return self


class SmartPolygonResponse(BaseModel):
    polygon: list[tuple[float, float]]
    score: float


def segmenter_client(settings: SegmenterSettings) -> SegmenterClient:
    return SegmenterClient(settings)


@router.post("/images/{image_id}/smart-box", response_model=SmartBoxResponse)
def smart_box(image_id: UUID, request: SmartBoxRequest) -> SmartBoxResponse:
    """The box around the object at a point on a canonical image. Nothing is saved; the
    canvas creates the box through the annotation API like any drawn box."""
    box = _ask_segmenter(
        image_id,
        lambda client, image, content: client.box_at(image, content, request.x, request.y),
    )
    return SmartBoxResponse(
        x=box.x,
        y=box.y,
        width=box.width,
        height=box.height,
        score=box.score,
    )


@router.post("/images/{image_id}/smart-polygon", response_model=SmartPolygonResponse)
def smart_polygon(image_id: UUID, request: SmartPolygonRequest) -> SmartPolygonResponse:
    """The outline of the object that included clicks lie on and excluded clicks do not.
    Nothing is saved; the canvas thins the outline and saves it with its box."""
    points = [PromptPoint(point.x, point.y, point.include) for point in request.points]
    outline = _ask_segmenter(
        image_id,
        lambda client, image, content: client.polygon_at(image, content, points),
    )
    return SmartPolygonResponse(polygon=outline.points, score=outline.score)


@router.post("/images/{image_id}/smart-prepare", status_code=status.HTTP_204_NO_CONTENT)
def smart_prepare(image_id: UUID) -> Response:
    """Sends the photo to the SAM service ahead of the first smart click. Nothing is
    saved."""
    _ask_segmenter(image_id, lambda client, image, content: client.prepare(content))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ask_segmenter[T](
    image_id: UUID,
    ask: Callable[[SegmenterClient, SmartBoxImage, bytes], T],
) -> T:
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
        return ask(segmenter_client(settings), image, content)
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
