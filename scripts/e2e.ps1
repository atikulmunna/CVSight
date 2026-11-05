$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Set-EnvironmentValue {
    param([Parameter(Mandatory)][string]$Name, [string]$Value)

    if ($Value) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
    else {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
}

if (-not $env:SHELFSIGHT_E2E_DATABASE_URL) {
    throw "SHELFSIGHT_E2E_DATABASE_URL is required. See .env.example."
}
if ($env:SHELFSIGHT_E2E_DATABASE_URL -eq $env:SHELFSIGHT_DATABASE_URL) {
    throw "SHELFSIGHT_E2E_DATABASE_URL must not point at the application database."
}

$workRoot = Join-Path ([IO.Path]::GetTempPath()) ("cvsight-e2e-" + [guid]::NewGuid())
$mediaRoot = Join-Path $workRoot "media"
$importRoot = Join-Path $workRoot "import"
New-Item -ItemType Directory -Path $mediaRoot, $importRoot -Force | Out-Null

# The account only exists for this run, so its password can be made up here.
$password = [guid]::NewGuid().ToString()
$overrides = @{
    SHELFSIGHT_DATABASE_URL = $env:SHELFSIGHT_E2E_DATABASE_URL
    SHELFSIGHT_MEDIA_ROOT = $mediaRoot
    SHELFSIGHT_IMPORT_ROOT = $importRoot
    SHELFSIGHT_SESSION_COOKIE_SECURE = "false"
    # The servers read the same variables a developer's shell may already hold for SAM.
    SHELFSIGHT_SEGMENTER_URL = $null
    SHELFSIGHT_SEGMENTER_TOKEN = $null
    CVSIGHT_E2E_PASSWORD = $password
}
$saved = @{ SHELFSIGHT_AUTH_USERS = $env:SHELFSIGHT_AUTH_USERS }
foreach ($name in $overrides.Keys) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name)
    Set-EnvironmentValue $name $overrides[$name]
}

try {
    $env:SHELFSIGHT_AUTH_USERS = uv run python -c @'
import json
import os

from shelfsight_api.auth_service import hash_password

password_hash = hash_password(os.environ["CVSIGHT_E2E_PASSWORD"])
print(json.dumps([{"username": "e2e-owner", "role": "owner", "password_hash": password_hash}]))
'@
    Assert-NativeSuccess "Test account"

    uv run alembic upgrade head
    Assert-NativeSuccess "Browser test database migration"
    npm --prefix frontend run build
    Assert-NativeSuccess "Frontend build"
    npm --prefix frontend run e2e
    Assert-NativeSuccess "Browser tests"
}
finally {
    foreach ($name in $saved.Keys) {
        Set-EnvironmentValue $name $saved[$name]
    }
    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
}
