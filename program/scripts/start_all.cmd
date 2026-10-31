@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
echo.
pause
endlocal
