#!/usr/bin/env bash
# 在 Debian 13 上安装"Bot 的电脑"桌面栈：
#   XFCE 桌面(:0, 自动登录) + xrdp(3389, 给用户 RDP) + x11vnc(5900, 仅本机, 给 AgentOS 截图/接管)
#   Chromium + 中文字体 + 截图/自动化工具
# 幂等：重复执行不会重复建用户，配置文件直接覆盖。
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== 1/6 apt update =="
apt-get update -y

echo "== 2/6 install packages =="
apt-get install -y --no-install-recommends \
  xfce4 xfce4-terminal xfce4-goodies dbus-x11 xauth \
  lightdm lightdm-gtk-greeter \
  x11vnc xrdp \
  chromium fonts-wqy-zenhei fonts-noto-cjk fonts-noto-color-emoji \
  xdotool scrot imagemagick ffmpeg x11-utils wmctrl \
  curl git unzip htop tmux jq ca-certificates

echo "== 3/6 desktop user =="
if ! id oaagent >/dev/null 2>&1; then
  useradd -m -s /bin/bash oaagent
  echo "oaagent:${OAAGENT_PASSWORD}" | chpasswd
  usermod -aG sudo oaagent
fi

echo "== 4/6 lightdm autologin (display :0 常驻) =="
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-autologin.conf <<'EOF'
[Seat:*]
autologin-user=oaagent
autologin-user-timeout=0
user-session=xfce
EOF

echo "== 5/6 xrdp session / x11vnc service =="
cat > /etc/xrdp/startwm.sh <<'EOF'
#!/bin/sh
if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi
exec startxfce4
EOF
chmod +x /etc/xrdp/startwm.sh

cat > /etc/systemd/system/x11vnc.service <<'EOF'
[Unit]
Description=x11vnc for display :0 (AgentOS computer view)
After=lightdm.service
Requires=lightdm.service

[Service]
ExecStart=/usr/bin/x11vnc -display :0 -auth /var/run/lightdm/root/:0 -forever -shared -rfbport 5900 -localhost -noxdamage -repeat
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lightdm xrdp x11vnc >/dev/null 2>&1 || true
systemctl restart lightdm
sleep 3
systemctl restart xrdp
systemctl restart x11vnc
sleep 3

echo "== 6/6 verify =="
echo "--- services ---"
systemctl is-active lightdm xrdp x11vnc || true
echo "--- display ---"
sudo -u oaagent bash -lc 'DISPLAY=:0 xdotool getdisplaygeometry' 2>&1 || true
echo "--- screenshot ---"
sudo -u oaagent bash -lc 'DISPLAY=:0 scrot /tmp/desktop_check.png' 2>&1 || true
ls -l /tmp/desktop_check.png 2>&1 || true
echo "--- versions ---"
chromium --version 2>&1 | head -1 || true
echo "PROVISION_DONE"
