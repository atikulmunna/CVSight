param(
    [string]$Output,
    [switch]$UseExistingEvidence
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$benchmarkRoot = Join-Path $projectRoot "benchmark-local"
$startedAt = Get-Date
if (-not $Output) {
    $Output = Join-Path $benchmarkRoot "t039-release-report.json"
}
$evidence = [ordered]@{
    install = Join-Path $benchmarkRoot "t039-install-report.json"
    main_licenses = Join-Path $benchmarkRoot "t039-main-license-report.json"
    fiftyone_licenses = Join-Path $benchmarkRoot "t039-fiftyone-license-report.json"
    security = Join-Path $benchmarkRoot "t039-security-report.json"
    performance = Join-Path $benchmarkRoot "t039-scale-report.json"
    canvas = Join-Path $benchmarkRoot "t037-canvas-evidence.json"
    recovery = Join-Path $benchmarkRoot "t038-recovery-report.json"
}

function Read-PassingReport {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Name evidence is missing: $Path"
    }
    $report = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($report.passed -ne $true) {
        throw "$Name evidence did not pass"
    }
    return $report
}

New-Item -ItemType Directory -Path $benchmarkRoot -Force | Out-Null
if (-not $UseExistingEvidence) {
    & (Join-Path $PSScriptRoot "install-check.ps1") -Output $evidence.install
    & (Join-Path $PSScriptRoot "license-check.ps1") `
        -MainOutput $evidence.main_licenses `
        -FiftyOneOutput $evidence.fiftyone_licenses
    & (Join-Path $PSScriptRoot "security-check.ps1") -Output $evidence.security
    & (Join-Path $PSScriptRoot "performance-check.ps1") -Output $evidence.performance
    & (Join-Path $PSScriptRoot "recovery-check.ps1")
}

$install = Read-PassingReport "Clean installation" $evidence.install
$mainLicenses = Read-PassingReport "Main license" $evidence.main_licenses
$fiftyOneLicenses = Read-PassingReport "FiftyOne license" $evidence.fiftyone_licenses
$security = Read-PassingReport "Security" $evidence.security
$performance = Read-PassingReport "Performance" $evidence.performance
$canvas = Read-PassingReport "Canvas" $evidence.canvas
$recovery = Read-PassingReport "Recovery" $evidence.recovery

$report = [ordered]@{
    schema_version = "cvsight-release-report/v1"
    release = "0.1.0"
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    assembly_duration_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 3)
    passed = $true
    gates = [ordered]@{
        clean_install = $install.schema_version
        functional = $install.functional_gate
        analytics = $install.analytics_gate
        main_licenses = $mainLicenses.python.package_count
        fiftyone_licenses = $fiftyOneLicenses.python.package_count
        security = $security.schema_version
        backend_performance = $performance.schema_version
        canvas_performance = $canvas.schema_version
        recovery = $recovery.schema_version
    }
    measured_gate_durations_seconds = [ordered]@{
        clean_install = $install.duration_seconds
        security = $security.duration_seconds
        recovery = $recovery.duration_seconds
    }
    evidence = $evidence
    release_boundary = [ordered]@{
        single_project = $true
        self_hosted = $true
        image_annotation_only = $true
        automatic_sku_assignment = $false
        selective_dataset_deletion = $false
        internet_edge = $false
    }
}
$report | ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath $Output -Encoding utf8
Write-Output "Release gate passed: $Output"
