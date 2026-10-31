#!/usr/bin/env bash
# =============================================================================
# AgentOS 本地 CA + 服务器证书 生成 / 安装脚本（幂等，可重复执行）
#
# 用途：服务器只有 IP（203.0.113.10）、没有域名，无法走 Let's Encrypt，
#       因此自建一条真实的 2 级证书链：本地根 CA -> 服务器证书（SAN 含 IP）。
#       把 CA 装进客户端（Windows 当前用户「受信任的根证书颁发机构」）后，
#       浏览器 / WebView2 / curl 即可正常校验，不再需要 --ignore-certificate-errors。
#
# 用法（在服务器上执行）：
#   bash deploy/tls/gen_ca_and_cert.sh                 # 幂等：缺什么补什么，然后安装到 nginx
#   bash deploy/tls/gen_ca_and_cert.sh --force          # 强制重签服务器证书（CA 不动）
#   bash deploy/tls/gen_ca_and_cert.sh --force --new-ca # 连 CA 一起重建（所有客户端要重装 CA）
#   bash deploy/tls/gen_ca_and_cert.sh --print          # 只打印现有指纹/有效期
#   bash deploy/tls/gen_ca_and_cert.sh --revoke         # 吊销当前服务器证书（并刷新 CRL）
#   bash deploy/tls/gen_ca_and_cert.sh --gen-crl        # 只重新生成/刷新 CRL
#
# 关于 CRL（很重要）：
#   Windows 的 Schannel（curl.exe / Edge / WebView2 底层）在建链时会做吊销检查，
#   如果证书扩展里没有 CRL 分发点（CDP），会直接报
#   CRYPT_E_NO_REVOCATION_CHECK 而拒绝连接（curl 退出码 35）。
#   因此服务器证书带 CDP：https://203.0.113.10/agentos-ca.crl
#   nginx 在 443 上发布这张 DER 格式 CRL（80 端口对外不可达，故不能用 http）。
#   CRL 有效期 365 天，每次跑脚本都会刷新；--cdp 可改签名里的 CRL 地址。
#
# 输出：deploy/tls/out/
#   ca.crt  ca.key           根 CA（ca.key 权限 600，绝不入 git、绝不复制到 web 目录）
#   server.key server.csr server.crt   服务器证书（含 CDP）
#   fullchain.crt            服务器证书 + CA（nginx 用它，客户端才能看到 2 级链）
#   ca.crl.pem  agentos-ca.crl         吊销列表（PEM / DER，DER 由 nginx 发布）
#   tls-manifest.txt         SHA256 指纹、有效期、SAN、CRL 一览
#   ca/                      openssl ca 数据库（index.txt / serial / crlnumber）
#
# 安装：默认安装到 /opt/agent-os/etc/
#       （ca.crt tls.crt fullchain.crt tls.key agentos-ca.crl），
#       保持现有 nginx 配置的证书路径习惯；--no-install 可跳过。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
INSTALL_DIR="/opt/agent-os/etc"
DO_INSTALL="auto"          # auto=目录存在才装；yes；no
FORCE_LEAF=0
FORCE_CA=0
LEAF_DAYS=397              # < 398，对 Chrome/WebView2 的证书有效期策略最安全
CA_DAYS=3650               # 10 年
RENEW_BEFORE_DAYS=30       # 剩余有效期小于此天数时自动续签
SERVER_IPS_DEFAULT="203.0.113.10 127.0.0.1 ::1"
SERVER_DNS_DEFAULT="localhost"
SERVER_IPS="$SERVER_IPS_DEFAULT"
SERVER_DNS="$SERVER_DNS_DEFAULT"
ACTION="ensure"
CDP_URI="https://203.0.113.10/agentos-ca.crl"   # 证书里的 CRL 分发点（必须客户端可达）
CRL_DAYS=365
CRL_DER=""                                          # 见下方赋值（依赖 OUT_DIR）

CA_CN="AgentOS Local Root CA (203.0.113.10)"
CA_O="AgentOS"
CA_OU="Local PKI"
LEAF_CN="203.0.113.10"

log()  { printf '\033[36m[tls]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[tls] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[tls] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '/^$/q'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --out-dir)      OUT_DIR="$2"; shift 2 ;;
    --install-dir)  INSTALL_DIR="$2"; shift 2 ;;
    --install)      DO_INSTALL="yes"; shift ;;
    --no-install)   DO_INSTALL="no"; shift ;;
    --force)        FORCE_LEAF=1; shift ;;
    --new-ca)       FORCE_CA=1; FORCE_LEAF=1; shift ;;
    --days)         LEAF_DAYS="$2"; shift 2 ;;
    --ca-days)      CA_DAYS="$2"; shift 2 ;;
    --server-ips)   SERVER_IPS="$2"; shift 2 ;;
    --server-dns)   SERVER_DNS="$2"; shift 2 ;;
    --cdp)          CDP_URI="$2"; shift 2 ;;
    --crl-days)     CRL_DAYS="$2"; shift 2 ;;
    --revoke)       ACTION="revoke"; shift ;;
    --gen-crl)      ACTION="gencrl"; shift ;;
    --print|-p)     ACTION="print"; shift ;;
    -h|--help)      usage ;;
    *) die "未知参数：$1（--help 查看用法）" ;;
  esac
done

OUT_DIR="${OUT_DIR%/}"
CA_DIR="$OUT_DIR/ca"
CA_CRT="$OUT_DIR/ca.crt"
CA_KEY="$OUT_DIR/ca.key"
CNF="$OUT_DIR/openssl-ca.cnf"
SRV_KEY="$OUT_DIR/server.key"
SRV_CSR="$OUT_DIR/server.csr"
SRV_CRT="$OUT_DIR/server.crt"
FULLCHAIN="$OUT_DIR/fullchain.crt"
MANIFEST="$OUT_DIR/tls-manifest.txt"
CRL_PEM="$OUT_DIR/ca.crl.pem"
CRL_DER="$OUT_DIR/agentos-ca.crl"

command -v openssl >/dev/null || die "找不到 openssl"
OPENSSL_V="$(openssl version)"

# ---------------------------------------------------------------- 配置文件
write_cnf() {
  local alt="" i d
  i=0
  for d in $SERVER_DNS; do i=$((i+1)); alt="${alt}DNS.${i} = ${d}"$'\n'; done
  i=0
  for d in $SERVER_IPS; do i=$((i+1)); alt="${alt}IP.${i}  = ${d}"$'\n'; done

  cat > "$CNF" <<EOF
# 由 deploy/tls/gen_ca_and_cert.sh 自动生成，请勿手改（重跑脚本会覆盖）
[ ca ]
default_ca = CA_default

[ CA_default ]
dir               = $CA_DIR
database          = \$dir/index.txt
new_certs_dir     = \$dir/newcerts
certificate       = $CA_CRT
private_key       = $CA_KEY
serial            = \$dir/serial
crlnumber         = \$dir/crlnumber
default_md        = sha256
default_days      = $LEAF_DAYS
default_crl_days  = $CRL_DAYS
policy            = policy_loose
unique_subject    = no
copy_extensions   = none
email_in_dn       = no

[ policy_loose ]
countryName             = optional
stateOrProvinceName     = optional
localityName            = optional
organizationName        = optional
organizationalUnitName  = optional
commonName              = optional
emailAddress            = optional

[ req ]
distinguished_name = req_dn
prompt             = no
default_md         = sha256

[ req_dn ]
O  = $CA_O
OU = $CA_OU
CN = $CA_CN

[ v3_ca ]
basicConstraints       = critical, CA:TRUE, pathlen:0
keyUsage               = critical, keyCertSign, cRLSign
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always

[ v3_leaf ]
basicConstraints       = critical, CA:FALSE
keyUsage               = critical, digitalSignature, keyEncipherment
extendedKeyUsage       = serverAuth
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid, issuer
subjectAltName         = @alt_names
crlDistributionPoints  = URI:$CDP_URI

[ alt_names ]
$alt
EOF
}

init_ca_db() {
  mkdir -p "$CA_DIR/newcerts"
  [ -f "$CA_DIR/index.txt" ]  || : > "$CA_DIR/index.txt"
  [ -f "$CA_DIR/serial" ]     || echo "1000" > "$CA_DIR/serial"
  [ -f "$CA_DIR/crlnumber" ]  || echo "1000" > "$CA_DIR/crlnumber"
}

# ---------------------------------------------------------------- 小工具
cert_ok_for_days() {  # $1=crt  $2=至少还要有效多少天
  [ -f "$1" ] || return 1
  openssl x509 -in "$1" -noout -checkend $(( $2 * 86400 )) >/dev/null 2>&1
}

key_matches_cert() {  # $1=key $2=crt
  [ -f "$1" ] && [ -f "$2" ] || return 1
  local a b
  a="$(openssl pkey -in "$1" -pubout 2>/dev/null | openssl sha256 2>/dev/null)"
  b="$(openssl x509 -in "$2" -pubkey -noout 2>/dev/null | openssl sha256 2>/dev/null)"
  [ -n "$a" ] && [ "$a" = "$b" ]
}

san_of() { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null \
             | grep -oE '(IP Address|DNS):[^,[:space:]]+' | sort -u; }

# 规范化 SAN 文本：openssl 会把 ::1 打印成 0:0:0:0:0:0:0:1，
# 直接用字符串比对会永远不相等（曾导致每次运行都重签证书）。
norm_san() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "
import sys, ipaddress
for line in sys.stdin:
    line = line.rstrip('\n')
    if line.startswith('IP Address:'):
        try:
            print('IP Address:' + str(ipaddress.ip_address(line[11:])))
        except ValueError:
            print(line)
    else:
        print(line)
"
  else
    cat
  fi
}

# 当前要求覆盖的 SAN 集合（与服务器证书实际内容同格式）
required_sans() {
  local d
  for d in $SERVER_DNS; do echo "DNS:$d"; done
  for d in $SERVER_IPS; do echo "IP Address:$d"; done
  sort -u
}

san_covers_required() {  # $1=crt
  [ -f "$1" ] || return 1
  local have item
  have="$(san_of "$1" | norm_san)"
  [ -n "$have" ] || return 1
  # 要求：需要的每一条都精确出现在现有证书里
  # （不用 comm —— 它按字节序校验排序，而 sort 用 locale 排序，两者不一致会误判）
  while IFS= read -r item; do
    [ -z "$item" ] && continue
    # 没有 python3 时无法规范化 IPv6，跳过 IPv6 条目，避免误判为「不覆盖」
    if ! command -v python3 >/dev/null 2>&1 && [ "${item#IP Address:}" != "${item#IP Address:::}" ]; then
      warn "无 python3，跳过 IPv6 SAN 校验：$item"
      continue
    fi
    printf '%s\n' "$have" | grep -Fxq -- "$item" || return 1
  done <<< "$(required_sans | norm_san)"
  return 0
}

is_ca_cert() { openssl x509 -in "$1" -noout -text 2>/dev/null | grep -q "CA:TRUE"; }

# 证书里是否已带正确的 CRL 分发点
cdp_matches() {  # $1=crt
  [ -f "$1" ] || return 1
  openssl x509 -in "$1" -noout -ext crlDistributionPoints 2>/dev/null | grep -Fq "$CDP_URI"
}

fp()     { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//'; }
subj()   { openssl x509 -in "$1" -noout -subject 2>/dev/null | sed 's/^subject=//'; }
issuer() { openssl x509 -in "$1" -noout -issuer  2>/dev/null | sed 's/^issuer=//'; }
dates()  { openssl x509 -in "$1" -noout -dates 2>/dev/null | tr '\n' ' '; }

archive_old() {  # $1=tag；没有任何旧材料时不建目录
  local ts dest f found=0
  for f in ca.crt ca.key server.crt server.key server.csr fullchain.crt tls-manifest.txt; do
    [ -f "$OUT_DIR/$f" ] && found=1
  done
  [ "$found" = "1" ] || return 0
  ts="$(date +%Y%m%d-%H%M%S)"
  dest="$OUT_DIR/archive-$1-$ts"
  mkdir -p "$dest"
  for f in ca.crt ca.key server.crt server.key server.csr fullchain.crt tls-manifest.txt; do
    [ -f "$OUT_DIR/$f" ] && cp -p "$OUT_DIR/$f" "$dest/" || true
  done
  log "旧的证书材料已归档到：$dest"
}

write_manifest() {
  {
    echo "# AgentOS TLS 材料清单（deploy/tls/gen_ca_and_cert.sh 生成）"
    echo "# 生成时间: $(date -Is)"
    echo "# openssl : $OPENSSL_V"
    echo
    echo "## 根 CA"
    echo "file        : $CA_CRT"
    echo "subject     : $(subj "$CA_CRT")"
    echo "issuer      : $(issuer "$CA_CRT")"
    echo "validity    : $(dates "$CA_CRT")"
    echo "sha256 fp   : $(fp "$CA_CRT")"
    echo
    echo "## 服务器证书"
    echo "file        : $SRV_CRT"
    echo "subject     : $(subj "$SRV_CRT")"
    echo "issuer      : $(issuer "$SRV_CRT")"
    echo "validity    : $(dates "$SRV_CRT")"
    echo "sha256 fp   : $(fp "$SRV_CRT")"
    echo "SAN         : $(san_of "$SRV_CRT" | tr '\n' ' ')"
    echo "CDP         : $CDP_URI"
    echo "chain depth : $(grep -c 'BEGIN CERTIFICATE' "$FULLCHAIN" 2>/dev/null || echo 0)"
    echo
    echo "## 吊销列表（nginx 在 CDP 地址以 application/pkix-crl 发布 DER）"
    echo "file        : $CRL_DER"
    echo "pem         : $CRL_PEM"
    if [ -f "$CRL_PEM" ]; then
      echo "validity    : $(openssl crl -in "$CRL_PEM" -noout -lastupdate -nextupdate 2>/dev/null | tr '\n' ' ')"
      echo "sha256 fp   : $(openssl crl -in "$CRL_PEM" -outform DER 2>/dev/null | openssl sha256 2>/dev/null | sed 's/^.*= //')"
    fi
    echo
    echo "## 私钥（600，绝不入 git）"
    echo "ca.key      : $CA_KEY"
    echo "server.key  : $SRV_KEY"
  } > "$MANIFEST"
  cat "$MANIFEST"
}

# ---------------------------------------------------------------- 主体步骤
ensure_ca() {
  local regenerate=0
  if [ "$FORCE_CA" = "1" ]; then
    log "指定 --new-ca，重建根 CA"
    regenerate=1
  elif [ ! -f "$CA_CRT" ] || [ ! -f "$CA_KEY" ]; then
    log "根 CA 不存在，开始创建"
    regenerate=1
  elif ! key_matches_cert "$CA_KEY" "$CA_CRT"; then
    warn "ca.key 与 ca.crt 不匹配，重建根 CA"
    regenerate=1
  elif ! is_ca_cert "$CA_CRT"; then
    warn "ca.crt 不是 CA 证书，重建根 CA"
    regenerate=1
  elif ! cert_ok_for_days "$CA_CRT" "$RENEW_BEFORE_DAYS"; then
    warn "根 CA 剩余有效期不足 ${RENEW_BEFORE_DAYS} 天，重建根 CA"
    regenerate=1
  fi

  if [ "$regenerate" = "1" ]; then
    archive_old "ca"
    rm -rf "$CA_DIR"
    ( umask 077
      openssl req -x509 -new -newkey rsa:4096 -sha256 -nodes \
        -days "$CA_DAYS" \
        -keyout "$CA_KEY" -out "$CA_CRT" \
        -subj "/O=$CA_O/OU=$CA_OU/CN=$CA_CN" \
        -config "$CNF" -extensions v3_ca >/dev/null 2>&1
    ) || die "根 CA 创建失败"
    chmod 600 "$CA_KEY"; chmod 644 "$CA_CRT"
    init_ca_db
    log "根 CA 已创建（${CA_DAYS} 天）"
  else
    init_ca_db
    log "根 CA 复用（剩余有效期 $(openssl x509 -in "$CA_CRT" -noout -enddate | cut -d= -f2)）"
  fi
}

ensure_leaf() {
  local regenerate=0 reason=""

  if [ "$FORCE_LEAF" = "1" ]; then
    regenerate=1; reason="指定 --force"
  elif [ ! -f "$SRV_CRT" ] || [ ! -f "$SRV_KEY" ]; then
    regenerate=1; reason="证书或私钥缺失"
  elif ! key_matches_cert "$SRV_KEY" "$SRV_CRT"; then
    regenerate=1; reason="server.key 与 server.crt 不匹配"
  elif ! openssl verify -CAfile "$CA_CRT" "$SRV_CRT" >/dev/null 2>&1; then
    regenerate=1; reason="现有证书不是当前 CA 签发的"
  elif ! cert_ok_for_days "$SRV_CRT" "$RENEW_BEFORE_DAYS"; then
    regenerate=1; reason="剩余有效期不足 ${RENEW_BEFORE_DAYS} 天"
  elif ! san_covers_required "$SRV_CRT"; then
    regenerate=1; reason="SAN 未覆盖 $(echo $SERVER_DNS $SERVER_IPS)"
  elif ! cdp_matches "$SRV_CRT"; then
    regenerate=1; reason="证书缺少/变更了 CRL 分发点 $CDP_URI"
  fi

  if [ "$regenerate" = "0" ]; then
    log "服务器证书复用：$(subj "$SRV_CRT")（到期 $(openssl x509 -in "$SRV_CRT" -noout -enddate | cut -d= -f2)）"
    return 0
  fi

  log "签发服务器证书：$reason"
  [ -f "$SRV_CRT" ] && cp -p "$SRV_CRT" "$SRV_CRT.prev" || true

  # 私钥：能复用就复用（避免每次重签都换 key）
  if [ ! -f "$SRV_KEY" ] || [ "$FORCE_CA" = "1" ]; then
    ( umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
        -out "$SRV_KEY" >/dev/null 2>&1 ) || die "生成服务器私钥失败"
  fi
  chmod 600 "$SRV_KEY"

  openssl req -new -key "$SRV_KEY" -sha256 \
    -subj "/O=$CA_O/OU=$CA_OU/CN=$LEAF_CN" \
    -out "$SRV_CSR" >/dev/null 2>&1 || die "生成 CSR 失败"

  openssl ca -batch -config "$CNF" -extensions v3_leaf \
    -days "$LEAF_DAYS" -md sha256 -notext \
    -in "$SRV_CSR" -out "$SRV_CRT" >/dev/null 2>&1 \
    || die "CA 签发服务器证书失败（可先单独运行 openssl ca 查看详细报错）"

  chmod 644 "$SRV_CRT"
  log "服务器证书已签发（${LEAF_DAYS} 天）"
}

write_fullchain() {
  cat "$SRV_CRT" "$CA_CRT" > "$FULLCHAIN"
  chmod 644 "$FULLCHAIN"
  local n; n="$(grep -c 'BEGIN CERTIFICATE' "$FULLCHAIN")"
  [ "$n" -ge 2 ] || die "fullchain.crt 级数异常：$n"
  log "fullchain.crt 已生成（${n} 级链）"
}

# 生成吊销列表（PEM + DER）。DER 由 nginx 在 CDP 地址发布，
# Windows/Schannel 建链时来取；没有它 curl.exe 会报 CRYPT_E_NO_REVOCATION_CHECK。
ensure_crl() {
  openssl ca -config "$CNF" -gencrl -out "$CRL_PEM" >/dev/null 2>&1 \
    || die "生成 CRL 失败"
  openssl crl -in "$CRL_PEM" -outform DER -out "$CRL_DER" 2>/dev/null \
    || die "CRL 转 DER 失败"
  chmod 644 "$CRL_PEM" "$CRL_DER"
  local revoked
  revoked="$(openssl crl -in "$CRL_PEM" -noout -text 2>/dev/null \
             | awk '/Revoked Certificates/{f=1;next} f&&/Serial Number:/{n++} END{print n+0}')"
  log "CRL 已刷新（已吊销 $revoked 张，到期 $(openssl crl -in "$CRL_PEM" -noout -nextupdate | cut -d= -f2)）"
}

do_install() {
  if [ "$DO_INSTALL" = "no" ]; then
    log "--no-install：跳过安装"
    return 0
  fi
  if [ "$DO_INSTALL" = "auto" ] && [ ! -d "$INSTALL_DIR" ]; then
    log "安装目录 $INSTALL_DIR 不存在，跳过安装（可用 --install-dir 指定）"
    return 0
  fi
  mkdir -p "$INSTALL_DIR"
  install -m 644 "$CA_CRT"    "$INSTALL_DIR/ca.crt"
  install -m 644 "$SRV_CRT"   "$INSTALL_DIR/tls.crt"
  install -m 644 "$FULLCHAIN" "$INSTALL_DIR/fullchain.crt"
  install -m 600 "$SRV_KEY"   "$INSTALL_DIR/tls.key"
  install -m 644 "$CRL_DER"   "$INSTALL_DIR/agentos-ca.crl"
  log "已安装到 $INSTALL_DIR：ca.crt tls.crt fullchain.crt tls.key agentos-ca.crl"
}

case "$ACTION" in
  print)
    [ -f "$CA_CRT" ] && [ -f "$SRV_CRT" ] || die "还没有生成证书，先不带参数跑一次"
    write_manifest
    ;;
  revoke)
    [ -f "$CNF" ] && [ -f "$SRV_CRT" ] || die "还没有生成证书"
    openssl ca -batch -config "$CNF" -revoke "$SRV_CRT" -crl_reason keyCompromise
    ensure_crl
    do_install
    log "已吊销 $SRV_CRT 并刷新 CRL（客户端会在 CRL 缓存到期后拒绝该证书）"
    ;;
  gencrl)
    [ -f "$CNF" ] || die "还没有生成 CA 配置，先跑一次 ensure"
    ensure_crl
    do_install
    ;;
  ensure)
    mkdir -p "$OUT_DIR"
    chmod 750 "$OUT_DIR"
    write_cnf
    init_ca_db
    ensure_ca
    ensure_leaf
    write_fullchain
    ensure_crl
    write_manifest
    do_install
    echo
    log "完成。客户端安装 CA："
    log "  Windows: powershell -ExecutionPolicy Bypass -File scripts/install_ca_windows.ps1 -CaFile deploy/tls/out/ca.crt"
    log "  Linux  : cp deploy/tls/out/ca.crt /usr/local/share/ca-certificates/agentos-local-ca.crt && update-ca-certificates"
    ;;
esac
