param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet("list", "add", "remove", "password", "share", "unshare")]
    [string]$Action,
    [Parameter(Position = 1)][string]$Username,
    [Parameter(Position = 2)][ValidateSet("owner", "annotator", "reviewer")][string]$Role
)

# Who can reach a Docker Compose deployment, in one command: CVSight accounts in .env and
# the Tailscale address. Account changes run inside the deployment's API image, so the
# host needs only Docker, and the API is restarted to pick them up.
#
#   ./scripts/cvsight-access.ps1 add rahim annotator
#   ./scripts/cvsight-access.ps1 share

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $projectRoot "compose.yaml"
$tunnelTarget = "http://127.0.0.1:8081"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Compose {
    docker compose -f $composeFile @args
}

function Get-Tailscale {
    $command = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $installed = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $installed) { return $installed }
    return $null
}

function Show-Address {
    $tailscale = Get-Tailscale
    if (-not $tailscale) {
        Write-Output "Address: https://localhost on this PC. Install Tailscale to reach it from other devices."
        return
    }
    $name = (& $tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd(".")
    $serving = -not ((& $tailscale serve status 2>&1 | Out-String) -match "No serve config")
    if ($serving) {
        Write-Output "Address: https://$name"
    }
    else {
        Write-Output "Address: https://$name (sharing is off; run: ./scripts/cvsight-access.ps1 share)"
    }
}

if ($Action -in @("share", "unshare")) {
    $tailscale = Get-Tailscale
    if (-not $tailscale) {
        throw "Tailscale is not installed. See the README section on using CVSight from your own devices."
    }
    if ($Action -eq "share") {
        & $tailscale serve --bg $tunnelTarget | Out-Null
    }
    else {
        & $tailscale serve --https=443 off | Out-Null
    }
    Assert-NativeSuccess "tailscale serve"
    if ($Action -eq "share") { Show-Address } else { Write-Output "The Tailscale address is off." }
    return
}

if ($Action -ne "list" -and -not $Username) {
    throw "Name the user, for example: ./scripts/cvsight-access.ps1 $Action rahim"
}
if ($Action -eq "add" -and -not $Role) {
    throw "Give the role: owner, annotator, or reviewer"
}

$arguments = @($Action) + @($Username, $Role | Where-Object { $_ })
# A password prompt needs a terminal; the other actions do not.
$terminal = if ($Action -in @("add", "password")) { "-it" } else { "-T" }
Invoke-Compose run --rm --no-deps $terminal --user root -v "${projectRoot}:/config" api `
    python -m shelfsight_api.auth_cli users --env-file /config/.env @arguments
Assert-NativeSuccess "Account change"
if ($Action -eq "list") {
    return
}

Invoke-Compose up -d api worker 2>&1 | Out-Null
Assert-NativeSuccess "Restarting the API"
Write-Output "The API restarted with the new accounts."
if ($Action -eq "add") {
    Write-Output ""
    Write-Output "Send $Username these three things:"
    Write-Output "  1. A Tailscale invite: open https://login.tailscale.com/admin/machines and choose Share on this PC."
    Write-Output "  2. $(Show-Address)"
    Write-Output "  3. Their username, $Username, and the password you just set."
}
