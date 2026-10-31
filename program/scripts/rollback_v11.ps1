# 一键回滚到 v1.1.0（研发期兜底）
#
#   .\scripts\rollback_v11.ps1              # 代码回到 v1.1.0，数据库不动
#   .\scripts\rollback_v11.ps1 -WithDb      # 连数据库一起回到 v1.1 基线快照
#   .\scripts\rollback_v11.ps1 -DryRun      # 只报告会做什么
#
# 做法：先把当前 HEAD 存成 pre-rollback-<时间> 分支（不丢任何东西），
# 再 reset --hard v1.1.0；然后停服务、按需恢复数据库、重新拉起、验收。
param(
  [switch]$WithDb,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$root = "C:\oaagent-m0"
Set-Location $root

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupBranch = "pre-rollback-$stamp"
$dbBackup = "$root\data\backups\oa_v11_baseline.db"

Write-Host "[rollback] root=$root"
$dirty = (git status --porcelain)
if ($dirty -and -not $DryRun) {
  Write-Host "[rollback] 工作区有未提交改动，先存到 $backupBranch"
  git add -A | Out-Null
  git -c user.name=rollback -c user.email=rollback@local commit -q -m "wip: snapshot before rollback ($stamp)"
}
if (-not $DryRun) { git branch -f $backupBranch HEAD | Out-Null }

$target = "v1.1.0"
Write-Host "[rollback] target tag: $target"
if ($DryRun) {
  Write-Host "[rollback] dry-run: 会执行 git reset --hard $target"
  if ($WithDb) { Write-Host "[rollback] dry-run: 会恢复 $dbBackup" }
  exit 0
}

# 1) 停服务（先停 exe，再停 8077）
Get-Process AgentOS-1.1, AgentOS-1.0, oaagent-console -ErrorAction SilentlyContinue | Stop-Process -Force
$conn = Get-NetTCPConnection -LocalPort 8077 -State Listen -ErrorAction SilentlyContinue
if ($conn) { $conn | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }
Start-Sleep -Seconds 2
Write-Host "[rollback] services stopped"

# 2) 代码回滚
git reset --hard $target | Out-Null
Write-Host "[rollback] code at $(git describe --tags --always)"

# 3) 数据库（可选）
if ($WithDb) {
  if (Test-Path $dbBackup) {
    Copy-Item "$root\data\oa.db" "$root\data\oa.db.before-rollback-$stamp" -Force
    Copy-Item $dbBackup "$root\data\oa.db" -Force
    Remove-Item "$root\data\oa.db-wal", "$root\data\oa.db-shm" -ErrorAction SilentlyContinue
    Write-Host "[rollback] db restored from $dbBackup (旧库留档 oa.db.before-rollback-$stamp)"
  } else {
    Write-Warning "[rollback] 没找到 $dbBackup，数据库保持不动"
  }
}

# 4) 重新拉起 + 验收
& "$root\scripts\start_all.ps1" -SkipOcto -SkipTunnel | Out-Null
Start-Sleep -Seconds 3
try {
  $h = Invoke-RestMethod "http://127.0.0.1:8077/health" -TimeoutSec 5
  Write-Host "[rollback] health: $($h | ConvertTo-Json -Compress)"
} catch {
  Write-Warning "[rollback] health check failed: $_"
}

Write-Host ""
Write-Host "[rollback] 完成。"
Write-Host "  代码      : $(git describe --tags --always)"
Write-Host "  回滚前状态: 分支 $backupBranch（要恢复：git reset --hard $backupBranch）"
Write-Host "  桌面安装包: dist\AgentOS-1.1-setup.exe"
