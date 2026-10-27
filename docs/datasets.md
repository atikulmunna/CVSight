# Datasets

Image storage, labeled imports, project progress and versions, and dataset exports. Back to the [README](../README.md).

## Managed image storage

Set `SHELFSIGHT_MEDIA_ROOT` to an application-owned directory. Set
`SHELFSIGHT_IMPORT_ROOT` to the read-only source directory used by bounded manifest
imports. Neither value may be a filesystem root.

The image API accepts JPEG and PNG uploads up to 50 MiB and 120 million decoded pixels.
It applies EXIF orientation, writes a canonical image, and creates a 512-pixel JPEG
thumbnail. Each database image row owns three relative managed keys:

- `original_media_key` contains the exact uploaded bytes.
- `canonical_media_key` contains the orientation-normalized annotation image.
- `thumbnail_media_key` contains the derived browser thumbnail.

No face blurring is applied. Ingest metadata states this explicitly, and the exact
original bytes are never silently replaced by a derived image. See [SECURITY.md](../SECURITY.md)
for the complete media privacy policy.

Files use content-addressed names under the managed root. Failed ingestion removes files
created by that attempt, and exact duplicates do not create new files. There is no image
deletion endpoint yet. Do not delete image rows or managed files manually. A future
deletion workflow must remove a media key only after its final database reference is
deleted.

Upload one image with multipart form data:

```text
POST /api/datasets/{dataset_id}/versions/{version_id}/images
```

Read an image's place in the grid order (newest first), with the images either side:

```text
GET /api/datasets/{dataset_id}/versions/{version_id}/images/{image_id}/neighbors
{"previous_id": "image-uuid", "next_id": null, "position": 42, "total": 42}
```

Import up to 250 files from the configured import root:

```text
POST /api/datasets/{dataset_id}/versions/{version_id}/images/import
{"items": [{"path": "relative/folder/shelf.jpg", "capture_metadata": {}}]}
```

Manifest paths must be relative POSIX paths. Absolute paths, parent traversal, Windows
paths, and URLs are rejected. Image bytes are available only through validated API
routes under `/api/images/{image_id}/media/{variant}`.

## Labeled dataset import

Owners bring an existing labeled dataset into the open version from the import root
(the `import-local` folder next to `compose.yaml` on a Docker deployment, or
`SHELFSIGHT_IMPORT_ROOT` in development), either with **Import labeled dataset** on the Images page or through the API:

```text
POST /api/datasets/{dataset_id}/versions/{version_id}/labeled-imports/preview
{"format": "yolo", "path": "datasets/shelf-audit"}

POST /api/datasets/{dataset_id}/versions/{version_id}/labeled-imports
{"format": "yolo", "path": "datasets/shelf-audit",
 "class_skus": {"cola": "sku-uuid", "chips": null},
 "annotation_state": "verified", "offset": 0}
```

- **Formats.** A YOLO folder with `data.yaml` in either the Roboflow layout
  (`train/images`, `train/labels`) or the Ultralytics layout (`images/train`,
  `labels/train`); polygon labels become their bounding boxes. A COCO JSON file whose
  image paths are relative to the file.
- **Classes.** The preview suggests the active SKU whose name matches a class exactly.
  The import requires every class to be mapped, to a SKU or to `null`, so no box gets a
  silently wrong SKU.
- **State.** `verified` imports ground truth (images become `labeled`); `proposed`
  imports boxes for review (images become `pre_labeled`). Boxes are recorded with
  source `imported`.
- **Pages.** Each request imports up to 100 images and returns `next_offset`. Rerunning
  is safe: an image already in the project keeps its labels, and one without labels is
  completed.
- **Splits.** A source dataset's train, valid, or test folder is stored as
  `source_split`, not `split`, because random source splits can leak near-duplicate
  photos into test.
- **Rotation.** Labeled images with EXIF rotation are rejected, since the source does
  not say whether its boxes refer to the stored or the rotated pixels.

## Project progress

The project overview reads bounded progress totals from:

```text
GET /api/dataset-versions/{version_id}/progress
```

The summary reports box decisions, known, Unknown, and unassigned accepted product
identities, reviewed images, and flagged annotations. Open versions use current
annotation revisions. Frozen versions use their immutable snapshot, so later edits
cannot change released progress.

## Project versions and releases

Owners can open the project release history at:

```text
http://127.0.0.1:5173/projects/{project_id}/versions
```

The page separates the current working version from immutable releases. It shows the
current review queue status, reviewer sign-off for each release, and deterministic
detection and recognition downloads. A frozen snapshot created outside Review QA is
shown separately and is never presented as reviewer-approved.

Project and version routes:

- `GET /api/datasets` lists every project with its open and latest version and image count.
- `GET /api/datasets/{dataset_id}` returns one project summary, or 404 when it does not exist.
- `GET /api/datasets/{dataset_id}/versions` lists working and immutable versions.
- `POST /api/datasets/{dataset_id}/versions` starts the next owner-only working version.

Starting the next working version is allowed only when no open version exists. It uses
the latest immutable version as its parent and copies that release's image membership.
The parent snapshot and its exports remain unchanged.

## Dataset exports

Only immutable, snapshotted dataset versions can be exported:

- `POST /api/datasets` creates a dataset and its first open version.
- `POST /api/dataset-versions/{version_id}/snapshot` freezes the export input.
- `GET /api/dataset-versions/{version_id}/exports/detection`
- `GET /api/dataset-versions/{version_id}/exports/recognition`
- `GET /api/dataset-versions/{version_id}/exports/{format}?classes=product&split=70,20,10`
  for the training formats `coco`, `yolo`, `yolo-seg`, `csv`, `createml`, and `tfrecord`

Every route returns a deterministic ZIP file with `manifest.json`. The manifest records
the dataset and version identifiers, snapshot timestamp, artifact schema, export
policies, counts, and SHA-256 checksum and byte size for every payload file.

The detection artifact contains canonical images and `annotations.coco.json`. It uses
fixed category IDs: product `1`, gap `2`, and shelf label `3`. Product detection is
SKU-agnostic, and only verified, accepted geometry is included.

The recognition artifact contains deterministic PNG crops and `samples.jsonl`. Each
sample preserves its annotation ID and revision, source image fingerprint, exact
floating-point box, integer crop boundary, attributes, source, and provenance. Active
SKUs are trainable. Merged SKUs resolve to their final active target while retaining
the source ID. Unknown, deprecated, and unassigned samples remain in the artifact with
`trainable=false`.

The training formats hold the same images and verified boxes, laid out the way common
tools read them. On the Versions page, **Training dataset** builds the download from a
format, a class mode, and split percentages.

| Format | Layout |
| --- | --- |
| COCO JSON | `train/`, `valid/`, `test/`, each with its images and `_annotations.coco.json` |
| YOLO | `images/` and `labels/` with `train`, `val`, and `test` folders, plus `data.yaml` |
| YOLO segmentation | the YOLO layout, with one polygon per label line: `class x1 y1 ... xn yn`, normalized to the photo |
| CSV | one folder per split with its images and `_annotations.csv`: one row per box with `filename,width,height,class,xmin,ymin,xmax,ymax` in pixels |
| CreateML JSON | one folder per split with its images and `_annotations.createml.json`, boxes by center |
| TFRecord | `train/train.tfrecord` and the other splits, plus `label_map.pbtxt`, using the TensorFlow Object Detection API feature names |

Product outlines from instance segmentation projects travel with their boxes. In COCO
each annotation carries a `segmentation`: the outline's points, with the outline's area
as `area`, or an empty list for a box without an outline. YOLO segmentation cannot mix
boxes and polygons in one label file, so a box without an outline, such as a gap or a
shelf label, is written as its four corners. CSV, CreateML, TFRecord, and plain YOLO
hold boxes only. `manifest.json` counts the outlined annotations.

`classes=product` names boxes by class type (product, gap, shelf label) in that fixed
order. `classes=sku` names each product box by its SKU, the way Roboflow exports a
labeled dataset; merged SKUs follow their final target, and unknown, deprecated, or
unassigned products become `unknown`. Two different SKUs with the same name in one
export are told apart by UPC, so merge duplicate catalog entries before release to keep
one class per product.

`split` takes whole train, validation, and test percentages that add up to 100. A split
already recorded on a photo is kept, including an imported dataset's own train, valid,
and test folders. The other photos are assigned by ratio, but photos that share content,
a near-duplicate group, a capture session, a store, or a fixture always land in the
same split, the same rule training preparation enforces, so test scores are not
inflated by near-identical photos. With few such groups the result can differ from the
requested ratio; `manifest.json` records the counts per split.
