# Agent OS Full Launcher (Backend + Desktop App)
param ()

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoDir

& (Join-Path $RepoDir "start-backend.ps1")

$DesktopExe = "C:\Users\anxin\Desktop\AgentOS.exe"
if (Test-Path $DesktopExe) {
    Write-Host "[*] Launching AgentOS Desktop Client..." -ForegroundColor Cyan
    Start-Process -FilePath $DesktopExe
} else {
    $ReleaseExe = Join-Path $RepoDir "releases\AgentOS.exe"
    if (Test-Path $ReleaseExe) {
        Write-Host "[*] Launching AgentOS Desktop Client from release ($ReleaseExe)..." -ForegroundColor Cyan
        Start-Process -FilePath $ReleaseExe
    }
}
