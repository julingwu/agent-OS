#!/usr/bin/env bash
# ==============================================================================
# AgentOS - Bring Your Own Host (BYOH) 一键自动化部署脚本
# 适用环境：任意干净的 Linux VPS (Ubuntu 20.04+, Debian 11+, CentOS 8+, RockyLinux)
# 运行方式：curl -sSL https://raw.githubusercontent.com/julingwu/agent-OS/mainline/scripts/setup_server.sh | bash
# ==============================================================================
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=========================================================="
echo "🚀 开始初始化 AgentOS 自备算力服务器 (BYOH 模式)..."
echo "=========================================================="

# 1. 探测包管理器并安装基础依赖
if command -v apt-get >/dev/null 2>&1; then
    echo "--> [1/6] 更新软件包列表并安装依赖 (APT)..."
    apt-get update -y
    apt-get install -y --no-install-recommends \
        git curl python3 python3-venv python3-pip openssl ca-certificates nginx
elif command -v dnf >/dev/null 2>&1; then
    echo "--> [1/6] 更新软件包列表并安装依赖 (DNF)..."
    dnf install -y git curl python3 python3-pip openssl ca-certificates nginx
elif command -v yum >/dev/null 2>&1; then
    echo "--> [1/6] 更新软件包列表并安装依赖 (YUM)..."
    yum install -y git curl python3 python3-pip openssl ca-certificates nginx
else
    echo "❌ 暂不支持的 Linux 发行版，请确保已安装 Python 3.10+、Git 和 Nginx。"
fi

INSTALL_DIR="/opt/agentos"
mkdir -p "$INSTALL_DIR" /opt/agentos-data /opt/agentos-workspace /opt/agentos-logs

# 2. 获取最新代码
echo "--> [2/6] 获取 AgentOS 核心代码仓库..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "检测到已有仓库，执行同步更新..."
    git -C "$INSTALL_DIR" fetch --all
    git -C "$INSTALL_DIR" reset --hard origin/mainline || git -C "$INSTALL_DIR" reset --hard origin/main
else
    echo "克隆官方仓库到 $INSTALL_DIR ..."
    git clone --depth 1 https://github.com/julingwu/agent-OS.git "$INSTALL_DIR"
fi

# 3. 准备 Python 虚拟环境与依赖
echo "--> [3/6] 构建 Python 独立虚拟环境..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple || "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple || "$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# 4. 生成安全密钥与环境配置
echo "--> [4/6] 配置独立环境与加密密钥..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env" || touch "$INSTALL_DIR/.env"
fi

SECRET=$("$INSTALL_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/^UI_SESSION_SECRET=.*/UI_SESSION_SECRET=$SECRET/" "$INSTALL_DIR/.env" || true
grep -q "UI_SESSION_SECRET" "$INSTALL_DIR/.env" || echo "UI_SESSION_SECRET=$SECRET" >> "$INSTALL_DIR/.env"

# 探测服务器公网 IP
SERVER_IP=$(curl -s --connect-timeout 5 https://api.ipify.org || curl -s --connect-timeout 5 https://ifconfig.me || echo "127.0.0.1")

# 生成对应当前公网 IP 的自签证书
CERT_DIR="$INSTALL_DIR/deploy/tls_auto"
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_DIR/server.crt" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout "$CERT_DIR/server.key" \
        -out "$CERT_DIR/server.crt" \
        -subj "/CN=$SERVER_IP" \
        -addext "subjectAltName=IP:$SERVER_IP,IP:127.0.0.1,DNS:localhost" >/dev/null 2>&1
    chmod 600 "$CERT_DIR/server.key"
fi

# 5. 配置并启动 Systemd 守护进程
echo "--> [5/6] 配置后台自启服务 (Systemd)..."
cat > /etc/systemd/system/agentos.service <<EOF
[Unit]
Description=AgentOS Core Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment=DB_PATH=/opt/agentos-data/oa.db
Environment=M0_WORKSPACE=/opt/agentos-workspace
Environment=PYTHONUNBUFFERED=1
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8077 --workers 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable agentos >/dev/null 2>&1 || true
systemctl restart agentos

# 等待服务探活
sleep 3
if ! systemctl is-active --quiet agentos; then
    echo "❌ AgentOS 服务启动失败，请检查日志: journalctl -u agentos -n 50 --no-pager"
    exit 1
fi

# 6. 生成超级管理员/设备配对令牌
echo "--> [6/6] 生成初始管理员与设备配对令牌..."
TOKEN_OUTPUT=$("$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/tools/auth_admin.py" issue --name "byoh-admin" --role ceo 2>/dev/null || echo "")
TOKEN=$(echo "$TOKEN_OUTPUT" | grep -o 'token=[^ ]*' | cut -d'=' -f2 || echo "$TOKEN_OUTPUT")

echo ""
echo "=========================================================================="
echo "🎉 AgentOS 自备服务器部署成功！(Server Ready)"
echo "=========================================================================="
echo "🌐 服务器 IP / 访问地址 : http://${SERVER_IP}:8077"
echo "🔒 本地 API 接口端口   : 8077"
echo "🔑 初始设备连接令牌     : ${TOKEN}"
echo "--------------------------------------------------------------------------"
echo "📱 客户端使用方式："
echo "  1. 打开 Android 手机端 App 或 Windows 桌面端；"
echo "  2. 在登录页面中点击【＋ 存为自备主机】；"
echo "  3. 输入服务器地址: http://${SERVER_IP}:8077"
echo "  4. 填入上述令牌即可秒级连通使用！"
echo "=========================================================================="
