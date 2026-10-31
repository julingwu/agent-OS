# Agent OS Backend Launcher
param (
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoDir

$PythonExe = Join-Path $RepoDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "      Agent OS Service Launching...     " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Check if port 8077 is already listening
$existing = Get-NetTCPConnection -LocalPort 8077 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[OK] Backend server is already running on port 8077." -ForegroundColor Green
} else {
    Write-Host "[*] Starting Uvicorn backend server (http://127.0.0.1:8077)..." -ForegroundColor Yellow
    Start-Process -FilePath $PythonExe -ArgumentList "-X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port 8077" -WorkingDirectory $RepoDir
    Start-Sleep -Seconds 2
}

# Verify Health
$healthy = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8077/health" -TimeoutSec 2 -ErrorAction Stop
        if ($resp.ok -eq $true) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

if ($healthy) {
    Write-Host "[OK] Agent OS backend is running healthy!" -ForegroundColor Green
    Write-Host "     - Web Console: http://127.0.0.1:8077/app/" -ForegroundColor Magenta
    Write-Host "     - Desktop Client: AgentOS.exe on Desktop" -ForegroundColor Magenta
} else {
    Write-Host "[ERROR] Service did not respond to health check in time." -ForegroundColor Red
}
