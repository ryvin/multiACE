#!/bin/sh
# Deploy the FilamentHub plugin to the printer's persistent partition.
# Usage (on printer): sh install_plugin.sh
# Safe to run only when NO print is active (does a pip install + starts a service).
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/userdata/filamenthub-plugin
NGINX_DROPIN=/etc/nginx/fluidd.d/multiace-plugin.conf

echo "== Deploying to $APP_DIR =="
mkdir -p "$APP_DIR"
cp -r "$SRC/src" "$SRC/pyproject.toml" "$APP_DIR/"

echo "== Checking system python3 deps (reused from decay71 — no venv/pip) =="
PYTHONPATH="/home/lava/.local/lib/python3.11/site-packages" \
  /usr/bin/python3 -c 'import fastapi,uvicorn,httpx,pydantic; print("  deps OK", fastapi.__version__, pydantic.VERSION)' \
  || { echo "  ERROR: system python3 is missing fastapi/uvicorn/httpx/pydantic (decay71 web console must be installed first)"; exit 1; }

echo "== Registering init script =="
cp "$SRC/install/S66filamenthub-plugin" /etc/init.d/S66filamenthub-plugin
chmod +x /etc/init.d/S66filamenthub-plugin

echo "== Verifying nginx /plugin/ route (required for the iframe) =="
if grep -rqs "location /plugin/" /etc/nginx/ ; then
  echo "  OK: an nginx 'location /plugin/' already exists (decay71 plugin routing present)."
else
  echo "  Adding $NGINX_DROPIN -> proxy /plugin/ to 127.0.0.1:7126"
  cat > "$NGINX_DROPIN" <<'EOF'
location /plugin/ {
    proxy_pass http://127.0.0.1:7126;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
}
EOF
  nginx -t && { /etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload; }
fi

echo "== Starting plugin =="
/etc/init.d/S66filamenthub-plugin restart
sleep 2
# Fetch with whatever the box has — PAXX ships wget, not always curl.
if command -v curl >/dev/null 2>&1; then
  curl -s http://127.0.0.1:8089/integration-manifest && echo && echo "Install OK."
else
  wget -qO- --timeout=5 http://127.0.0.1:8089/integration-manifest && echo && echo "Install OK."
fi
