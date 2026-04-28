#!/bin/bash
# multiACE Web Console installer
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_BASE="/userdata/multiace-web"
APP_DIR="$INSTALL_BASE/app"
VENV_DIR="$INSTALL_BASE/venv"
NGINX_CONF="/etc/nginx/conf.d/multiace.conf"
SYSTEMD_UNIT="/etc/systemd/system/multiace-web.service"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console install ==="

# Sanity check: /oem/.debug must exist or overlay will be wiped on next boot
if [ ! -f /oem/.debug ]; then
  log "ERROR: /oem/.debug missing — overlay will not persist. Aborting."
  log "Create it with: touch /oem/.debug"
  exit 1
fi

log "Source: $SOURCE_DIR"
log "Target: $INSTALL_BASE"

# Stop existing service if running
systemctl stop multiace-web 2>/dev/null || true

# Copy app to persistent partition
mkdir -p "$INSTALL_BASE"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/src" "$APP_DIR/"
cp "$SOURCE_DIR/pyproject.toml" "$APP_DIR/"
log "App files copied to $APP_DIR"

# Create venv on persistent partition
if [ ! -d "$VENV_DIR" ]; then
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$APP_DIR" --quiet
log "Python dependencies installed"

# Install systemd unit
cp "$SCRIPT_DIR/multiace-web.service" "$SYSTEMD_UNIT"
systemctl daemon-reload
systemctl enable multiace-web
systemctl start multiace-web
log "systemd unit installed and started"

# Install nginx snippet
mkdir -p /etc/nginx/conf.d
cp "$SCRIPT_DIR/nginx-multiace.conf" "$NGINX_CONF"
nginx -t && systemctl reload nginx
log "nginx config installed; reloaded"

log "Service status:"
systemctl status multiace-web --no-pager -l | head -10 || true

log ""
log "=== Install complete ==="
log "Open http://$(hostname -I | awk '{print $1}')/multiace/"
log ""
