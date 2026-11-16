@echo off
setlocal
cd /d "%~dp0"
netstat -ano | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo Dashboard already listening on 8080; nothing to do.
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\python-backend.ps1" web
