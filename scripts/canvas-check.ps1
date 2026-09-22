param([switch]$KeepRunning)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

& (Join-Path $PSScriptRoot "stage-canvas-fixture.ps1")
Assert-NativeSuccess "Canvas fixture staging"

npm --prefix (Join-Path $projectRoot "frontend") run build
Assert-NativeSuccess "Production frontend build"

# Report what else is competing for the CPU and GPU, so the measurement is not
# attributed to the application when the machine was busy.
Write-Output ""
Write-Output "Top processes by CPU time:"
Get-Process |
    Sort-Object CPU -Descending |
    Select-Object -First 6 Name, Id, @{ Name = "CpuSeconds"; Expression = { [math]::Round($_.CPU, 0) } } |
    Format-Table -AutoSize |
    Out-String |
    Write-Output

$preview = Start-Process `
    -FilePath "npm" `
    -ArgumentList @("--prefix", (Join-Path $projectRoot "frontend"), "run", "preview") `
    -NoNewWindow `
    -PassThru

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            if ((Invoke-WebRequest "http://127.0.0.1:4173/" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "The preview server did not start"
    }

    Write-Output "Measure the production build at:"
    Write-Output "  http://127.0.0.1:4173/?fixture=local"
    Write-Output ""
    Write-Output "Close other applications first. The panel reports whether the host was"
    Write-Output "steady enough for the run to count."
    if ($KeepRunning) {
        Write-Output "Press Ctrl+C when the measurement is recorded."
        Wait-Process -Id $preview.Id
    }
    else {
        Read-Host "Press Enter once the measurement is recorded"
    }
}
finally {
    if (-not $preview.HasExited) {
        Stop-Process -Id $preview.Id -Force
    }
}
