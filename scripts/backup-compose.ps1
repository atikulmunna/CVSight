param(
    [Parameter(Mandatory)][string]$OutputDirectory
)

# Back up a Docker Compose deployment on Windows into a verified recovery bundle, the same
# format scripts/backup.ps1 and scripts/backup-compose.sh write. Everything runs inside the
# deployment's own containers.

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

$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw "Backup output already exists"
}
$outputParent = [IO.Path]::GetDirectoryName($output)
New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
$runId = [guid]::NewGuid().ToString("N")
$staging = Join-Path $outputParent ".cvsight-backup-$runId"
$dump = "/tmp/cvsight-backup-$runId.dump"
New-Item -ItemType Directory -Path $staging | Out-Null
$writers = @(Invoke-Compose ps --status running --services | Where-Object { $_ -in @("api", "worker") })

try {
    # A quiescent recovery point: nothing may change the database or media during the copy.
    if ($writers.Count -gt 0) {
        Invoke-Compose stop @writers
        Assert-NativeSuccess "Stopping the API and worker"
    }
    Invoke-Compose exec -T database pg_dump --username shelfsight --dbname shelfsight `
        --format custom --no-owner --no-privileges --file $dump
    Assert-NativeSuccess "PostgreSQL backup"
    Invoke-Compose exec -T database pg_restore --list $dump | Out-Null
    Assert-NativeSuccess "PostgreSQL backup validation"
    Invoke-Compose cp "database:$dump" (Join-Path $staging "database.dump")
    Assert-NativeSuccess "PostgreSQL backup copy"

    # Media files are owner-only, so they are copied as root inside the API image.
    Invoke-Compose run --rm --no-deps -T --user root -v "${staging}:/backup" api sh -c `
        "mkdir /backup/media && if [ -d /data/media ]; then cp -R /data/media/. /backup/media/; fi && python -m shelfsight_api.recovery create-manifest --bundle /backup && python -m shelfsight_api.recovery verify --bundle /backup"
    Assert-NativeSuccess "Media copy and bundle verification"

    Move-Item -LiteralPath $staging -Destination $output
    Write-Output "Backup created: $output"
}
finally {
    Invoke-Compose exec -T database rm -f $dump 2>$null | Out-Null
    # Restart only what was running before the backup stopped it.
    if ($writers.Count -gt 0) {
        Invoke-Compose start @writers | Out-Null
    }
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
