$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot "..\.uv-cache"

if (-not $env:SHELFSIGHT_DATABASE_URL) {
    throw "SHELFSIGHT_DATABASE_URL is required. See .env.example."
}

uv run alembic upgrade head

$api = Start-Process `
    -FilePath "uv" `
    -ArgumentList @("run", "python", "-m", "shelfsight_api") `
    -NoNewWindow `
    -PassThru

$worker = Start-Process `
    -FilePath "uv" `
    -ArgumentList @(
        "run",
        "python",
        "-m",
        "shelfsight_api.worker",
        "--worker-id",
        "local-worker"
    ) `
    -NoNewWindow `
    -PassThru

try {
    npm --prefix frontend run dev
}
finally {
    if (-not $worker.HasExited) {
        Stop-Process -Id $worker.Id
    }
    if (-not $api.HasExited) {
        Stop-Process -Id $api.Id
    }
}
