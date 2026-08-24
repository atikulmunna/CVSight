$ErrorActionPreference = "Stop"

$projectRoot = Split-Path $PSScriptRoot -Parent
$prototypeRoot = Join-Path $projectRoot "benchmark-local\t007-prototype"
$sourceFixture = Join-Path $prototypeRoot "fixture.json"
$allowedImageRoot = [IO.Path]::GetFullPath(
    (Join-Path $projectRoot "benchmark-local\images")
)
$outputRoot = Join-Path $projectRoot "frontend\public\local-fixtures"

if (-not (Test-Path -LiteralPath $sourceFixture -PathType Leaf)) {
    throw "The T007 fixture is missing."
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$fixture = Get-Content -LiteralPath $sourceFixture -Raw | ConvertFrom-Json
$fixture | Add-Member -NotePropertyName "name" -NotePropertyValue "T007 real shelf fixture" -Force

for ($index = 0; $index -lt $fixture.images.Count; $index += 1) {
    $sourceImage = $fixture.images[$index]
    $decodedRelativePath = [Uri]::UnescapeDataString($sourceImage.url)
    $resolvedSource = [IO.Path]::GetFullPath(
        (Join-Path $prototypeRoot $decodedRelativePath)
    )
    if (-not $resolvedSource.StartsWith($allowedImageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Fixture image path leaves the allowed benchmark image directory."
    }
    if (-not (Test-Path -LiteralPath $resolvedSource -PathType Leaf)) {
        throw "Fixture image is missing: $resolvedSource"
    }

    $extension = [IO.Path]::GetExtension($resolvedSource).ToLowerInvariant()
    if ($extension -notin @(".jpg", ".jpeg", ".png")) {
        throw "Fixture image has an unsupported extension."
    }
    $outputName = "shelf-{0}{1}" -f ($index + 1), $extension
    Copy-Item -LiteralPath $resolvedSource -Destination (
        Join-Path $outputRoot $outputName
    ) -Force
    $sourceImage.url = "/local-fixtures/$outputName"
}

$outputFixture = Join-Path $outputRoot "t007.json"
$fixture | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $outputFixture -Encoding utf8
Write-Output "Staged $($fixture.boxes.Count) real annotations in $outputRoot"
