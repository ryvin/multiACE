#!/bin/sh
# License: GPL-3.0
# Deploy the FilamentHub plugin to the printer's persistent partition.
# Usage (on printer, as root): sh install_plugin.sh      [FORCE=1 to bypass the print guard]
# Idempotent. Refuses to run while Moonraker reports a print printing/paused.
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
. "$SRC/install/common.sh"

LOCAL_SITE=/home/lava/.local/lib/python3.11/site-packages
DEPS_CHECK='import fastapi, uvicorn, httpx, pydantic; print(fastapi.__version__, pydantic.VERSION)'

require_root
guard_print_state
[ "$SRC" != "$APP_DIR" ] || die "run install_plugin.sh from a repo copy (e.g. /tmp/filamenthub), not from $APP_DIR; use $APP_DIR/install/restore.sh to re-create /etc pieces"

log "== Deploying code to $APP_DIR =="
mkdir -p "$APP_DIR/state" "$APP_DIR/install"
stage="$APP_DIR/.src.new"
rm -rf "$stage"
cp -r "$SRC/src" "$stage"           # includes src/filamenthub_plugin/static
rm -rf "$APP_DIR/src"
mv "$stage" "$APP_DIR/src"
cp "$SRC/pyproject.toml" "$APP_DIR/"
for f in S66filamenthub-plugin install_plugin.sh restore.sh common.sh; do
  tr -d '\r' <"$SRC/install/$f" >"$APP_DIR/install/$f"
  chmod 755 "$APP_DIR/install/$f"
done

log "== Vendoring python deps into $APP_DIR/deps =="
# -s: ignore the user site so the check proves deps/ (not ~/.local) satisfies imports.
if PYTHONPATH="$LOCAL_SITE" "$PY" -s -c "$DEPS_CHECK" >/dev/null 2>&1; then
  stage="$APP_DIR/.deps.new"
  rm -rf "$stage"
  cp -a "$LOCAL_SITE" "$stage"
  if ! PYTHONPATH="$stage" "$PY" -s -c "$DEPS_CHECK" >/dev/null 2>&1; then
    rm -rf "$stage"
    die "copied $LOCAL_SITE but imports fail from the copy; deps/ left unchanged"
  fi
  rm -rf "$APP_DIR/deps"
  mv "$stage" "$APP_DIR/deps"
  log "vendored $LOCAL_SITE -> $APP_DIR/deps"
elif PYTHONPATH="$APP_DIR/deps" "$PY" -s -c "$DEPS_CHECK" >/dev/null 2>&1; then
  log "$LOCAL_SITE not importable; keeping existing $APP_DIR/deps (imports OK)"
else
  die "fastapi/uvicorn/httpx/pydantic not importable from $LOCAL_SITE and no usable $APP_DIR/deps.
  Reinstall the decay71 multiACE web console (it pip-installs them for user lava), then re-run this script."
fi
log "deps: $(PYTHONPATH="$APP_DIR/deps" "$PY" -s -c "$DEPS_CHECK")"

log "== Registering init script =="
install_initd

log "== nginx /plugin/ route =="
ensure_nginx_dropin

log "== Starting plugin =="
start_and_probe
log "Install OK."
