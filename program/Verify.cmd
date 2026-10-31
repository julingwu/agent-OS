@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"..\.venv\Scripts\python.exe" -m pytest tests/test_controlled_release.py tests/test_launcher.py tests/test_agent_v1.py tests/test_agent_full_toolkit.py tests/test_bots_v1.py tests/test_processes_v1.py tests/test_routines_skills_v1.py tests/test_events_v1.py tests/test_stream_v1.py tests/test_streaming_v1.py -q --disable-warnings
if errorlevel 1 goto failed
"..\.venv\Scripts\python.exe" -X utf8 scripts/verify_controlled_release.py
if errorlevel 1 goto failed
echo [PASS] Verification completed.
pause
exit /b 0
:failed
echo [FAIL] Review the error above.
pause
exit /b 1
