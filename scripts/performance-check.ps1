param(
    [string]$Output
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$benchmarkRoot = Join-Path $projectRoot "benchmark-local"
$runId = [guid]::NewGuid().ToString("N")
$mediaRoot = Join-Path $benchmarkRoot "t037-media-$runId"
$containerName = "cvsight-performance-db-$runId"
$databaseImage = "cvsight-postgres:17-pgvector-0.8.6"
$databasePassword = [guid]::NewGuid().ToString("N")
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"

if (-not $Output) {
    $Output = Join-Path $benchmarkRoot "t037-scale-report.json"
}

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Remove-RunMedia {
    if (-not (Test-Path -LiteralPath $mediaRoot)) {
        return
    }
    $resolvedMedia = (Resolve-Path -LiteralPath $mediaRoot).Path
    $resolvedParent = [IO.Path]::GetDirectoryName($resolvedMedia)
    if ($resolvedParent -ne $benchmarkRoot) {
        throw "Refusing to remove media outside benchmark-local"
    }
    Remove-Item -LiteralPath $resolvedMedia -Recurse -Force
}

New-Item -ItemType Directory -Path $benchmarkRoot -Force | Out-Null
New-Item -ItemType Directory -Path $mediaRoot -Force | Out-Null

try {
    docker build `
        --file docker/postgres/Dockerfile `
        --tag $databaseImage `
        $projectRoot
    Assert-NativeSuccess "Database image build"

    docker run `
        --detach `
        --rm `
        --name $containerName `
        --env "POSTGRES_DB=shelfsight_scale" `
        --env "POSTGRES_USER=shelfsight" `
        --env "POSTGRES_PASSWORD=$databasePassword" `
        --publish "127.0.0.1::5432" `
        $databaseImage
    Assert-NativeSuccess "Scale database start"

    $ready = $false
    foreach ($attempt in 1..60) {
        docker exec $containerName pg_isready --username shelfsight --dbname shelfsight_scale |
            Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "Scale database did not become ready"
    }

    $portMapping = docker port $containerName "5432/tcp"
    Assert-NativeSuccess "Scale database port lookup"
    $databasePort = ($portMapping.Trim() -split ":")[-1]
    if ($databasePort -notmatch "^\d+$") {
        throw "Scale database returned an invalid port"
    }

    $env:SHELFSIGHT_DATABASE_URL = (
        "postgresql+psycopg://shelfsight:$databasePassword@127.0.0.1:" +
        "$databasePort/shelfsight_scale"
    )
    $env:SHELFSIGHT_MEDIA_ROOT = $mediaRoot

    uv run alembic upgrade head
    Assert-NativeSuccess "Scale database migration"
    uv run python -m benchmark_tool.scale --output $Output
    Assert-NativeSuccess "Scale benchmark"
}
finally {
    docker rm --force $containerName 2>$null | Out-Null
    Remove-RunMedia
}
