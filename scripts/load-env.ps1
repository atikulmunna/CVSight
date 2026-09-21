param([string]$Path = ".env")

# Dot-source this script so the variables land in the calling session:
#     . ./scripts/load-env.ps1
if ($MyInvocation.InvocationName -ne ".") {
    throw "Dot-source this script so the variables reach your session: . ./scripts/load-env.ps1"
}

$resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$loaded = 0
foreach ($line in Get-Content -LiteralPath $resolved) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    $name, $value = $trimmed.Split("=", 2)
    if ($null -eq $value -or $name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
        throw "Malformed line in ${resolved}: expected NAME=value"
    }
    Set-Item -Path "Env:$name" -Value $value
    $loaded += 1
}
Write-Output "Loaded $loaded variables from $resolved"
