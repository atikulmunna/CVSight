$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".." ".uv-cache"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

if (-not $env:SHELFSIGHT_TEST_DATABASE_URL) {
    throw "SHELFSIGHT_TEST_DATABASE_URL is required. The database tests skip silently without it. See .env.example."
}
if ($env:SHELFSIGHT_TEST_DATABASE_URL -eq $env:SHELFSIGHT_DATABASE_URL) {
    throw "SHELFSIGHT_TEST_DATABASE_URL must not point at the application database."
}

# Alembic reads SHELFSIGHT_DATABASE_URL, so point it at the test database only for the migration.
$applicationDatabaseUrl = $env:SHELFSIGHT_DATABASE_URL
try {
    $env:SHELFSIGHT_DATABASE_URL = $env:SHELFSIGHT_TEST_DATABASE_URL
    uv run alembic upgrade head
    Assert-NativeSuccess "Test database migration"
}
finally {
    $env:SHELFSIGHT_DATABASE_URL = $applicationDatabaseUrl
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
