param(
    [string]$Day,
    [int]$WaitSeconds = 900
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$arguments = @('--merge', '--wait', [string]$WaitSeconds)
if ($Day) {
    $day = [datetime]::ParseExact($Day, 'yyyy-MM-dd', $null).ToString('yyyy-MM-dd')
    $arguments += @('--day', $day)
}

Write-Output "Merging prior-day flow comparison (wait up to ${WaitSeconds}s)..."
& (Join-Path $PSScriptRoot 'python-backend.ps1') -Mode flow-comparison -Arguments $arguments
$code = $LASTEXITCODE
if ($code -eq 0) {
    $uiDir = Join-Path $root 'data/private/etf-burst/replay'
    Get-ChildItem (Join-Path $root 'data/private/flow-replay') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $source = Join-Path $_.FullName "replay/$($_.Name).json"
        $target = Join-Path $uiDir "$($_.Name).json"
        if ((Test-Path -LiteralPath $source) -and !(Test-Path -LiteralPath $target)) {
            New-Item -ItemType Directory -Force -Path $uiDir | Out-Null
            Copy-Item -LiteralPath $source -Destination $target -Force
        }
    }
}
exit $code
