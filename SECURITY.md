# Security and privacy

CVSight is a self-hosted, single-project annotation system. It is not an internet-edge
service or a multi-tenant security boundary. The operator controls the host, database,
media directory, model files, user configuration, reverse proxy, and backups.

## Release boundary

- Bind PostgreSQL, the API, and the development frontend to loopback. Remote access
  requires an operator-managed HTTPS reverse proxy.
- Set `SHELFSIGHT_SESSION_COOKIE_SECURE=true` behind HTTPS. The reverse proxy must rate
  limit login attempts and reject malformed Host headers.
- Give each person a named account with the least privileged role. Do not share the
  owner account for annotation work.
- Keep `.env`, database dumps, media, imports, model checkpoints, and exports outside
  source control. Limit their filesystem access to the service account and operators.
- Treat exported datasets and backups with the same sensitivity as original images.

## Threat model

| Boundary | Realistic threat | Current control | Residual decision |
| --- | --- | --- | --- |
| Uploads | Oversized, malformed, deceptive, or decompression-bomb images | JPEG and PNG decoding only, 50 MiB input limit, 120 million pixel limit, bounded metadata, filename sanitization, and decode before persistence | EXIF fields other than orientation are retained only in exact original bytes. Upload only authorized imagery. |
| Manifest imports | Path traversal, URL fetch, symlink escape, or unbounded batch | At most 250 items, relative POSIX paths only, resolved path confinement to a configured import root, no network fetch, and the same image validation as uploads | The import root is an operator trust boundary and should be mounted read-only. |
| Managed media | Guessing an object identifier, path traversal, browser or proxy cache disclosure | Authenticated routes, database-selected managed keys, resolved path confinement, private no-store responses, and MIME sniffing disabled | This release has one project. Any authenticated role can view its annotation media. |
| Exports | Archive path injection, export of mutable work, or unintended data release | Owner-only access, immutable snapshots, fixed generated entry names, bounded canonical media, deterministic checksums, and no user-selected output paths | An owner can export all approved snapshot content. Store and transmit the ZIP as sensitive data. |
| Model inputs | Arbitrary file reads, checkpoint selection, command injection, or oversized output | API requests use database identifiers, bounded geometry and payloads, server-resolved canonical media, operator-only jobs, and environment-selected local checkpoints | Model code and checkpoint files are operator-supplied trusted components and run in separate workers. |
| Administration | Credential theft, role spoofing, CSRF, or audit tampering | Scrypt password hashes, opaque server sessions stored as hashes, HttpOnly SameSite Strict cookies, server-derived actors, least-privilege roles, and immutable auth audit rows | The built-in server is not a public login edge. Internet exposure requires HTTPS and proxy rate limiting. |
| Database container | Vulnerable base packages or privilege escalation | Pinned pgvector base, current security package upgrades, removal of the root helper, non-root runtime, loopback port binding, and repeatable Trivy checks | See the explicit upstream vulnerability acceptance below. |

## Media privacy

CVSight preserves the uploaded bytes exactly as the original asset. It creates a
separate orientation-normalized canonical image for annotation and a separate JPEG
thumbnail for the browser. Ingest metadata records `original_preserved=true` and
`face_blurring=not_applied` so a derived transform cannot be mistaken for a privacy
operation.

Face blurring is not enabled because no confirmed product requirement defines which
faces, false-negative tolerance, or training policy to use. Operators must crop or
redact incidental people before ingestion when their collection policy requires it.
If face blurring is added later, it must create a named derived asset, preserve the
original bytes, identify which derived asset is used for annotation and export, and
record the detector version and settings. It must never silently replace a training
original.

## Retention, deletion, and backup policy

- CVSight performs no automatic expiry. Original, canonical, thumbnail, annotation,
  audit, model-lineage, and exportable snapshot records remain until the isolated
  deployment is deliberately retired.
- The operator must record a purpose and retention deadline before importing a dataset
  and review active datasets at least every 90 days.
- Dataset-level deletion is not supported in this release. Do not delete individual
  database rows or media files manually because shared content-addressed files may
  still be referenced. When deletion is required, retire the complete isolated
  deployment, including PostgreSQL data, managed media, exports, projections, and all
  backup copies.
- Backups must contain PostgreSQL and managed media from the same recovery point, be
  encrypted at rest, be limited to operators, and be retained for no more than 30 days
  after replacement. A source-retirement request also applies to every backup copy.
- T038 must validate the backup and complete-deployment deletion procedures before the
  first release. Until that gate passes, this repository does not claim verified
  recovery or selective deletion.

## Access logging

Uvicorn operational access logs are host-managed. Retain them for at most 30 days and
restrict them to operators. Do not enable request-body, cookie, authorization-header,
or multipart-content logging. Authentication successes, failures, logouts, and
authorization denials are stored as immutable database audit events without passwords
or session tokens and remain for the lifetime of the isolated deployment.

## Vulnerability gate

Run the release security gate from the repository root:

```powershell
./scripts/security-check.ps1
```

It audits the frozen Python runtime graph with pip-audit, audits the full npm graph,
rebuilds the rootless database image from its pinned base with current Debian security
updates, and scans the resulting local image with pinned Trivy 0.74.0. Any fixable high
or critical finding fails the command. The script requires network access and Docker.

On 2026-08-24 the gate fixed five Starlette advisories by upgrading FastAPI and
Starlette, fixed two high npm transitive advisories, and removed 22 fixable high or
critical findings from the upstream pgvector Bookworm image. The hardened image
retained 60 high or critical Debian findings with no available vendor fix: 45 high and
15 critical.
Most duplicate package findings belong to Perl runtime packages; the others are in
base tools and libraries including libxml2 and OpenSSL.

Those 60 findings are accepted for this release only because no fixed package version
exists, the database process runs as `postgres`, the root helper is absent, the port is
bound to loopback, and untrusted users cannot execute code or connect directly to the
database. Re-run the gate for every release and after base-image updates. A finding
becomes release-blocking as soon as a vendor fix is available or deployment conditions
expand beyond this boundary.
