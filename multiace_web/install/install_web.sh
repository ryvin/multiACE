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
WATCHDOG_SCRIPT="/etc/init.d/S63multiace-web-watchdog"
GOVEE_SCRIPT="/etc/init.d/S64govee-bridge"
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

# Stop existing service + watchdog if running (watchdog first so it can't
# resurrect the daemon mid-install).
[ -x "$WATCHDOG_SCRIPT" ] && "$WATCHDOG_SCRIPT" stop || true
[ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop || true

# Copy app to persistent partition
mkdir -p "$INSTALL_BASE"
chmod 0755 "$INSTALL_BASE"  # so user 'lava' can traverse into app/ and venv/
# Persistence files (e.g. .autodry_state.json) live at $INSTALL_BASE — outside
# $APP_DIR so they survive `rm -rf "$APP_DIR"` below. uvicorn runs as user
# 'lava' (see S62multiace-web), so $INSTALL_BASE must be lava-writable for
# atomic-write-via-tmp-then-rename to succeed. Without this chown the saves
# silently fail and per-ACE autodry config doesn't persist across restarts.
chown lava:lava "$INSTALL_BASE"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/src" "$APP_DIR/"
cp "$SOURCE_DIR/pyproject.toml" "$APP_DIR/"
# tools/ holds out-of-package entrypoints (Govee bridge etc.) that the init
# scripts run via `uvicorn govee_bridge:app` from $APP_DIR.
if [ -d "$SOURCE_DIR/tools" ]; then
  cp -r "$SOURCE_DIR/tools" "$APP_DIR/"
fi
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

# Install init.d scripts (overlay; persisted via /oem/.debug)
cp "$SCRIPT_DIR/S62multiace-web" "$INIT_SCRIPT"
chmod +x "$INIT_SCRIPT"
log "Init script installed at $INIT_SCRIPT"
cp "$SCRIPT_DIR/S63multiace-web-watchdog" "$WATCHDOG_SCRIPT"
chmod +x "$WATCHDOG_SCRIPT"
log "Watchdog installed at $WATCHDOG_SCRIPT"
# Govee BLE bridge — optional, no-ops without GOVEE_BRIDGE_MAC in .env.
cp "$SCRIPT_DIR/S64govee-bridge" "$GOVEE_SCRIPT"
chmod +x "$GOVEE_SCRIPT"
log "Govee bridge installed at $GOVEE_SCRIPT (no-op until GOVEE_BRIDGE_MAC is set in .env)"

# Install nginx snippet into the fluidd include dir (loaded inside fluidd's server{})
mkdir -p /etc/nginx/fluidd.d
cp "$SCRIPT_DIR/nginx-multiace.conf" "$NGINX_SNIPPET"
nginx -t
/etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload
log "nginx snippet installed at $NGINX_SNIPPET; reloaded"

# Start the service, then the watchdog.
"$INIT_SCRIPT" start
sleep 1
log "Service status: $("$INIT_SCRIPT" status)"

# Wire the watchdog into /etc/inittab as a respawn entry so PID 1 keeps it
# alive across crashes and restarts it automatically on every boot. Without
# this, if the watchdog itself dies (or the printer reboots in a way that
# skips the rcS sweep) nothing brings the multiace-web service back. With
# this, init re-execs the watchdog any time it exits.
INITTAB=/etc/inittab
INITTAB_LINE="::respawn:$WATCHDOG_SCRIPT _supervise"
if [ -f "$INITTAB" ] && ! grep -qF "$INITTAB_LINE" "$INITTAB" 2>/dev/null; then
  echo "$INITTAB_LINE" >> "$INITTAB"
  log "inittab respawn entry added — init now keeps the watchdog alive"
  kill -HUP 1 2>/dev/null || true
  log "init signalled (SIGHUP) to re-read inittab"
  # When init picks up the new entry it'll spawn the foreground watchdog.
  # Don't start the watchdog manually below — that would race the inittab
  # respawn and create a duplicate process.
else
  # Fallback for systems without /etc/inittab (or already-installed line):
  # use the existing detached `start` path so the watchdog still runs.
  "$WATCHDOG_SCRIPT" start
  sleep 1
  log "Watchdog status: $("$WATCHDOG_SCRIPT" status)"
fi

log ""
log "=== Install complete ==="
IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1; i<=NF; i++) if ($i == "src") print $(i+1)}' | head -1)
log "Open http://${IP:-<printer-ip>}/multiace/"
log ""
