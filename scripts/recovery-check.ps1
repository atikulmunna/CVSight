param(
    [string]$DatabaseImage = "cvsight-postgres:17-pgvector-0.8.6"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
$runId = [guid]::NewGuid().ToString("N")
$sourceContainer = "cvsight-t038-source-$runId"
$targetContainer = "cvsight-t038-target-$runId"
$runRoot = Join-Path $projectRoot "benchmark-local\t038-recovery-$runId"
$reportPath = Join-Path $projectRoot "benchmark-local\t038-recovery-report.json"
$sourceMedia = Join-Path $runRoot "source-media"
$restoredMedia = Join-Path $runRoot "restored-media"
$backupBundle = Join-Path $runRoot "backup"
$statePath = Join-Path $runRoot "fixture-state.json"
$fiftyOneRoot = Join-Path $runRoot "fiftyone"
$apiLog = Join-Path $runRoot "api.log"
$apiErrorLog = Join-Path $runRoot "api-error.log"
$databasePassword = [guid]::NewGuid().ToString("N")
$databaseUser = "shelfsight"
$databaseName = "shelfsight"
$databaseImagePattern = "^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$"
$apiProcess = $null
$projectionName = "cvsight-t038-$($runId.Substring(0, 12))"
$startedAt = Get-Date
$checks = [ordered]@{}

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Wait-Database {
    param([Parameter(Mandatory)][string]$Container)

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        docker exec $Container pg_isready -U $databaseUser -d $databaseName 2>$null |
            Out-Null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Database container did not become ready: $Container"
}

function Start-Database {
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][int]$Port
    )

    docker run --name $Container `
        -e "POSTGRES_DB=$databaseName" `
        -e "POSTGRES_USER=$databaseUser" `
        -e "POSTGRES_PASSWORD=$databasePassword" `
        -p "127.0.0.1:${Port}:5432" `
        -d $DatabaseImage | Out-Null
    Assert-NativeSuccess "Start database container"
    Wait-Database $Container
}

function Set-ApplicationEnvironment {
    param(
        [Parameter(Mandatory)][int]$DatabasePort,
        [Parameter(Mandatory)][string]$MediaRoot
    )

    $env:SHELFSIGHT_DATABASE_URL =
        "postgresql+psycopg://${databaseUser}:${databasePassword}@127.0.0.1:${DatabasePort}/${databaseName}"
    $env:SHELFSIGHT_MEDIA_ROOT = $MediaRoot
    $env:SHELFSIGHT_IMPORT_ROOT = Join-Path $runRoot "import"
    $env:SHELFSIGHT_SESSION_COOKIE_SECURE = "false"
}

function Test-ApiRestart {
    param([Parameter(Mandatory)][int]$Port)

    $python = (Resolve-Path (Join-Path $projectRoot ".venv\Scripts\python.exe")).Path
    $env:SHELFSIGHT_API_PORT = $Port.ToString()
    $script:apiProcess = Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "shelfsight_api") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $apiLog `
        -RedirectStandardError $apiErrorLog `
        -WindowStyle Hidden `
        -PassThru
    try {
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $response = Invoke-RestMethod "http://127.0.0.1:$Port/api/health"
                if ($response.status -eq "ok") {
                    uv run python -m benchmark_tool.recovery_fixture verify --state $statePath |
                        Out-Null
                    Assert-NativeSuccess "Verify fixture after API start"
                    return
                }
            }
            catch {
                Start-Sleep -Milliseconds 250
            }
        }
        $details = if (Test-Path -LiteralPath $apiErrorLog) {
            Get-Content -LiteralPath $apiErrorLog -Raw
        }
        else {
            "no API error log"
        }
        throw "API did not become ready on port ${Port}: $details"
    }
    finally {
        if ($null -ne $script:apiProcess -and -not $script:apiProcess.HasExited) {
            Stop-Process -Id $script:apiProcess.Id
            $script:apiProcess.WaitForExit()
        }
        $script:apiProcess = $null
    }
}

function Remove-RunDirectory {
    if (-not (Test-Path -LiteralPath $runRoot)) {
        return
    }
    $resolvedRun = (Resolve-Path -LiteralPath $runRoot).Path
    $expectedParent = (Resolve-Path (Join-Path $projectRoot "benchmark-local")).Path
    if (
        [IO.Path]::GetDirectoryName($resolvedRun) -ne $expectedParent -or
        -not [IO.Path]::GetFileName($resolvedRun).StartsWith("t038-recovery-")
    ) {
        throw "Refusing to remove an unexpected recovery run directory"
    }
    Remove-Item -LiteralPath $resolvedRun -Recurse -Force
}

if ($DatabaseImage -notmatch $databaseImagePattern) {
    throw "Database image contains unsupported characters"
}

New-Item -ItemType Directory -Path $sourceMedia -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $runRoot "import") -Force | Out-Null
$sourcePort = Get-FreePort
$targetPort = Get-FreePort
$apiPort = Get-FreePort

try {
    docker image inspect $DatabaseImage | Out-Null
    Assert-NativeSuccess "Inspect database image"
    Start-Database $sourceContainer $sourcePort
    Set-ApplicationEnvironment $sourcePort $sourceMedia

    uv run alembic upgrade head | Out-Null
    Assert-NativeSuccess "Migrate source database"
    uv run python -m benchmark_tool.recovery_fixture seed --state $statePath | Out-Null
    Assert-NativeSuccess "Seed recovery fixture"
    $checks.fixture_seeded = $true

    Test-ApiRestart $apiPort
    Test-ApiRestart $apiPort
    $checks.api_restarts = 2

    docker restart $sourceContainer | Out-Null
    Assert-NativeSuccess "Restart source database"
    Wait-Database $sourceContainer
    uv run python -m benchmark_tool.recovery_fixture verify --state $statePath | Out-Null
    Assert-NativeSuccess "Verify fixture after database restart"
    $checks.database_restart_preserved_state = $true

    & (Join-Path $PSScriptRoot "backup.ps1") `
        -DatabaseContainer $sourceContainer `
        -MediaRoot $sourceMedia `
        -OutputDirectory $backupBundle
    $manifestHash = (Get-FileHash `
        -LiteralPath (Join-Path $backupBundle "manifest.json") `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    $checks.backup_manifest_sha256 = $manifestHash

    Start-Database $targetContainer $targetPort
    & (Join-Path $PSScriptRoot "restore.ps1") `
        -DatabaseContainer $targetContainer `
        -BundleDirectory $backupBundle `
        -MediaRoot $restoredMedia
    Set-ApplicationEnvironment $targetPort $restoredMedia
    uv run python -m benchmark_tool.recovery_fixture verify --state $statePath | Out-Null
    Assert-NativeSuccess "Verify restored fixture"
    $checks.clean_restore_matches_source = $true

    $recoveryResult = uv run python -m benchmark_tool.recovery_fixture `
        recover-job --state $statePath | ConvertFrom-Json
    Assert-NativeSuccess "Recover expired worker claim"
    $checks.worker_recovery = $recoveryResult

    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $env:FIFTYONE_DATABASE_DIR = $fiftyOneRoot
    $fiftyOnePython = (Resolve-Path `
        (Join-Path $projectRoot ".venv-fiftyone\Scripts\python.exe")).Path
    $firstProjection = & $fiftyOnePython -m shelfsight_api.fiftyone_projection `
        rebuild $state.identity.dataset_version_id --dataset-name $projectionName
    Assert-NativeSuccess "First FiftyOne projection rebuild"
    $secondProjection = & $fiftyOnePython -m shelfsight_api.fiftyone_projection `
        rebuild $state.identity.dataset_version_id --dataset-name $projectionName
    Assert-NativeSuccess "Second FiftyOne projection rebuild"
    $firstSummary = $firstProjection |
        Where-Object { $_ -like "projection rebuilt:*" } |
        Select-Object -Last 1
    $secondSummary = $secondProjection |
        Where-Object { $_ -like "projection rebuilt:*" } |
        Select-Object -Last 1
    if (
        $firstSummary -ne $secondSummary -or
        $secondSummary -ne "projection rebuilt: 1 images, 1 product crops"
    ) {
        throw "Repeated FiftyOne rebuilds returned different summaries"
    }
    $checks.fiftyone_rebuild = $secondSummary

    $similarityResult = uv run python -m shelfsight_api.similarity_projection |
        ConvertFrom-Json
    Assert-NativeSuccess "Rebuild similarity projections"
    if (
        $similarityResult.recognition_reference_targets -ne 1 -or
        $similarityResult.recognition_annotation_targets -ne 1 -or
        $similarityResult.propagation_annotation_targets -ne 1 -or
        $similarityResult.deleted_embeddings -ne 3 -or
        $similarityResult.queued_new_jobs -ne 3
    ) {
        throw "Similarity projection rebuild returned unexpected counts"
    }
    $checks.similarity_rebuild = $similarityResult
    $checks.projection_rebuilds_equivalent = $true

    $report = [ordered]@{
        schema_version = "cvsight-recovery-report/v1"
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        duration_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
        database_image = $DatabaseImage
        fixture_fingerprint = $state.fingerprint
        checks = $checks
        passed = $true
    }
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "Recovery drill passed: $reportPath"
}
finally {
    if ($null -ne $apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id
    }
    if ($env:FIFTYONE_DATABASE_DIR -and (Test-Path -LiteralPath $fiftyOneRoot)) {
        $fiftyOnePythonPath = Join-Path $projectRoot ".venv-fiftyone\Scripts\python.exe"
        if (Test-Path -LiteralPath $fiftyOnePythonPath) {
            & $fiftyOnePythonPath -m shelfsight_api.fiftyone_projection `
                delete --dataset-name $projectionName 2>$null | Out-Null
        }
    }
    foreach ($container in @($sourceContainer, $targetContainer)) {
        docker rm -f $container 2>$null | Out-Null
    }
    Remove-RunDirectory
}
