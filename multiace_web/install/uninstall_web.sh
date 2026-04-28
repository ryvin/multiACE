#!/bin/sh
# multiACE Web Console uninstaller for Snapmaker U1 PAXX firmware (Buildroot/BusyBox).
set -e

INSTALL_BASE="/userdata/multiace-web"
INIT_SCRIPT="/etc/init.d/S62multiace-web"
NGINX_SNIPPET="/etc/nginx/fluidd.d/multiace.conf"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console uninstall ==="

[ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop || true
rm -f "$INIT_SCRIPT"
log "Init script removed"

rm -f "$NGINX_SNIPPET"
nginx -t && (/etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload)
log "nginx snippet removed; reloaded"

rm -rf "$INSTALL_BASE"
log "App + venv removed from $INSTALL_BASE"

log "=== Uninstall complete ==="
