#!/bin/sh
# License: GPL-3.0
# Shared helpers for install_plugin.sh and restore.sh (sourced, not executed).
# BusyBox ash compatible: no bashisms.

APP_DIR=${APP_DIR:-/userdata/filamenthub-plugin}
INITD=/etc/init.d/S66filamenthub-plugin
NGINX_ROOT=${NGINX_ROOT:-/etc/nginx}
NGINX_DIR=${NGINX_DIR:-$NGINX_ROOT/fluidd.d}
NGINX_DROPIN=$NGINX_DIR/multiace-plugin.conf
MOONRAKER_URL=${MOONRAKER_URL:-http://127.0.0.1:7125}
PY=${PY:-/usr/bin/python3}
PLUGIN_PORT=${FILAMENTHUB_PLUGIN_PORT:-8089}

log() { echo "[filamenthub-plugin] $*"; }
die() { echo "[filamenthub-plugin] ERROR: $*" >&2; exit 1; }

require_root() {
  [ "$(id -u)" = "0" ] || die "must run as root (writes /etc/init.d and /etc/nginx)"
}

# Refuse to touch services/nginx while a print is printing or paused.
# Unknown state (Moonraker unreachable / unparsable) is also refused:
# we cannot prove it is safe. FORCE=1 overrides both.
guard_print_state() {
  state=$("$PY" - "$MOONRAKER_URL" <<'PYEOF' 2>/dev/null
import json, sys, urllib.request
url = sys.argv[1].rstrip("/") + "/printer/objects/query?print_stats=state"
with urllib.request.urlopen(url, timeout=5) as r:
    print(json.load(r)["result"]["status"]["print_stats"]["state"])
PYEOF
) || true
  [ -n "$state" ] || state=unknown
  log "Moonraker print_stats.state = $state"
  case "$state" in
    standby|complete|cancelled|error) return 0 ;;
  esac
  if [ "${FORCE:-0}" = "1" ]; then
    log "WARNING: FORCE=1 set - proceeding despite print state '$state'"
    return 0
  fi
  die "print state is '$state' - refusing (re-run with FORCE=1 only if you are sure no print is active)"
}

# Print the nginx drop-in. auth_request is included when PAXX's
# /auth_check location exists, mirroring decay71's install_multiace.sh.
# proxy_pass has NO URI part, so /plugin/<name>/... reaches decay71's backend
# route /plugin/{name}/{path} unchanged.
render_nginx_dropin() {
  echo "# Managed by multiace_plugins/filamenthub/install - do not edit."
  echo "# Routes decay71 plugin iframes (/plugin/<name>/) to the multiACE"
  echo "# web backend, which proxies to the plugin's own port."
  echo "location /plugin/ {"
  if grep -rqs 'auth_check' "$NGINX_ROOT" 2>/dev/null; then
    echo "    auth_request /auth_check;"
  fi
  cat <<'EOF'
    proxy_pass http://127.0.0.1:7126;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
}
EOF
}

# Files other than our own drop-in that already declare `location /plugin/`.
foreign_plugin_locations() {
  grep -rls 'location /plugin/' "$NGINX_ROOT" 2>/dev/null | grep -vxF "$NGINX_DROPIN" || true
}

# Backups live OUTSIDE the nginx tree so an `include fluidd.d/*` can never
# load a stale copy.
BACKUP_DIR=${BACKUP_DIR:-$APP_DIR/backup}

nginx_reload() {
  if [ -x /etc/init.d/S50nginx ]; then
    /etc/init.d/S50nginx reload >/dev/null 2>&1 || nginx -s reload
  else
    nginx -s reload
  fi
}

# Idempotently install the drop-in; `nginx -t` gates the reload and a failed
# test restores the previous state (old file, or no file).
ensure_nginx_dropin() {
  [ -d "$NGINX_DIR" ] || die "$NGINX_DIR missing - unsupported nginx layout; add a /plugin/ location by hand"
  foreign=$(foreign_plugin_locations)
  if [ -n "$foreign" ]; then
    log "nginx: 'location /plugin/' already provided by: $foreign - not adding ours"
    if [ -f "$NGINX_DROPIN" ]; then
      log "nginx: removing our duplicate $NGINX_DROPIN"
      mkdir -p "$BACKUP_DIR"
      mv "$NGINX_DROPIN" "$BACKUP_DIR/multiace-plugin.conf.disabled"
      if nginx -t >/dev/null 2>&1; then nginx_reload; else
        mv "$BACKUP_DIR/multiace-plugin.conf.disabled" "$NGINX_DROPIN"
        die "nginx -t failed after removing duplicate drop-in; restored it"
      fi
    fi
    return 0
  fi
  tmp=$(mktemp)
  render_nginx_dropin >"$tmp"
  if [ -f "$NGINX_DROPIN" ] && cmp -s "$tmp" "$NGINX_DROPIN"; then
    rm -f "$tmp"
    log "nginx: $NGINX_DROPIN already up to date"
    return 0
  fi
  backup=""
  if [ -f "$NGINX_DROPIN" ]; then
    mkdir -p "$BACKUP_DIR"
    backup="$BACKUP_DIR/multiace-plugin.conf.bak"
    cp "$NGINX_DROPIN" "$backup"
  fi
  cp "$tmp" "$NGINX_DROPIN"
  rm -f "$tmp"
  if nginx -t >/dev/null 2>&1; then
    nginx_reload
    if [ -n "$backup" ]; then rm -f "$backup"; fi
    log "nginx: installed $NGINX_DROPIN and reloaded"
  else
    nginx -t || true
    if [ -n "$backup" ]; then mv "$backup" "$NGINX_DROPIN"; else rm -f "$NGINX_DROPIN"; fi
    die "nginx -t failed with new drop-in; rolled back (nginx not reloaded)"
  fi
}

install_initd() {
  [ -f "$APP_DIR/install/S66filamenthub-plugin" ] || die "$APP_DIR/install/S66filamenthub-plugin missing - run install_plugin.sh first"
  tr -d '\r' <"$APP_DIR/install/S66filamenthub-plugin" >"$INITD"
  chmod 755 "$INITD"
  log "installed $INITD"
}

http_get() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "$1"
  else
    wget -qO- -T 5 "$1"
  fi
}

start_and_probe() {
  "$INITD" restart
  i=0
  while [ $i -lt 10 ]; do
    if out=$(http_get "http://127.0.0.1:$PLUGIN_PORT/integration-manifest" 2>/dev/null); then
      log "manifest OK: $out"
      if http_get "http://127.0.0.1:7126/plugin/filamenthub/integration-manifest" >/dev/null 2>&1; then
        log "multiACE backend proxy /plugin/filamenthub/ OK"
      else
        log "WARNING: multiACE backend has not proxied /plugin/filamenthub/ yet (discovery cache TTL) - re-check in a minute"
      fi
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "plugin did not answer http://127.0.0.1:$PLUGIN_PORT/integration-manifest within 10s - see /home/lava/printer_data/logs/filamenthub-plugin.log"
}
