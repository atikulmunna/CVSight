$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot "..\.uv-cache"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

uv run python -m pytest --basetemp .pytest-run
Assert-NativeSuccess "Python tests"
uv run ruff check benchmark_tool shelfsight_api tests migrations
Assert-NativeSuccess "Ruff"
uv run mypy
Assert-NativeSuccess "mypy"
npm --prefix frontend test
Assert-NativeSuccess "Frontend tests"
npm --prefix frontend run lint
Assert-NativeSuccess "ESLint"
npm --prefix frontend run typecheck
Assert-NativeSuccess "TypeScript"
npm --prefix frontend run build
Assert-NativeSuccess "Frontend build"
