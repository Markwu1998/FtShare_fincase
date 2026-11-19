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

# 1. ETF RQData daily seed.
Invoke-Step 'RQData history' {
    $arguments = @('--source', 'rqdata', '--seed', 'data/private/rqdata-seed.json')
    if (-not $Full) { $arguments += '--recent' }
    & $backend history -Arguments $arguments
}

# 2. ETF DDB daily seed. DDB is now the default source; a stale/missing
# fund_etf_nr must be visible as a failed step, not silently ignored.
Invoke-Step 'DDB history' {
    $arguments = @('--source', 'ddb', '--seed', 'data/private/ddb-seed.json')
    if (-not $Full) { $arguments += '--recent' }
    & $backend history -Arguments $arguments
}

# 3. Daily crowding (concentration / style).
Invoke-Step 'Crowding daily' {
    & $backend crowding
}

# 4. Daily margin and valuation.
Invoke-Step 'Crowding financing' {
    & $backend -Mode crowding -Arguments @('--crowding-module=financing')
}

# 5. Daily industry crowding.
Invoke-Step 'Crowding industry' {
    & $backend -Mode crowding -Arguments @('--crowding-module=industry')
}

if ($failed.Count -gt 0) {
    Write-Warning "Daily update completed with failures: $($failed -join ', ')"
    exit 1
}
Write-Output 'Daily update completed.'
exit 0
