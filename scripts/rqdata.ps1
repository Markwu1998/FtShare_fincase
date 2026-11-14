param(
    [Parameter(Mandatory)][ValidateSet('history', 'live')][string]$Mode,
    [Parameter(ValueFromRemainingArguments)][string[]]$CollectorArgs
)

$ErrorActionPreference = 'Stop'
$previous = $env:RQDATAC2_CONF
Push-Location (Split-Path $PSScriptRoot)
try {
    $path = $env:RQDATA_CREDENTIAL_FILE
    if (!$path -and (Test-Path -LiteralPath '.env')) {
        foreach ($line in Get-Content -LiteralPath '.env') {
            if ($line -match '^\s*RQDATA_CREDENTIAL_FILE\s*=\s*(.+?)\s*$') { $path = $Matches[1].Trim('"', "'") }
        }
    }
    if ($path) {
        $secret = ConvertTo-SecureString ([IO.File]::ReadAllText((Resolve-Path -LiteralPath $path).Path))
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try { $env:RQDATAC2_CONF = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
    $entry = if ($Mode -eq 'history') { 'scripts/collect.mjs' } else { 'scripts/collect-live.mjs' }
    & node --env-file-if-exists=.env $entry @CollectorArgs
    exit $LASTEXITCODE
} finally {
    $env:RQDATAC2_CONF = $previous
    Pop-Location
}
