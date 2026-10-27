from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import Connection, Engine, delete, insert

from shelfsight_api.app import app
from shelfsight_api.data_model import append_annotation_revision, create_annotation
from shelfsight_api.geometry import padded_crop_bounds
from shelfsight_api.models import (
    dataset_version_images,
    dataset_versions,
    datasets,
    images,
    sku_reference_images,
    skus,
)

client = TestClient(app)
PHOTO_SIZE = (400, 300)
# The product box drawn on every photo; the rest of the photo is white shelf.
PRODUCT_BOX = (100.0, 50.0, 200.0, 150.0)


@dataclass(frozen=True)
class LineupTarget:
    version_id: UUID
    other_version_id: UUID
    image_ids: dict[str, UUID]
    sku_id: UUID
    other_sku_id: UUID
    reference_id: UUID
    annotation_ids: dict[str, UUID]
    media_root: Path


@pytest.fixture
def lineup_target(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[LineupTarget]:
    dataset_id = uuid4()
    other_dataset_id = uuid4()
    version_id = uuid4()
    other_version_id = uuid4()
    image_ids = {name: uuid4() for name in ("first", "second", "elsewhere")}
    sku_id = uuid4()
    other_sku_id = uuid4()
    reference_id = uuid4()
    media_root = tmp_path / "media"
    # A dataset has one open version, so the other version lives in a second dataset.
    photos = [
        ("first", "a-shelf.png", dataset_id, version_id),
        ("second", "b-shelf.png", dataset_id, version_id),
        ("elsewhere", "c-shelf.png", other_dataset_id, other_version_id),
    ]

    with database_engine.begin() as connection:
        connection.execute(
            insert(datasets),
            [
                {"id": dataset_id, "name": f"lineup-{dataset_id}"},
                {"id": other_dataset_id, "name": f"lineup-{other_dataset_id}"},
            ],
        )
        connection.execute(
            insert(dataset_versions),
            [
                {"id": version_id, "dataset_id": dataset_id},
                {"id": other_version_id, "dataset_id": other_dataset_id},
            ],
        )
        for index, (name, file_name, owner_id, owner_version_id) in enumerate(photos):
            _insert_photo(connection, media_root, owner_id, image_ids[name], file_name, index)
            connection.execute(
                insert(dataset_version_images).values(
                    dataset_id=owner_id,
                    dataset_version_id=owner_version_id,
                    image_id=image_ids[name],
                )
            )
        connection.execute(
            insert(skus),
            [
                {"id": sku_id, "name": "Lineup Cola 330 ml"},
                {"id": other_sku_id, "name": "Lineup Lemonade"},
            ],
        )
        connection.execute(
            insert(sku_reference_images).values(
                id=reference_id,
                sku_id=sku_id,
                original_media_key=f"sku-references/original/{reference_id}.png",
                canonical_media_key=f"sku-references/canonical/{reference_id}.png",
                thumbnail_media_key=f"sku-references/thumbnails/{reference_id}.jpg",
                media_type="image/png",
                original_filename="cola-front.png",
                content_sha256="e" * 64,
                width=120,
                height=240,
            )
        )
        annotation_ids = _create_lineup_annotations(connection, image_ids, sku_id, other_sku_id)

    monkeypatch.setattr("shelfsight_api.annotation_api.get_engine", lambda: database_engine)
    monkeypatch.setattr("shelfsight_api.annotation_api.get_media_root", lambda: media_root)
    monkeypatch.setattr("shelfsight_api.review_api.get_engine", lambda: database_engine)
    yield LineupTarget(
        version_id=version_id,
        other_version_id=other_version_id,
        image_ids=image_ids,
        sku_id=sku_id,
        other_sku_id=other_sku_id,
        reference_id=reference_id,
        annotation_ids=annotation_ids,
        media_root=media_root,
    )

    with database_engine.begin() as connection:
        connection.execute(
            delete(datasets).where(datasets.c.id.in_((dataset_id, other_dataset_id)))
        )
        connection.execute(delete(skus).where(skus.c.id.in_((sku_id, other_sku_id))))


def test_crop_pads_the_box_with_shelf_context(lineup_target: LineupTarget) -> None:
    annotation_id = lineup_target.annotation_ids["accepted_right"]

    response = client.get(f"/api/annotations/{annotation_id}/crop")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    crop = _decoded(response.content)
    # 8% of 200 x 150 on every side: 16 px left and right, 12 px top and bottom.
    assert crop.size == (232, 174)
    assert _is_white(crop.getpixel((4, 4)))
    assert _is_red(crop.getpixel((116, 87)))


def test_crop_padding_stays_inside_the_photo_and_shrinks_large_boxes(
    database_engine: Engine,
    lineup_target: LineupTarget,
) -> None:
    with database_engine.begin() as connection:
        corner_id = create_annotation(
            connection,
            lineup_target.image_ids["first"],
            _values(lineup_target.sku_id, x=0, y=0, width=50, height=40),
        )
        whole_id = create_annotation(
            connection,
            lineup_target.image_ids["first"],
            _values(lineup_target.sku_id, x=0, y=0, width=400, height=300),
        )

    corner = client.get(f"/api/annotations/{corner_id}/crop")
    whole = client.get(f"/api/annotations/{whole_id}/crop")

    assert _decoded(corner.content).size == (54, 44)
    assert _decoded(whole.content).size == (256, 192)


def test_padded_crop_bounds_round_outward_and_clamp_to_the_photo() -> None:
    assert padded_crop_bounds(100, 50, 100, 50, 0.08, (400, 300)) == (92, 46, 208, 104)
    assert padded_crop_bounds(10.5, 1, 20, 10, 0.08, (400, 300)) == (8, 0, 33, 12)
    assert padded_crop_bounds(390, 290, 10, 10, 0.08, (400, 300)) == (389, 289, 400, 300)


def test_named_revision_is_pinned_and_cacheable(
    database_engine: Engine,
    lineup_target: LineupTarget,
) -> None:
    annotation_id = lineup_target.annotation_ids["accepted_right"]
    with database_engine.begin() as connection:
        append_annotation_revision(
            connection,
            annotation_id,
            1,
            _values(lineup_target.sku_id, x=100, y=50, width=50, height=100),
        )

    first = client.get(f"/api/annotations/{annotation_id}/crop", params={"revision": 1})
    current = client.get(f"/api/annotations/{annotation_id}/crop")
    second = client.get(f"/api/annotations/{annotation_id}/crop", params={"revision": 2})

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, max-age=86400"
    assert _decoded(first.content).size == (232, 174)
    assert current.headers["cache-control"] == "private, no-store"
    assert _decoded(current.content).size == (58, 116)
    assert second.content == current.content


@pytest.mark.parametrize(
    ("path", "params", "status_code", "code"),
    [
        ("missing_annotation", {}, 404, "annotation_not_found"),
        ("accepted_right", {"revision": 9}, 404, "annotation_revision_not_found"),
    ],
)
def test_crop_reports_missing_annotations_and_revisions(
    lineup_target: LineupTarget,
    path: str,
    params: dict[str, Any],
    status_code: int,
    code: str,
) -> None:
    annotation_id = lineup_target.annotation_ids.get(path, uuid4())

    response = client.get(f"/api/annotations/{annotation_id}/crop", params=params)

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    assert response.json()["detail"]["message"]


def test_crop_reports_missing_media_and_refuses_invalid_revisions(
    lineup_target: LineupTarget,
) -> None:
    annotation_id = lineup_target.annotation_ids["accepted_right"]
    image_id = lineup_target.image_ids["first"]
    (lineup_target.media_root / "canonical" / f"{image_id}.png").unlink()

    missing = client.get(f"/api/annotations/{annotation_id}/crop", params={"revision": 1})
    invalid = client.get(f"/api/annotations/{annotation_id}/crop", params={"revision": 0})

    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "media_missing",
        "message": "managed image file does not exist",
    }
    assert missing.headers["cache-control"] == "private, no-store"
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "canonical image is empty"),
        (b"not an image", "canonical image cannot be decoded"),
        (None, "canonical image dimensions do not match metadata"),
    ],
)
def test_crop_refuses_canonical_media_it_cannot_trust(
    lineup_target: LineupTarget,
    content: bytes | None,
    message: str,
) -> None:
    annotation_id = lineup_target.annotation_ids["accepted_right"]
    path = lineup_target.media_root / "canonical" / f"{lineup_target.image_ids['first']}.png"
    if content is None:
        resized = BytesIO()
        Image.new("RGB", (200, 150), "white").save(resized, format="PNG")
        content = resized.getvalue()
    path.write_bytes(content)

    response = client.get(f"/api/annotations/{annotation_id}/crop")

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "corrupt_image", "message": message}


def test_lineup_lists_accepted_products_of_one_sku_in_this_version(
    lineup_target: LineupTarget,
) -> None:
    response = client.get(
        f"/api/dataset-versions/{lineup_target.version_id}/sku-lineup",
        params={"sku_id": str(lineup_target.sku_id)},
    )

    assert response.status_code == 200
    body = response.json()
    ids = lineup_target.annotation_ids
    expected = [
        ("accepted_left", 1, "first", "a-shelf.png"),
        ("reassigned_to_sku", 2, "first", "a-shelf.png"),
        ("accepted_right", 1, "first", "a-shelf.png"),
        ("second_photo", 1, "second", "b-shelf.png"),
    ]
    assert body["items"] == [
        {
            "annotation_id": str(ids[name]),
            "revision": revision,
            "image_id": str(lineup_target.image_ids[image]),
            "image_name": image_name,
            "crop_url": f"/api/annotations/{ids[name]}/crop?revision={revision}",
        }
        for name, revision, image, image_name in expected
    ]
    assert body["truncated"] is False
    assert body["dataset_version_id"] == str(lineup_target.version_id)
    assert body["sku"]["id"] == str(lineup_target.sku_id)
    assert body["sku"]["name"] == "Lineup Cola 330 ml"
    reference_prefix = f"/api/sku-reference-images/{lineup_target.reference_id}/media"
    assert [
        (reference["id"], reference["original_filename"], reference["thumbnail_url"])
        for reference in body["sku"]["reference_images"]
    ] == [
        (str(lineup_target.reference_id), "cola-front.png", f"{reference_prefix}/thumbnail")
    ]


def test_lineup_for_another_version_or_sku_only_shows_its_own_boxes(
    lineup_target: LineupTarget,
) -> None:
    elsewhere = client.get(
        f"/api/dataset-versions/{lineup_target.other_version_id}/sku-lineup",
        params={"sku_id": str(lineup_target.sku_id)},
    ).json()
    other_sku = client.get(
        f"/api/dataset-versions/{lineup_target.version_id}/sku-lineup",
        params={"sku_id": str(lineup_target.other_sku_id)},
    ).json()

    assert [item["annotation_id"] for item in elsewhere["items"]] == [
        str(lineup_target.annotation_ids["other_version"])
    ]
    assert [item["annotation_id"] for item in other_sku["items"]] == [
        str(lineup_target.annotation_ids["other_sku"]),
        str(lineup_target.annotation_ids["reassigned_away"]),
    ]
    assert other_sku["sku"]["reference_images"] == []


def test_lineup_stops_at_two_hundred_boxes(
    database_engine: Engine,
    lineup_target: LineupTarget,
) -> None:
    with database_engine.begin() as connection:
        for index in range(200):
            create_annotation(
                connection,
                lineup_target.image_ids["second"],
                _values(lineup_target.other_sku_id, x=index, y=200, width=1, height=1),
            )

    body = client.get(
        f"/api/dataset-versions/{lineup_target.version_id}/sku-lineup",
        params={"sku_id": str(lineup_target.other_sku_id)},
    ).json()

    assert len(body["items"]) == 200
    assert body["truncated"] is True
    assert body["items"][0]["annotation_id"] == str(lineup_target.annotation_ids["other_sku"])


@pytest.mark.parametrize(
    ("missing", "code"),
    [("version", "dataset_version_not_found"), ("sku", "sku_not_found")],
)
def test_lineup_reports_unknown_versions_and_skus(
    lineup_target: LineupTarget,
    missing: str,
    code: str,
) -> None:
    version_id = uuid4() if missing == "version" else lineup_target.version_id
    sku_id = uuid4() if missing == "sku" else lineup_target.sku_id

    response = client.get(
        f"/api/dataset-versions/{version_id}/sku-lineup",
        params={"sku_id": str(sku_id)},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == code


def test_lineup_requires_a_sku(lineup_target: LineupTarget) -> None:
    response = client.get(f"/api/dataset-versions/{lineup_target.version_id}/sku-lineup")

    assert response.status_code == 422


def _create_lineup_annotations(
    connection: Connection,
    image_ids: dict[str, UUID],
    sku_id: UUID,
    other_sku_id: UUID,
) -> dict[str, UUID]:
    first = image_ids["first"]
    x, y, width, height = PRODUCT_BOX
    box = {"x": x, "y": y, "width": width, "height": height}
    created = {
        "accepted_right": create_annotation(connection, first, _values(sku_id, **box)),
        "accepted_left": create_annotation(
            connection, first, _values(sku_id, x=10, y=10, width=20, height=30)
        ),
        "second_photo": create_annotation(
            connection, image_ids["second"], _values(sku_id, x=5, y=5, width=20, height=30)
        ),
        "proposed": create_annotation(
            connection,
            first,
            _values(sku_id, x=20, lifecycle_state="proposed", review_state="unreviewed"),
        ),
        "flagged": create_annotation(
            connection, first, _values(sku_id, x=30, review_state="flagged")
        ),
        "other_sku": create_annotation(connection, first, _values(other_sku_id, x=40)),
        "reassigned_away": create_annotation(connection, first, _values(sku_id, x=50)),
        "reassigned_to_sku": create_annotation(connection, first, _values(other_sku_id, x=60)),
        "other_version": create_annotation(
            connection, image_ids["elsewhere"], _values(sku_id, x=70)
        ),
    }
    append_annotation_revision(
        connection, created["reassigned_away"], 1, _values(other_sku_id, x=50)
    )
    append_annotation_revision(
        connection, created["reassigned_to_sku"], 1, _values(sku_id, x=60)
    )
    return created


def _values(
    sku_id: UUID,
    *,
    x: float,
    y: float = 10,
    width: float = 20,
    height: float = 30,
    lifecycle_state: str = "verified",
    review_state: str = "accepted",
) -> dict[str, Any]:
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "class_type": "product",
        "sku_id": sku_id,
        "lifecycle_state": lifecycle_state,
        "review_state": review_state,
        "source": "human",
        "provenance": {},
        "confidence": None,
        "occluded": False,
        "truncated": False,
        "shelf_row": None,
        "polygon": None,
    }


def _insert_photo(
    connection: Connection,
    media_root: Path,
    dataset_id: UUID,
    image_id: UUID,
    file_name: str,
    index: int,
) -> None:
    canonical_key = f"canonical/{image_id}.png"
    path = media_root / canonical_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_photo_bytes())
    connection.execute(
        insert(images).values(
            id=image_id,
            dataset_id=dataset_id,
            original_media_key=f"original/{image_id}.png",
            canonical_media_key=canonical_key,
            thumbnail_media_key=f"thumbnails/{image_id}.jpg",
            media_type="image/png",
            original_filename=file_name,
            content_sha256=f"{index:x}" * 64,
            canonical_width=PHOTO_SIZE[0],
            canonical_height=PHOTO_SIZE[1],
            capture_metadata={},
        )
    )


def _photo_bytes() -> bytes:
    photo = Image.new("RGB", PHOTO_SIZE, "white")
    x, y, width, height = PRODUCT_BOX
    ImageDraw.Draw(photo).rectangle((x, y, x + width - 1, y + height - 1), fill="red")
    output = BytesIO()
    photo.save(output, format="PNG")
    return output.getvalue()


def _decoded(content: bytes) -> Image.Image:
    decoded = Image.open(BytesIO(content))
    assert decoded.format == "JPEG"
    return decoded


def _is_white(pixel: Any) -> bool:
    return all(channel > 235 for channel in pixel)


def _is_red(pixel: Any) -> bool:
    red, green, blue = pixel
    return bool(red > 200 and green < 60 and blue < 60)
