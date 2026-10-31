<#
.SYNOPSIS
    把 AgentOS 本地根 CA 安装到 Windows「当前用户 → 受信任的根证书颁发机构」，
    或从该存储中移除。安装后 curl.exe / WebView2 / Edge 会正常校验
    https://203.0.113.10 的证书链，不再需要 -k 或 --ignore-certificate-errors。

.DESCRIPTION
    只写入 CurrentUser\Root，不碰机器级存储，所以通常不需要管理员权限。
    安装前后都会打印 SHA256 指纹，便于和服务器上 openssl 的输出逐字比对。
    幂等：已装过同指纹的证书不会重复写入。

.PARAMETER CaFile
    CA 证书文件路径（PEM 或 DER 均可）。默认 <repo>\deploy\tls\out\ca.crt。

.PARAMETER Remove
    卸载：按指纹从 CurrentUser\Root 删除该 CA。

.PARAMETER Verify
    安装/卸载后顺带用 curl.exe 验证一次 https 端点状态码（不带 -k）。

.PARAMETER Url
    验证用的 URL，默认 https://203.0.113.10/v1/health。

.PARAMETER Server
    本地没有 ca.crt 时，用 scp 从服务器拉取到 deploy\tls\out\ca.crt 再安装。

.PARAMETER SshHost / SshKey
    --Server 时使用的 SSH 目标与私钥，默认 root@203.0.113.10 和 ~/.ssh/oa_agent_ed25519.pem。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_ca_windows.ps1 -Verify

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_ca_windows.ps1 -Remove

.NOTES
    回退方案（企业策略禁止安装根证书时）见 docs/design/TLS.md「失败回退」一节。
#>
[CmdletBinding()]
param(
    [string]$CaFile = '',
    [switch]$Remove,
    [switch]$Verify,
    [string]$Url = 'https://203.0.113.10/v1/health',
    [switch]$Server,
    [string]$SshHost = 'root@203.0.113.10',
    [string]$SshKey = (Join-Path $env:USERPROFILE '.ssh\oa_agent_ed25519.pem')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Write-Head([string]$t) { Write-Host "`n== $t ==" -ForegroundColor Cyan }
function Write-Ok([string]$t)   { Write-Host "   OK   $t" -ForegroundColor Green }
function Write-Warn2([string]$t){ Write-Host "   WARN $t" -ForegroundColor Yellow }
function Write-Err2([string]$t) { Write-Host "   ERR  $t" -ForegroundColor Red }

# ---------------------------------------------------------------- 路径解析
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $CaFile) { $CaFile = Join-Path $RepoRoot 'deploy\tls\out\ca.crt' }
elseif (-not [System.IO.Path]::IsPathRooted($CaFile)) { $CaFile = Join-Path $RepoRoot $CaFile }

# ---------------------------------------------------------------- PEM/DER 解析
function Get-CertFromFile([string]$Path) {
    $raw = Get-Content -LiteralPath $Path -Raw
    # 优先按 PEM 解析（ca.crt 就是 PEM）
    $m = [regex]::Match($raw, '-----BEGIN CERTIFICATE-----(?<b64>[\s\S]*?)-----END CERTIFICATE-----')
    if ($m.Success) {
        $b64 = ($m.Groups['b64'].Value -replace '\s', '')
        $bytes = [Convert]::FromBase64String($b64)
        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(, $bytes)
    }
    # 否则交给 .NET（DER 或 Base64 编码的 .cer）
    return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($Path)
}

function Get-Sha256Fp($Cert) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash($Cert.RawData) } finally { $sha.Dispose() }
    return (($hash | ForEach-Object { $_.ToString('X2') }) -join ':')
}

function Get-Sha1Thumb($Cert) { return $Cert.Thumbprint }

function Test-Installed([string]$Thumbprint) {
    return [bool](Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Thumbprint })
}

# ---------------------------------------------------------------- 可选：从服务器拉取
if ($Server) {
    Write-Head "从服务器拉取 ca.crt"
    if (-not (Test-Path -LiteralPath $SshKey)) { Write-Err2 "SSH 私钥不存在：$SshKey"; exit 1 }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CaFile) | Out-Null
    $remote = 'root@203.0.113.10' -replace '^root@', 'root@'
    if ($SshHost -ne 'root@203.0.113.10') { $remote = $SshHost }
    & scp.exe -i $SshKey -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL `
        "${remote}:/opt/agent-os/app-src/deploy/tls/out/ca.crt" $CaFile 2>&1 | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $CaFile)) {
        Write-Err2 "scp 拉取失败（服务器上是否已执行 deploy/tls/gen_ca_and_cert.sh？）"
        exit 1
    }
    Write-Ok "已拉取到 $CaFile"
}

# ---------------------------------------------------------------- 证书信息
if (-not (Test-Path -LiteralPath $CaFile)) {
    Write-Err2 "找不到 CA 证书：$CaFile"
    Write-Host "   可在服务器生成后拷回本机：" -ForegroundColor Gray
    Write-Host "   scp -i `"$SshKey`" root@203.0.113.10:/opt/agent-os/app-src/deploy/tls/out/ca.crt deploy\tls\out\ca.crt" -ForegroundColor Gray
    Write-Host "   或直接加 -Server 参数让本脚本自动拉取。" -ForegroundColor Gray
    exit 1
}

$cert      = Get-CertFromFile $CaFile
$sha256    = Get-Sha256Fp $cert
$thumb     = Get-Sha1Thumb $cert
$store     = 'Cert:\CurrentUser\Root'
$alreadyIn = Test-Installed $thumb

# 判断是否 CA 证书：直接解析 basicConstraints(2.5.29.19) 的 DER。
# 不用 X509Extension.Format()——它走 CryptFormatObject，输出随系统语言本地化，
# 在中文 Windows 上匹配 "CA=TRUE" 会失败（曾误报「不是根 CA」）。
function Test-IsCaCert($Cert) {
    $bc = $Cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' } | Select-Object -First 1
    if (-not $bc) { return $false }
    $raw = $bc.RawData
    # SEQUENCE { BOOLEAN TRUE ... } => 30 03 01 01 FF
    return ($raw.Length -ge 5 -and $raw[0] -eq 0x30 -and $raw[2] -eq 0x01 -and
            $raw[3] -eq 0x01 -and $raw[4] -eq 0xFF)
}
$isCaCert = Test-IsCaCert $cert

Write-Head "CA 证书信息（$CaFile）"
Write-Host "   Subject        : $($cert.Subject)"
Write-Host "   Issuer         : $($cert.Issuer)"
Write-Host "   NotBefore      : $($cert.NotBefore.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')) UTC"
Write-Host "   NotAfter       : $($cert.NotAfter.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')) UTC"
Write-Host "   SHA256 指纹    : $sha256"
Write-Host "   SHA1 指纹      : $($thumb -replace '(..)(?=.)', '$1:')"
Write-Host "   是 CA 证书     : $isCaCert"

if (-not $isCaCert) { Write-Warn2 "这张证书的 basicConstraints 里没有 CA:TRUE，可能不是根 CA。" }
if ($cert.Subject -ne $cert.Issuer) { Write-Warn2 "Subject 与 Issuer 不同，这不是自签根 CA。" }

# ---------------------------------------------------------------- 卸载
if ($Remove) {
    Write-Head "从 $store 卸载"
    if (-not $alreadyIn) {
        Write-Ok "当前用户根存储中本来就没有这张证书，无需卸载"
        exit 0
    }
    # certutil -delstore 用 SHA1 指纹定位证书
    $out = & certutil.exe -delstore -user Root $thumb 2>&1
    $out | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Err2 "certutil -delstore 失败（退出码 $LASTEXITCODE）"
        exit 1
    }
    if (Test-Installed $thumb) { Write-Err2 "删除后仍能在存储中找到该证书"; exit 1 }
    Write-Ok "已卸载（指纹 $sha256）"
}
else {
    # ------------------------------------------------------------ 安装
    Write-Head "安装到 $store（仅当前用户，通常无需管理员）"
    if ($alreadyIn) {
        Write-Ok "已存在同指纹证书，跳过安装（幂等）"
    }
    else {
        $out = & certutil.exe -addstore -user Root $CaFile 2>&1
        $out | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Err2 "certutil -addstore 失败（退出码 $LASTEXITCODE）——很可能被企业策略拦截。"
            Write-Host ""
            Write-Host "   可行的回退顺序（详见 docs/design/TLS.md「失败回退」）：" -ForegroundColor Yellow
            Write-Host "     1) 以管理员运行本脚本，改商店级存储：certutil -addstore Root <ca.crt>（需 IT 批准）" -ForegroundColor Yellow
            Write-Host "     2) 组策略推送：计算机配置 → Windows 设置 → 安全设置 → 公钥策略 → 受信任的根证书颁发机构" -ForegroundColor Yellow
            Write-Host "     3) 客户端保留 --ignore-certificate-errors（安全性回到改造前，仅作临时可用）" -ForegroundColor Yellow
            Write-Host "     4) 若将来拿到域名：改用 Let's Encrypt/acme.sh，彻底不需要本地 CA" -ForegroundColor Yellow
            exit 1
        }
        if (-not (Test-Installed $thumb)) {
            Write-Err2 "certutil 返回成功，但在 CurrentUser\Root 中找不到该证书（可能被策略重定向）"
            exit 1
        }
        Write-Ok "已安装（指纹 $sha256）"
    }
}

# ---------------------------------------------------------------- 验证
function Invoke-CurlCheck {
    $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 25 $Url
    return @{ Code = $code; Exit = $LASTEXITCODE }
}

if ($Verify) {
    Write-Head "curl.exe 验证（不带 -k，走 Windows 证书存储）"
    $r = Invoke-CurlCheck
    if ($r.Exit -ne 0) {
        Write-Warn2 "第一次失败（curl 退出码 $($r.Exit)），重试一次（刚 reload 时可能命中旧 worker）"
        Start-Sleep -Seconds 1
        $r = Invoke-CurlCheck
    }
    if ($r.Exit -ne 0) {
        Write-Err2 "curl 仍然失败（退出码 $($r.Exit)）"
        switch ($r.Exit) {
            35 {
                Write-Host "   退出码 35 = Schannel 吊销检查失败（CRYPT_E_NO_REVOCATION_CHECK）。" -ForegroundColor Yellow
                Write-Host "   说明服务器证书缺 CRL 分发点或 CRL 取不到：" -ForegroundColor Yellow
                Write-Host "     curl.exe -sv $Url   # 看 schannel 报错" -ForegroundColor Gray
                Write-Host "     curl.exe -s -o NUL -w `"%{http_code}`" https://203.0.113.10/agentos-ca.crl   # 应 200" -ForegroundColor Gray
                Write-Host "     服务器上重跑 deploy/tls/gen_ca_and_cert.sh（会重签证书 + 刷新 CRL）" -ForegroundColor Yellow
            }
            60 {
                Write-Host "   退出码 60 = 证书不受信任：CA 没装进当前用户的根存储，或装错了用户。" -ForegroundColor Yellow
            }
            default {
                Write-Host "   排查：curl.exe -v $Url" -ForegroundColor Gray
            }
        }
        exit 1
    }
    Write-Host "   $Url -> HTTP $($r.Code)"
    if ($r.Code -eq '200') { Write-Ok "校验通过：不再需要 --ignore-certificate-errors" }
    else { Write-Warn2 "证书校验通过了，但状态码不是 200（$($r.Code)）" }
}

Write-Host ""
Write-Host "常用命令：" -ForegroundColor Gray
Write-Host "  验证：curl.exe -s -o NUL -w `"%{http_code}`" $Url" -ForegroundColor Gray
Write-Host "  查看存储：certutil -user -store Root | findstr /i AgentOS" -ForegroundColor Gray
Write-Host "  卸载：powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Remove" -ForegroundColor Gray
exit 0
