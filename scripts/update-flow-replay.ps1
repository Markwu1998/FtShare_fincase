param(
    [string]$Day = (Get-Date).ToString('yyyy-MM-dd'),
    [ValidateSet('rqdata', 'ddb')][string]$Source = 'ddb',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$day = [datetime]::ParseExact($Day, 'yyyy-MM-dd', $null).ToString('yyyy-MM-dd')
$target = Join-Path $root "data/private/flow-replay/$day/replay/$day.json"
$uiTarget = Join-Path $root "data/private/etf-burst/replay/$day.json"
$expectedProvider = if ($Source -eq 'ddb') { 'DDB' } else { 'RQData' }

$existingProvider = $null
if (Test-Path -LiteralPath $target) {
    try { $existingProvider = (Get-Content -LiteralPath $target -Raw | ConvertFrom-Json).provider } catch { $existingProvider = $null }
}

if ((Test-Path -LiteralPath $target) -and -not $Force -and $existingProvider -eq $expectedProvider) {
    if (!(Test-Path -LiteralPath $uiTarget)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $uiTarget) | Out-Null
        Copy-Item -LiteralPath $target -Destination $uiTarget -Force
        Write-Output "Mirrored existing flow replay to UI archive: $uiTarget"
    } else {
        Write-Output "Flow replay already exists: $target"
    }
    exit 0
}

$temp = Join-Path $root ".local/flow-replay-build/$day-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $temp | Out-Null
Write-Output "Building flow replay for $day from $Source..."
& (Join-Path $PSScriptRoot 'python-backend.ps1') -Mode day-replay -Arguments @(
    '--day', $day, '--source', $Source, '--out', $temp, '--clock', 'market'
)
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Warning "Day replay failed for $day (exit $code); temporary files kept at $temp"
    exit $code
}

$replayFile = Join-Path $temp "replay/$day.json"
if (!(Test-Path -LiteralPath $replayFile)) {
    Write-Warning "Day replay did not produce $replayFile"
    exit 1
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
Copy-Item -LiteralPath $replayFile -Destination $target -Force
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $uiTarget) | Out-Null
Copy-Item -LiteralPath $replayFile -Destination $uiTarget -Force
Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
Write-Output "Flow replay ready: $target"
Write-Output "UI replay ready: $uiTarget"
exit 0

