param(
    [string]$Output
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$benchmarkRoot = Join-Path $projectRoot "benchmark-local"
$runId = [guid]::NewGuid().ToString("N")
$runRoot = Join-Path $benchmarkRoot "t039-install-$runId"
$sourceRoot = Join-Path $runRoot "source"
$reportPath = if ($Output) {
    [IO.Path]::GetFullPath($Output)
}
else {
    Join-Path $benchmarkRoot "t039-install-report.json"
}
$containerName = "cvsight-t039-install-$runId"
$databaseImage = "cvsight-t039-install:$runId"
$databasePassword = [guid]::NewGuid().ToString("N")
$databaseName = "shelfsight"
$databaseUser = "shelfsight"
$apiProcess = $null
$workerProcess = $null
$frontendProcess = $null
$oldUvProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
$startedAt = Get-Date

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
    # Probe over TCP: the entrypoint's temporary init server listens only on the socket.
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        docker exec $containerName pg_isready -h 127.0.0.1 -U $databaseUser -d $databaseName `
            2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Clean-install database did not become ready"
}

function Wait-JsonHealth {
    param([Parameter(Mandatory)][string]$Uri)

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-RestMethod $Uri
            if ($response.status -eq "ok") {
                return $response
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Service did not become ready: $Uri"
}

function Stop-ChildProcess {
    param([Diagnostics.Process]$Process)

    if ($null -ne $Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id
        $Process.WaitForExit()
    }
}

function Remove-RunDirectory {
    if (-not (Test-Path -LiteralPath $runRoot)) {
        return
    }
    $resolvedRun = (Resolve-Path -LiteralPath $runRoot).Path
    if (
        [IO.Path]::GetDirectoryName($resolvedRun) -ne $benchmarkRoot -or
        -not [IO.Path]::GetFileName($resolvedRun).StartsWith("t039-install-")
    ) {
        throw "Refusing to remove an unexpected install-check directory"
    }
    Remove-Item -LiteralPath $resolvedRun -Recurse -Force
}

New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
$sourceFiles = @(git ls-files --cached --others --exclude-standard)
Assert-NativeSuccess "List release source files"
foreach ($relativePath in $sourceFiles) {
    if (
        -not $relativePath -or
        [IO.Path]::IsPathRooted($relativePath) -or
        $relativePath -match '(^|[\\/])\.\.([\\/]|$)'
    ) {
        throw "Release source contains an unsafe path"
    }
    $source = Join-Path $projectRoot $relativePath
    $destination = Join-Path $sourceRoot $relativePath
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) `
        -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

$databasePort = Get-FreePort
$apiPort = Get-FreePort
$frontendPort = Get-FreePort
$mediaRoot = Join-Path $sourceRoot "media-local"
$importRoot = Join-Path $sourceRoot "import-local"
New-Item -ItemType Directory -Path $mediaRoot, $importRoot -Force | Out-Null

try {
    Push-Location $sourceRoot
    try {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $sourceRoot ".venv"
        uv sync --frozen --all-groups
        Assert-NativeSuccess "Clean Python dependency install"
        npm --prefix frontend ci
        Assert-NativeSuccess "Clean frontend dependency install"

        docker build `
            --file docker/postgres/Dockerfile `
            --tag $databaseImage `
            $sourceRoot | Out-Null
        Assert-NativeSuccess "Clean database image build"
        docker run `
            --name $containerName `
            --env "POSTGRES_DB=$databaseName" `
            --env "POSTGRES_USER=$databaseUser" `
            --env "POSTGRES_PASSWORD=$databasePassword" `
            --publish "127.0.0.1:${databasePort}:5432" `
            --detach $databaseImage | Out-Null
        Assert-NativeSuccess "Clean database start"
        Wait-Database
        docker exec $containerName createdb -U $databaseUser "${databaseName}_test"
        Assert-NativeSuccess "Clean test database creation"

        $env:SHELFSIGHT_DATABASE_URL =
            "postgresql+psycopg://${databaseUser}:${databasePassword}@127.0.0.1:${databasePort}/${databaseName}"
        $env:SHELFSIGHT_TEST_DATABASE_URL =
            "postgresql+psycopg://${databaseUser}:${databasePassword}@127.0.0.1:${databasePort}/${databaseName}_test"
        $env:SHELFSIGHT_MEDIA_ROOT = $mediaRoot
        $env:SHELFSIGHT_IMPORT_ROOT = $importRoot
        $env:SHELFSIGHT_SESSION_COOKIE_SECURE = "false"
        uv run alembic upgrade head
        Assert-NativeSuccess "Clean database migration"

        ./scripts/check.ps1
        Assert-NativeSuccess "Clean functional gate"
        uv run pytest -q `
            tests/test_analytics.py `
            tests/test_analytics_api.py `
            tests/test_gap_analytics.py `
            --basetemp .pytest-install-analytics
        Assert-NativeSuccess "Clean analytics acceptance gate"

        $password = [guid]::NewGuid().ToString("N")
        $env:T039_INSTALL_PASSWORD = $password
        $python = Join-Path $sourceRoot ".venv\Scripts\python.exe"
        $passwordHash = & $python -c `
            'import os; from shelfsight_api.auth_service import hash_password; print(hash_password(os.environ["T039_INSTALL_PASSWORD"]))'
        Assert-NativeSuccess "Generate clean-install credential"
        $env:SHELFSIGHT_AUTH_USERS = ConvertTo-Json -Compress -InputObject @(
            @{
                username = "owner"
                role = "owner"
                password_hash = $passwordHash.Trim()
            }
        )
        Remove-Item Env:T039_INSTALL_PASSWORD
        $env:SHELFSIGHT_API_PORT = $apiPort.ToString()

        $apiProcess = Start-Process `
            -FilePath $python `
            -ArgumentList @("-m", "shelfsight_api") `
            -WorkingDirectory $sourceRoot `
            -RedirectStandardOutput (Join-Path $runRoot "api.log") `
            -RedirectStandardError (Join-Path $runRoot "api-error.log") `
            -WindowStyle Hidden `
            -PassThru
        $apiHealth = Wait-JsonHealth "http://127.0.0.1:$apiPort/api/health"
        $databaseHealth = Wait-JsonHealth `
            "http://127.0.0.1:$apiPort/api/health/database"

        $workerId = "clean-install-worker"
        $workerProcess = Start-Process `
            -FilePath $python `
            -ArgumentList @("-m", "shelfsight_api.worker", "--worker-id", $workerId) `
            -WorkingDirectory $sourceRoot `
            -RedirectStandardOutput (Join-Path $runRoot "worker.log") `
            -RedirectStandardError (Join-Path $runRoot "worker-error.log") `
            -WindowStyle Hidden `
            -PassThru
        Start-Sleep -Seconds 1
        if ($workerProcess.HasExited) {
            throw "Clean-install worker exited unexpectedly"
        }

        $loginBody = @{username = "owner"; password = $password} |
            ConvertTo-Json -Compress
        $login = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$apiPort/api/auth/login" `
            -Method Post `
            -ContentType "application/json" `
            -Body $loginBody `
            -SessionVariable webSession
        $dataset = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$apiPort/api/datasets" `
            -Method Post `
            -ContentType "application/json" `
            -Body '{"name":"Clean install verification"}' `
            -WebSession $webSession
        # The worker registers its heartbeat only after Python starts, imports its
        # dependencies, and connects to the database, so poll instead of sampling once.
        # The endpoint answers 503 until a worker is live, which Invoke-RestMethod
        # raises, so each attempt is isolated.
        $workerVisible = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $workerHealth = Invoke-RestMethod `
                    -Uri "http://127.0.0.1:$apiPort/api/workers/health" `
                    -WebSession $webSession
                if ($workerId -in $workerHealth.workers.worker_id) {
                    $workerVisible = $true
                    break
                }
            }
            catch {
                $workerHealth = $null
            }
            if ($workerProcess.HasExited) {
                throw "Clean-install worker exited before registering a heartbeat"
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $workerVisible) {
            throw "Clean-install worker heartbeat was not visible"
        }

        $node = (Get-Command node).Source
        $frontendProcess = Start-Process `
            -FilePath $node `
            -ArgumentList @(
                "node_modules/vite/bin/vite.js",
                "preview",
                "--host",
                "127.0.0.1",
                "--port",
                $frontendPort.ToString(),
                "--strictPort"
            ) `
            -WorkingDirectory (Join-Path $sourceRoot "frontend") `
            -RedirectStandardOutput (Join-Path $runRoot "frontend.log") `
            -RedirectStandardError (Join-Path $runRoot "frontend-error.log") `
            -WindowStyle Hidden `
            -PassThru
        $frontendReady = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $html = Invoke-WebRequest "http://127.0.0.1:$frontendPort/"
                if ($html.StatusCode -eq 200 -and $html.Content -match 'id="root"') {
                    $frontendReady = $true
                    break
                }
            }
            catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if (-not $frontendReady) {
            $frontendLog = Get-Content -LiteralPath `
                (Join-Path $runRoot "frontend.log") -Raw -ErrorAction SilentlyContinue
            $frontendError = Get-Content -LiteralPath `
                (Join-Path $runRoot "frontend-error.log") -Raw -ErrorAction SilentlyContinue
            throw (
                "Clean-install frontend did not become ready. " +
                "stdout=$frontendLog stderr=$frontendError"
            )
        }

        $report = [ordered]@{
            schema_version = "cvsight-install-check/v1"
            completed_at = (Get-Date).ToUniversalTime().ToString("o")
            duration_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
            source_file_count = $sourceFiles.Count
            python = (& $python --version)
            node = (& node --version)
            npm = (& npm --version)
            database_image = $databaseImage
            functional_gate = "passed"
            analytics_gate = "passed"
            api_health = $apiHealth.status
            database_health = $databaseHealth.status
            authenticated_role = $login.role
            created_dataset_id = $dataset.id
            worker_heartbeat = $workerId
            frontend_http_status = 200
            passed = $true
        }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($reportPath)) `
            -Force | Out-Null
        $report | ConvertTo-Json -Depth 10 |
            Set-Content -LiteralPath $reportPath -Encoding utf8
        Write-Output "Clean installation passed: $reportPath"
    }
    finally {
        Pop-Location
    }
}
finally {
    Stop-ChildProcess $frontendProcess
    Stop-ChildProcess $workerProcess
    Stop-ChildProcess $apiProcess
    docker rm -f $containerName 2>$null | Out-Null
    docker image rm -f $databaseImage 2>$null | Out-Null
    $env:UV_PROJECT_ENVIRONMENT = $oldUvProjectEnvironment
    Remove-RunDirectory
}
