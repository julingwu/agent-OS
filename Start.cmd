@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python environment missing. See docs\START.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "program\launcher.py"
if errorlevel 1 (
    pause
    exit /b 1
)
exit /b 0
