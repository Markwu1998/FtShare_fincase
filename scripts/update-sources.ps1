param(
    [switch]$Full,
    [switch]$SkipCrowding
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-History([string]$source, [string]$seed) {
    $arguments = @('--source', $source, '--seed', $seed)
    if (-not $Full) { $arguments += '--recent' }
    & (Join-Path $PSScriptRoot 'python-backend.ps1') history -Arguments $arguments
}

# RQData is the default. A late RQData history failure must not prevent the
# independent crowding archive from being refreshed.
$failed = $false
try {
    Invoke-History 'rqdata' 'data/private/rqdata-seed.json'
} catch {
    Write-Warning "RQData history failed: $($_.Exception.Message)"
    $failed = $true
}

# DDB is optional: a missing credential or an unavailable ETL must not block
# the default RQData snapshot. The DDB seed stays unready until it succeeds.
try {
    Invoke-History 'ddb' 'data/private/ddb-seed.json'
} catch {
    Write-Warning "DDB seed skipped: $($_.Exception.Message)"
    $global:LASTEXITCODE = 0
}

# Crowding is a daily, pre-market dataset. It is normally built by the
# dedicated 09:20 task; -SkipCrowding keeps the 08:20/12:30 ETF runs light.
if (-not $SkipCrowding) {
    try {
        & (Join-Path $PSScriptRoot 'python-backend.ps1') crowding
    } catch {
        Write-Warning "Crowding update failed: $($_.Exception.Message)"
        $failed = $true
    }
}

if ($failed) { exit 1 } else { exit 0 }
