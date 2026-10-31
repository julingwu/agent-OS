#!/usr/bin/env bash
# =============================================================================
# verify_tls.sh —— 真机验证 AgentOS 的 TLS 证书链 / 协议 / 反代
#
# 在服务器上运行（读 out/ca.crt 做校验）：
#     bash deploy/tls/verify_tls.sh
# 指定其它证书或目标：
#     bash deploy/tls/verify_tls.sh --ca deploy/tls/out/ca.crt --host 127.0.0.1 --port 443
#
# 输出会被 docs/design/TLS.md 引用，因此保持稳定、可复制。
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA="$SCRIPT_DIR/out/ca.crt"
HOST="127.0.0.1"
PORT="443"
SNI="203.0.113.10"
FAIL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ca)   CA="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --sni)  SNI="$2"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

hr() { printf '%s\n' '----------------------------------------------------------------------'; }
sec() { hr; printf '### %s\n' "$*"; hr; }
ok()  { printf '  [PASS] %s\n' "$*"; }
bad() { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }

TARGET="${HOST}:${PORT}"
[ -f "$CA" ] || { echo "找不到 CA：$CA（先运行 gen_ca_and_cert.sh）" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/chain"

# ---------------------------------------------------------------------------
sec "0. 磁盘上的证书与 CA 指纹"
openssl x509 -in "$CA" -noout -subject -issuer -dates -fingerprint -sha256 | sed 's/^/  /'
echo
if [ -f "$SCRIPT_DIR/out/server.crt" ]; then
  openssl x509 -in "$SCRIPT_DIR/out/server.crt" -noout -subject -issuer -dates -fingerprint -sha256 | sed 's/^/  /'
fi

# ---------------------------------------------------------------------------
sec "1. nginx 实际提供的证书（openssl s_client -showcerts）"
if timeout 15 openssl s_client -connect "$TARGET" -servername "$SNI" -showcerts \
      -CAfile "$CA" < /dev/null > "$TMP/sclient.txt" 2>&1; then
  ok "s_client 连接成功：$TARGET（SNI=$SNI）"
else
  bad "s_client 连接失败：$TARGET"
  cat "$TMP/sclient.txt"
fi

echo "--- 协议 / 套件 / verify ---"
grep -E '^(New, |Protocol|Cipher)' "$TMP/sclient.txt" | sed 's/^/  /'
# s_client 在 3.x 里会把 Verify return code 重复打印，去重后只取最后一条
grep -E '^ *Verify return code' "$TMP/sclient.txt" | sed 's/^ *//' | sort -u | sed 's/^/  /'

VR="$(grep -E '^ *Verify return code' "$TMP/sclient.txt" | sed 's/^ *//' | cut -d: -f2- | xargs | tr ' ' '\n' \
      | grep -E '0|ok' | head -1)"
[ -z "$VR" ] && VR="（未获取到）"
if [ "$VR" = "0" ]; then VR="0 (ok)"; ok "verify return code = 0 (ok)"; else bad "verify return code = $VR"; fi

# 把 s_client 实际下发的每张证书拆成单独文件（只取 BEGIN..END 之间的内容）
awk -v dir="$TMP/chain" '
  /-----BEGIN CERTIFICATE-----/ { inblk=1; n++; f=sprintf("%s/cert-%02d.pem", dir, n) }
  inblk            { print >> f }
  /-----END CERTIFICATE-----/   { inblk=0; close(f) }
' "$TMP/sclient.txt"

echo "--- 证书链（s_client 展示）---"
sed -n '/Certificate chain/,/-----BEGIN CERTIFICATE-----/p' "$TMP/sclient.txt" \
  | sed '$d' | sed 's/^/  /'

SENT_N="$(ls -1 "$TMP"/chain/cert-*.pem 2>/dev/null | wc -l)"
echo "  实际下发的证书张数 = $SENT_N"
if [ "${SENT_N:-0}" -ge 2 ]; then ok "服务端下发完整链（$SENT_N 张：叶 + 根）"; else bad "服务端只下发 $SENT_N 张证书"; fi

# ---------------------------------------------------------------------------
sec "2. 逐张证书内容"
i=0
for f in "$TMP"/chain/cert-*.pem; do
  i=$((i+1))
  echo "  --- 第 $i 张 ---"
  openssl x509 -in "$f" -noout -subject -issuer -serial -dates 2>/dev/null | sed 's/^/      /'
done

SRV_PEM="$(ls -1 "$TMP"/chain/cert-*.pem 2>/dev/null | head -1)"
if [ -n "${SRV_PEM:-}" ] && [ -s "$SRV_PEM" ]; then
  echo "  --- 叶证书扩展 ---"
  openssl x509 -in "$SRV_PEM" -noout -ext subjectAltName,keyUsage,extendedKeyUsage,basicConstraints 2>/dev/null | sed 's/^/      /'
  SAN="$(openssl x509 -in "$SRV_PEM" -noout -ext subjectAltName 2>/dev/null | tr -d '\n')"
  case "$SAN" in *"IP Address:203.0.113.10"*) ok "SAN 含 IP:203.0.113.10" ;; *) bad "SAN 缺 203.0.113.10" ;; esac
  case "$SAN" in *"DNS:localhost"*)                ok "SAN 含 DNS:localhost" ;; *) bad "SAN 缺 localhost" ;; esac
  case "$SAN" in *"127.0.0.1"*)                    ok "SAN 含 127.0.0.1" ;; *) bad "SAN 缺 127.0.0.1" ;; esac
  if openssl verify -CAfile "$CA" "$SRV_PEM" >/dev/null 2>&1; then
    ok "openssl verify -CAfile ca.crt 通过"
  else
    bad "openssl verify -CAfile ca.crt 失败"
  fi
  # 叶证书 有效期 <= 398 天（Chrome/WebView2 对公共 CA 的策略，私有 CA 也保持安全）
  LEAF_END="$(date -d "$(openssl x509 -in "$SRV_PEM" -noout -enddate | cut -d= -f2)" +%s)"
  LEFT_DAYS=$(( (LEAF_END - $(date +%s)) / 86400 ))
  echo "  叶证书剩余有效期 = ${LEFT_DAYS} 天"
  [ "$LEFT_DAYS" -le 398 ] && ok "有效期 ${LEFT_DAYS} 天 ≤ 398 天策略" || bad "有效期超过 398 天"
else
  bad "无法从 s_client 输出中提取服务器证书"
fi

# ---------------------------------------------------------------------------
sec "3. 反代与业务可用性（本机 curl，走 443）"
for path in /v1/health / /ui/ /nonexistent-check; do
  read -r code redirect < <(curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' --max-time 15 \
      "https://${HOST}${path}" || echo "000 -")
  printf '  %-20s -> HTTP %s %s\n' "$path" "$code" "$redirect"
  case "$path" in
    /v1/health) [ "$code" = "200" ] && ok "/v1/health = 200" || bad "/v1/health = $code" ;;
    /)          [ "$code" = "302" ] && ok "/ = 302（跳 /ui/）" || bad "/ = $code（预期 302）" ;;
    /ui/)       [ "$code" = "200" ] && ok "/ui/ = 200" || bad "/ui/ = $code" ;;
  esac
done

echo "  /v1/health body:"
curl -sk --max-time 15 "https://${HOST}/v1/health" | head -c 400 | sed 's/^/    /'
echo

# ---------------------------------------------------------------------------
sec "4. HSTS 与协议版本"
HSTS="$(curl -sk -D - -o /dev/null --max-time 15 "https://${HOST}/v1/health" | tr -d '\r' | grep -i '^strict-transport-security')"
echo "  ${HSTS:-（无 Strict-Transport-Security 响应头）}"
[ -n "$HSTS" ] && ok "HSTS 已下发" || bad "缺少 HSTS"

for v in tls1_2 tls1_3; do
  if timeout 15 openssl s_client -connect "$TARGET" -servername "$SNI" -CAfile "$CA" -"$v" \
       < /dev/null >/dev/null 2>&1; then
    ok "$v 可用"
  else
    bad "$v 不可用"
  fi
done
if timeout 15 openssl s_client -connect "$TARGET" -servername "$SNI" -CAfile "$CA" -tls1_1 \
     < /dev/null >/dev/null 2>&1; then
  bad "tls1_1 仍被接受（应拒绝）"
else
  ok "tls1_1 被拒绝"
fi

# ---------------------------------------------------------------------------
sec "5. CRL 分发点（Windows/Schannel 吊销检查依赖）"
CDP_URI="https://203.0.113.10/agentos-ca.crl"
if [ -n "${SRV_PEM:-}" ] && openssl x509 -in "$SRV_PEM" -noout -ext crlDistributionPoints 2>/dev/null | grep -Fq "$CDP_URI"; then
  ok "叶证书含 CDP: $CDP_URI"
else
  bad "叶证书缺少 CDP $CDP_URI"
fi

read -r crl_code crl_type < <(curl -sk -o "$TMP/crl.der" -D "$TMP/crl.hdr" -w '%{http_code} %{content_type}\n' \
    --max-time 15 "https://${HOST}/agentos-ca.crl" || echo "000 -")
echo "  GET $CDP_URI -> HTTP $crl_code  内容类型: $crl_type  大小: $(wc -c < "$TMP/crl.der" 2>/dev/null || echo 0) 字节"
if [ "$crl_code" = "200" ]; then ok "CRL 可访问"; else bad "CRL 不可访问（$crl_code）"; fi
if openssl crl -inform DER -in "$TMP/crl.der" -noout -issuer -lastupdate -nextupdate 2>/dev/null | sed 's/^/    /'; then
  ok "CRL 为合法 DER"
else
  bad "CRL 不是合法 DER"
fi
echo "  CRL 里的吊销条目:"
openssl crl -inform DER -in "$TMP/crl.der" -noout -text 2>/dev/null \
  | awk '/Revoked Certificates/{f=1} f' | head -10 | sed 's/^/    /' || true

# ---------------------------------------------------------------------------
sec "结果"
if [ "$FAIL" -eq 0 ]; then
  echo "  全部通过（0 项失败）"
  exit 0
else
  echo "  失败项：$FAIL"
  exit 1
fi
