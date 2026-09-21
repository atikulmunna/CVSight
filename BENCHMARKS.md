# CVSight benchmark evidence

This document publishes a curated, reproducible summary of the evidence used for the
CVSight 0.1.0 release. The machine-readable snapshot is
[`docs/benchmarks/release-0.1.0.json`](docs/benchmarks/release-0.1.0.json). Raw reports,
datasets, images, database dumps, credentials, local paths, and private identifiers are
not committed.

## Evidence boundary

Release gates test installation, correctness, scale, recovery, security, and licensing.
They do not establish production model accuracy. RF-DETR, SAM, and CLIP results below
are exploratory measurements on an incompletely labeled dataset with related scenes
across the original splits. CVSight therefore keeps model output suggestion-only and
requires a human decision.

The published snapshot summarizes evidence collected from source commit
`d86e02b84f6775b4ed561a3767f1fe78053ffb1a` on August 24, 2026.

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

The isolated installation rehearsal completed in 48.214 seconds. It built a clean
source copy, started the database, API, worker, and frontend, then ran the following
checks:

| Check | Result |
| --- | ---: |
| Python tests | 204 passed, 95 database integration tests skipped |
| Frontend tests | 100 passed |
| Ruff | Passed |
| mypy | Passed |
| ESLint | Passed |
| TypeScript | Passed |
| Production frontend build | Passed |
| API, database, worker, and frontend smoke checks | Passed |

The 95 skipped tests were the PostgreSQL-backed integration tests, not model tests. The
rehearsal configured only the application database, so `SHELFSIGHT_TEST_DATABASE_URL`
was unset and those tests skipped. `scripts/install-check.ps1` now creates a test
database and `scripts/check.ps1` fails when the variable is missing, so the next
rehearsal runs the complete Python suite.

The source file count was 205 when this evidence was captured. Later documentation and
test additions can change that count without invalidating the measured release tree.

## Scale results

The deterministic fixture contained 100,000 image metadata rows, 2,000 catalog SKUs,
300 annotations on one dense image, 1,000 queued jobs, 2,000 gallery embeddings, and an
export snapshot with 250 images and 3,000 annotations. All 18 configured scale gates
passed with zero API, job, similarity, or export errors.

| Operation | Result | Gate |
| --- | ---: | ---: |
| Image metadata API p95 | 44.05 ms | at most 100 ms |
| Dense annotation API p95 | 57.24 ms | at most 500 ms |
| Full catalog API p95 | 431.89 ms | at most 750 ms |
| Catalog search API p95 | 64.96 ms | at most 250 ms |
| Expected failure paths p95 | 76.57 ms | at most 250 ms |
| Four-worker job throughput | 124.73 jobs/s | at least 50 jobs/s |
| Job duration p95 | 42.03 ms | at most 150 ms |
| Similarity search p95 | 114.84 ms | at most 250 ms |
| Detection export p95 | 834.47 ms | at most 3,000 ms |
| Recognition export p95 | 2,798.19 ms | at most 12,000 ms |

The seed added 112,053,084 bytes to PostgreSQL, or 1,120.53 bytes per image metadata
row. The measured workload added 603,300 bytes. Python traced peak memory was
22,702,279 bytes, with 814,530 bytes retained after the workload.

## Canvas responsiveness

The production canvas evidence used 354 boxes and was reported by a human operator.
Its calendar capture date was not recorded, and a fresh automated browser rerun was not
available in that environment. The p95 gates passed, while isolated maximum frame and
input latency values show that occasional long frames still occurred.

| Measurement | p95 | Gate | Maximum |
| --- | ---: | ---: | ---: |
| Browser frame baseline | 16.8 ms | at most 20 ms | 16.8 ms |
| Canvas frame interval | 16.8 ms | at most 25 ms | 66.9 ms |
| Selection update | 1.8 ms | at most 8 ms | 2.7 ms |
| Input latency | 16.9 ms | at most 25 ms | 46.2 ms |

## Recovery evidence

The recovery drill completed in 30.497 seconds. It verified a database backup and clean
restore by comparing fixture state and checksums, preserved state through a database
restart, recovered one expired worker claim, rebuilt an equivalent FiftyOne projection
twice, and rebuilt three stale similarity targets. The retained backup contained six
managed media files. No backup archive or application data is published here.

## Security and licensing

The dependency and container scan took 21.811 seconds. `pip-audit` found no known
vulnerabilities in the locked Python environment, `npm audit` found zero frontend
vulnerabilities, and Trivy found zero fixable high or critical findings in the rebuilt
database image. The underlying Debian image still reported 60 high or critical findings
without a vendor fix. That narrow acceptance and the deployment controls are documented
in [SECURITY.md](SECURITY.md).

The license gate covered 62 locked core Python packages, 128 installed optional
FiftyOne packages, and 266 npm packages. It also verified that datasets, checkpoints,
database dumps, archives, and local benchmark evidence were absent from the shipped
source. See [LICENSE_POLICY.md](LICENSE_POLICY.md) for component terms. The CVSight
source currently has no source license grant and must not be described as open source.

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

Run these commands from the repository root. Security and release checks require
network access. The scripts create raw reports under ignored local directories.

```powershell
./scripts/install-check.ps1
./scripts/license-check.ps1
./scripts/security-check.ps1
./scripts/performance-check.ps1 -Output benchmark-local/t039-scale-report.json
./scripts/recovery-check.ps1
./scripts/release-check.ps1
```

Review the generated reports before publishing any derived summary. Do not commit raw
reports without first removing paths, private identifiers, infrastructure fingerprints,
credentials, and dataset content.
