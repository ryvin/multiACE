#!/bin/bash
# multiACE Web Console uninstaller
set -e

INSTALL_BASE="/userdata/multiace-web"
NGINX_CONF="/etc/nginx/conf.d/multiace.conf"
SYSTEMD_UNIT="/etc/systemd/system/multiace-web.service"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console uninstall ==="

systemctl stop multiace-web 2>/dev/null || true
systemctl disable multiace-web 2>/dev/null || true
rm -f "$SYSTEMD_UNIT"
systemctl daemon-reload
log "systemd unit removed"

rm -f "$NGINX_CONF"
nginx -t && systemctl reload nginx
log "nginx config removed; reloaded"

rm -rf "$INSTALL_BASE"
log "App + venv removed from $INSTALL_BASE"

log "=== Uninstall complete ==="
