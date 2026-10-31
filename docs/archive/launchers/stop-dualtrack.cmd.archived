@echo off
setlocal
echo [*] 正在停止 AgentOS 双轨服务与所有后端进程...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8077, 8081, 8082 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo [*] 已停止所有双轨服务 (8077, 8081, 8082).
endlocal
