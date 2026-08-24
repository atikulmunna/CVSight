# Backup and recovery

PostgreSQL and the managed media directory are the authoritative CVSight state. The
database contains annotations, catalog records, jobs, immutable snapshots, model
lineage, authentication audit records, and derived similarity vectors. The media
directory contains original, canonical, thumbnail, and SKU reference images.

FiftyOne is a rebuildable projection. Similarity vectors are also rebuildable from
accepted annotations and active SKU reference images. Neither projection replaces the
authoritative database and media backup.

## Create a backup

Use a quiescent recovery point. Stop every API and worker process that can change the
database or managed media, but keep PostgreSQL running. Confirm no imports, uploads,
annotation saves, exports, or model jobs are active.

From the repository root, run:

```powershell
./scripts/backup.ps1 `
  -DatabaseContainer cvsight-database-1 `
  -MediaRoot ./media-local `
  -OutputDirectory ./backup-local/2026-08-24
```

The output directory must not already exist and must not be inside the media root.
The command creates:

```text
2026-08-24/
  database.dump
  manifest.json
  media/
```

The manifest records the size and SHA-256 checksum of the PostgreSQL dump and every
media file. The command validates the PostgreSQL archive and the complete bundle
before publishing the output directory.

Copy the finished bundle to encrypted, access-controlled storage on another device.
Follow the 30-day replacement and retirement policy in `SECURITY.md`. A backup stored
only beside the live data is not a disaster-recovery copy.

Verify a retained bundle without changing a database or media directory:

```powershell
uv run python -m shelfsight_api.recovery verify `
  --bundle ./backup-local/2026-08-24
```

## Restore a clean deployment

Create a new empty PostgreSQL database container. Do not run Alembic migrations on
the restore target. The restore command refuses a database that already has public
tables and a media target that already exists.

```powershell
./scripts/restore.ps1 `
  -DatabaseContainer cvsight-recovery-database `
  -BundleDirectory ./backup-local/2026-08-24 `
  -MediaRoot ./media-recovered
```

Set `SHELFSIGHT_DATABASE_URL` to the restored database and
`SHELFSIGHT_MEDIA_ROOT` to the restored media directory. Start the API, verify both
health endpoints, inspect a known reviewed image, and then start workers:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/health/database
./scripts/dev.ps1
```

If restore fails after changing the target database, discard that isolated target and
retry with a new empty database and absent media target. Do not continue from a partial
restore.

## Rebuild projections

Rebuild a named FiftyOne projection from an immutable snapshot. PostgreSQL and media
must point to the restored state:

```powershell
$env:FIFTYONE_DATABASE_DIR='./fiftyone-local/database'
.venv-fiftyone\Scripts\python.exe -m shelfsight_api.fiftyone_projection `
  rebuild <dataset-version-uuid> `
  --dataset-name cvsight-review
```

The replacement is staged before an existing projection with the same name is
removed. A failed rebuild leaves the previous projection available.

To rebuild PostgreSQL similarity projections, first stop every embedding worker. The
command refuses to proceed while an embedding job is running. It queues current
recognition and propagation targets, preserves historical job rows, and removes old
derived vectors in one transaction:

```powershell
uv run python -m shelfsight_api.similarity_projection
```

Restart the configured embedding worker and wait for the queued jobs to finish before
serving similarity candidates or propagation suggestions.

## Expected restart behavior

- Browser drafts are written before synchronization and survive a browser refresh.
- Acknowledged annotation revisions are committed in PostgreSQL and survive API and
  database restarts.
- Expired worker leases return eligible jobs to the durable queue. Job result writes
  and successful completion use one database transaction.
- Failed managed-media writes remove files created by that attempt.
- An interrupted export does not register a completed snapshot artifact.

## Run the recovery drill

The automated drill uses two uniquely named disposable PostgreSQL containers and a
small generated fixture. It performs two API starts, a database restart, a checksummed
backup, a clean restore, expired-lease recovery, two equivalent FiftyOne rebuilds, and
a similarity rebuild:

```powershell
./scripts/recovery-check.ps1
```

Evidence is retained at `benchmark-local/t038-recovery-report.json`. Temporary
containers, databases, media, backup contents, and FiftyOne data are removed after the
run. The existing development database and media directory are not used.

## Complete deployment retirement

This release has no selective dataset deletion workflow. To satisfy a deletion or
retention request, retire the complete isolated deployment: PostgreSQL storage,
managed media, exports, FiftyOne projections, local model artifacts derived from the
data, operational copies, and every backup. Confirm the exact deployment paths and
container volumes before deletion. Never delete individual content-addressed media
files because another record may reference them.
