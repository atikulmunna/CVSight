param(
    [string]$Output
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$benchmarkRoot = Join-Path $projectRoot "benchmark-local"
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
$auditRequirements = Join-Path (
    [IO.Path]::GetTempPath()
) ("cvsight-python-audit-" + [guid]::NewGuid() + ".txt")
$imageArchiveName = ".security-image-scan-" + [guid]::NewGuid() + ".tar"
$imageArchive = Join-Path $projectRoot $imageArchiveName
$databaseImage = "cvsight-postgres:17-pgvector-0.8.6"
$trivyImage = "aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
$startedAt = Get-Date
$checks = [ordered]@{}
if (-not $Output) {
    $Output = Join-Path $benchmarkRoot "t039-security-report.json"
}

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

try {
    uv export `
        --quiet `
        --frozen `
        --no-dev `
        --no-emit-project `
        --format requirements.txt `
        --output-file $auditRequirements
    Assert-NativeSuccess "Python dependency export"

    uv run pip-audit `
        --requirement $auditRequirements `
        --require-hashes `
        --disable-pip `
        --strict `
        --progress-spinner off
    Assert-NativeSuccess "Python dependency audit"
    $checks.python_dependency_audit = $true

    npm --prefix frontend audit --audit-level=high
    Assert-NativeSuccess "Frontend dependency audit"
    $checks.frontend_dependency_audit = $true

    docker build `
        --pull `
        --file docker/postgres/Dockerfile `
        --tag $databaseImage `
        $projectRoot
    Assert-NativeSuccess "Database image build"
    $databaseImageId = docker image inspect --format "{{.Id}}" $databaseImage
    Assert-NativeSuccess "Database image identity"

    docker save --output $imageArchive $databaseImage
    Assert-NativeSuccess "Database image export"

    docker run `
        --rm `
        --volume "${projectRoot}:/scan:ro" `
        --volume trivy-cache:/root/.cache/ `
        $trivyImage `
        image `
        --input "/scan/$imageArchiveName" `
        --scanners vuln `
        --severity HIGH,CRITICAL `
        --ignore-unfixed `
        --skip-version-check `
        --exit-code 1
    Assert-NativeSuccess "Database image vulnerability audit"
    $checks.fixable_high_or_critical_container_findings = 0

    $report = [ordered]@{
        schema_version = "cvsight-security-report/v1"
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        duration_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
        database_image = $databaseImage
        database_image_id = $databaseImageId.Trim()
        trivy_image = $trivyImage
        checks = $checks
        passed = $true
    }
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Output)) `
        -Force | Out-Null
    $report | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $Output -Encoding utf8
    Write-Output "Security gate passed: $Output"
}
finally {
    Remove-Item -LiteralPath $auditRequirements -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $imageArchive -Force -ErrorAction SilentlyContinue
}
