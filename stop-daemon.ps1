# Stop AgentOS Local Daemon
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*agentos-daemon.py*" }
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "[*] Stopping AgentOS Daemon PID: $($p.ProcessId)..." -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[OK] AgentOS Daemon stopped." -ForegroundColor Green
} else {
    Write-Host "[INFO] AgentOS Daemon is not running." -ForegroundColor Cyan
}
