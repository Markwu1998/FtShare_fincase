@echo off
setlocal
cd /d "%~dp0"
if not exist ".local" mkdir ".local"
echo [%date% %time%] update-daily start >> ".local\update-daily.log"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0scripts\python-backend.ps1' history -Arguments @('--recent')" >> ".local\update-daily.log" 2>&1
set "code=%errorlevel%"
echo [%date% %time%] update-daily exit %code% >> ".local\update-daily.log"
exit /b %code%
