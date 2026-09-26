# License and artifact policy

CVSight itself is released under the MIT License in `LICENSE`. This document covers the
third-party components, datasets, and checkpoints the release depends on.

This document records the CVSight 0.3.0 release posture. It is an engineering inventory,
not legal advice.

## CVSight source

CVSight source is released under the MIT License in `LICENSE`. Third-party packages
remain governed by their own licenses, and bundling them does not change those terms.

## Shipped repository contents

The public source release contains application code, migrations, tests, PowerShell and
POSIX shell operator scripts, container build files, documentation, the CVSight logo, and
a generated demonstration shelf image. It does not contain customer shelf imagery, benchmark datasets, annotations,
database dumps, model checkpoints, training exports, or local evaluation reports.

The release license gate rejects common model, dataset, and archive suffixes in tracked
or otherwise shippable files. Local data, model evidence, planning documents, backups,
and runtime state remain ignored by Git.

## Dependency posture

The locked main Python graph uses permissive OSI licenses plus unmodified dynamically
loaded LGPL psycopg packages. The npm graph uses the approved set recorded by
`benchmark_tool.license_audit`, including MIT, BSD, Apache-2.0, ISC, MPL-2.0,
BlueOak-1.0.0, CC0-1.0, CC-BY-4.0, and OFL-1.1. MPL packages are build dependencies and
are not modified in this repository. OFL-1.1 covers the bundled Bricolage Grotesque and
IBM Plex typefaces, which ship unmodified inside the frontend build so the interface
makes no network font requests.

FiftyOne 1.19.0 is an optional Apache-licensed operator projection. Its isolated Python
environment includes permissive and unmodified LGPL dependencies. It is not bundled in
the frontend or core API distribution. The PostgreSQL image uses PostgreSQL and pgvector
components under their upstream PostgreSQL licenses. The web image bundles Caddy and its
caddy-ratelimit module, both Apache-2.0, compiled unmodified from their published Go
modules.

Run the repeatable metadata audit from the repository root:

```powershell
./scripts/license-check.ps1
```

The command checks every locked main Python package, every installed optional FiftyOne
package, every npm lock entry, the exact FiftyOne pin, and the source artifact boundary.
It retains ignored local evidence under `benchmark-local`.

## Models and datasets

No model weight or dataset is shipped in this repository.

| Component | Recorded license | Release posture |
| --- | --- | --- |
| RF-DETR Nano code and official weights | Apache-2.0 | Permitted as an operator-supplied detector. Checkpoint bytes and version must be configured outside Git. |
| OpenAI CLIP ViT-B/32 code | MIT | Permitted only as an exploratory, operator-supplied suggestion model. Current evidence does not permit automatic SKU assignment. |
| SAM 3 source and checkpoint | Custom SAM license | Optional local refinement only. Redistribution is not approved by this release. Operators must review the upstream terms. |
| QPDS-Seg benchmark data | CC-BY-4.0 | Local benchmark input only. It is not shipped. Redistribution or derivatives require attribution. |
| Externally trained detectors | Operator declared | Registered with external lineage, a named source, and the approved-license flag. They run as separate services behind the HTTP runtime contract, so their code and licenses, including AGPL runtimes such as Ultralytics, stay outside CVSight. |
| Operator datasets and checkpoints | Operator declared | Training preparation refuses inputs without an approved name, license, source, and checkpoint checksum. |

Model registry rows record dataset snapshot identity, artifact checksums, evaluation
lineage, and approved-license metadata. A registry entry does not itself redistribute a
checkpoint or replace the operator's license review.
