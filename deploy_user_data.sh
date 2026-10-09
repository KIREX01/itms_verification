#!/bin/bash
set -euo pipefail

# ITMS Verification Copilot - EC2 Cloud Provisioning
exec > /var/log/itms-deploy.log 2>&1

echo "=== [1/6] Updating APT & Installing System Prerequisites ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-pip python3-venv curl tesseract-ocr libgl1 libglib2.0-0

echo "=== [2/6] Setting Up ITMS Application Directory ==="
mkdir -p /opt/itms
cd /opt/itms

echo "=== [3/6] Cloning Repository ==="
git clone https://github.com/KIREX01/itms_verification.git .
chmod +x itms scripts/bootstrap.py

echo "=== [4/6] Creating Python Virtual Environment & Installing Dependencies ==="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== [5/6] Database Migrations & Initial Setup ==="
.venv/bin/python manage.py migrate
.venv/bin/python scripts/bootstrap.py

echo "=== [6/6] Configuring systemd Service for Automatic Startup ==="
mkdir -p /opt/itms/media
cat << 'EOF' > /etc/systemd/system/itms.service
[Unit]
Description=ITMS Verification Copilot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/itms
ExecStart=/opt/itms/.venv/bin/python manage.py run_web --no-ssl --port 80 --noreload
Restart=always
RestartSec=5
StandardOutput=append:/opt/itms/media/itms_web.log
StandardError=append:/opt/itms/media/itms_web.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now itms.service

echo "=== ITMS Deployment Complete! ==="
