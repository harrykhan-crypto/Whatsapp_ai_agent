#!/usr/bin/env bash
# HARRY server setup (Ubuntu 22.04/24.04). Safe to run again: used for first install and every update.
# Run from the project folder:  bash deploy/server_setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
RUN_USER="$(whoami)"
echo "==> Setting up HARRY in $APP_DIR as $RUN_USER"

echo "==> Installing system packages"
sudo apt-get update -y -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip unzip >/dev/null

# Small servers: add swap so Chromium never runs out of memory.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 3000 ] && [ ! -f /swapfile ]; then
  echo "==> Adding 2 GB swap (server has ${MEM_MB} MB RAM)"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Python environment"
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

echo "==> Chromium for Playwright"
sudo "$APP_DIR/venv/bin/python" -m playwright install-deps chromium >/dev/null
venv/bin/python -m playwright install chromium

echo "==> Server settings in .env"
touch .env
set_env() {  # replace or add KEY=VALUE
  grep -v "^$1=" .env > .env.tmp || true
  echo "$1=$2" >> .env.tmp
  mv .env.tmp .env
}
set_env BROWSER_HEADLESS true
set_env SERVER_MODE true
set_env BROWSER_PROFILE_DIR storage/server_profile
set_env SESSION_FILE storage/moodle_session.json
set_env BROWSER_EXECUTABLE_PATH ""
set_env BROWSER_CHANNEL ""
set_env HOST 127.0.0.1
chmod 600 .env
mkdir -p storage
[ -f storage/moodle_session.json ] && chmod 600 storage/moodle_session.json || true
sudo timedatectl set-timezone Asia/Karachi || true

echo "==> Background service (starts on boot, restarts after crashes)"
sudo tee /etc/systemd/system/harry.service >/dev/null <<UNIT
[Unit]
Description=HARRY - Abasyn WhatsApp LMS Assistant
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python app.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable harry >/dev/null 2>&1
sudo systemctl restart harry
sleep 5
if systemctl is-active --quiet harry; then
  echo ""
  echo "✅ HARRY is running on the server."
  [ -f storage/moodle_session.json ] || echo "⚠️  No Moodle login yet: on your laptop double-click LOGIN_AND_SEND.bat"
  echo "Live log:  journalctl -u harry -f"
else
  echo "❌ HARRY did not start. Last log lines:"
  sudo journalctl -u harry -n 40 --no-pager
  exit 1
fi
