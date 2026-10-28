#!/usr/bin/env bash
# 修复：安装 X 服务器 + dummy 驱动，让 :0 上有一个常驻的 XFCE 桌面。
# 幂等：可重复执行。
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== install xserver + dummy driver =="
apt-get install -y --no-install-recommends \
  xserver-xorg xserver-xorg-legacy xserver-xorg-video-dummy \
  xinit x11-apps x11-utils xauth accountsservice

echo "== dummy xorg config =="
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/99-dummy.conf <<'EOF'
Section "Device"
    Identifier  "dummy"
    Driver      "dummy"
    VideoRam    256000
EndSection

Section "Monitor"
    Identifier  "dummy_mon"
    HorizSync   5.0 - 1000.0
    VertRefresh 5.0 - 200.0
    Modeline    "1600x900" 118.25 1600 1696 1856 2112 900 903 908 934 -hsync +vsync
EndSection

Section "Screen"
    Identifier  "dummy_screen"
    Device      "dummy"
    Monitor     "dummy_mon"
    DefaultDepth 24
    SubSection "Display"
        Depth   24
        Modes   "1600x900"
    EndSubSection
EndSection
EOF

echo "== lightdm autologin config =="
cat > /etc/lightdm/lightdm.conf.d/50-autologin.conf <<'EOF'
[Seat:*]
autologin-user=oaagent
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
EOF

echo "== restart lightdm =="
systemctl enable lightdm >/dev/null 2>&1 || true
systemctl reset-failed lightdm || true
systemctl restart lightdm
sleep 5
systemctl is-active lightdm || (journalctl -u lightdm -n 20 --no-pager; exit 1)

echo "== verify display :0 =="
sudo -u oaagent bash -lc 'DISPLAY=:0 xdotool getdisplaygeometry' 2>&1 || true
sudo -u oaagent bash -lc 'DISPLAY=:0 xdpyinfo | head -8' 2>&1 || true

echo "== x11vnc (localhost only) =="
systemctl enable x11vnc >/dev/null 2>&1 || true
systemctl restart x11vnc || true
sleep 2
systemctl is-active x11vnc || true

echo "== screenshot =="
sudo -u oaagent bash -lc 'DISPLAY=:0 scrot /tmp/desktop_check.png' 2>&1 || true
ls -l /tmp/desktop_check.png 2>/dev/null || echo "no screenshot"

echo "== xrdp =="
systemctl enable xrdp >/dev/null 2>&1 || true
systemctl restart xrdp || true
systemctl is-active xrdp || true

echo "PROVISION_FIX_DONE"
