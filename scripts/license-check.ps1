param(
    [string]$MainOutput,
    [string]$FiftyOneOutput
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$benchmarkRoot = Join-Path $projectRoot "benchmark-local"
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
if (-not $MainOutput) {
    $MainOutput = Join-Path $benchmarkRoot "t039-main-license-report.json"
}
if (-not $FiftyOneOutput) {
    $FiftyOneOutput = Join-Path $benchmarkRoot "t039-fiftyone-license-report.json"
}

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Path $benchmarkRoot -Force | Out-Null
uv run python -m benchmark_tool.license_audit --output $MainOutput
Assert-NativeSuccess "Main dependency license audit"

$fiftyOnePython = Join-Path $projectRoot ".venv-fiftyone\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $fiftyOnePython -PathType Leaf)) {
    throw "The optional FiftyOne environment is required for the release license gate"
}
& $fiftyOnePython -m benchmark_tool.license_audit `
    --output $FiftyOneOutput `
    --all-installed `
    --python-only `
    --environment-label fiftyone
Assert-NativeSuccess "FiftyOne dependency license audit"
