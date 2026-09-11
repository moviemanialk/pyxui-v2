#!/usr/bin/env bash
# Installs Xray-core + PyXUI v2 panel on a fresh Debian/Ubuntu VPS.
# Run as root:  sudo bash install.sh
set -e

echo "==> Installing base packages"
apt update
apt install -y python3 python3-pip python3-venv nginx certbot curl unzip openssh-server stunnel4

echo "==> Enabling TCP forwarding for OpenSSH tunnel accounts"
if ! grep -q "^AllowTcpForwarding yes" /etc/ssh/sshd_config; then
  echo "AllowTcpForwarding yes" >> /etc/ssh/sshd_config
fi
systemctl restart ssh 2>/dev/null || systemctl restart sshd

echo "==> Enabling stunnel service"
sed -i 's/ENABLED=0/ENABLED=1/' /etc/default/stunnel4 2>/dev/null || true

echo "==> Installing Xray-core"
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"

echo "==> Setting up PyXUI panel"
PANEL_DIR="/opt/pyxui"
mkdir -p "$PANEL_DIR"
cp -r ./* "$PANEL_DIR"/
cd "$PANEL_DIR"
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install gunicorn

echo "==> Creating systemd service for the panel"
cat > /etc/systemd/system/pyxui.service <<'EOF'
[Unit]
Description=PyXUI Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/pyxui
ExecStart=/opt/pyxui/venv/bin/gunicorn -w 2 -b 127.0.0.1:2053 app:app
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now pyxui

echo "=================================================================="
echo " PyXUI v4 installed. Panel bound to 127.0.0.1:2053 (localhost only)."
echo " Reach it via nginx (see docs/INSTALL.md) or an SSH tunnel:"
echo "   ssh -L 2053:127.0.0.1:2053 root@<vps-ip>"
echo " Login: admin / admin123 — CHANGE THIS IMMEDIATELY under Settings."
echo ""
echo " Don't forget to open firewall ports for the new direct-listen"
echo " protocols if you use them (check Settings for current values):"
echo "   REALITY:       tcp:8443"
echo "   Shadowsocks:   tcp:8388"
echo "   SSH-over-TLS:  tcp:444"
echo "   e.g. gcloud compute firewall-rules create allow-pyxui-extra \\"
echo "          --allow=tcp:8443,tcp:8388,tcp:444 --target-tags=vpn-server"
echo ""
echo " See docs/INSTALL.md for the remaining steps: DNS, TLS cert, nginx."
echo "=================================================================="
