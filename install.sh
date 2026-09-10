#!/usr/bin/env bash
# Installs Xray-core + PyXUI v2 panel on a fresh Debian/Ubuntu VPS.
# Run as root:  sudo bash install.sh
set -e

echo "==> Installing base packages"
apt update
apt install -y python3 python3-pip python3-venv nginx certbot curl unzip

echo "==> Installing Xray-core"
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"

echo "==> Setting up PyXUI panel"
PANEL_DIR="/opt/pyxui"
mkdir -p "$PANEL_DIR"
cp -r ./* "$PANEL_DIR"/
cd "$PANEL_DIR"
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

echo "==> Creating systemd service for the panel"
cat > /etc/systemd/system/pyxui.service <<'EOF'
[Unit]
Description=PyXUI Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/pyxui
ExecStart=/opt/pyxui/venv/bin/python3 /opt/pyxui/app.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now pyxui

echo "=================================================================="
echo " PyXUI v2 installed."
echo " Panel:   http://<your-vps-ip>:2053  (login: admin / admin123)"
echo " CHANGE THE DEFAULT PASSWORD IMMEDIATELY under Settings."
echo ""
echo " See docs/INSTALL.md for the remaining steps:"
echo "  - point your domain at this server"
echo "  - issue a TLS cert with certbot"
echo "  - install nginx.conf.example to route all 3 protocol paths"
echo "=================================================================="
