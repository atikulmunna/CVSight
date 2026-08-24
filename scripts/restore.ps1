param(
    [Parameter(Mandatory)][string]$DatabaseContainer,
    [Parameter(Mandatory)][string]$BundleDirectory,
    [Parameter(Mandatory)][string]$MediaRoot,
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

$bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
$mediaTarget = [IO.Path]::GetFullPath($MediaRoot)
if (Test-Path -LiteralPath $mediaTarget) {
    throw "Media restore target already exists"
}
$bundlePrefix = $bundle.TrimEnd([IO.Path]::DirectorySeparatorChar) +
    [IO.Path]::DirectorySeparatorChar
if (
    $mediaTarget.Equals($bundle, [StringComparison]::OrdinalIgnoreCase) -or
    $mediaTarget.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Media restore target cannot be inside the backup"
}
uv run python -m shelfsight_api.recovery verify --bundle $bundle
Assert-NativeSuccess "Backup bundle verification"

$running = docker inspect --format "{{.State.Running}}" $DatabaseContainer
Assert-NativeSuccess "Database container inspection"
if ($running.Trim() -ne "true") {
    throw "Database container is not running"
}

$tableCount = docker exec $DatabaseContainer psql `
    --username $DatabaseUser `
    --dbname $DatabaseName `
    --tuples-only `
    --no-align `
    --command "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';"
Assert-NativeSuccess "Restore target inspection"
if ([int]$tableCount.Trim() -ne 0) {
    throw "Restore requires a clean database with no public tables"
}

$runId = [guid]::NewGuid().ToString("N")
$containerDump = "/tmp/cvsight-restore-$runId.dump"
try {
    docker cp (Join-Path $bundle "database.dump") "${DatabaseContainer}:${containerDump}"
    Assert-NativeSuccess "Backup upload"
    docker exec $DatabaseContainer pg_restore `
        --username $DatabaseUser `
        --dbname $DatabaseName `
        --exit-on-error `
        --single-transaction `
        --no-owner `
        --no-privileges `
        $containerDump
    Assert-NativeSuccess "PostgreSQL restore"
    docker exec $DatabaseContainer psql `
        --username $DatabaseUser `
        --dbname $DatabaseName `
        --command "ANALYZE;" | Out-Null
    Assert-NativeSuccess "PostgreSQL analyze"

    uv run python -m shelfsight_api.recovery restore-media `
        --bundle $bundle `
        --target $mediaTarget
    Assert-NativeSuccess "Media restore"
    Write-Output "Restore completed: database=$DatabaseName media=$mediaTarget"
}
finally {
    docker exec $DatabaseContainer rm -f $containerDump 2>$null | Out-Null
}
