# CVSight

CVSight is a self-hosted image annotation platform for dense retail shelves. The
current application shell contains a React and TypeScript frontend, a FastAPI backend,
PostgreSQL connectivity, and an Alembic migration path.

## Requirements

- Python 3.12 or newer
- uv
- Node.js 24 or newer
- npm
- Docker with Compose for the local PostgreSQL service

## First-time setup

1. Copy `.env.example` to `.env`.
2. Replace the database password placeholder with a local-only value.
3. Generate a password hash for each local application user:

```powershell
uv run python -m shelfsight_api.auth_cli
```

4. Put the hashes in `SHELFSIGHT_AUTH_USERS`. Use only the `owner`, `annotator`, and
   `reviewer` roles. The example keeps secure cookies off for local HTTP; set
   `SHELFSIGHT_SESSION_COOKIE_SECURE=true` when serving through HTTPS.
5. Load the variables from `.env` into the current terminal.

```powershell
Get-Content .env | Where-Object { $_ -and -not $_.StartsWith('#') } | ForEach-Object {
    $name, $value = $_.Split('=', 2)
    Set-Item -Path "Env:$name" -Value $value
}
```

6. Install dependencies:

```powershell
uv sync
npm --prefix frontend install
```

7. Start PostgreSQL:

```powershell
docker compose --env-file .env -f compose.dev.yaml up -d database
```

8. Start the API, worker, and frontend:

```powershell
./scripts/dev.ps1
```

The frontend runs at `http://127.0.0.1:5173` and proxies `/api` requests to
`http://127.0.0.1:8000`. The development script also starts one separate durable-job
worker named `local-worker`.

## Quality checks

Run the complete local check:

```powershell
./scripts/check.ps1
```

The command runs Python tests, Ruff, mypy, frontend tests, ESLint, TypeScript checking,
and the production frontend build.

Run the networked dependency and container vulnerability gate before a release:

```powershell
./scripts/security-check.ps1
```

The threat model, privacy behavior, retention rules, deletion boundary, access logging,
and current vulnerability acceptance are documented in [SECURITY.md](SECURITY.md).
The verified backup, clean restore, restart, and projection rebuild procedures are in
[RECOVERY.md](RECOVERY.md).
The supported release boundary, configuration matrix, GPU guidance, workflow, and known
limitations are in [RELEASE.md](RELEASE.md). Dependency, model, dataset, and artifact
licensing is recorded in [LICENSE_POLICY.md](LICENSE_POLICY.md). Curated release
measurements, thresholds, limitations, and reproducibility commands are published in
[BENCHMARKS.md](BENCHMARKS.md), with a sanitized machine-readable snapshot under
[`docs/benchmarks`](docs/benchmarks/release-0.1.0.json).

## Database migrations

Apply migrations:

```powershell
uv run alembic upgrade head
```

Revert the most recent migration:

```powershell
uv run alembic downgrade -1
```

`SHELFSIGHT_DATABASE_URL` is required. The application never stores database
credentials in source files.

## Authentication and roles

Every application API, including managed media, exports, jobs, analytics, and model
operations, requires a server-issued session. Health probes and the login route remain
public. Passwords are stored only as scrypt hashes in the local environment. Successful
login creates a random opaque session token; PostgreSQL stores only its SHA-256 hash.
The browser receives the token in an HttpOnly, SameSite Strict cookie with a 12-hour
lifetime. Logout revokes the server-side session.

Use the three roles according to their current responsibilities:

- `annotator` verifies geometry, assigns SKUs, and confirms propagation suggestions.
- `reviewer` corrects annotations, resolves the review queue, and signs off snapshots.
- `owner` manages datasets, catalog records, jobs, models, exports, and analytics.

Login successes, failures, logouts, and authenticated authorization denials create
immutable audit events. Passwords and session tokens are never written to audit rows.
Changing or removing a configured user's role invalidates that user's existing
sessions. Add or update accounts by editing the local `SHELFSIGHT_AUTH_USERS` value and
restarting the API. This single-project release does not include team administration.

## Background jobs

Background work is stored in PostgreSQL and claimed by the separate Python worker with
transactional `SKIP LOCKED` selection. Redis, Celery, and in-process model execution
are not used.

Job API routes:

- `POST /api/jobs` creates or deduplicates work by job type and idempotency key.
- `GET /api/jobs/{job_id}` returns state, progress, bounded error summaries, and every
  attempt.
- `POST /api/jobs/{job_id}/cancel` cancels queued work or requests cancellation of
  running work.
- `GET /api/workers/health` reports live and stale worker heartbeats.

Run a worker directly with:

```powershell
uv run python -m shelfsight_api.worker --worker-id local-worker
```

Claims use renewable leases. A new worker returns expired claims to the queue, or marks
them failed when their attempt limit is exhausted. Result writers and successful job
completion share one database transaction, so a failed commit cannot leave duplicated
derived rows. Workers claim only job types they have registered, which lets detector
and refiner workers share the queue without consuming each other's work.

To register the selected RF-DETR detector in a worker environment that has RF-DETR,
PyTorch, and CUDA available, set:

```text
SHELFSIGHT_RFDETR_CHECKPOINT=path/to/checkpoint_best_total.pth
SHELFSIGHT_RFDETR_VERSION=1.8.3-one-epoch
```

The checkpoint path is operator configuration and is never accepted from an API
request. Its SHA-256 fingerprint and declared version are included in every valid
response. This worker claims `detect` jobs.

SAM box refinement is optional. Run it in a separate worker environment because the
measured SAM checkpoint nearly fills the development GPU:

```text
SHELFSIGHT_SAM3_CHECKPOINT=path/to/sam3.pt
SHELFSIGHT_SAM3_VERSION=46957e47805eaa273f4aa7bbbd25a88bca9108ce
```

This worker claims `refine` jobs. It accepts one positive normalized box prompt, as
supported by the tested SAM image API, and returns the valid result with the greatest
overlap with the hint. It does not expose point prompts or persist masks.

The optional T005 CLIP baseline runs in its own worker process:

```text
SHELFSIGHT_CLIP_MODEL_ROOT=path/to/openai-clip-vit-base-patch32-snapshot
```

The directory must contain `config.json`, `preprocessor_config.json`, and
`pytorch_model.bin`. The weights must match the pinned T005 SHA-256 fingerprint.
RF-DETR, SAM, and CLIP configuration are mutually exclusive in one worker process so
GPU models do not contend for the measured device memory.

## Model boundary

Model jobs use four strict operations:

- `detect` returns bounded axis-aligned proposals.
- `refine` returns one bounded axis-aligned box from a box hint.
- `embed` returns a normalized vector for either recognition or propagation.
- `retrieve` returns score-ordered candidates from the matching purpose namespace.

Requests contain stable database identifiers, canonical image dimensions and SHA-256,
the model role, and an operation-specific configuration. They never contain a media
path, checkpoint path, executable name, or concrete model choice. Responses repeat the
request identity and effective configuration and include an input fingerprint,
versioned model or index provenance, timing, and validated normalized results.

Request and response payloads, decoded image size, prediction count, embedding
dimension, retrieval count, geometry, scores, and operation time are bounded. Invalid
input, stale fingerprints, missing models, timeouts, runtime failures, and malformed
model output map to stable errors without exposing internal exception text. Detector
results pass through a validated transactional writer before becoming annotation
proposals. Refinement results remain job suggestions until a user explicitly applies
one through the revision-safe annotation API.

## Pre-labeling

Queue detector work for up to 250 canonical images:

```text
POST /api/prelabels/batch
{
  "image_ids": ["image-uuid"],
  "idempotency_key": "store-a-run-001",
  "configuration": {
    "confidence_threshold": 0.3,
    "inference_strategy": "full_image"
  }
}
```

Each image becomes an independent resumable `detect` job. The response reports queued,
deduplicated, and rejected images separately, so one missing image or idempotency
conflict does not prevent valid images from being queued.

Successful detections create `model` annotations in `proposed` and `unreviewed` state.
Their confidence, model identity and version, artifact fingerprint, effective
configuration, input fingerprint, request identity, proposal identity, and job identity
are retained in provenance. Exact repeated outputs share a deterministic proposal
fingerprint. Geometrically distinct boxes are not suppressed by IoU, including close
neighbors on dense shelves.

Request optional box refinement without changing the annotation:

```text
POST /api/annotations/{annotation_id}/refine
{
  "expected_revision": 1,
  "idempotency_key": "refine-image-001-box-012"
}
```

The returned job can be read through `GET /api/jobs/{job_id}`. A stale revision is
rejected, and a successful refinement remains a suggestion. Human verification still
requires the existing explicit annotation accept action.

## Recognition embeddings and SKU candidates

T022 stores normalized recognition embeddings in PostgreSQL using pgvector. Development
uses a rootless local image built from the pinned pgvector 0.8.6 PostgreSQL 17 base,
and migration `0006` enables the `vector` extension. Candidate search is exact cosine
search. No approximate index or separate vector service is used at the current gallery
size.

Queue full-image embeddings for one or more active, non-unknown SKU references:

```text
POST /api/recognition/gallery-embeddings
{
  "reference_image_ids": ["reference-uuid"]
}
```

Queue an embedding for an accepted product annotation at an exact revision:

```text
POST /api/annotations/{annotation_id}/recognition-embedding
{
  "expected_revision": 3
}
```

Both routes create durable `embed` jobs and deduplicate the model, target, and version
combination. Annotation embeddings retain the exact annotation revision and crop.
Reference embeddings retain the immutable reference identity and content fingerprint.
The worker rejects a result if the annotation or reference changed before its
transaction commits.

Read up to nine grouped SKU suggestions after the jobs succeed:

```text
GET /api/annotations/{annotation_id}/sku-candidates?expected_revision=3&top_k=5
```

The response uses the best cosine score among each SKU's reference exemplars and includes
the frozen T005 calibration version, unknown threshold, and
`automatic_confirmation=false`. Annotation embeddings are never searched as gallery
items. All active reference images must have a current embedding before results are
served, so adding a new reference temporarily returns `gallery_not_current` until its
job succeeds. An edited annotation returns `stale_embedding` instead of serving its old
crop silently.

The pinned CLIP baseline remains an exploratory suggestion model. T005 measured 76.50
percent top-five accuracy and 50.00 percent unknown false acceptance on its test set.
These results do not permit automatic SKU assignment.

## Similarity propagation

The Propagate workspace follows the handoff's label-one-confirm-many layout while
keeping every label decision explicit. It uses a separate propagation embedding
purpose and index namespace even when the exploratory T005 CLIP adapter supplies the
vectors. T005 measured only 50.00 percent top-one same-SKU crop retrieval and 13.33
percent hard-pair confusion, so the grid never preselects a crop.

Queue current accepted product crops for asynchronous propagation embeddings:

```text
POST /api/propagation/embeddings
{"annotation_ids":["<annotation-uuid>"]}
```

After the worker completes, create an auditable suggestion set from an exact
human-assigned seed revision:

```text
POST /api/annotations/{annotation_id}/propagation-suggestions
{"expected_revision":3,"top_k":18}
```

Every returned crop starts skipped. The response includes exact cosine scores,
selected and skipped counts, model quality evidence, and any configured SKU hard
pairs. Hard-pair suggestions require an individual acknowledgement.

Confirm or skip every suggestion in one transaction:

```text
POST /api/propagation/suggestion-sets/{suggestion_set_id}/confirm
{"decisions":[{"suggestion_id":"<uuid>","decision":"confirm","reviewed_individually":true}]}
```

The candidate index is scoped to the currently open shelf image, matching the
confirmation-grid workflow and the bounded browser queue. Suggestion rows remain
separate from annotation revisions. Only this actor-authored confirmation action
appends a final `source=propagated` revision. If the seed or any candidate changed,
the whole batch rolls back. Explicit Unknown / Other assignments are never candidates
or propagation seeds.

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
original bytes are never silently replaced by a derived image. See [SECURITY.md](SECURITY.md)
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

Import up to 250 files from the configured import root:

```text
POST /api/datasets/{dataset_id}/versions/{version_id}/images/import
{"items": [{"path": "relative/folder/shelf.jpg", "capture_metadata": {}}]}
```

Manifest paths must be relative POSIX paths. Absolute paths, parent traversal, Windows
paths, and URLs are rejected. Image bytes are available only through validated API
routes under `/api/images/{image_id}/media/{variant}`.

## Annotation API

Annotation writes derive their author from the authenticated server session. A client
cannot select or override the audit actor. Coordinates are pixels in the canonical
image coordinate system. Boxes must have positive dimensions and remain inside the
canonical image bounds.

The available routes are:

- `GET /api/images/{image_id}/annotations`
- `GET /api/annotations/{annotation_id}`
- `POST /api/images/{image_id}/annotations`
- `PUT /api/annotations/{annotation_id}`
- `POST /api/annotations/{annotation_id}/accept`
- `POST /api/annotations/{annotation_id}/assign-sku`
- `POST /api/annotations/{annotation_id}/reject`
- `POST /api/annotations/{annotation_id}/restore`
- `POST /api/annotations/batch`

`PUT` is a full annotation replacement and requires `expected_revision`. Accept,
reject, restore, and every batch operation also require the expected current revision.
Accept transitions an active proposal to verified and accepted. A stale revision
returns HTTP 409 without overwriting newer work. Batch update, reject, and restore
operations run in one transaction, so one failure rolls back the whole batch.

SKU assignment accepts `expected_revision` and `sku_id`. The annotation must be an
accepted product box and the target SKU must be active. Each assignment appends a
revision with `assign_sku` provenance. Earlier model proposal and human verification
revisions remain unchanged, which keeps identity review separate from box provenance.

Domain errors use a stable response shape:

```json
{
  "detail": {
    "code": "stale_revision",
    "message": "annotation changed after the expected revision"
  }
}
```

## SKU catalog

The catalog stores the product hierarchy as category, subcategory, brand, and variant,
with an optional unique UPC. Migration `0004_sku_catalog` creates the explicit
`Unknown / Other` target. The unknown target remains active and cannot be edited,
deprecated, or merged.

Catalog routes:

- `GET /api/skus`
- `GET /api/skus/{sku_id}`
- `POST /api/skus`
- `PUT /api/skus/{sku_id}`
- `POST /api/skus/{sku_id}/deprecate`
- `POST /api/skus/{sku_id}/merge`
- `POST /api/skus/{sku_id}/reference-images`
- `GET /api/sku-reference-images/{reference_id}/media/{variant}`

Merge targets must be active. A merge appends auditable annotation revisions that
point current annotations to the target, moves non-duplicate reference images, and
changes the source to `merged` in one database transaction. Reference uploads use the
same size, decoded-pixel, format, orientation, and path validation as dataset images.
Only decoded JPEG and PNG content is accepted.

The web catalog loads up to 2,500 records into a virtualized result list. Search
matches names, UPCs, hierarchy terms, prefixes, small typos, and abbreviations.
Favorites and the 12 most recent selections are stored locally in the browser. If the
catalog API is unavailable, the screen displays a read-only 2,001-SKU fixture for
search and rendering checks.

Owners can optionally attach a CSV catalog while creating a project. The file must be
2 MiB or smaller and contain no more than 2,500 SKU rows. The `name` header is required;
`upc`, `category`, `subcategory`, `brand`, and `variant` are optional. UPC values must
contain 8, 12, 13, or 14 digits and cannot repeat in the file or existing catalog.
Quoted commas and UTF-8 byte-order marks are supported. Project and catalog creation
use one database transaction, so any catalog conflict rolls back the new project and
all rows. `POST /api/datasets` accepts the validated rows in its optional `catalog`
array and returns `imported_skus`.

After creation, the web app opens the new project overview and shows a first-image
checklist. The checklist leads the owner to upload a shelf image, open it, correct and
decide its boxes, assign identities as needed, wait for saved changes, and mark the
image reviewed. Matching guidance on the Images page disappears after a reviewed
image is present.

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

Version management routes:

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

Both routes return deterministic ZIP files with `manifest.json`. The manifest records
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

## RF-DETR training from reviewed snapshots

The detector pipeline accepts only an immutable detection export. Images must declare
`train`, `validation`, or `test` in their capture metadata. Exact duplicates,
near-duplicate groups, capture sessions, stores, and fixtures cannot cross those split
boundaries. The test split is written separately and RF-DETR training runs with
`run_test=false`.

Create a local license approval file for every dataset in the snapshot and the exact
starting checkpoint:

```json
{
  "datasets": [
    {
      "name": "Reviewed shelf dataset",
      "license": "CC BY 4.0",
      "source": "https://example.com/dataset-source",
      "approved": true
    }
  ],
  "starting_checkpoint": {
    "name": "RF-DETR Nano",
    "license": "Apache-2.0",
    "source": "https://github.com/roboflow/rf-detr",
    "approved": true
  }
}
```

Download the signed snapshot export, then prepare the class-agnostic product dataset.
The code version must identify the exact source revision used for the run:

```powershell
Invoke-WebRequest `
  http://127.0.0.1:8000/api/dataset-versions/<version-id>/exports/detection `
  -OutFile snapshot-detection.zip

python -m benchmark_tool prepare-rfdetr-training `
  --export snapshot-detection.zip `
  --output training-dataset `
  --checkpoint rf-detr-nano.pth `
  --licenses license-approvals.json `
  --code-version <source-revision> `
  --seed 1337 `
  --epochs 5
```

The prepared manifest records the snapshot and export checksums, checkpoint checksum,
licenses, code version, random seed, configuration, split counts, and every generated
file checksum. Human, imported, propagated, and teacher-generated labels retain
separate origins in the COCO annotation attributes.

Run training in the retained RF-DETR CUDA environment, then generate both fixed test
strategies and evaluate them:

```powershell
python -m benchmark_tool train-rfdetr `
  --dataset training-dataset `
  --checkpoint rf-detr-nano.pth `
  --output training-run

python -m benchmark_tool predict-rfdetr-training `
  --dataset training-dataset `
  --checkpoint training-run/checkpoint_best_total.pth `
  --output test-predictions.json

python -m benchmark_tool evaluate-rfdetr-training `
  --dataset training-dataset `
  --predictions test-predictions.json `
  --output evaluation-report.json
```

The fixed strategies follow T004: full-image inference at confidence 0.3 is the
default, and 2 by 2 sliced inference with 0.1 overlap and NMS IoU 0.5 is the dense-scene
retry. The evaluation report includes product recall, precision, mAP 50, mAP 50:95,
duplicate rate, and per-image dense-scene failures for both strategies.

## Evaluated model registry

Register the trained checkpoint and its frozen evaluation report as immutable snapshot
artifacts before adding a model candidate. Model metadata must include the training
manifest checksum, code version, random seed, approved-license flag, training
configuration, and runtime compatibility. Evaluation metadata must bind the exact
model checksum and frozen evaluation snapshot checksum, use the same code version and
seed, and include mAP, product recall, duplicate rate, dense-scene recall, and
overlapping-product recall.

Registry routes:

- `POST /api/model-registry/candidates`
- `GET /api/model-registry?model_role=known_sku_detector`
- `GET /api/model-deployments/{model_role}`
- `POST /api/model-deployments/{model_role}/promote`
- `POST /api/model-deployments/{model_role}/rollback`

Candidate registration never changes the default model, even when the candidate is
newer or has different metrics. Promotion requires the candidate ID and the exact
currently active ID. The first promotion uses `expected_active_id: null`. Concurrent
or stale requests return a conflict instead of overwriting deployment state.

Every promotion and rollback creates an immutable audit event. Deployment state keeps
both active and previous candidates, so rollback swaps the pointers without a database
migration or model re-registration. Responses expose artifact keys and SHA-256 values.
The current GPU workers still load operator-mounted local checkpoint paths, so the
operator must materialize the selected artifact and restart the corresponding worker
after changing the deployment pointer.

## Realogram reconstruction

`benchmark_tool.analytics.reconstruct_realogram` converts verified product facings
into top-to-bottom shelf rows and left-to-right facing sequences. Existing reviewed
`shelf_row_id` values are authoritative. When every row value is absent, the function
clusters boxes by vertical center and marks the result `provisional` until a reviewer
confirms the rows. Unknown SKU identity is preserved and does not affect ordering.

Complete reviewed inputs return `complete`. An incomplete observation returns a
visible `partial` result. A severe oblique view returns `unsupported` with the reason
`severe_oblique_view` instead of producing misleading row order. Mixed manual and
automatic row assignments are rejected. Annotators can correct the selected facing's
shelf row directly in the annotation inspector, using row 0 for the top row.

## Gap and availability analysis

Automatic SAM gap prompting was rejected by the T003 benchmark, so geometry-derived
gaps are review proposals only. `benchmark_tool.gap_analytics.derive_gap_proposals`
finds visible horizontal intervals not occupied by reviewed product facings within a
reviewed shelf-row ROI. It never assigns a SKU. Reviewers can duplicate or reshape a
box, change its class to `Visible gap`, assign its shelf row, and accept it.

`summarize_visible_gaps` computes visible gap fraction from accepted gap annotations
only. Unreviewed or flagged gaps keep the row provisional. Cropped, incomplete, or
occluded observations are partial. `evaluate_gap_predictions` reports precision and
recall only against accepted reviewed gap truth and explicitly reports when no such
truth exists.

`analyze_sku_availability` returns a SKU state with exact observation, expectation,
slot, facing, and gap evidence identifiers. A `shelf_out_of_stock` state requires an
authoritative expectation, complete reviewed observation, reviewed expected slot, and
zero observed facings in the fixture. A visual gap alone returns `not_observed` or
`possible_absence`. Confidence is categorical because no numeric probability has been
calibrated; the response retains `probability: null` and `calibrated: false`.

## FiftyOne projection

FiftyOne is an operator-only, read-only projection of an immutable dataset snapshot.
PostgreSQL remains authoritative, and the projection is not exposed to the browser.
Deleting a projection never deletes snapshot, annotation, catalog, or managed media
records.

FiftyOne 1.19.0 requires Python 3.10 through 3.12, so install it in a separate Python
3.12 environment when the main application uses a newer Python version:

```powershell
uv venv --python 3.12 .venv-fiftyone
uv pip install --python .venv-fiftyone\Scripts\python.exe -e . -r projection-requirements.txt
```

Set `FIFTYONE_DATABASE_DIR` to an application-local ignored directory, then rebuild a
named projection from an exact immutable snapshot:

```powershell
.venv-fiftyone\Scripts\python.exe -m shelfsight_api.fiftyone_projection rebuild <snapshot-uuid> --dataset-name cvsight-review
```

The command stages the complete replacement before deleting an existing projection of
the same name. A failed build removes only its staging dataset. Repeated rebuilds have
the same projected samples and lineage. Image and accepted product-crop similarity use
small deterministic local embeddings, so projection rebuilds do not download model
weights. Near-duplicate views are computed separately for every evaluation split.

Delete only the rebuildable projection with:

```powershell
.venv-fiftyone\Scripts\python.exe -m shelfsight_api.fiftyone_projection delete --dataset-name cvsight-review
```

## Quality review and snapshot sign-off

Open the risk-ranked review workspace for a project with:

```text
http://127.0.0.1:5173/projects/{project_id}/review
```

Select **Review QA**. Each queue item shows every blocking reason and its combined
risk score. The current signals are propagated origin, Unknown or unassigned product
identity, recorded hard-pair review, low model-to-human box agreement, a propagated
label that conflicts with its current seed, and an explicit reviewer flag.

The workspace embeds the same revision-safe annotation canvas used for box correction.
After saving a correction, refresh the queue before deciding. Approve and Flag append a
new annotation revision with reviewer provenance and an immutable review-decision row.
A stale item returns a conflict instead of overwriting the corrected revision.

Review routes:

- `GET /api/dataset-versions/{version_id}/review-queue`
- `POST /api/dataset-versions/{version_id}/review-items/{annotation_id}/decision`
- `POST /api/dataset-versions/{version_id}/review-signoff`

Sign-off is blocked while any current risk item lacks an approval or has an unresolved
flag. A successful sign-off snapshots the version and records the reviewer in one
database transaction. A version frozen through the general snapshot route before QA
cannot later be assigned a review sign-off.

## Live annotation workspace

Open an ingested image and its current database-backed annotations with:

```text
http://127.0.0.1:5173/projects/{project_id}/annotate/{image_id}
```

The workspace loads the canonical managed image and current annotation revisions.
Verification, geometry edits, and SKU assignment use the API autosave path. Reloading
the same URL restores server state, with an image-specific local draft used only for
interrupted saves.

Workspace routes are bookmarkable:

- Verify: `/projects/{project_id}/annotate/{image_id}`
- Assign: `/projects/{project_id}/annotate/{image_id}/assign`
- Propagate: `/projects/{project_id}/annotate/{image_id}/propagate`
- Review: `/projects/{project_id}/review`
- Catalog: `/projects/{project_id}/catalog`
- Analytics: `/projects/{project_id}/analytics`

Legacy `?image=`, `?version=`, `?fixture=`, and `?benchmark=` URLs remain supported.

## Annotation canvas

The production canvas uses native SVG overlays over an image layer inside one shared
pan and zoom transform. Every overlay has a constant-width dark halo and a workflow
cue that does not rely on color alone. The box list is virtualized while all overlays
remain present in the SVG for direct hit testing.

Canvas controls:

- `Left Arrow` or `K` selects the previous box in spatial reading order.
- `Right Arrow` or `J` selects the next box.
- `A` accepts, `R` rejects, `F` flags, and `D` duplicates the selected box.
- `Alt` plus an arrow key nudges the selected box by one pixel.
- `Alt` plus `Shift` plus an arrow key resizes the selected box by one pixel.
- `+`, `-`, or the mouse wheel changes zoom.
- Hold `Space` and drag, or middle-button drag, to pan.
- `Escape` clears selection and focus dimming.

Every edit is written to local draft storage before synchronization. Autosave can run
after 300 milliseconds, after one second, or manually. Network failures retain the
pending local draft and retry when the browser returns online. An HTTP 409 conflict
shows both the server revision and the local intent, then requires an explicit choice
between keeping the local edit and using the server version.

The Assign SKUs workspace shows only accepted product boxes. It combines the selected
crop with catalog search, recent SKUs, candidate reference images, and an explicit
Unknown / Other action. Number keys `1` through `9` assign visible candidates, `/`
focuses catalog search, `S` repeats the previous assignment, and `U` assigns Unknown /
Other. Assignment advances to the next verified product box and uses the same local
draft, retry, and conflict behavior as box verification.

To stage and open the retained local 354-box performance fixture:

```powershell
./scripts/stage-canvas-fixture.ps1
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/?fixture=local`, then use the Measure button to collect
p50, p95, and maximum frame, selection, and input timings. The staged real images and
fixture JSON remain local under the ignored `frontend/public/local-fixtures` directory.
