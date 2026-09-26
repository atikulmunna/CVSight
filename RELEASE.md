# CVSight 0.3.0 release guide

CVSight 0.3.0 is a self-hosted, single-project image annotation platform for dense retail
shelves. PostgreSQL and local managed media are authoritative. The tested development
setup is Windows 11 with PowerShell 7, Docker Desktop, Python 3.13, uv, and Node.js 24.
The Docker Compose deployment runs the whole platform on one Linux Docker host.

## Release scope

The release supports image upload and bounded local import, axis-aligned product boxes,
explicit verification, SKU assignment, optional suggestion-only model jobs, reviewed
similarity propagation, QA sign-off, immutable exports, retail analytics, and rebuildable
FiftyOne projections.

New in 0.3.0:

- Drawing, resizing, and deleting boxes in the workspace, with select and pan tools, a
  repeated last SKU, and moving between photos without leaving the canvas.
- Queueing pre-labels for every unlabeled photo from the Images page.
- Importing already labeled YOLO and COCO datasets, and exporting YOLO archives.
- Registering models trained outside CVSight with recorded lineage, and reaching a
  detector runtime over HTTP so third-party model code and licenses stay out of process.
- Pre-labels only from the promoted detector: a worker stores proposals only when its
  model's artifact checksum matches the active registry entry.
- Overlapping-product recall in the detector evaluation report.
- A Docker Compose deployment with HTTPS, sign-in rate limiting, restart policies, and
  backup and restore scripts.

It is not a multi-tenant SaaS, public internet edge, automatic SKU decision system,
planogram authority, shopper analytics product, video tool, mask editor, or mobile
inference runtime.

## Installation and configuration

Follow the numbered setup in `README.md`. A usable base installation needs no GPU. Its
required services are the React frontend, FastAPI API, one durable-job worker, PostgreSQL
17 with pgvector, and an application-owned media directory.

Required configuration:

| Variable | Purpose |
| --- | --- |
| `SHELFSIGHT_DB_PASSWORD` | Local PostgreSQL container password used by Compose. |
| `SHELFSIGHT_DATABASE_URL` | PostgreSQL SQLAlchemy URL used by the API, workers, migrations, and tools. |
| `SHELFSIGHT_MEDIA_ROOT` | Private application-owned original and derived media directory. |
| `SHELFSIGHT_IMPORT_ROOT` | Read-only operator source for bounded manifest imports. |
| `SHELFSIGHT_AUTH_USERS` | JSON array of one to twenty named users with scrypt hashes and fixed roles. |
| `SHELFSIGHT_SESSION_COOKIE_SECURE` | `true` behind HTTPS; `false` only for local HTTP. |

Optional configuration:

| Variable | Purpose |
| --- | --- |
| `SHELFSIGHT_DB_PORT` | Loopback PostgreSQL port used by Compose. Default is 5432. |
| `SHELFSIGHT_TEST_DATABASE_URL` | Separate PostgreSQL database for the test suite. `scripts/check.ps1` requires it and migrates it before the Python tests. |
| `FIFTYONE_DATABASE_DIR` | Local rebuildable FiftyOne database directory. |
| `SHELFSIGHT_RFDETR_CHECKPOINT` and `SHELFSIGHT_RFDETR_VERSION` | Operator-supplied RF-DETR detector identity. |
| `SHELFSIGHT_SAM3_CHECKPOINT` and `SHELFSIGHT_SAM3_VERSION` | Operator-supplied optional SAM refiner identity. |
| `SHELFSIGHT_CLIP_MODEL_ROOT` | Operator-supplied pinned CLIP snapshot for suggestion embeddings. |
| `SHELFSIGHT_DETECTOR_RUNTIME_URL` | HTTP detector runtime for the worker, instead of an in-process RF-DETR checkpoint. |
| `CVSIGHT_SITE_ADDRESS` | Compose deployment only: the host name Caddy serves and certifies. |

Keep `.env`, media, imports, checkpoints, database data, exports, and backups outside
source control. Bind local services to loopback unless an HTTPS reverse proxy supplies
access controls and login rate limiting. The Caddy service in `compose.yaml` provides
both.

## GPU requirements

The web application, API, database, exports, analytics, and deterministic FiftyOne
projection work without a GPU. GPU requirements apply only to optional model workers.

The measured development machine used an NVIDIA RTX 5060 Laptop GPU with 8,151 MiB VRAM.
RF-DETR Nano inference reserved about 308 MiB in the fixed exploratory run, while its
one-epoch training probe stayed below 6 GiB. CLIP crop embedding reserved about 754 MiB.
SAM reserved 7,910 MiB and therefore requires a dedicated one-job worker on the tested
8 GiB device. These are measured reference points, not minimum hardware guarantees.

Run RF-DETR, SAM, and CLIP in separate processes. CVSight does not install PyTorch, CUDA,
RF-DETR, SAM, or CLIP into the core environment. Operators must build compatible model
environments, mount the approved artifact, set its version, and validate it against the
model contract before enabling work.

## Annotation workflow

1. An owner creates a dataset and imports authorized JPEG or PNG shelf images, or a
   dataset already labeled in YOLO or COCO format.
2. An annotator optionally requests pre-label jobs from the promoted detector, then
   verifies every proposed box. A human can also draw, resize, reject, flag, or
   duplicate boxes.
3. In Assign SKUs, the annotator selects an accepted product box and chooses a catalog
   identity or the explicit Unknown / Other value. Model candidates are suggestions only.
4. Propagation starts from a human-assigned seed. Every candidate starts skipped and
   requires explicit confirmation, with individual review for hard pairs.
5. A reviewer resolves the risk-ranked QA queue, corrects current revisions, and signs
   off the dataset version. Sign-off freezes an immutable snapshot.
6. An owner exports deterministic detection, recognition, or YOLO archives and reads
   analytics tied to the immutable snapshot and formula version.

The complete API routes, keyboard controls, autosave behavior, conflict handling,
catalog workflow, propagation contract, and review process are documented in
`README.md`.

## Backup, recovery, and security

Use `RECOVERY.md` for quiescent backup, clean restore, worker recovery, and projection
rebuild procedures. Use `SECURITY.md` for the deployment threat model, privacy behavior,
retention, complete-deployment deletion, access logging, and accepted container risk.

## Licenses

CVSight is released under the MIT License in `LICENSE`. `LICENSE_POLICY.md` records the
dependency, model, dataset, and shipped-artifact posture.

## Known limitations

- Development and the release gates run on Windows 11 with PowerShell 7. The Compose
  deployment and its POSIX backup and restore scripts were exercised against Docker
  Desktop's Linux engine, not on a native Linux server.
- The built-in server is local infrastructure, not an internet-facing authentication
  edge. HTTPS termination and login rate limiting belong to the reverse proxy, which the
  Compose deployment provides.
- Dataset-level selective deletion is unavailable. Retention requests require complete
  isolated-deployment retirement, including backups.
- GPU checkpoints are mounted local files, and external detectors run as separate
  services. Registry promotion does not distribute a model artifact or restart a worker;
  it decides whose proposals a worker may store.
- Model results remain proposals or suggestions. No model may assign a SKU
  automatically.
- The retained real dataset has no release-quality detector or recognition metric claim.
  Exploratory benchmark numbers must not be presented as production accuracy.
- Planogram compliance requires an authoritative fixture-matched planogram and remains
  unavailable without one. Gap geometry alone does not prove out-of-stock state.
- Video, tracking, rotated boxes, persistent masks, multi-project workspaces, billing,
  S3 or MinIO, Qdrant, Redis, edge inference, and demographic or biometric analytics are
  outside the fixed MVP.
- No labeling-effort or active-learning result is published with this release.

## Release validation

Run all release gates from the repository root:

```powershell
./scripts/release-check.ps1
```

The command rehearses installation and startup from a clean source copy, runs functional
and analytics checks, audits dependency licenses and shipped artifacts, runs dependency
and container security scans, repeats scale and recovery drills, and consolidates ignored
local evidence at `benchmark-local/t039-release-report.json`. The curated public summary
is in [BENCHMARKS.md](BENCHMARKS.md), with sanitized machine-readable evidence at
[`docs/benchmarks/release-0.3.0.json`](docs/benchmarks/release-0.3.0.json). Raw local
reports remain ignored because they can contain paths, infrastructure fingerprints, and
private identifiers.
