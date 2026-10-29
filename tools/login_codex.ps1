$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$env:CODEX_HOME = Join-Path $repoRoot 'data\codex-cli'
New-Item -ItemType Directory -Path $env:CODEX_HOME -Force | Out-Null
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:CODEX_API_KEY -ErrorAction SilentlyContinue
Write-Host 'Agent OS - Codex CLI sign-in (dedicated local profile)'
Write-Host 'Complete sign-in on the official page opened by Codex. Never paste credentials into chat.'
codex login
if ($LASTEXITCODE -eq 0) { codex login status }
