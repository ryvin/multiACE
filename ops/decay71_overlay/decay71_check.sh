#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Report-only: is a newer decay71 multiACE out, and will our overlay patches
# still apply to it? Never installs anything and never touches the printer
# beyond one read-only HTTP GET.
#
# Usage: ops/decay71_overlay/decay71_check.sh [host]
#   host defaults to $DAVINCI_U1_HOST, then 192.168.1.136.
# Exit: 0 up to date, 10 update available (patches OK), 11 update available
#       but a patch reports `unknown` (re-check the patch first), 1 error.
set -euo pipefail

HOST="${1:-${DAVINCI_U1_HOST:-192.168.1.136}}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
APP_JS_PATH="multiace/web/frontend/app.js"
ACE_PY_PATH="multiace/klipper/extras/ace.py"

resp="$(curl -fsS --max-time 30 "http://$HOST/multiace/api/update/check")" || {
    echo "ERROR: cannot reach http://$HOST/multiace/api/update/check" >&2; exit 1; }
line="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(next((l for l in json.load(sys.stdin).get("status_lines", []) if l.startswith("current=")), ""))')"
current="$(printf '%s' "$line" | sed -n 's/.*current=\([^ ]*\).*/\1/p')"
latest="$(printf '%s' "$line" | sed -n 's/.*latest=\([^ ]*\).*/\1/p')"
[ -n "$current" ] && [ -n "$latest" ] || { echo "ERROR: unexpected update/check reply: $resp" >&2; exit 1; }
current="${DECAY71_INSTALLED_OVERRIDE:-$current}"   # testing only: pretend an older install
echo "installed: $current   latest: $latest"

if [ "v${current#v}" = "v${latest#v}" ]; then
    echo "up to date"; exit 0
fi

git -C "$REPO" fetch -q decay71 --tags
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
git -C "$REPO" show "$latest:$APP_JS_PATH" > "$tmp" || {
    echo "ERROR: $latest has no $APP_JS_PATH - the web layout changed, re-check the overlays" >&2; exit 11; }

rc=10
for p in web_queue_batch_patch swap_park_patch; do
    state="$(python3 "$HERE/$p.py" "$tmp" --check || true)"
    echo "  $p against $latest: $state"
    case "$state" in unpatched|partial|patched) ;; *) rc=11 ;; esac
done
ace_tmp="$(mktemp)"; trap 'rm -f "$tmp" "$ace_tmp"' EXIT
if git -C "$REPO" show "$latest:$ACE_PY_PATH" > "$ace_tmp" 2>/dev/null; then
    state="$(python3 "$HERE/airlog_chew_patch.py" "$ace_tmp" --check || true)"
else
    state="missing"
fi
echo "  airlog_chew_patch against $latest: $state"
case "$state" in unpatched|partial|patched) ;; *) rc=11 ;; esac
echo "update available - follow 'Upgrading decay71' in $HERE/README.md"
exit $rc
