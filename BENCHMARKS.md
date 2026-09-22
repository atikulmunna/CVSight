# CVSight benchmark evidence

This document publishes a curated, reproducible summary of the evidence used for the
CVSight 0.2.0 release. The machine-readable snapshot is
[`docs/benchmarks/release-0.2.0.json`](docs/benchmarks/release-0.2.0.json). Raw reports,
datasets, images, database dumps, credentials, local paths, and private identifiers are
not committed.

## Evidence boundary

Release gates test installation, correctness, scale, recovery, security, and licensing.
They do not establish production model accuracy. RF-DETR, SAM, and CLIP results below
are exploratory measurements on an incompletely labeled dataset with related scenes
across the original splits. CVSight therefore keeps model output suggestion-only and
requires a human decision.

This snapshot summarizes evidence collected from source commit `abc540eace7c770bcbca15a842f984d689a87738` on
September 22, 2026. Every figure below was measured on that tree.

## How these numbers should be read

Each measurement is one sample from one machine, and several of them move more than the
code does. Where a figure is known to vary between runs, the variation is published
beside it rather than hidden behind the best result. Two measurements were corrected
during this release after they were found to describe the host rather than the
application:

- Canvas timings now come from the production build and each run reports whether the
  host held a steady frame cadence. A run that did not is marked invalid, and its
  numbers are not evidence.
- The full catalog latency is sampled over 100 requests rather than 20. At the smaller
  size the p95 estimator resolved to the second slowest request, so the same unchanged
  build measured between 410 ms and 777 ms across five runs against a 750 ms gate.

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

The isolated installation rehearsal completed in 87.520 seconds. It built a clean
source copy, started the database, API, worker, and frontend, then ran the following
checks:

| Check | Result |
| --- | ---: |
| Python tests | 307 passed, 0 skipped |
| Frontend tests | 158 passed |
| Ruff | Passed |
| mypy | Passed |
| ESLint | Passed |
| TypeScript | Passed |
| Production frontend build | Passed |
| API, database, worker, and frontend smoke checks | Passed |

The 0.1.0 evidence reported 204 passing Python tests with 95 skipped. Those skips were
the PostgreSQL-backed integration tests, which the rehearsal never configured a test
database for. `scripts/check.ps1` now refuses to run without one, so the complete suite
executes.

The source file count was 243 when this evidence was captured. Later documentation
and test additions can change that count without invalidating the measured release tree.

## Scale results

The deterministic fixture contained 100,000 image metadata rows, 2,000 catalog SKUs,
300 annotations on one dense image, 1,000 queued jobs, 2,000 gallery embeddings, and an
export snapshot with 250 images and 3,000 annotations. All 18 configured scale gates
passed with zero API, job, similarity, or export errors.

| Operation | Result | Gate |
| --- | ---: | ---: |
| Image metadata API p95 | 47.05 ms | at most 100 ms |
| Dense annotation API p95 | 65.36 ms | at most 500 ms |
| Full catalog API p95 | 550.39 ms | at most 750 ms |
| Catalog search API p95 | 151.93 ms | at most 250 ms |
| Expected failure paths p95 | 76.85 ms | at most 250 ms |
| Four-worker job throughput | 110.24 jobs/s | at least 50 jobs/s |
| Job duration p95 | 50.12 ms | at most 150 ms |
| Similarity search p95 | 105.37 ms | at most 250 ms |
| Detection export p95 | 1,050.82 ms | at most 3,000 ms |
| Recognition export p95 | 3,578.66 ms | at most 12,000 ms |

The full catalog figure is the least stable measurement in this table. Across four runs
of this tree it ranged from 468 ms to 709 ms, so it carries roughly 13 percent headroom
against its gate at the median and less on a loaded machine. The threshold was left
unchanged when the sampling was corrected, because moving a gate to fit a measurement
would make the result easier to pass rather than easier to trust.

The seed added 112,053,084 bytes to PostgreSQL, or 1,120.53 bytes per image metadata
row. The measured workload added 603,300 bytes. Python traced peak memory was
22,695,440 bytes, with 804,830 bytes retained after the workload.

## Canvas responsiveness

Canvas timings are measured against the production build, with 354 boxes loaded. The
development server was used for the 0.1.0 evidence, where React double-renders every
component under `StrictMode`, so those figures described a build no annotator runs.

Every run first measures an idle animation loop and reports it as the browser frame
baseline. That baseline is the control: a host that cannot hold a steady cadence with no
canvas work, or a canvas measurement faster than the idle loop, marks the run invalid.
This run reported the host as valid.

| Measurement | p95 | Gate | Maximum |
| --- | ---: | ---: | ---: |
| Browser frame baseline | 16.8 ms | at most 20 ms | 17.0 ms |
| Canvas frame interval | 16.9 ms | at most 25 ms | 17.7 ms |
| Selection update | 3.5 ms | at most 8 ms | 10.7 ms |
| Input latency | 16.8 ms | at most 25 ms | 17.1 ms |

The 0.1.0 evidence recorded isolated long frames of 66.9 ms and 46.2 ms. Those maxima do
not appear here: the worst frame in this run was 17.7 ms against a 16.7 ms display
floor.

The interface overhaul in this release was checked for regression directly. The
pre-overhaul build and the current build were measured back to back on the same busy
host, giving a selection update p50 of 9.8 ms and 9.5 ms respectively, so the redesign
did not change canvas cost. Both of those runs would now be rejected as invalid.

## Recovery evidence

The recovery drill completed in 33.073 seconds. It verified a database backup and clean
restore by comparing fixture state and checksums, preserved state through a database
container restart, and confirmed that a restored deployment serves the same annotation
and media content.

## Security and licensing

The dependency and container scan audits the frozen Python graph with pip-audit, the npm
graph with `npm audit`, and the rebuilt database image with pinned Trivy 0.74.0. This
release reports 0 fixable high or critical container findings.

Three high severity findings were resolved during this release. `libpcre2-8-0` carried
CVE-2026-86145, CVE-2026-89157, and CVE-2026-89161 at version 10.42-1 and now ships
10.42-1+deb12u1. They were present because the image build reused a cached `apt-get
upgrade` layer: the base image is pinned by digest, so nothing invalidated that layer and
it had stopped receiving Debian security updates. The scan build now passes `--no-cache`.
Operators running the image outside the gate must rebuild it the same way.

See [LICENSE_POLICY.md](LICENSE_POLICY.md) for component terms. The CVSight source is
released under the MIT License.

## Exploratory model measurements

These numbers are useful engineering baselines only. The selected QPDS-Seg annotations
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
