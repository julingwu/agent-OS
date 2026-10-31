# One-click start for the oaagent stack (Windows / PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
#   ... -SkipOcto        skip the Octo docker stack
#   ... -SkipTunnel      skip the cloudflared quick tunnel (approval needs none)
#   ... -Status          only print the health board
#
# Idempotent: every step checks before it acts, so running it twice is safe.

[CmdletBinding()]
param(
    [switch]$SkipOcto,
    [switch]$SkipTunnel,
    [switch]$Status
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Say($msg) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) }

# DSH 运行环境说明：
# 本项目 Agent OS 本身就是以 DeepSeek Harness (DSH) 作为底层执行内核。
# 此处仅做环境检测，绝不关闭用户的 DSH 桌面端窗口。
function Check-DshRuntime {
    $procs = Get-Process -Name "DeepSeek Harness" -ErrorAction SilentlyContinue
    if ($procs) {
        Say ("DSH 环境: 检测到 DeepSeek Harness 正常运行中 ({0} 进程) — 内核就绪" -f $procs.Count)
    } else {
        Say "DSH 环境: 使用独立 DSH CLI/ACP 运行"
    }
}

function Get-EnvValue($key) {
    $p = Join-Path $Root ".env"
    if (-not (Test-Path $p)) { return $null }
    foreach ($line in Get-Content $p -Encoding UTF8) {
        if ($line -match "^\s*$key\s*=\s*(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

function Set-EnvValue($key, $value) {
    $p = Join-Path $Root ".env"
    $text = if (Test-Path $p) { Get-Content $p -Raw -Encoding UTF8 } else { "" }
    if ($text -match "(?m)^\s*$key\s*=") {
        $text = [regex]::Replace($text, "(?m)^\s*$key\s*=.*$", "$key=$value")
    } else {
        $text = $text.TrimEnd() + "`r`n$key=$value`r`n"
    }
    [System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function ListenOn($port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    return [bool]$c
}

function Wait-Url($url, $tries = 40, $sleep = 3) {
    for ($i = 0; $i -lt $tries; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds $sleep
    }
    return $false
}

Say "root: $Root"

if ($Status) { $SkipOcto = $true; $SkipTunnel = $true }

if (-not $Status) { Check-DshRuntime }

# ---------------------------------------------------------------- 1. Docker
if (-not $SkipOcto -and -not $Status) {
    $dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
    if (Test-Path $dockerBin) { $env:PATH = "$env:PATH;$dockerBin" }
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    $hasDockerCli = [bool](Get-Command docker -ErrorAction SilentlyContinue)
    $engine = $false
    if ($hasDockerCli) {
        try { docker info --format "{{.ServerVersion}}" 2>$null | Out-Null; $engine = $LASTEXITCODE -eq 0 } catch { }
    }
    if (-not $engine -and (Test-Path $dockerExe)) {
        Say "starting Docker Desktop ..."
        try {
            Start-Process $dockerExe -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 10; $i++) {
                Start-Sleep -Seconds 3
                try { docker info --format "{{.ServerVersion}}" 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $engine = $true; break } } catch { }
            }
        } catch {
            Say "failed to launch Docker Desktop - Octo skipped"
        }
    } elseif (-not $engine) {
        Say "Docker not detected on machine - Octo container skipped (optional)"
    }
    if ($engine) {
        Say "docker engine up"
        $compose = Join-Path $Root "vendor\octo-deployment\docker"
        Push-Location $compose
        docker compose up -d 2>&1 | Out-Null
        Pop-Location
        $ok = Wait-Url "http://127.0.0.1:28080/" 40 3
        Say ("octo web: " + $(if ($ok) { "up" } else { "NOT responding yet (containers still booting)" }))
    } else {
        Say "Octo workshop stack: skipped (fast local mode)"
    }
}

# ---------------------------------------------------------------- 2. FastAPI
if (-not (ListenOn 8077)) {
    Say "starting oaagent on 8077 ..."
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    $out = Join-Path $LogDir "oaagent-8077.log"
    $err = Join-Path $LogDir "oaagent-8077.err.log"
    Start-Process -FilePath $py -WorkingDirectory $Root -WindowStyle Hidden -ArgumentList @("-m", "uvicorn", "app.main:app", "--port", "8077", "--log-level", "info") -RedirectStandardOutput $out -RedirectStandardError $err
    $ok = Wait-Url "http://127.0.0.1:8077/health" 20 2
    Say ("oaagent: " + $(if ($ok) { "up" } else { "FAILED - see data\logs\oaagent-8077.err.log" }))
} else {
    Say "oaagent already listening on 8077"
}

# ---------------------------------------------------------------- 3. Tunnel
$tunnelHost = $null
if (-not $SkipTunnel) {
    $cf = Join-Path $env:USERPROFILE "bin\cloudflared.exe"
    if (-not (Test-Path $cf)) { $cf = "cloudflared" }
    $running = Get-Process cloudflared -ErrorAction SilentlyContinue
    if (-not $running) {
        Say "starting cloudflared quick tunnel ..."
        $log = Join-Path $LogDir "cloudflared.log"
        try {
            Start-Process -FilePath $cf -WindowStyle Hidden -ArgumentList @("tunnel", "--url", "http://127.0.0.1:8077", "--no-autoupdate", "--logfile", $log) -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 8
        } catch {
            Say "cloudflared not available - tunnel skipped"
        }
    }
    $logs = @()
    $logs += Get-ChildItem -Path (Join-Path $Root "data") -Filter "cloudflared*.log" -ErrorAction SilentlyContinue
    $logs += Get-ChildItem -Path $LogDir -Filter "cloudflared*.log" -ErrorAction SilentlyContinue
    foreach ($f in ($logs | Sort-Object LastWriteTime -Descending)) {
        $m = Select-String -Path $f.FullName -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches |
             Select-Object -Last 1
        if ($m) { $tunnelHost = ($m.Matches | Select-Object -Last 1).Value; break }
    }
    if ($tunnelHost) {
        $old = Get-EnvValue "WECOM_CALLBACK_URL"
        Set-EnvValue "WECOM_CALLBACK_URL" "$tunnelHost/wecom/callback"
        Say "tunnel: $tunnelHost"
        if ($old -and $old -notlike "$tunnelHost*") {
            Say "NOTE: tunnel host changed. Native approval needs NO callback;"
            Say "      only the backup sign link uses it. Paste this into WeCom if you want buttons back:"
            Say "      $tunnelHost/wecom/callback"
        }
    } else {
        Say "tunnel: no hostname parsed yet (check data\logs\cloudflared.log)"
    }
}

# ---------------------------------------------------------------- 4. board
Say "health board"
$rows = @()
$rows += @{ n = "oaagent  /health"; u = "http://127.0.0.1:8077/health" }
if (Test-Path (Join-Path $Root "vendor\octo-deployment\docker")) {
    $rows += @{ n = "watch    /octo/watch"; u = "http://127.0.0.1:8077/octo/watch?token=" + (Get-EnvValue "OCTO_SIM_TOKEN") }
}
$rows += @{ n = "workspace  /ui/"; u = "http://127.0.0.1:8077/ui/" }
$rows += @{ n = "cyberdeck  /app/"; u = "http://127.0.0.1:8077/app/" }
foreach ($r in $rows) {
    $ok = $false
    try { $resp = Invoke-WebRequest -Uri $r.u -UseBasicParsing -TimeoutSec 6; $ok = $resp.StatusCode -eq 200 } catch { }
    Write-Host ("  {0,-28} {1}" -f $r.n, $(if ($ok) { "OK" } else { "DOWN" }))
}
$tok = Get-EnvValue "WECOM_SIM_TOKEN"
try {
    $ap = Invoke-RestMethod -Uri "http://127.0.0.1:8077/wecom/approval/list" -Headers @{ "X-Wecom-Token" = $tok } -TimeoutSec 8
    Say ("approval poller: running=" + $ap.poller.running + " last=" + ($ap.poller.last | ConvertTo-Json -Compress))
} catch { Say "approval poller: cannot query (is the service up?)" }

Say "=========================================================="
Say "  🚀 AgentOS 服务已就绪！"
Say "  【主工作台】: http://127.0.0.1:8077/ (AI 助手协作与电脑操控)
Say "  【赛博指挥舱】: http://127.0.0.1:8077/app/ (项目流水线与研讨会)"
Say "=========================================================="
Say "done. logs in data\logs"
