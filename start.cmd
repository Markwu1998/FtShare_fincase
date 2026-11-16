@echo off
setlocal
cd /d "%~dp0"
title FtShare ETF Observatory

echo ============================================================
echo   FtShare ETF Observatory
echo   Open in browser:  http://127.0.0.1:8080
echo   Stop:             Ctrl+C  or close this window
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\python-backend.ps1" web
set "code=%errorlevel%"

echo.
if not "%code%"=="0" (
  echo Startup stopped with exit code %code%.
  echo To see the full error, run this in PowerShell:
  echo   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\python-backend.ps1 web
  pause
)
endlocal & exit /b %code%
