@echo off
setlocal
cd /d "%~dp0"
start "" "..\.venv\Scripts\pythonw.exe" "%~dp0tools\homelab_private_input.py"
