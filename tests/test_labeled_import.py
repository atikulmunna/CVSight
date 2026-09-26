from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, delete, func, insert, select, update

from shelfsight_api.app import app
from shelfsight_api.labeled_import import LabeledBox, LabeledImportError, read_labeled_dataset
from shelfsight_api.media import UnsafePathError
from shelfsight_api.models import (
    annotation_records,
    annotation_revisions,
    dataset_versions,
    datasets,
    images,
    skus,
)

client = TestClient(app)


def save_image(
    path: Path, color: str, size: tuple[int, int] = (100, 50), rotated: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    if rotated:
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, format="JPEG", exif=exif)
    else:
        image.save(path, format="JPEG")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def roboflow_dataset(import_root: Path) -> Path:
    """A Roboflow-style YOLO export: split/images and split/labels, names as a list."""
    root = import_root / "shelf"
    write_text(root / "data.yaml", "names: ['cola', 'chips']\nnc: 2\n")
    save_image(root / "train" / "images" / "a.jpg", "blue")
    write_text(
        root / "train" / "labels" / "a.txt",
        "0 0.5 0.5 0.4 0.4\n1 0.1 0.1 0.3 0.1 0.3 0.3 0.1 0.3\nnot a line\n5 0.5 0.5 0.1 0.1\n",
    )
    save_image(root / "valid" / "images" / "b.jpg", "green")
    write_text(root / "valid" / "labels" / "b.txt", "0 0.25 0.25 0.1 0.1\n")
    return root


def test_reads_a_roboflow_yolo_layout_with_boxes_and_polygons(tmp_path: Path) -> None:
    roboflow_dataset(tmp_path)

    dataset = read_labeled_dataset(tmp_path, "yolo", "shelf")

    assert dataset.class_names == ("cola", "chips")
    assert [image.path for image in dataset.images] == [
        "shelf/train/images/a.jpg",
        "shelf/valid/images/b.jpg",
    ]
    assert [image.source_split for image in dataset.images] == ["train", "validation"]
    cola, chips = dataset.images[0].boxes
    assert cola == LabeledBox("cola", 0.3, 0.3, 0.7, 0.7)
    assert chips == LabeledBox("chips", 0.1, 0.1, 0.3, 0.3)
    assert dataset.skipped_labels == 2


def test_reads_an_ultralytics_layout_with_indexed_names(tmp_path: Path) -> None:
    root = tmp_path / "ultra"
    write_text(root / "data.yaml", "names:\n  0: cola\n  1: chips\n")
    save_image(root / "images" / "train" / "c.jpg", "red")
    write_text(root / "labels" / "train" / "c.txt", "1 0.5 0.5 0.2 0.2\n")
    save_image(root / "images" / "val" / "unlabeled.jpg", "white")

    dataset = read_labeled_dataset(tmp_path, "yolo", "ultra")

    assert dataset.class_names == ("cola", "chips")
    assert [(image.source_split, len(image.boxes)) for image in dataset.images] == [
        ("train", 1),
        ("validation", 0),
    ]


@pytest.mark.parametrize(
    ("data_yaml", "code"),
    [
        (None, "missing_data_yaml"),
        ("names: {0: a, 2: b}\n", "invalid_class_names"),
        ("names: [a, a]\n", "invalid_class_names"),
        ("names: [a\n", "invalid_data_yaml"),
    ],
)
def test_refuses_yolo_datasets_it_cannot_read(
    tmp_path: Path, data_yaml: str | None, code: str
) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    if data_yaml is not None:
        write_text(root / "data.yaml", data_yaml)

    with pytest.raises(LabeledImportError) as error:
        read_labeled_dataset(tmp_path, "yolo", "broken")
    assert error.value.code == code


def test_refuses_paths_outside_the_import_root_and_missing_sources(tmp_path: Path) -> None:
    with pytest.raises(UnsafePathError):
        read_labeled_dataset(tmp_path, "yolo", "../outside")
    with pytest.raises(LabeledImportError) as missing:
        read_labeled_dataset(tmp_path, "coco", "nothing.json")
    assert missing.value.code == "dataset_not_found"


def test_reads_coco_boxes_normalized_to_each_image(tmp_path: Path) -> None:
    folder = tmp_path / "coco" / "valid"
    save_image(folder / "d.jpg", "yellow", size=(200, 100))
    write_text(
        folder / "_annotations.coco.json",
        json.dumps(
            {
                "categories": [{"id": 3, "name": "cola"}],
                "images": [{"id": 1, "file_name": "d.jpg", "width": 200, "height": 100}],
                "annotations": [
                    {"id": 1, "image_id": 1, "category_id": 3, "bbox": [20, 10, 40, 30]},
                    {"id": 2, "image_id": 1, "category_id": 3, "bbox": [0, 0, 5, 5], "iscrowd": 1},
                    {"id": 3, "image_id": 1, "category_id": 9, "bbox": [0, 0, 5, 5]},
                ],
            }
        ),
    )

    dataset = read_labeled_dataset(tmp_path, "coco", "coco/valid/_annotations.coco.json")

    assert dataset.class_names == ("cola",)
    (image,) = dataset.images
    assert image.path == "coco/valid/d.jpg"
    assert image.source_split == "validation"
    assert image.boxes == (LabeledBox("cola", 0.1, 0.1, 0.3, 0.4),)
    assert dataset.skipped_labels == 2


def test_reports_a_coco_image_that_is_missing(tmp_path: Path) -> None:
    write_text(
        tmp_path / "set.json",
        json.dumps(
            {
                "categories": [{"id": 1, "name": "cola"}],
                "images": [{"id": 1, "file_name": "gone.jpg", "width": 10, "height": 10}],
                "annotations": [],
            }
        ),
    )

    with pytest.raises(LabeledImportError) as error:
        read_labeled_dataset(tmp_path, "coco", "set.json")
    assert error.value.code == "image_not_found"


@pytest.fixture
def import_target(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[UUID, UUID, UUID, Path]:
    dataset_id, version_id, sku_id = uuid4(), uuid4(), uuid4()
    with database_engine.begin() as connection:
        connection.execute(insert(datasets).values(id=dataset_id, name=f"dataset-{dataset_id}"))
        connection.execute(insert(dataset_versions).values(id=version_id, dataset_id=dataset_id))
        connection.execute(insert(skus).values(id=sku_id, name=f"cola-{sku_id}"))
    import_root = tmp_path / "imports"
    for name, value in (
        ("get_engine", lambda: database_engine),
        ("get_media_root", lambda: tmp_path / "media"),
        ("get_import_root", lambda: import_root),
    ):
        monkeypatch.setattr(f"shelfsight_api.labeled_import_api.{name}", value)
    yield dataset_id, version_id, sku_id, import_root
    with database_engine.begin() as connection:
        connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        connection.execute(delete(skus).where(skus.c.id == sku_id))


def imports_url(dataset_id: UUID, version_id: UUID, suffix: str = "") -> str:
    return f"/api/datasets/{dataset_id}/versions/{version_id}/labeled-imports{suffix}"


def current_boxes(engine: Engine, dataset_id: UUID) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                images.c.original_filename,
                images.c.status,
                annotation_revisions.c.x,
                annotation_revisions.c.y,
                annotation_revisions.c.width,
                annotation_revisions.c.height,
                annotation_revisions.c.sku_id,
                annotation_revisions.c.source,
                annotation_revisions.c.lifecycle_state,
                annotation_revisions.c.review_state,
            )
            .select_from(
                images.join(annotation_records, annotation_records.c.image_id == images.c.id).join(
                    annotation_revisions,
                    (annotation_revisions.c.annotation_id == annotation_records.c.id)
                    & (annotation_revisions.c.revision == annotation_records.c.current_revision),
                )
            )
            .where(images.c.dataset_id == dataset_id)
            .order_by(images.c.original_filename, annotation_revisions.c.x)
        ).mappings()
        return [dict(row) for row in rows]


def test_preview_counts_classes_and_suggests_exactly_named_skus(
    import_target: tuple[UUID, UUID, UUID, Path],
) -> None:
    dataset_id, version_id, sku_id, import_root = import_target
    root = roboflow_dataset(import_root)
    write_text(root / "data.yaml", f"names: ['cola-{sku_id}', 'chips']\n")

    response = client.post(
        imports_url(dataset_id, version_id, "/preview"), json={"format": "yolo", "path": "shelf"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["images"], body["boxes"], body["skipped_labels"]) == (2, 3, 2)
    assert body["classes"] == [
        {"name": f"cola-{sku_id}", "boxes": 2, "images": 2, "suggested_sku_id": str(sku_id)},
        {"name": "chips", "boxes": 1, "images": 1, "suggested_sku_id": None},
    ]
    assert body["source_splits"] == {"train": 1, "validation": 1}


def test_import_needs_every_class_mapped(
    import_target: tuple[UUID, UUID, UUID, Path],
) -> None:
    dataset_id, version_id, sku_id, import_root = import_target
    roboflow_dataset(import_root)

    response = client.post(
        imports_url(dataset_id, version_id),
        json={"format": "yolo", "path": "shelf", "class_skus": {"cola": str(sku_id)}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unmapped_classes"


def test_import_writes_verified_boxes_and_is_safe_to_rerun(
    database_engine: Engine,
    import_target: tuple[UUID, UUID, UUID, Path],
) -> None:
    dataset_id, version_id, sku_id, import_root = import_target
    roboflow_dataset(import_root)
    request = {
        "format": "yolo",
        "path": "shelf",
        "class_skus": {"cola": str(sku_id), "chips": None},
    }

    first = client.post(imports_url(dataset_id, version_id), json=request)
    second = client.post(imports_url(dataset_id, version_id), json=request)

    assert first.status_code == 200
    assert [(r["outcome"], r["boxes"]) for r in first.json()["results"]] == [
        ("imported", 2),
        ("imported", 1),
    ]
    assert first.json()["next_offset"] is None
    assert [r["code"] for r in second.json()["results"]] == ["already_labeled", "already_labeled"]
    boxes = current_boxes(database_engine, dataset_id)
    assert len(boxes) == 3
    assert {(b["source"], b["lifecycle_state"], b["review_state"]) for b in boxes} == {
        ("imported", "verified", "accepted")
    }
    assert {b["status"] for b in boxes} == {"labeled"}
    chips, cola = [b for b in boxes if b["original_filename"] == "a.jpg"]
    assert (chips["x"], chips["y"], chips["width"], chips["height"], chips["sku_id"]) == (
        10.0,
        5.0,
        20.0,
        10.0,
        None,
    )
    assert (cola["x"], cola["y"], cola["width"], cola["height"], cola["sku_id"]) == (
        30.0,
        15.0,
        40.0,
        20.0,
        sku_id,
    )


def test_import_pages_through_images_and_can_leave_boxes_for_review(
    database_engine: Engine,
    import_target: tuple[UUID, UUID, UUID, Path],
) -> None:
    dataset_id, version_id, sku_id, import_root = import_target
    roboflow_dataset(import_root)
    request = {
        "format": "yolo",
        "path": "shelf",
        "class_skus": {"cola": str(sku_id), "chips": None},
        "annotation_state": "proposed",
        "limit": 1,
    }

    first = client.post(imports_url(dataset_id, version_id), json=request).json()
    rest = client.post(imports_url(dataset_id, version_id), json={**request, "offset": 1}).json()

    assert (first["next_offset"], first["total_images"]) == (1, 2)
    assert rest["next_offset"] is None
    boxes = current_boxes(database_engine, dataset_id)
    assert {(b["lifecycle_state"], b["review_state"], b["status"]) for b in boxes} == {
        ("proposed", "unreviewed", "pre_labeled")
    }


def test_import_refuses_rotated_labeled_images_and_frozen_versions(
    database_engine: Engine,
    import_target: tuple[UUID, UUID, UUID, Path],
) -> None:
    dataset_id, version_id, sku_id, import_root = import_target
    root = import_root / "rotated"
    write_text(root / "data.yaml", "names: [cola]\n")
    save_image(root / "train" / "images" / "r.jpg", "purple", rotated=True)
    write_text(root / "train" / "labels" / "r.txt", "0 0.5 0.5 0.2 0.2\n")
    request = {"format": "yolo", "path": "rotated", "class_skus": {"cola": str(sku_id)}}

    rotated = client.post(imports_url(dataset_id, version_id), json=request)
    with database_engine.begin() as connection:
        connection.execute(
            update(dataset_versions)
            .where(dataset_versions.c.id == version_id)
            .values(snapshot_at=func.now())
        )
    frozen = client.post(imports_url(dataset_id, version_id), json=request)

    assert [r["code"] for r in rotated.json()["results"]] == ["rotated_image_labels"]
    assert frozen.status_code == 409
    assert frozen.json()["detail"]["code"] == "dataset_version_frozen"


def test_annotators_cannot_import(import_target: tuple[UUID, UUID, UUID, Path]) -> None:
    dataset_id, version_id, _, import_root = import_target
    roboflow_dataset(import_root)

    response = client.post(
        imports_url(dataset_id, version_id, "/preview"),
        json={"format": "yolo", "path": "shelf"},
        headers={"X-ShelfSight-Actor": "annotator:anna"},
    )

    assert response.status_code == 403
