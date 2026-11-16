@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Test-Path 'data\private\collector.heartbeat') -and (((Get-Date) - (Get-Item 'data\private\collector.heartbeat').LastWriteTime).TotalSeconds -lt 300)) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  echo Collector already running.
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0scripts\python-backend.ps1' -Mode live -Arguments @('--watch')"
