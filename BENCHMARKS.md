# CVSight benchmark evidence

This document publishes a curated, reproducible summary of the evidence used for the
CVSight 0.3.0 release. The machine-readable snapshot is
[`docs/benchmarks/release-0.3.0.json`](docs/benchmarks/release-0.3.0.json). Raw reports,
datasets, images, database dumps, credentials, local paths, and private identifiers are
not committed. The 0.2.0 snapshot remains at
[`docs/benchmarks/release-0.2.0.json`](docs/benchmarks/release-0.2.0.json).

## Evidence boundary

Release gates test installation, correctness, scale, recovery, security, and licensing.
They do not establish production model accuracy. RF-DETR, SAM, and CLIP results below
are exploratory measurements on an incompletely labeled dataset with related scenes
across the original splits. CVSight therefore keeps model output suggestion-only and
requires a human decision.

This snapshot summarizes evidence collected from source commit `5163e9f81777b8c800288c853f2d4123a18cb180` on
September 27, 2026. Every figure below was measured on that tree.

## How these numbers should be read

Each measurement is one sample from one machine, and several of them move more than the
code does. Where a figure is known to vary between runs, the variation is published
beside it rather than hidden behind the best result.

- The full catalog latency failed its gate twice on this tree, at 792.6 ms and 860.4 ms against
  750 ms, both on a loaded host: the first run started as the security gate's image
  builds finished, the second overlapped an unrelated archive extraction. The run after
  both had ended measured 399.8 ms. The gate was not changed.
- Canvas timings come from the production build, and each run reports whether the host
  held a steady frame cadence. A run that did not is marked invalid, and its numbers are
  not evidence.

## Test environment

| Component | Measured environment |
| --- | --- |
| Operating system | Windows 11 |
| Python | 3.13.5 |
| Node.js | 24.15.0 |
| npm | 11.12.1 |
| Database | PostgreSQL 17 with pgvector 0.8.6 |
| CPU | 32 logical processors |
| Optional model GPU | NVIDIA RTX 5060 Laptop GPU, 8,151 MiB VRAM |

These measurements describe one development machine. They are reference results, not
minimum hardware guarantees.

## Clean installation and correctness

The isolated installation rehearsal completed in 157.324 seconds. It built a clean
source copy, started the database, API, worker, and frontend, then ran the following
checks:

| Check | Result |
| --- | ---: |
| Python tests | 340 passed, 0 skipped |
| Frontend tests | 200 passed |
| Ruff | Passed |
| mypy | Passed |
| ESLint | Passed |
| TypeScript | Passed |
| Production frontend build | Passed |
| API, database, worker, and frontend smoke checks | Passed |

The source file count was 269 when this evidence was captured. Later documentation
and test additions can change that count without invalidating the measured release tree.

## Scale results

The deterministic fixture contained 100,000 image metadata rows, 2,000 catalog SKUs,
300 annotations on one dense image, 1,000 queued jobs, 2,000 gallery embeddings, and an
export snapshot with 250 images and 3,000 annotations. All 18 configured scale gates
passed on the quieter run with zero API, job, similarity, or export errors.

| Operation | Result | Gate |
| --- | ---: | ---: |
| Image metadata API p95 | 41.71 ms | at most 100 ms |
| Dense annotation API p95 | 56.20 ms | at most 500 ms |
| Full catalog API p95 | 399.84 ms | at most 750 ms |
| Catalog search API p95 | 102.08 ms | at most 250 ms |
| Expected failure paths p95 | 36.77 ms | at most 250 ms |
| Four-worker job throughput | 131.25 jobs/s | at least 50 jobs/s |
| Job duration p95 | 37.96 ms | at most 150 ms |
| Similarity search p95 | 99.06 ms | at most 250 ms |
| Detection export p95 | 881.46 ms | at most 3,000 ms |
| Recognition export p95 | 3,350.50 ms | at most 12,000 ms |

The full catalog figure remains the least stable measurement in this table. Across the
0.2.0 runs it ranged from 468 ms to 709 ms, and on this tree it measured 792.6 ms and 860.4 ms on a
busy host and 399.8 ms on a quieter one. It has little or no headroom against the
750 ms gate on a loaded machine.

The seed added 112,192,348 bytes to PostgreSQL, or 1,121.92 bytes per image metadata
row. The measured workload added 504,996 bytes. Python traced peak memory was
23,012,168 bytes, with 808,790 bytes retained after the workload.

## Canvas responsiveness

**Not re-measured for 0.3.0.** This release added the tool rail, box drawing, and eight
resize handles on the selected box, so the canvas was due a fresh measurement. Four
attempts on the 0.3.0 production build, with 354 boxes loaded, were all rejected by the host
check and none of their timings is published.

Every run first measures an idle animation loop and reports it as the browser frame
baseline. That baseline is the control: a host that cannot hold a steady cadence with no
canvas work, or a canvas measurement faster than the idle loop, marks the run invalid.
On the measurement host the idle loop dropped frames in every run, with intervals up to
183.6 ms, and in one run it fell to 15 frames per second while the canvas drew at 60.
Display or browser power saving on idle frames is the likely cause. It was not isolated, so the
runs are simply excluded.

The last valid measurement is the 0.2.0 build's, recorded on September 22, 2026. It is
shown for reference and does not describe the 0.3.0 canvas:

| 0.2.0 measurement | p95 | Gate | Maximum |
| --- | ---: | ---: | ---: |
| Browser frame baseline | 16.8 ms | at most 20 ms | 17.0 ms |
| Canvas frame interval | 16.9 ms | at most 25 ms | 17.7 ms |
| Selection update | 3.5 ms | at most 8 ms | 10.7 ms |
| Input latency | 16.8 ms | at most 25 ms | 17.1 ms |

## Recovery evidence

The recovery drill completed in 72.123 seconds. It verified a database backup and clean
restore by comparing fixture state and checksums, preserved state through a database
container restart, and confirmed that a restored deployment serves the same annotation
and media content.

## Deployment rehearsal

The Docker Compose deployment is new in this release. It was rehearsed by hand on Docker
Desktop's Linux engine rather than by a scripted gate, and not on a native Linux server.
A fresh stack applied all 12 migrations; HTTPS sign-in set a Secure, HttpOnly session
cookie; an upload was stored in the media volume and served back; Docker restarted the
API and worker after their processes stopped; ten failed sign-ins were answered 401 and
the next with 429 and `Retry-After`; and a backup, removal of every volume, and restore
brought back a deployment that served the uploaded image again. The rehearsal found one
defect before release: the restore could not read owner-only media files in a bundle.

## Security and licensing

The dependency and container scan audits the frozen Python graph with pip-audit, the npm
graph with `npm audit`, and every image the deployment runs (database, API and worker,
and web proxy) with pinned Trivy 0.74.0. This release reports 0 fixable high or
critical container findings.

The API and web images are scanned for the first time in this release. The API runtime
stage no longer ships pip, whose vendored msgpack and setuptools code carried
GHSA-6v7p-g79w-8964 and CVE-2025-47273, or the uv binary, which only the build stage
needs. Stock Caddy images failed the gate: 2.10 carried 83 fixable high or critical
findings, and 2.11.4, the latest release, carried 17 in its Go toolchain and modules. The
web image therefore builds Caddy 2.11.4 with Go 1.26.6 and fixed `x/crypto`, `x/net`,
`x/text`, and gRPC versions. Every image is built with `--no-cache`, so package upgrade
layers are current when scanned.

See [LICENSE_POLICY.md](LICENSE_POLICY.md) for component terms. The CVSight source is
released under the MIT License.

## Exploratory model measurements

These numbers are carried over unchanged from 0.2.0; this release did not re-measure
them. They are useful engineering baselines only. The selected QPDS-Seg annotations
cover 48 SKUs rather than every visible product, and related scenes crossed the original
splits. Generic precision is therefore withheld.

| Model and strategy | Selected measurements | Decision |
| --- | --- | --- |
| RF-DETR Nano, full image | 90.41% target-label recall, 39.0 ms warm p50, 308 MiB peak reserved VRAM, 11.86% duplicate rate | Suggestion-only detector |
| RF-DETR Nano, 2 by 2 sliced | 92.62% target-label recall, 82.05% overlap-stratum recall, 93.2 ms warm p50 | Dense-scene retry only |
| SAM box refinement | 98.89% selected-label recall, 704.3 ms warm p95, 1.80 images/s, 7,910 MiB peak reserved VRAM | Optional dedicated worker only |
| CLIP ViT-B/32 recognition | 47.00% top-one, 76.50% top-five, 50.00% unknown false acceptance, 754 MiB peak reserved VRAM | Rejected for automatic assignment; top-five review suggestions only |
| CLIP ViT-B/32 propagation | 50.00% top-one same-SKU retrieval, 76.50% top-five, 13.33% hard-pair confusion | Rejected for automatic propagation |

## Reproduce the release gates

```powershell
./scripts/release-check.ps1
```

Canvas timings are collected separately, because they need a human at a machine that is
otherwise idle:

```powershell
./scripts/canvas-check.ps1
```
