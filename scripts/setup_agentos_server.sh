#!/usr/bin/env bash
# AgentOS 服务端首次部署（Debian 13）：
#   nginx(TLS 443) -> uvicorn(127.0.0.1:8077) -> SQLite(/opt/agent-os/data)
# 自签证书用于 IP 193.112.172.223；exe 侧以后做证书指纹固定。
# 幂等：可重复执行（证书已存在则复用，代码每次覆盖解包）。
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== 1/7 apt =="
apt-get update -y
apt-get install -y --no-install-recommends \
  nginx python3-venv python3-pip openssl ca-certificates curl

echo "== 2/7 dirs + unpack =="
mkdir -p /opt/agent-os/data /opt/agent-os/workspace /opt/agent-os/docs/runs /opt/agent-os/logs /opt/agent-os/etc /opt/agent-os/app-src
tar -xzf /root/agentos-src.tar.gz -C /opt/agent-os/app-src

echo "== 3/7 self-signed cert (IP SAN) =="
if [ ! -f /opt/agent-os/etc/tls.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout /opt/agent-os/etc/tls.key \
    -out /opt/agent-os/etc/tls.crt \
    -subj "/CN=193.112.172.223" \
    -addext "subjectAltName=IP:193.112.172.223"
  chmod 600 /opt/agent-os/etc/tls.key
fi
openssl x509 -in /opt/agent-os/etc/tls.crt -noout -fingerprint -sha256 | head -1

echo "== 4/7 venv + deps =="
python3 -m venv /opt/agent-os/venv
/opt/agent-os/venv/bin/pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
/opt/agent-os/venv/bin/pip install -q -r /opt/agent-os/app-src/requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
/opt/agent-os/venv/bin/python -c "import sys, fastapi, uvicorn; print('python', sys.version.split()[0], '| fastapi', fastapi.__version__)"

echo "== 5/7 systemd =="
cat > /etc/systemd/system/agent-os.service <<'EOF'
[Unit]
Description=AgentOS v1 (FastAPI)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/agent-os/app-src
Environment=DB_PATH=/opt/agent-os/data/oa.db
Environment=M0_WORKSPACE=/opt/agent-os/workspace
Environment=M0_SANDBOX=none
Environment=M0_SANDBOX_ACP=0
Environment=WECOM_WATCHER=0
Environment=WECOM_APPROVAL_POLLER=0
Environment=UI_CRON=0
Environment=OCTO_RUNNER=0
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/agent-os/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable agent-os >/dev/null 2>&1 || true
systemctl restart agent-os
sleep 5
if ! systemctl is-active --quiet agent-os; then
  echo "!! agent-os failed:"
  journalctl -u agent-os -n 60 --no-pager
  exit 1
fi
curl -s -o /dev/null -w "local health http=%{http_code}\n" http://127.0.0.1:8077/v1/health || true
curl -s http://127.0.0.1:8077/v1/health | head -c 400; echo

echo "== 6/7 nginx TLS =="
cat > /etc/nginx/sites-available/agent-os <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    http2 on;
    server_name _;

    ssl_certificate     /opt/agent-os/etc/tls.crt;
    ssl_certificate_key /opt/agent-os/etc/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;

    client_max_body_size 256m;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:8077;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
    }
}
EOF
ln -sf /etc/nginx/sites-available/agent-os /etc/nginx/sites-enabled/agent-os
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
sleep 2

echo "== 7/7 verify =="
curl -sk -o /dev/null -w "https health=%{http_code}\n" https://127.0.0.1/v1/health
curl -sk https://127.0.0.1/v1/health | head -c 400; echo
echo "TLS fingerprint:"
openssl x509 -in /opt/agent-os/etc/tls.crt -noout -fingerprint -sha256
echo "SETUP_DONE"
