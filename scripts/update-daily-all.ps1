param(
    [switch]$Full
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$backend = Join-Path $PSScriptRoot 'python-backend.ps1'
$failed = @()

function Invoke-Step([string]$name, [scriptblock]$action) {
    Write-Output "=== $name ==="
    try {
        & $action
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    } catch {
        Write-Warning "$name failed: $($_.Exception.Message)"
        $script:failed += $name
    }
}

# 1. ETF RQData daily seed. A late NAV must not block crowding updates.
Invoke-Step 'RQData history' {
    $arguments = @('--source', 'rqdata', '--seed', 'data/private/rqdata-seed.json')
    if (-not $Full) { $arguments += '--recent' }
    & $backend history -Arguments $arguments
}

# 2. Daily crowding (concentration / style).
Invoke-Step 'Crowding daily' {
    & $backend crowding
}

# 3. Daily margin and valuation.
Invoke-Step 'Crowding financing' {
    & $backend -Mode crowding -Arguments @('--crowding-module=financing')
}

# 4. Daily industry crowding.
Invoke-Step 'Crowding industry' {
    & $backend -Mode crowding -Arguments @('--crowding-module=industry')
}

if ($failed.Count -gt 0) {
    Write-Warning "Daily update completed with failures: $($failed -join ', ')"
    exit 1
}
Write-Output 'Daily update completed.'
exit 0
