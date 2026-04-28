#!/bin/sh
# multiACE Web Console installer for Snapmaker U1 PAXX firmware (Buildroot/BusyBox).
# Uses sysvinit /etc/init.d, not systemd.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_BASE="/userdata/multiace-web"
APP_DIR="$INSTALL_BASE/app"
VENV_DIR="$INSTALL_BASE/venv"
INIT_SCRIPT="/etc/init.d/S62multiace-web"
NGINX_SNIPPET="/etc/nginx/fluidd.d/multiace.conf"

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
[ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop || true

# Copy app to persistent partition
mkdir -p "$INSTALL_BASE"
chmod 0755 "$INSTALL_BASE"  # so user 'lava' can traverse into app/ and venv/
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/src" "$APP_DIR/"
cp "$SOURCE_DIR/pyproject.toml" "$APP_DIR/"
chown -R lava:lava "$APP_DIR"
log "App files copied to $APP_DIR"

# Create venv on persistent partition
if [ ! -d "$VENV_DIR" ]; then
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$APP_DIR" --quiet
chown -R lava:lava "$VENV_DIR"
log "Python dependencies installed"

# Install init.d script (overlay; persisted via /oem/.debug)
cp "$SCRIPT_DIR/S62multiace-web" "$INIT_SCRIPT"
chmod +x "$INIT_SCRIPT"
log "Init script installed at $INIT_SCRIPT"

# Install nginx snippet into the fluidd include dir (loaded inside fluidd's server{})
mkdir -p /etc/nginx/fluidd.d
cp "$SCRIPT_DIR/nginx-multiace.conf" "$NGINX_SNIPPET"
nginx -t
/etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload
log "nginx snippet installed at $NGINX_SNIPPET; reloaded"

# Start the service
"$INIT_SCRIPT" start
sleep 1
log "Service status: $("$INIT_SCRIPT" status)"

log ""
log "=== Install complete ==="
IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1; i<=NF; i++) if ($i == "src") print $(i+1)}' | head -1)
log "Open http://${IP:-<printer-ip>}/multiace/"
log ""
