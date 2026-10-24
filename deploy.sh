#!/usr/bin/env bash
# deploy.sh — One-shot production setup for АНЫҚ on a 1 vCPU / 1 GB RAM VPS
# Usage: sudo bash deploy.sh
set -euo pipefail

APP_DIR="/opt/anyk"
APP_USER="anyk"
REPO_URL="${REPO_URL:-}"   # optional: set to your GitHub URL to clone
PORT=8000

log() { echo -e "\n\033[1;32m>>> $*\033[0m"; }

# ── 1. System packages ────────────────────────────────────────────────────
log "Installing system dependencies"
apt-get update -q
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    git nginx \
    libgl1 libglib2.0-0 libgomp1

# ── 2. SWAP (2 GB) — prevents OOM kills on PyTorch/EasyOCR init ──────────
log "Configuring 2 GB swap"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    # Persist across reboots
    grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    # Reduce swappiness so RAM is preferred
    sysctl -w vm.swappiness=10
    grep -q vm.swappiness /etc/sysctl.conf \
        || echo 'vm.swappiness=10' >> /etc/sysctl.conf
    log "Swap enabled: $(swapon --show)"
else
    log "Swap already active — skipping"
fi

# ── 3. App user & directory ───────────────────────────────────────────────
log "Preparing app directory $APP_DIR"
id "$APP_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"

if [ -d "$APP_DIR/.git" ]; then
    log "Pulling latest code"
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
elif [ -n "$REPO_URL" ]; then
    log "Cloning $REPO_URL → $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
else
    log "No REPO_URL set — copy code to $APP_DIR manually, then re-run"
    mkdir -p "$APP_DIR"
fi

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 4. Python virtual environment & dependencies ──────────────────────────
log "Installing Python dependencies"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

# ── 5. Environment file ───────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    log "Creating .env from .env.example — EDIT IT to add GEMINI_API_KEY"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
fi

# ── 6. systemd service ────────────────────────────────────────────────────
log "Installing systemd service"
cat > /etc/systemd/system/anyk.service <<EOF
[Unit]
Description=АНЫҚ — Kaspi receipt analyser (FastAPI)
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT --workers 1
Restart=on-failure
RestartSec=5
# Prevent a single worker from hogging RAM
MemoryMax=900M
OOMScoreAdjust=500

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable anyk
systemctl restart anyk
log "Service status: $(systemctl is-active anyk)"

# ── 7. Nginx ──────────────────────────────────────────────────────────────
log "Configuring Nginx"
cp "$APP_DIR/nginx.conf.example" /etc/nginx/sites-available/anyk
ln -sf /etc/nginx/sites-available/anyk /etc/nginx/sites-enabled/anyk
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

log "✅  Done!  Open http://$(hostname -I | awk '{print $1}') in your browser."
