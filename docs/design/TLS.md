# TLS 证书链改造：本地 CA + 真实信任链

> 状态：已上线并验证通过（2026-10-28）
> 关联任务：安心债 1/2 —— 用真实证书链替换「自签证书 + `--ignore-certificate-errors`」
> 交付物：`deploy/tls/gen_ca_and_cert.sh`、`deploy/tls/nginx-agentos.conf`、
> `deploy/tls/verify_tls.sh`、`scripts/install_ca_windows.ps1`、本文档

---

## 1. 结论摘要

| 项目 | 改造前 | 改造后 |
|---|---|---|
| 证书 | 自签单证书（`CN=203.0.113.10`，自己签自己） | 2 级链：`AgentOS Local Root CA` → 服务器证书 |
| 证书链 | 1 级（自签，任何客户端都不认） | 2 级（叶 + 根，`verify return code = 0`） |
| SAN | 只有 `IP:203.0.113.10` | `IP:203.0.113.10`、`127.0.0.1`、`::1`、`DNS:localhost` |
| 吊销 | 无 | 有 CRL，`https://203.0.113.10/agentos-ca.crl`（`application/pkix-crl`） |
| Windows 客户端 | `curl` 退出码 60，WebView2 必须加 `--ignore-certificate-errors` | 装 CA 后 `curl.exe` 不带 `-k` 返回 200，可删掉该参数 |
| TLS 策略 | TLS1.2/1.3，无 HSTS | TLS1.2/1.3、关闭会话票据、HSTS `max-age=15552000` |

验收结果（实测，见 §11 附录）：

1. ✅ 服务器 `nginx -t` 通过，`systemctl reload nginx` 后 `openssl s_client` 显示 2 级链、SAN 含 `IP:203.0.113.10`、`Verify return code: 0 (ok)`
2. ✅ 本机 Windows `curl.exe -s -o NUL -w "%{http_code}" https://203.0.113.10/v1/health` → `200`（不带 `-k`）
3. ✅ CA / 服务器证书 / CRL 的 SHA256 指纹已记录（§4）

---

## 2. 为什么不用 Let's Encrypt

四条独立理由，任意一条都足以否掉 ACME 方案：

1. **没有域名，只有 IP**。ACME 的 `HTTP-01` / `TLS-ALPN-01` 都要求申请者控制一个**域名**（签发主体必须是域名，IP 证书只有极少数商业 CA 提供，且要付费 + 人工审核）。
2. **服务器（203.0.113.10）的 80 端口对客户端不可达**
   —— 注意是**服务器侧入向**，不是客户端本机的 80 端口。实测证据（2026-10-28）：

   | 检查点 | 结果 |
   |---|---|
   | 从本机 `Test-NetConnection 203.0.113.10 -Port 80` | `False`（无响应；`curl` 直连 exit=28 超时） |
   | 从本机 `... -Port 443` / `-Port 22` | `True` / `True` |
   | 从本机到 `example.com:80`、`www.baidu.com:80` | `True`（本机出站 80 正常，排除客户端侧问题） |
   | 服务器上 `ss -ltn` | nginx 在监听 `0.0.0.0:80`、`[::]:80` |
   | 服务器上 `curl http://127.0.0.1/` | `301 → https://127.0.0.1/`（服务器自己访问 80 完全正常） |
   | 服务器上 `iptables`/`nft` | `YJ-FIREWALL-INPUT` 链里**没有**针对 80 的规则 |

   结论：应用层与主机防火墙都没拦，是**云厂商安全组**未放通 80（SYN 被静默丢弃），只放通了 443/22 等。
   因此任何客户端（浏览器、WebView2、Windows 的 CRL 抓取器）都取不到 `http://203.0.113.10/...`；
   即使用 `HTTP-01`，验证请求也进不来；`TLS-ALPN-01` 又要独占 443，与 nginx 冲突（需要前置 SNI 分流）。
3. **机器是单点内网式部署**，客户端只来自少数几台内部 Windows（连的是固定 IP），不存在「任意公网用户访问」的场景，
   公共信任链带来的收益（免装根证书）在这里可以接受用「一次安装本地 CA」替代。
4. **可运维性**：ACME 证书 90 天一轮，需要 cron + 续期失败告警；本地 CA 的叶证书 397 天、CA 10 年，
   续期就是重跑一次幂等脚本。

> 如果将来拿到域名并放开了 80/443，最佳路径是 `acme.sh --standalone -d <域名>`（HTTP-01）或 DNS-01 通配符，
> 那时可以彻底删掉本地 CA 这一层（见 §9 回退方案第 4 条）。

---

## 3. 方案结构

```
                     ┌──────────────────────────────────────────┐
                     │  AgentOS Local Root CA (自签, RSA 4096)    │
                     │  CN=AgentOS Local Root CA (203.0.113.10)│
                     │  10 年，仅签名，私钥 ca.key 权限 600       │
                     └───────────────┬──────────────────────────┘
                                     │ 签发
                     ┌───────────────▼──────────────────────────┐
                     │  服务器证书 (RSA 2048, 397 天)             │
                     │  CN=203.0.113.10                       │
                     │  SAN=IP:203.0.113.10,127.0.0.1,::1,    │
                     │      DNS:localhost                        │
                     │  CDP=https://203.0.113.10/agentos-ca.crl│
                     └───────────────┬──────────────────────────┘
                                     │ nginx 下发 fullchain.crt（叶+根）
   Windows 客户端 ──────────────────┘
     ① 根证书装进 CurrentUser\Root（一次性）
     ② 建链时按 CDP 拉 CRL 做吊销检查（nginx 443 提供）
```

三个关键点，缺一个客户端就报错：

1. **根 CA 必须在客户端受信任存储里**，否则 `curl` 退出码 60（证书不受信任）。
2. **nginx 必须下发完整链**（`fullchain.crt` = 叶 + 根），否则 `s_client` 只能看到 1 级。
3. **叶证书必须带 CDP，且 CRL 可访问**。
   这条最容易踩：Windows 的 Schannel（`curl.exe` / Edge / WebView2 的底层）建链时**强制做吊销检查**，
   如果证书里没有 CRL 分发点，会直接返回 `CRYPT_E_NO_REVOCATION_CHECK (0x80092012)`，
   `curl` 退出码 35、HTTP 状态 000。**本轮改造实测踩到了这个坑**（详见 §8 排查表）。

> 注意：CDP 用的是 **https**，不是 http —— 因为**服务器侧**的 80 端口入向被云安全组挡掉了（§2 第 2 条有完整证据），
> 客户端（含 Windows 拉 CRL 的那个组件）根本连不上 `http://203.0.113.10/`。
> 443 已验证可达，所以 CDP 走 `https://203.0.113.10/agentos-ca.crl`。
> 这与「客户端本机有没有开 80」无关——本机的 80 端口在这个方案里完全不参与。

---

## 4. 指纹（用于核对，务必逐字比对）

| 对象 | SHA256 指纹 | SHA1 指纹 | 有效期 |
|---|---|---|---|
| 根 CA `ca.crt` | `30:C5:5E:71:3D:77:3C:6C:96:AC:71:FB:F6:02:77:90:A0:F1:8B:30:29:A3:DB:19:07:E0:E2:15:84:73:63:47` | `D0:0D:B5:DC:18:61:46:41:59:00:5B:83:CE:78:14:0F:2C:68:AB:76` | 2026-10-27 → **2036-10-24** |
| 服务器证书 `server.crt` | `55:A3:C0:AC:1F:B4:39:35:1C:F7:DB:F5:FC:E9:02:F5:72:C6:23:0E:A2:CD:FE:54:CA:37:C8:8C:0A:CA:48:31` | `14:1E:0E:A2:7C:80:83:D2:50:C5:96:33:A4:97:82:9B:AD:17:2C:06` | 2026-10-27 → **2027-11-28**（397 天） |
| CRL（DER）`agentos-ca.crl` | `D1:AB:4F:7F:44:AC:91:EB:D8:1B:23:A3:9D:CB:35:81:64:28:07:CF:8A:41:91:20:E0:78:90:EA:60:71:B1:59` | — | 2026-10-27 → **2027-10-27**（365 天） |

> ⚠️ CA 与服务器证书的指纹是**稳定标识**，装完 CA 后应逐字比对。
> CRL 的指纹**每次重跑脚本都会变**（重新签名 + 刷新 `thisUpdate`），上表只是本次留档值；
> 核对 CRL 请用「条目内容 + 有效期」，不要用指纹。

* 服务器证书序列号：`1001`；CA 序列号：`6C2A652B40A94384765191458E1F61E1D7EDD206`
* `ca.crt` 的 SHA256 指纹同时被 `scripts/install_ca_windows.ps1` 打印，装完必须和上表一致。
* 完整清单随 `gen_ca_and_cert.sh` 每次运行刷新到 `deploy/tls/out/tls-manifest.txt`。

核对命令：

```bash
# 服务器
cd /opt/agent-os/app-src && bash deploy/tls/gen_ca_and_cert.sh --print
```

```powershell
# Windows
certutil -hashfile deploy\tls\out\ca.crt SHA256
Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like '*AgentOS*' |
    Select-Object Subject, Thumbprint, NotAfter
```

---

## 5. 文件与路径

| 路径 | 作用 | 是否入库 |
|---|---|---|
| `deploy/tls/gen_ca_and_cert.sh` | 生成/续期/安装 CA + 叶证书 + CRL（幂等） | ✅ 入库 |
| `deploy/tls/nginx-agentos.conf` | 完整 `server` 块（443 http2 + HSTS + 反代 + CRL 发布） | ✅ 入库 |
| `deploy/tls/verify_tls.sh` | 真机验证脚本（链、SAN、CRL、反代、协议） | ✅ 入库 |
| `scripts/install_ca_windows.ps1` | 把 CA 装进/移出 `CurrentUser\Root`（带 `-Verify`） | ✅ 入库 |
| `deploy/tls/out/ca.key` | **CA 私钥**，权限 600 | ❌ `.gitignore` |
| `deploy/tls/out/server.key` | 服务器私钥，权限 600 | ❌ `.gitignore` |
| `deploy/tls/out/ca.crt` | 分发给客户端安装的根证书 | ❌ `.gitignore`（从服务器取） |
| `deploy/tls/out/ca/` | `openssl ca` 数据库（`index.txt`/`serial`/`crlnumber`） | ❌ `.gitignore` |

服务器侧落地（`gen_ca_and_cert.sh` 自动安装）：

```
/opt/agent-os/etc/ca.crt            # 根证书（供人工分发/核对）
/opt/agent-os/etc/tls.crt           # 叶证书（单张）
/opt/agent-os/etc/fullchain.crt     # 叶 + 根（nginx ssl_certificate 用这个）
/opt/agent-os/etc/tls.key           # 叶私钥 600
/opt/agent-os/etc/agentos-ca.crl    # CRL（DER），nginx 在 CDP 地址发布
```

> **CA 私钥永不进 `/opt/agent-os/etc/`、永不进仓库**。它只存在于
> `/opt/agent-os/app-src/deploy/tls/out/ca.key`（600），一旦泄露等于整套 PKI 被攻破。

---

## 6. 签发 / 续期 / 轮换

### 6.1 日常：幂等（推荐，改完就跑）

```bash
ssh -i ~/.ssh/oa_agent_ed25519.pem root@203.0.113.10
cd /opt/agent-os/app-src
bash deploy/tls/gen_ca_and_cert.sh          # 缺什么补什么，然后把证书装到 /opt/agent-os/etc
nginx -t && systemctl reload nginx
```

脚本的「复用 vs 重签」判据（任一不满足才重签，因此重复执行不会换证书）：

| 对象 | 复用条件 |
|---|---|
| 根 CA | `ca.crt`/`ca.key` 存在、公私钥匹配、带 `CA:TRUE`、剩余有效期 > 30 天 |
| 服务器证书 | 公私钥匹配、能被当前 CA 验过、剩余有效期 > 30 天、SAN 覆盖全部要求项、CDP 等于当前配置 |
| CRL | 每次都重新生成（幂等且无副作用，保证 CRL 不过期） |

实测（对同一目录连跑两次，指纹不变）：

```
== run A（应全部复用）==
[tls] 根 CA 复用（剩余有效期 Oct 24 16:15:44 2036 GMT）
[tls] 服务器证书复用：O=AgentOS, OU=Local PKI, CN=203.0.113.10（到期 Nov 28 16:31:53 2027 GMT）
[tls] fullchain.crt 已生成（2 级链）
[tls] CRL 已刷新（已吊销 0 张，到期 Oct 27 16:31:53 2027 GMT）
== 指纹对比 ==
before: sha256 Fingerprint=AB:6F:A2:...:49
runA  : sha256 Fingerprint=AB:6F:A2:...:49
runB  : sha256 Fingerprint=AB:6F:A2:...:49
IDEMPOTENT_YES
```

### 6.2 只续期叶证书（CA 不动，客户端无需重装 CA）

```bash
bash deploy/tls/gen_ca_and_cert.sh --force
nginx -t && systemctl reload nginx
```

`--force` 会先把旧证书备份成 `server.crt.prev`。叶证书每年一换，根 CA 不受影响，**客户端不需要重装**。

### 6.3 重建整条链（CA 换新，所有客户端必须重装 CA）

```bash
bash deploy/tls/gen_ca_and_cert.sh --new-ca      # 旧材料自动归档到 out/archive-ca-<时间戳>/
nginx -t && systemctl reload nginx
# 然后把新的 ca.crt 发到每台客户端，重新执行 scripts/install_ca_windows.ps1
```

仅在 CA 私钥疑似泄露、或 CA 即将过期时使用。

### 6.4 参数

```bash
bash deploy/tls/gen_ca_and_cert.sh --help
--out-dir DIR      输出目录（默认 deploy/tls/out）
--install-dir DIR  安装目录（默认 /opt/agent-os/etc）
--install/--no-install
--force            强制重签叶证书
--new-ca           重建根 CA（连带重签叶证书）
--days N           叶证书天数（默认 397，务必 < 398）
--ca-days N        CA 天数（默认 3650）
--crl-days N       CRL 天数（默认 365）
--server-ips "..." --server-dns "..."   SAN 内容
--cdp URI          CRL 分发点（默认 https://203.0.113.10/agentos-ca.crl）
--revoke           吊销当前叶证书 + 刷新 CRL
--gen-crl          只刷新 CRL
--print            只打印指纹/有效期
```

---

## 7. 吊销

### 7.1 步骤

```bash
cd /opt/agent-os/app-src
bash deploy/tls/gen_ca_and_cert.sh --revoke    # 写 index.txt + 刷新 CRL + 安装到 /opt/agent-os/etc
# 立刻换一张新证书，避免服务中断
bash deploy/tls/gen_ca_and_cert.sh --force
nginx -t && systemctl reload nginx
```

`--revoke` 用的是 `openssl ca -revoke -crl_reason keyCompromise`，同时会把新 CRL 发布出去，
nginx 不需要 reload（文件内容变了，但 `location` 是每次请求读文件）。

### 7.2 已在真机做过一次「不带破坏性」的吊销演练

用一次性证书（`CN=revocation-drill`）走完整流程，不动线上证书：

```
== 1. 签发一张一次性证书（CN=revocation-drill）==
drill serial = 1002

== 2. 之前 CRL 的条目数 ==
0

== 3. 吊销它并刷新 CRL（--gen-crl，同时会安装到 /opt/agent-os/etc）==
Revoking Certificate 1002.
Database updated
[tls] CRL 已刷新（已吊销 1 张，到期 Oct 27 16:38:25 2027 GMT）
[tls] 已安装到 /opt/agent-os/etc：ca.crt tls.crt fullchain.crt tls.key agentos-ca.crl

== 4. 新 CRL 内容 ==
lastUpdate=Oct 27 16:38:25 2026 GMT
nextUpdate=Oct 27 16:38:25 2027 GMT
Revoked Certificates:
    Serial Number: 1002
        Revocation Date: Oct 27 16:38:25 2026 GMT
        CRL entry extensions:
            X509v3 CRL Reason Code:
                Key Compromise

== 5. nginx 发布出去的 CRL 是否包含该序列号 ==
DRILL_SERIAL_PRESENT_IN_SERVED_CRL=YES

== 6. 线上证书序列号（应不在 CRL 里）==
live serial = 1001
LIVE_REVOKED=NO(good)

== 7. openssl verify 是否受影响（线上链应仍 OK）==
out/server.crt: OK
== 8. 本机 https 仍 200 ==
200
```

### 7.3 注意事项

* Windows 对 CRL 有缓存（nginx 给了 `Cache-Control: max-age=3600`），吊销后最多 1 小时才在所有客户端生效。
  需要立刻生效时，在客户端清缓存：`certutil -URLcache * delete`（部分企业策略禁用该命令，见 §8）。
* CRL 有效期 365 天。**每年至少重跑一次脚本**（或定期 `--gen-crl`），否则 CRL 过期后
  Schannel 会按「吊销状态未知」处理，可能重新出现退出码 35。
* 每台服务器只签发一张叶证书，吊销只有「私钥泄露 / 证书错发」两种场景，正常运维用不到。

---

## 8. Windows 客户端：安装 / 验证 / 卸载 / 排查

### 8.1 安装（当前用户存储，通常不需要管理员）

```powershell
cd C:\oaagent-m0
powershell -ExecutionPolicy Bypass -File scripts\install_ca_windows.ps1 -Verify
# 本机没有 ca.crt 时，直接从服务器拉：
powershell -ExecutionPolicy Bypass -File scripts\install_ca_windows.ps1 -Server -Verify
```

实测输出：

```
== CA 证书信息（C:\oaagent-m0\deploy\tls\out\ca.crt） ==
   Subject        : CN=AgentOS Local Root CA (203.0.113.10), OU=Local PKI, O=AgentOS
   Issuer         : CN=AgentOS Local Root CA (203.0.113.10), OU=Local PKI, O=AgentOS
   NotBefore      : 2026-10-27 16:15:44 UTC
   NotAfter       : 2036-10-24 16:15:44 UTC
   SHA256 指纹    : 30:C5:5E:71:3D:77:3C:6C:96:AC:71:FB:F6:02:77:90:A0:F1:8B:30:29:A3:DB:19:07:E0:E2:15:84:73:63:47
   SHA1 指纹      : D0:0D:B5:DC:18:61:46:41:59:00:5B:83:CE:78:14:0F:2C:68:AB:76
   是 CA 证书     : True

== 安装到 Cert:\CurrentUser\Root（仅当前用户，通常无需管理员） ==
   OK   已安装（指纹 30:C5:5E:71:...:63:47）

== curl.exe 验证（不带 -k，走 Windows 证书存储） ==
   https://203.0.113.10/v1/health -> HTTP 200
   OK   校验通过：不再需要 --ignore-certificate-errors
```

> `certutil -addstore` 自身的中文提示按 GBK 输出，在 UTF-8 控制台里可能显示为乱码，不影响结果。

### 8.2 验证（手工，三行命令）

```powershell
# 1) 核心验收：不带 -k 必须是 200
curl.exe -s -o NUL -w "%{http_code}`n" https://203.0.113.10/v1/health

# 2) 看证书链和是否走了 Schannel 吊销检查
curl.exe -sv --max-time 20 -o NUL https://203.0.113.10/v1/health 2>&1 | Select-String 'schannel|revocation|TLS'

# 3) WebView2/Edge 的实际效果（.NET 默认不查吊销，能过说明链本身没问题）
(Invoke-WebRequest https://203.0.113.10/v1/health -UseBasicParsing).StatusCode
```

实测：

```
#1  -> 200   exit=0
#3  -> 200   body: {"ok":true,"service":"agent-os-v1",...,"subscribers":1}
根存储里有：
Subject    : CN=AgentOS Local Root CA (203.0.113.10), OU=Local PKI, O=AgentOS
Thumbprint : D00DB5DC1861464159005B83CE78140F2C68AB76
NotAfter   : 2036/10/25 0:15:44
```

### 8.2.1 用 Edge（与 WebView2 同引擎）验证「去掉 `--ignore-certificate-errors` 后能正常加载」

这一步直接模拟客户端删掉参数后的行为——Chromium 有自己的证书校验器（读 Windows 根存储）：
不加任何忽略证书的参数，headless 加载受保护端点：

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
& $edge --headless=new --disable-gpu --virtual-time-budget=8000 --dump-dom `
        https://203.0.113.10/v1/health
```

实测（2026-10-28 00:48）：

```html
<html><head><meta name="color-scheme" content="light dark"><meta charset="utf-8"></head>
<body><pre>{"ok":true,"service":"agent-os-v1","version":"1.0.0",
"caps":["screen","local","voice","processes"],"cursor":493,"subscribers":1}</pre>
<div class="json-formatter-container"></div></body></html>
```

页面渲染出真实 JSON（不是 `ERR_CERT_AUTHORITY_INVALID` 错误页），
说明 **Chromium/WebView2 侧校验通过，可以安全删掉 `--ignore-certificate-errors`**。
（Edge 进程的 stderr 里有 3 条与证书无关的日志：VBS enclave、QQ 浏览器导入器、任务管理器 fallback，可忽略。）

### 8.3 卸载（回滚用）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_ca_windows.ps1 -Remove
```

实测卸载后立刻恢复为「不受信任」：

```
   OK   已卸载（指纹 30:C5:5E:71:...:63:47）
--- 卸载后 curl ---
http=000
exit=60
```

### 8.4 排查表（都实测过）

| 现象 | 原因 | 处理 |
|---|---|---|
| `curl` 退出码 **60**、HTTP `000` | CA 没装 / 装错用户（装进了管理员用户而不是运行程序的用户） | `scripts\install_ca_windows.ps1 -Verify`；确认 `Get-ChildItem Cert:\CurrentUser\Root` 里有 `AgentOS` |
| `curl` 退出码 **35**、`schannel: ... CRYPT_E_NO_REVOCATION_CHECK` | 叶证书缺 CDP，或 CRL 取不到（例如把 CDP 写成 `http://`——服务器 80 入向被云安全组挡掉，取不到） | 服务器重跑 `gen_ca_and_cert.sh`；`curl.exe -s -o NUL -w "%{http_code}" https://203.0.113.10/agentos-ca.crl` 应返回 200 |
| `curl --ssl-no-revoke` 能过，不加就 35 | 就是吊销链问题，等价于上一条 | 同上（`--ssl-no-revoke` 只当临时定位手段，不要长期用） |
| `certutil` 报「Access has been restricted by your Administrator by policy rule」 | 企业策略（AppLocker/WDAC）限制了 certutil 子命令 | 见 §9；`-addstore` 实测可用，`-URLcache` 会被拦 |
| reload 后第一次请求失败、重试就成功 | nginx reload 期间旧 worker 还在用旧证书（旧证书没有 CDP） | 客户端脚本已内置「失败重试一次」；运维上把「重签证书」和「验证」隔几秒 |

---

## 9. 失败回退：企业策略禁止安装根证书

**本机实测结论**：`certutil -addstore -user Root` **没有被策略拦截**，安装成功（见 §8.1）。
但如果将来在受管控的机器上遇到禁止安装根证书（`certutil` 返回拒绝 / 组策略锁定「受信任的根证书颁发机构」），
按下面顺序回退，**从安全到可用**排列：

1. **换商店级存储（推荐，需 IT 一次性批准）**
   以管理员执行 `certutil -addstore Root deploy\tls\out\ca.crt`，装到「本地计算机」而不是当前用户。
   对本机所有用户和 WebView2 子进程都生效，且不依赖每个用户手工装。
2. **用组策略 / Intune 推送根证书（正规做法）**
   组策略：`计算机配置 → Windows 设置 → 安全设置 → 公钥策略 → 受信任的根证书颁发机构 → 导入`；
   或 Intune「信任的证书」配置文件。这样客户端零手工操作，且策略本身就是 IT 批准的凭据。
3. **不改 Windows 信任，改客户端信任锚（应急）**
   Tauri/WebView2 无法单独指定 CA bundle，但可以让桌面端把服务器地址的证书校验交给一个**自带 CA 的本地代理**：
   在客户端起一个仅监听 `127.0.0.1` 的反代（如 `stunnel`/`caddy`），用它把 `https://127.0.0.1:8443` 转成上游，
   WebView2 只访问本地回环地址并用私有 CA（同样要装根证书，**不解决问题**）——因此仅在「有域名 + 公共证书」时才成立。
   > 结论：这条在无域名场景下不可行，列出来是为了避免有人绕远路，**请直接跳到第 4 或第 5 条**。
4. **临时保留 `--ignore-certificate-errors`（可用性优先，安全性退回改造前）**
   即**不改** `desktop/src-tauri/src/main.rs` 里的参数。此时 TLS 仍然加密，但**失去身份认证**，
   存在中间人风险。必须同时做到：①在内网/VPN 内使用；②在 `docs/design/PROGRESS.md` 里登记为待还技术债；
   ③一旦 CA 能在客户端安装，立刻删参数（见 §12）。
5. **根治：拿到域名 + 公共证书**
   有域名后走 `acme.sh` / `certbot`（HTTP-01 或 DNS-01），客户端开箱即信，彻底不需要本地 CA。
   这是唯一能同时满足「零客户端配置」和「强身份认证」的方案。

> 无论走哪条，都**不要**改成 `--ignore-certificate-errors-spki-list` 之类的「半信任」参数：
> 它在 IP 场景下无法稳定工作，且同样等于放弃吊销与主机名校验。

---

## 10. 已知限制与运维待办

1. **叶证书 397 天**：2027-11-28 到期。到期前 30 天脚本会自动重签，但**需要有人真的去跑**。
   建议加 cron（每月 1 号）：`0 4 1 * * cd /opt/agent-os/app-src && bash deploy/tls/gen_ca_and_cert.sh && nginx -t && systemctl reload nginx`
2. **CRL 365 天**：同上时间点刷新；过期会导致 Schannel 重新报退出码 35。
3. **根 CA 10 年**：2036-10-24 到期，届时必须换 CA 并给所有客户端重装（§6.3）。
4. **服务器的 80 端口入向不通**（云安全组未放通）：外部访问只能走 443。旧的 `http → https` 301 跳转对外实际不可用（不影响 443）。
   若要恢复，去云控制台安全组放通 80/tcp 即可，但**本方案不依赖它**（CDP 已固定为 https）。
5. **本机已安装该 CA**（当前用户），卸载命令见 §8.3；这是本机 curl 能 200 的前提。
6. **CA 私钥无口令保护**（`-nodes`），仅靠文件权限 600 + 只存在于服务器 `deploy/tls/out/`。
   如需更高保障，可加 `-aes256` 并用 systemd 的 `LoadCredential`/`pass` 管理口令——当前规模不必要。

---

## 11. 附录 A：服务器真机验证输出（`bash deploy/tls/verify_tls.sh`）

```
----------------------------------------------------------------------
### 0. 磁盘上的证书与 CA 指纹
----------------------------------------------------------------------
  subject=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
  issuer=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
  notBefore=Oct 27 16:15:44 2026 GMT
  notAfter=Oct 24 16:15:44 2036 GMT
  sha256 Fingerprint=30:C5:5E:71:3D:77:3C:6C:96:AC:71:FB:F6:02:77:90:A0:F1:8B:30:29:A3:DB:19:07:E0:E2:15:84:73:63:47

  subject=O=AgentOS, OU=Local PKI, CN=203.0.113.10
  issuer=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
  notBefore=Oct 27 16:31:53 2026 GMT
  notAfter=Nov 28 16:31:53 2027 GMT
  sha256 Fingerprint=55:A3:C0:AC:1F:B4:39:35:1C:F7:DB:F5:FC:E9:02:F5:72:C6:23:0E:A2:CD:FE:54:CA:37:C8:8C:0A:CA:48:31
----------------------------------------------------------------------
### 1. nginx 实际提供的证书（openssl s_client -showcerts）
----------------------------------------------------------------------
  [PASS] s_client 连接成功：127.0.0.1:443（SNI=203.0.113.10）
--- 协议 / 套件 / verify ---
  New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
  Protocol: TLSv1.3
  Verify return code: 0 (ok)
  [PASS] verify return code = 0 (ok)
--- 证书链（s_client 展示）---
  Certificate chain
   0 s:O=AgentOS, OU=Local PKI, CN=203.0.113.10
     i:O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
     a:PKEY: RSA, 2048 (bit); sigalg: sha256WithRSAEncryption
     v:NotBefore: Oct 27 16:31:53 2026 GMT; NotAfter: Nov 28 16:31:53 2027 GMT
  实际下发的证书张数 = 2
  [PASS] 服务端下发完整链（2 张：叶 + 根）
----------------------------------------------------------------------
### 2. 逐张证书内容
----------------------------------------------------------------------
  --- 第 1 张 ---
      subject=O=AgentOS, OU=Local PKI, CN=203.0.113.10
      issuer=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
      serial=1001
      notBefore=Oct 27 16:31:53 2026 GMT
      notAfter=Nov 28 16:31:53 2027 GMT
  --- 第 2 张 ---
      subject=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
      issuer=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
      serial=6C2A652B40A94384765191458E1F61E1D7EDD206
      notBefore=Oct 27 16:15:44 2026 GMT
      notAfter=Oct 24 16:15:44 2036 GMT
  --- 叶证书扩展 ---
      X509v3 Basic Constraints: critical
          CA:FALSE
      X509v3 Key Usage: critical
          Digital Signature, Key Encipherment
      X509v3 Extended Key Usage:
          TLS Web Server Authentication
      X509v3 Subject Alternative Name:
          DNS:localhost, IP Address:203.0.113.10, IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1
  [PASS] SAN 含 IP:203.0.113.10
  [PASS] SAN 含 DNS:localhost
  [PASS] SAN 含 127.0.0.1
  [PASS] openssl verify -CAfile ca.crt 通过
  叶证书剩余有效期 = 396 天
  [PASS] 有效期 396 天 ≤ 398 天策略
----------------------------------------------------------------------
### 3. 反代与业务可用性（本机 curl，走 443）
----------------------------------------------------------------------
  /v1/health           -> HTTP 200
  [PASS] /v1/health = 200
  /                    -> HTTP 302 https://127.0.0.1/ui/
  [PASS] / = 302（跳 /ui/）
  /ui/                 -> HTTP 200
  [PASS] /ui/ = 200
  /nonexistent-check   -> HTTP 404
  /v1/health body:
    {"ok":true,"service":"agent-os-v1","version":"1.0.0","caps":["screen","local","voice","processes"],"cursor":493,"subscribers":1}
----------------------------------------------------------------------
### 4. HSTS 与协议版本
----------------------------------------------------------------------
  strict-transport-security: max-age=15552000
  [PASS] HSTS 已下发
  [PASS] tls1_2 可用
  [PASS] tls1_3 可用
  [PASS] tls1_1 被拒绝
----------------------------------------------------------------------
### 5. CRL 分发点（Windows/Schannel 吊销检查依赖）
----------------------------------------------------------------------
  [PASS] 叶证书含 CDP: https://203.0.113.10/agentos-ca.crl
  GET https://203.0.113.10/agentos-ca.crl -> HTTP 200  内容类型: application/pkix-crl  大小: 731 字节
  [PASS] CRL 可访问
    issuer=O=AgentOS, OU=Local PKI, CN=AgentOS Local Root CA (203.0.113.10)
    lastUpdate=Oct 27 16:38:25 2026 GMT
    nextUpdate=Oct 27 16:38:25 2027 GMT
  [PASS] CRL 为合法 DER
  CRL 里的吊销条目:
    Revoked Certificates:
        Serial Number: 1002
            Revocation Date: Oct 27 16:38:25 2026 GMT
            X509v3 CRL Reason Code:
                Key Compromise
----------------------------------------------------------------------
### 结果
----------------------------------------------------------------------
  全部通过（0 项失败）
```

`nginx -t` / reload 实测：

```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
nginx -t exit=0
reload OK
active
```

---

## 12. 附录 B：客户端要删的参数（**由 Lead 改，本任务未改**）

文件：`desktop/src-tauri/src/main.rs`，第 32–38 行现状：

```rust
// 自签证书阶段：内嵌 WebView 需要跳过证书校验（换成正式证书后可移除）
// AGENTOS_DEBUG_PORT=1 时才开 CDP，正常使用不暴露调试端口
.additional_browser_args(if std::env::var("AGENTOS_DEBUG_PORT").is_ok() {
    "--ignore-certificate-errors --remote-debugging-port=9333"
} else {
    "--ignore-certificate-errors"
});
```

建议改成「只在调试时才加参数」，正常启动完全不带 `--ignore-certificate-errors`：

```rust
let mut builder = WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()))
    .title("AgentOS")
    .inner_size(1320.0, 860.0);
// 证书由本地 CA 提供，客户端装根证书后正常校验；CDP 仅在明确开启时暴露
if std::env::var("AGENTOS_DEBUG_PORT").is_ok() {
    builder = builder.additional_browser_args("--remote-debugging-port=9333");
}
```

注意三点：

1. 删参数的前提是**客户端已装根 CA**（`scripts\install_ca_windows.ps1`）。没装就会看到 `ERR_CERT_AUTHORITY_INVALID`。
   安装包里应把 `ca.crt` 一起带上，并在首启/安装时调用该脚本。
2. 第 32 行的注释「换成正式证书后可移除」现在是过期说明，一并删掉。
3. 删完后 `additional_browser_args` 在非调试路径下不再被调用；若不想留空分支，就按上面的写法把 builder 变成 `mut`。

---

## 13. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-28 | 建立本地 CA（10 年）+ 397 天叶证书（SAN 含 IP/localhost），nginx 改发 fullchain + HSTS；发布 CRL 并加 CDP（解决 Windows Schannel `CRYPT_E_NO_REVOCATION_CHECK`）；Windows 当前用户根存储安装 CA，`curl.exe` 不带 `-k` 返回 200；补齐幂等脚本、验证脚本与回退方案 |
