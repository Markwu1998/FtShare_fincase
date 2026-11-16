# Read-only status report for the FtShare dashboard (Windows).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\status.ps1
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host ("FtShare status  " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
    Write-Host ''

    Write-Host '== Windows scheduled tasks (optional) =='
    foreach ($name in 'FtShare-UpdateDaily', 'FtShare-UpdateNoon', 'FtShare-Web', 'FtShare-Live') {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
            Write-Host ("  {0,-22} {1,-10} next: {2}" -f $name, $task.State, $info.NextRunTime)
        } else {
            $out = & schtasks /Query /TN $name /FO LIST 2>$null
            if ($LASTEXITCODE -eq 0) { Write-Host ("  {0,-22} registered (schtasks)" -f $name) }
            else { Write-Host ("  {0,-22} not registered" -f $name) }
        }
    }
    Write-Host ''

    Write-Host '== web =='
    foreach ($port in 8080, 8081) {
        try {
            $health = Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $port) -TimeoutSec 4
            Write-Host ("  {0}: {1} / {2} / built {3}" -f $port, $health.provider, $health.mode, $health.history_built_at)
        } catch {
            Write-Host ("  {0}: not reachable" -f $port)
        }
    }
    Write-Host ''

    Write-Host '== daily snapshot =='
    $seedPath = 'data\private\rqdata-seed.json'
    if (Test-Path $seedPath) {
        $seed = Get-Content -LiteralPath $seedPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $minutes = @($seed.minutes.PSObject.Properties).Count
        Write-Host ("  provider    {0}" -f $seed.provider)
        Write-Host ("  flow/market {0} / {1}" -f $seed.latest_flow_date, $seed.latest_market_date)
        Write-Host ("  broad days  {0}  minute days {1}  funds {2}" -f @($seed.broad.dates).Count, $minutes, @($seed.universe).Count)
        Write-Host ("  built_at    {0}" -f $seed.built_at)
    } else {
        Write-Host '  missing (run history once)'
    }
    Write-Host ''

    Write-Host '== intraday cache =='
    $heartbeat = 'data\private\collector.heartbeat'
    if (Test-Path $heartbeat) {
        $age = ((Get-Date) - (Get-Item $heartbeat).LastWriteTime).TotalSeconds
        Write-Host ("  collector heartbeat {0:N0}s ago" -f $age)
    } else {
        Write-Host '  collector heartbeat missing'
    }
    $cachePath = 'data\private\rqdata-live.json'
    if (Test-Path $cachePath) {
        $cache = Get-Content -LiteralPath $cachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $entry = $cache.entries.'live:rqdata:snapshot'
        if ($entry) {
            $value = ($entry | ConvertFrom-Json).value
            $fetched = [datetimeoffset]::Parse($value.fetched_at).UtcDateTime
            $age = ((Get-Date).ToUniversalTime() - $fetched).TotalMinutes
            Write-Host ("  date {0}  last minute {1}  fetched {2}  ({3:N0} min ago)" -f $value.date, $value.last_complete_minute, $value.fetched_at, $age)
        } else {
            Write-Host '  cache file has no live entry'
        }
    } else {
        Write-Host '  missing (start the collector)'
    }
    Write-Host ''

    Write-Host '== crowding =='
    foreach ($file in 'rqdata-crowding.json', 'rqdata-crowding-industry.json', 'rqdata-crowding-funds.json', 'rqdata-crowding-financing.json') {
        $path = Join-Path 'data\private' $file
        if (Test-Path $path) {
            $data = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            $rows = @($data.rows)
            Write-Host ("  {0,-34} {1,4} rows  {2} .. {3}" -f $file, $rows.Count, $rows[0].date, $rows[-1].date)
        } else {
            Write-Host ("  {0,-34} missing" -f $file)
        }
    }
    Write-Host ''

    $bytes = (Get-ChildItem 'data\private' -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    Write-Host ("== data dir ==`n  data\private  {0:N0} bytes ({1:N2} MB)" -f $bytes, ($bytes / 1MB))
} finally {
    Pop-Location
}
