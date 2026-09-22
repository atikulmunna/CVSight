param([switch]$Wait)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontend = Join-Path $projectRoot "frontend"
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"

function Assert-NativeSuccess {
    param([Parameter(Mandatory)][string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

& (Join-Path $PSScriptRoot "stage-canvas-fixture.ps1")
Assert-NativeSuccess "Canvas fixture staging"

npm --prefix $frontend run build
Assert-NativeSuccess "Production frontend build"

# Report what else is competing for the CPU, so a measurement taken on a busy
# machine is visible in the transcript instead of being read as a slow canvas.
Write-Output ""
Write-Output "Top processes by CPU time:"
Get-Process |
    Sort-Object CPU -Descending |
    Select-Object -First 6 Name, Id, @{ Name = "CpuSeconds"; Expression = { [math]::Round($_.CPU, 0) } } |
    Format-Table -AutoSize |
    Out-String |
    Write-Output

$existing = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*vite*preview*" -and $_.CommandLine -like "*cvsight*" }
foreach ($process in $existing) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

$preview = Start-Process `
    -FilePath "npm" `
    -ArgumentList @("--prefix", $frontend, "run", "preview") `
    -NoNewWindow `
    -PassThru

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
    if (-not $preview.HasExited) {
        Stop-Process -Id $preview.Id -Force
    }
    throw "The preview server did not start"
}

Write-Output "Measure the production build at:"
Write-Output "  http://127.0.0.1:4173/?fixture=local"
Write-Output ""
Write-Output "Close other applications first. The panel reports whether the host was"
Write-Output "steady enough for the run to count."
Write-Output ""
Write-Output "Stop the preview server with: Stop-Process -Id $($preview.Id)"

if ($Wait) {
    Write-Output "Waiting. Press Ctrl+C to stop the preview server."
    try {
        Wait-Process -Id $preview.Id
    }
    finally {
        if (-not $preview.HasExited) {
            Stop-Process -Id $preview.Id -Force
        }
    }
}
