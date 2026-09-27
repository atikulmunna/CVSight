param(
    [Parameter(Mandatory)][string]$BundleDirectory
)

# Restore a recovery bundle into a new, empty Docker Compose deployment on Windows. Like
# scripts/restore-compose.sh, it refuses a database that has tables or media that already
# exist, and everything runs inside the deployment's own containers.

$ErrorActionPreference = "Stop"
$composeFile = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "compose.yaml"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Compose {
    docker compose -f $composeFile @args
}

$bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
$dump = "/tmp/cvsight-restore-" + [guid]::NewGuid().ToString("N") + ".dump"

Invoke-Compose build
Assert-NativeSuccess "Image build"
# Media files are owner-only, so the bundle is read as root inside the API image.
Invoke-Compose run --rm --no-deps -T --user root -v "${bundle}:/backup:ro" api `
    python -m shelfsight_api.recovery verify --bundle /backup
Assert-NativeSuccess "Backup bundle verification"

Invoke-Compose up -d --wait database
Assert-NativeSuccess "Database start"
$tables = Invoke-Compose exec -T database psql --username shelfsight --dbname shelfsight `
    --tuples-only --no-align `
    --command "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';"
Assert-NativeSuccess "Restore target inspection"
if ([int]"$tables".Trim() -ne 0) {
    throw "Restore requires a clean database with no public tables"
}

try {
    Invoke-Compose cp (Join-Path $bundle "database.dump") "database:$dump"
    Assert-NativeSuccess "Backup upload"
    Invoke-Compose exec -T --user root database chown postgres $dump
    Assert-NativeSuccess "Backup ownership"
    Invoke-Compose exec -T database pg_restore --username shelfsight --dbname shelfsight `
        --exit-on-error --single-transaction --no-owner --no-privileges $dump
    Assert-NativeSuccess "PostgreSQL restore"
    Invoke-Compose exec -T database psql --username shelfsight --dbname shelfsight `
        --command "ANALYZE;" | Out-Null
    Assert-NativeSuccess "PostgreSQL analyze"
}
finally {
    Invoke-Compose exec -T database rm -f $dump 2>$null | Out-Null
}

# restore-media checks every file against the manifest and refuses an existing target.
Invoke-Compose run --rm --no-deps -T --user root -v "${bundle}:/backup:ro" api sh -c `
    "python -m shelfsight_api.recovery restore-media --bundle /backup --target /data/media && chown -R cvsight /data/media"
Assert-NativeSuccess "Media restore"

Invoke-Compose up -d
Assert-NativeSuccess "Deployment start"
Write-Output "Restore completed from $bundle"
