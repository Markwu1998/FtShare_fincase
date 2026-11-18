param(
    [switch]$Full
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-History([string]$source, [string]$seed) {
    $arguments = @('--source', $source, '--seed', $seed)
    if (-not $Full) { $arguments += '--recent' }
    & (Join-Path $PSScriptRoot 'python-backend.ps1') history -Arguments $arguments
}

# RQData is the default and must succeed.
Invoke-History 'rqdata' 'data/private/rqdata-seed.json'

# DDB is optional: a missing credential or an unavailable ETL must not block
# the default RQData snapshot. The DDB seed stays unready until it succeeds.
try {
    Invoke-History 'ddb' 'data/private/ddb-seed.json'
} catch {
    Write-Warning "DDB seed skipped: $($_.Exception.Message)"
    $global:LASTEXITCODE = 0
}

& (Join-Path $PSScriptRoot 'python-backend.ps1') crowding
exit 0
