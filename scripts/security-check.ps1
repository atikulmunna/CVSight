$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
$auditRequirements = Join-Path (
    [IO.Path]::GetTempPath()
) ("cvsight-python-audit-" + [guid]::NewGuid() + ".txt")
$imageArchiveName = ".security-image-scan-" + [guid]::NewGuid() + ".tar"
$imageArchive = Join-Path $projectRoot $imageArchiveName
$databaseImage = "cvsight-postgres:17-pgvector-0.8.6"
$trivyImage = "aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"

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

    npm --prefix frontend audit --audit-level=high
    Assert-NativeSuccess "Frontend dependency audit"

    docker build `
        --pull `
        --file docker/postgres/Dockerfile `
        --tag $databaseImage `
        $projectRoot
    Assert-NativeSuccess "Database image build"

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
}
finally {
    Remove-Item -LiteralPath $auditRequirements -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $imageArchive -Force -ErrorAction SilentlyContinue
}
