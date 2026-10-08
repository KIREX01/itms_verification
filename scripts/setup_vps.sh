#!/usr/bin/env bash
#
# VPS Deployment Script for ITMS Verification Copilot
# Domain: close.kirex.online
# Sets up Gunicorn (Systemd) and Caddy (Reverse Proxy for HTTPS)
#
set -e

DOMAIN="close.kirex.online"
INSTALL_DIR="/opt/itms_verification"
USER="root"

echo "===================================================================="
echo "   ITMS VERIFICATION COPILOT - VPS DEPLOYMENT SCRIPT"
echo "===================================================================="
echo "[>] Domain: $DOMAIN"

# 1. System Dependencies
echo "[>] Installing System Dependencies (Caddy & Python3-venv)..."
apt-get update
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl python3-venv python3-pip

# Install Caddy
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
fi

# 2. Setup Application Environment
if [ ! -d "$INSTALL_DIR" ]; then
    echo "[!] Install directory $INSTALL_DIR not found! Please clone the repository first."
    exit 1
fi

cd "$INSTALL_DIR"
echo "[>] Configuring Python Virtual Environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn

# Run Migrations
echo "[>] Running database migrations and collectstatic..."
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput

# 3. Configure Systemd Service for Gunicorn
echo "[>] Configuring Systemd Service (itms-web.service)..."
cat <<EOF > /etc/systemd/system/itms-web.service
[Unit]
Description=ITMS Verification Copilot (Gunicorn)
After=network.target

[Service]
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment="DJANGO_ALLOWED_HOSTS=$DOMAIN,127.0.0.1,localhost"
ExecStart=$INSTALL_DIR/.venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 itms_project.wsgi:application
Restart=always
StandardOutput=append:/var/log/itms_gunicorn.log
StandardError=append:/var/log/itms_gunicorn_err.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable itms-web
systemctl restart itms-web

# 4. Configure Caddy for Auto-HTTPS
echo "[>] Configuring Caddyfile..."
cat <<EOF > /etc/caddy/Caddyfile
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
    
    # Optional: Serve static files directly via Caddy for better performance
    handle_path /static/* {
        root * $INSTALL_DIR/static
        file_server
    }
}
EOF

systemctl enable caddy
systemctl restart caddy

echo "===================================================================="
echo "   [✓] DEPLOYMENT COMPLETE!"
echo "   Your application is now running at https://$DOMAIN"
echo "===================================================================="
