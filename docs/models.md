# Models

The model boundary, background jobs, pre-labeling, recognition embeddings, RF-DETR training, the model registry, and the FiftyOne projection. Back to the [README](../README.md).

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

A detector can also run outside CVSight. Set `SHELFSIGHT_DETECTOR_RUNTIME_URL` for the
worker instead of `SHELFSIGHT_RFDETR_CHECKPOINT`, and the worker sends each detect
request to `{url}/v1/execute` as multipart form data: a `request` part with the
contract request as JSON and an `image` part with the canonical image. The runtime
answers with JSON holding `model_provenance` and `predictions`, which pass the same
validation as an in-process adapter's output. An unreachable runtime, HTTP 5xx, or 429
is retried; HTTP 4xx and malformed or oversized answers fail the job. Third-party
runtimes and their licenses stay outside this repository.

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

## Pre-labeling

The Images page's **Pre-label unlabeled** action queues every unlabeled image in the
open version under one idempotency key per version, so repeating it never duplicates
proposals. The API queues detector work for up to 250 canonical images:

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
duplicate rate, per-image dense-scene failures, and overlapping-product recall for both
strategies. Overlapping-product recall covers only labeled products whose box overlaps
another labeled box at IoU 0.1 or more, so products that hide part of each other are
scored apart from neighbors that merely touch.

## Evaluated model registry

Register the trained checkpoint and its frozen evaluation report as immutable snapshot
artifacts before adding a model candidate. Model metadata must include the training
manifest checksum, code version, random seed, approved-license flag, training
configuration, and runtime compatibility. Evaluation metadata must bind the exact
model checksum and frozen evaluation snapshot checksum, use the same code version and
seed, and include mAP, product recall, duplicate rate, dense-scene recall, and
overlapping-product recall.

An evaluation set can lack the scenes a subset recall needs, for example with no photo
of 50 or more products. Dense-scene recall may then be `null`, but only when the metrics
also record `"dense_scene_images": 0`; overlapping-product recall likewise needs
`"overlapping_products": 0`. Any other missing or non-numeric score is refused, and the
Models page shows an accepted `null` as "Not measured".

A model trained outside CVSight registers with `"lineage": "external"` in its model
metadata, a `source` naming where it came from, the approved-license flag,
configuration, and runtime compatibility; it has no training manifest or seed. Its
artifact record sits on the frozen snapshot it was evaluated on, and its registry entry
leaves the training version empty rather than claiming one. The evaluation must still
bind its checksum and that snapshot and name the evaluator's code version. The Models
page labels such entries as trained outside CVSight.

Owners can browse candidates, the active deployment, and its rollback target for every
model role at `http://127.0.0.1:5173/projects/{project_id}/models`, and promote or roll
back from there. The registry is shared by every project.

A product detector served over HTTP, like any model trained outside CVSight, is
registered from the same page. **Register a model** takes the model service address,
a signed-off release to test on, and a confidence threshold, then queues a worker job
that sends every photo in the release to the model and scores its boxes against the
verified products with the same evaluator as RF-DETR training. The panel shows progress
and the scores, and the model's fingerprint as the service reports it. After the owner
describes where the model comes from and confirms its license, **Register model**
records the model and its evaluation on that release and adds it as a candidate.
Promotion stays a separate step. Each model fingerprint is registered once, so a second
evaluation of the same model is refused rather than replacing the first.

```text
GET  /api/model-registry/evaluations/defaults
POST /api/model-registry/evaluations
     {"dataset_version_id": "...", "runtime_url": "http://...", "confidence_threshold": 0.3}
GET  /api/jobs/{evaluation_id}
POST /api/model-registry/evaluations/{evaluation_id}/register
     {"source": "...", "licenses_approved": true}
```

Test a model on a release it was not trained on and, ideally, one labeled without its
pre-labels: boxes that started as that model's proposals agree with it by
construction and flatter its scores.

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

Promotion decides which detector may pre-label. A detect job stores proposals only
when the model that produced them reports the artifact checksum of the promoted
`known_sku_detector`; otherwise the job fails with `model_not_promoted` and writes
nothing. The check runs in the transaction that stores the proposals, so a promotion or
rollback governs every job that finishes after it. Workers still load the model they
are configured with, so the operator points each worker at the promoted artifact.

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
