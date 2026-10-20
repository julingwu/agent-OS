@echo off
rem Pinned DSH headless entry (ASCII only, CRLF).
rem Run surface: dsh --profile headless --json -   (task MUST be on stdin).
rem Do not pass the task as a positional arg on Windows: the launcher splits
rem the app path at the space in 'DeepSeek Harness' and cmd reports a bad command.
setlocal
set "DSH_LOCAL=%~dp0..\..\dsh-runtime\node_modules\.bin\dsh.cmd"
if exist "%DSH_LOCAL%" (
  call "%DSH_LOCAL%" %*
  exit /b
)
set "DSH=%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
if not exist "%DSH%" (
  echo dsh CLI not found at %DSH% 1>&2
  exit /b 127
)
call "%DSH%" %*
exit /b %errorlevel%
