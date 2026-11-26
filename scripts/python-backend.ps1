param(
    [Parameter(Mandatory)][ValidateSet('web', 'history', 'live', 'crowding', 'burst-replay', 'tick-baseline', 'day-replay', 'flow-comparison', 'monitor-preopen')][string]$Mode,
    [string]$ReplayCache,
    [Parameter(ValueFromRemainingArguments)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot)
$previousCredential = $env:RQDATAC2_CONF
$settings = @{}
try {
    if (Test-Path -LiteralPath '.env') {
        # Read as UTF-8: Windows PowerShell 5.1 defaults to the ANSI code page,
        # which turns non-ASCII comments into mojibake that can swallow the next line.
        foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
            if ($line -match '^\s*(RQDATA_PYTHON|RQDATA_CREDENTIAL_FILE|DATA_MODE|ENABLE_RADAR|FTSHARE_SEED_FILE|FTSHARE_LIVE_CACHE_FILE|MARKET_DATA_CONFIG_FILE|CROWDING_FILE|ETF_BURST_DIR|ETF_BURST_CONFIG|FTSHARE_SOURCE_CONFIG|DDB_HOST|DDB_PORT|DDB_USER|DDB_PASSWORD|HOST|PORT)\s*=\s*(.*?)\s*$') {
                $name = $Matches[1]
                $value = $Matches[2].Trim('"', "'")
                if (!(Test-Path "Env:$name")) {
                    $settings[$name] = [Environment]::GetEnvironmentVariable($name)
                    [Environment]::SetEnvironmentVariable($name, $value)
                }
            }
        }
    }
    $python = $env:ETF_BACKEND_PYTHON
    if (!$python) {
        $python = if (Test-Path -LiteralPath '.local/python-backend/Scripts/python.exe') {
            '.local/python-backend/Scripts/python.exe'
        } elseif ($env:RQDATA_PYTHON) { $env:RQDATA_PYTHON } else { 'python' }
    }
    if ($Mode -eq 'web') {
        if ($ReplayCache) {
            if (!$settings.ContainsKey('FTSHARE_LIVE_CACHE_FILE')) { $settings['FTSHARE_LIVE_CACHE_FILE'] = $env:FTSHARE_LIVE_CACHE_FILE }
            $env:FTSHARE_LIVE_CACHE_FILE = (Resolve-Path -LiteralPath $ReplayCache).Path
            $settings['RQDATA_ALLOW_REPLAY'] = $env:RQDATA_ALLOW_REPLAY
            $env:RQDATA_ALLOW_REPLAY = '1'
            if (!$settings.ContainsKey('DATA_MODE')) { $settings['DATA_MODE'] = $env:DATA_MODE }
            $env:DATA_MODE = 'live'
        }
        $address = if ($env:HOST) { $env:HOST } else { '127.0.0.1' }
        $port = if ($env:PORT) { $env:PORT } else { '8080' }
        & $python -B -m uvicorn etf_backend.web:app --host $address --port $port @Arguments
    } else {
        $localReplay = $Mode -eq 'monitor-preopen' -or $Arguments -match '^--burst-input(?:=|$)' -or
            ($Mode -eq 'tick-baseline' -and ($Arguments -match '^--input-dir(?:=|$)')) -or
            ($Mode -eq 'day-replay' -and ($Arguments -match '^(--offline|--source=ddb)$'))
        if (!$env:RQDATAC2_CONF -and !$env:RQDATAC_CONF -and $env:RQDATA_CREDENTIAL_FILE -and !$localReplay) {
            $secure = ConvertTo-SecureString ([IO.File]::ReadAllText((Resolve-Path -LiteralPath $env:RQDATA_CREDENTIAL_FILE).Path))
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $env:RQDATAC2_CONF = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        }
        if ($Mode -eq 'monitor-preopen') {
            & $python -B -X utf8 scripts/prepare-monitor-preopen.py @Arguments
        } elseif ($Mode -eq 'tick-baseline') {
            & $python -B scripts/prepare-tick-baseline.py @Arguments
        } elseif ($Mode -eq 'day-replay') {
            & $python -B -X utf8 scripts/replay-burst-day.py @Arguments
        } elseif ($Mode -eq 'flow-comparison') {
            & $python -B -m etf_backend.flow_comparison @Arguments
        } else {
            & $python -B -m etf_backend.collect $Mode @Arguments
        }
    }
    exit $LASTEXITCODE
} finally {
    $env:RQDATAC2_CONF = $previousCredential
    foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $settings[$name]) }
    Pop-Location
}
