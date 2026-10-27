from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import Connection, and_, select

from shelfsight_api.data_model import AnnotationNotFoundError
from shelfsight_api.geometry import padded_crop_bounds
from shelfsight_api.media import encode_thumbnail, open_canonical_image
from shelfsight_api.models import annotation_records, annotation_revisions, images

CROP_PADDING = 0.08
CROP_MAX_SIZE = 256


class AnnotationRevisionNotFoundError(ValueError):
    """Raised when an annotation exists but the requested revision does not."""


@dataclass(frozen=True)
class CropSource:
    canonical_media_key: str
    image_size: tuple[int, int]
    box: tuple[float, float, float, float]


def crop_source(
    connection: Connection,
    annotation_id: UUID,
    revision: int | None,
) -> CropSource:
    """The box of one annotation revision, the current one when none is named, and the
    photo it lies on."""
    wanted_revision = annotation_records.c.current_revision if revision is None else revision
    row = connection.execute(
        select(
            annotation_revisions.c.x,
            annotation_revisions.c.y,
            annotation_revisions.c.width,
            annotation_revisions.c.height,
            images.c.canonical_media_key,
            images.c.canonical_width,
            images.c.canonical_height,
        )
        .select_from(
            annotation_records.join(
                images,
                images.c.id == annotation_records.c.image_id,
            ).outerjoin(
                annotation_revisions,
                and_(
                    annotation_revisions.c.annotation_id == annotation_records.c.id,
                    annotation_revisions.c.revision == wanted_revision,
                ),
            )
        )
        .where(annotation_records.c.id == annotation_id)
    ).one_or_none()
    if row is None:
        raise AnnotationNotFoundError("annotation does not exist")
    if row.x is None:
        raise AnnotationRevisionNotFoundError("annotation revision does not exist")
    return CropSource(
        canonical_media_key=str(row.canonical_media_key),
        image_size=(int(row.canonical_width), int(row.canonical_height)),
        box=(float(row.x), float(row.y), float(row.width), float(row.height)),
    )


def render_crop(media_root: Path, source: CropSource) -> bytes:
    """A JPEG of the box with a little of the shelf around it, small enough for a grid."""
    x, y, width, height = source.box
    bounds = padded_crop_bounds(x, y, width, height, CROP_PADDING, source.image_size)
    with open_canonical_image(
        media_root,
        source.canonical_media_key,
        source.image_size,
    ) as photo:
        crop = photo.crop(bounds)
    return encode_thumbnail(crop, CROP_MAX_SIZE)
