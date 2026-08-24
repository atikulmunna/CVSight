param(
    [Parameter(Mandatory)][string]$DatabaseContainer,
    [Parameter(Mandatory)][string]$MediaRoot,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$DatabaseName = "shelfsight",
    [string]$DatabaseUser = "shelfsight"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
$namePattern = "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

foreach ($value in @($DatabaseContainer, $DatabaseName, $DatabaseUser)) {
    if ($value -notmatch $namePattern) {
        throw "Database identifiers must contain only letters, numbers, dots, underscores, or hyphens"
    }
}

$resolvedMedia = (Resolve-Path -LiteralPath $MediaRoot).Path
if (-not (Test-Path -LiteralPath $resolvedMedia -PathType Container)) {
    throw "Media root must be an existing directory"
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw "Backup output already exists"
}
$mediaPrefix = $resolvedMedia.TrimEnd([IO.Path]::DirectorySeparatorChar) +
    [IO.Path]::DirectorySeparatorChar
if (
    $output.Equals($resolvedMedia, [StringComparison]::OrdinalIgnoreCase) -or
    $output.StartsWith($mediaPrefix, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Backup output cannot be inside the media root"
}

$outputParent = [IO.Path]::GetDirectoryName($output)
New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
$runId = [guid]::NewGuid().ToString("N")
$staging = Join-Path $outputParent ".cvsight-backup-$runId"
$containerDump = "/tmp/cvsight-backup-$runId.dump"
New-Item -ItemType Directory -Path $staging | Out-Null

try {
    $running = docker inspect --format "{{.State.Running}}" $DatabaseContainer
    Assert-NativeSuccess "Database container inspection"
    if ($running.Trim() -ne "true") {
        throw "Database container is not running"
    }

    docker exec $DatabaseContainer pg_dump `
        --username $DatabaseUser `
        --dbname $DatabaseName `
        --format custom `
        --no-owner `
        --no-privileges `
        --file $containerDump
    Assert-NativeSuccess "PostgreSQL backup"

    docker exec $DatabaseContainer pg_restore --list $containerDump | Out-Null
    Assert-NativeSuccess "PostgreSQL backup validation"
    docker cp "${DatabaseContainer}:${containerDump}" (Join-Path $staging "database.dump")
    Assert-NativeSuccess "PostgreSQL backup copy"

    Copy-Item -LiteralPath $resolvedMedia -Destination (Join-Path $staging "media") -Recurse
    uv run python -m shelfsight_api.recovery create-manifest --bundle $staging
    Assert-NativeSuccess "Backup manifest creation"
    uv run python -m shelfsight_api.recovery verify --bundle $staging
    Assert-NativeSuccess "Backup bundle verification"

    Move-Item -LiteralPath $staging -Destination $output
    Write-Output "Backup created: $output"
}
finally {
    docker exec $DatabaseContainer rm -f $containerDump 2>$null | Out-Null
    if (Test-Path -LiteralPath $staging) {
        $resolvedStaging = (Resolve-Path -LiteralPath $staging).Path
        if (
            [IO.Path]::GetDirectoryName($resolvedStaging) -ne $outputParent -or
            -not [IO.Path]::GetFileName($resolvedStaging).StartsWith(".cvsight-backup-")
        ) {
            throw "Refusing to remove an unexpected backup staging directory"
        }
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}
