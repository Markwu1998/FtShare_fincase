param([switch]$WhatIf)

# Register the daily data refresh and the two long-running processes.
# Run once in a normal PowerShell (not a restricted sandbox):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-tasks.ps1
# Preview without creating anything:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-tasks.ps1 -WhatIf

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

try {
    function New-TaskAction([string]$file) {
        New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "{0}"' -f (Join-Path $root $file)) -WorkingDirectory $root
    }

    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    $morning = (Get-Date).Date.AddHours(8).AddMinutes(20)
    $noon = (Get-Date).Date.AddHours(12).AddMinutes(30)
    $weekdays = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'

    $tasks = @(
        @{ Name = 'FtShare-UpdateDaily'
           Action = New-TaskAction 'update-daily.cmd'
           Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $morning
           Description = 'Refresh the last 14 days of ETF history and merge into the local snapshot' }
        @{ Name = 'FtShare-UpdateNoon'
           Action = New-TaskAction 'update-daily.cmd'
           Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $noon
           Description = 'Second daily pass for late share/NAV disclosures' }
        @{ Name = 'FtShare-Web'
           Action = New-TaskAction 'start-web.cmd'
           Trigger = New-ScheduledTaskTrigger -AtLogOn
           Description = 'Read-only dashboard on http://127.0.0.1:8080 (skips if already running)' }
        @{ Name = 'FtShare-Live'
           Action = New-TaskAction 'start-collector.cmd'
           Trigger = New-ScheduledTaskTrigger -AtLogOn
           Description = 'RQData intraday collector; preloads at 09:15 and subscribes at 09:30' }
    )

    foreach ($task in $tasks) {
        Register-ScheduledTask -TaskName $task.Name -Action $task.Action -Trigger $task.Trigger `
            -Settings $settings -Description $task.Description -Force -WhatIf:$WhatIf | Out-Null
        if ($WhatIf) { Write-Host ('would register: ' + $task.Name) }
        else { Write-Host ('registered: ' + $task.Name) }
    }

    Write-Host ''
    Write-Host 'Verify:  schtasks /Query /TN FtShare-UpdateDaily /FO LIST'
    Write-Host 'Remove:  Unregister-ScheduledTask -TaskName FtShare-UpdateDaily -Confirm:$false'
} catch {
    Write-Host ''
    Write-Host ('Task registration failed: ' + $_.Exception.Message) -ForegroundColor Yellow
    Write-Host 'Run this script in a normal (not sandboxed) PowerShell session.'
    Write-Host 'If it still fails, open Task Scheduler and create the four tasks manually;'
    Write-Host 'see docs/operations.md section 2.6 for the exact trigger and action.'
    exit 1
}
