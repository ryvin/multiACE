#!/bin/sh
# multiACE Web Console uninstaller for Snapmaker U1 PAXX firmware (Buildroot/BusyBox).
set -e

INSTALL_BASE="/userdata/multiace-web"
INIT_SCRIPT="/etc/init.d/S62multiace-web"
WATCHDOG_SCRIPT="/etc/init.d/S63multiace-web-watchdog"
GOVEE_SCRIPT="/etc/init.d/S64govee-bridge"
GOVEE_WATCHDOG_SCRIPT="/etc/init.d/S65govee-bridge-watchdog"
NGINX_SNIPPET="/etc/nginx/fluidd.d/multiace.conf"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console uninstall ==="

# Drop the inittab respawn entry first so init doesn't immediately re-spawn
# the watchdog after we stop it. Match the line install_web.sh added.
INITTAB=/etc/inittab
INITTAB_LINE="::respawn:$WATCHDOG_SCRIPT _supervise"
if [ -f "$INITTAB" ] && grep -qF "$INITTAB_LINE" "$INITTAB"; then
  grep -vF "$INITTAB_LINE" "$INITTAB" > "$INITTAB.new"
  mv "$INITTAB.new" "$INITTAB"
  kill -HUP 1 2>/dev/null || true
  log "inittab respawn entry removed; init signalled to re-read"
fi

# Stop watchdog before daemon so it can't resurrect it.
[ -x "$WATCHDOG_SCRIPT" ] && "$WATCHDOG_SCRIPT" stop || true
rm -f "$WATCHDOG_SCRIPT"
log "Watchdog removed"

[ -x "$GOVEE_WATCHDOG_SCRIPT" ] && "$GOVEE_WATCHDOG_SCRIPT" stop || true
rm -f "$GOVEE_WATCHDOG_SCRIPT"
log "Govee bridge watchdog removed"

[ -x "$GOVEE_SCRIPT" ] && "$GOVEE_SCRIPT" stop || true
rm -f "$GOVEE_SCRIPT"
log "Govee bridge removed"

[ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop || true
rm -f "$INIT_SCRIPT"
log "Init script removed"

rm -f "$NGINX_SNIPPET"
nginx -t && (/etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload)
log "nginx snippet removed; reloaded"

rm -rf "$INSTALL_BASE"
log "App + venv removed from $INSTALL_BASE"

log "=== Uninstall complete ==="
