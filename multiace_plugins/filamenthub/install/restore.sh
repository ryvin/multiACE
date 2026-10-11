#!/bin/sh
# License: GPL-3.0
# Re-create the overlay-resident pieces of the FilamentHub plugin after a
# firmware (PAXX) update wiped /etc: the S66 init script and the nginx
# /plugin/ drop-in. Code, deps and state are read from /userdata, untouched.
# Usage (on printer, as root): sh /userdata/filamenthub-plugin/install/restore.sh
# Idempotent. Refuses to run while a print is printing/paused unless FORCE=1.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/common.sh"

require_root
guard_print_state

[ -d "$APP_DIR/src/filamenthub_plugin" ] || die "$APP_DIR/src missing - run install_plugin.sh from the repo instead"
PYTHONPATH="$APP_DIR/deps" "$PY" -s -c 'import fastapi, uvicorn, httpx, pydantic' 2>/dev/null \
  || die "$APP_DIR/deps cannot import fastapi/uvicorn/httpx/pydantic - run install_plugin.sh from the repo"

install_initd
ensure_nginx_dropin
start_and_probe
log "Restore OK."
