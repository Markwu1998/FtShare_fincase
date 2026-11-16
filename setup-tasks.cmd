@echo off
setlocal
cd /d "%~dp0"
echo Registering FtShare scheduled tasks (update 08:20 / 12:30, web and collector at logon)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-tasks.ps1"
set "code=%errorlevel%"
echo.
if not "%code%"=="0" (
  echo Registration failed with exit code %code%.
  echo Open Task Scheduler and add the tasks manually, or run this file as Administrator.
)
echo.
pause
exit /b %code%
