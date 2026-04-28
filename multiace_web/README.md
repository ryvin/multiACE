# multiACE Web Console

Web console for managing Anycubic ACE Pro filament changers on a Snapmaker U1 running multiACE.

Mobile-responsive UI accessible on the LAN at `http://<printer-ip>/multiace/` (or via your existing FilamentHub Cloudflare tunnel). Provides:

- Live state for ACE slots and toolheads
- Per-toolhead Load / Unload, ACE switching, dryer controls
- Activity feed of all multiACE events with error highlighting
- Inline `ace.cfg` editor with Klipper RESTART trigger
- Diagnostics view with state log, klippy.log slice, per-ACE detail

## Architecture

FastAPI backend on the printer (port 7126), reverse-proxied through nginx at `/multiace/`. Frontend is vanilla HTML/JS/CSS — no framework, no build step, matches FilamentHub's stack.

The backend tails `multiace_state.log` for activity events and polls `ACE_HEAD_STATUS` every 5s for current state. Commands proxy through Moonraker's existing gcode endpoint. WebSocket pushes live updates to all connected browsers.

## Local development

```bash
cd multiace_web
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                    # all backend tests
MOONRAKER_URL=http://192.168.1.171:7125 \
  MULTIACE_LOG_DIR=/path/to/synthetic/logs \
  uvicorn multiace_web.server:app --port 7126 --reload
```

Open http://localhost:7126/.

For frontend testing without a real printer, append synthetic events to your `MULTIACE_LOG_DIR/multiace_state.log` and watch the UI update in real time.

## Install on the printer

`install_multiace.sh` runs `install/install_web.sh` automatically as part of its flow. To install just the web console (skipping the rest):

```bash
ssh root@<printer-ip>
bash /tmp/multiace/multiace_web/install/install_web.sh
```

Prerequisites:
- `/oem/.debug` must exist (overlay persistence; the main multiACE installer ensures this).
- nginx must already be running (it is on PAXX firmware).

The installer creates a Python venv at `/userdata/multiace-web/venv/` (persistent partition; survives reboots), drops a systemd unit at `/etc/systemd/system/multiace-web.service`, and an nginx snippet at `/etc/nginx/conf.d/multiace.conf`.

## Configuration

Set via systemd unit `EnvironmentFile=-/userdata/multiace-web/app/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `MULTIACE_LOG_DIR` | `/home/lava/printer_data/logs` | Where multiACE writes its logs |
| `MULTIACE_CONFIG` | `.../config/extended/ace.cfg` | Path to ace.cfg for editor |
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Moonraker base URL |
| `MULTIACE_WEB_PORT` | `7126` | Port the FastAPI app binds |
| `MULTIACE_TOKEN` | (unset) | If set, requires `Authorization: Bearer <token>` |

## Security & known limitations (v0.1)

This console is designed for **LAN-only use** behind your home network's perimeter, optionally protected by `MULTIACE_TOKEN`. It is **not hardened against untrusted networks** — do not expose it directly to the internet.

- **HTML interpolation in some views.** The Toolheads card renderer interpolates filament-vendor and error strings (sourced from Klipper state) directly into HTML. If your G-code macros or print metadata contain hostile strings, they would render as HTML. The Config editor renders inputs via DOM construction (safe). The Activity feed and Diagnostics views use `textContent` (safe).
- **Token reload required.** The bearer token is read from `localStorage` once at page load. To update it, paste the new value into DevTools (`localStorage.setItem("multiace_token", "...")`) and refresh the page. There is no in-UI token entry form yet.
- **Config tab caches once.** The Config view fetches `/api/config` once per page load and caches the result. If you edit `ace.cfg` via SSH while the console is open, refresh the page to see the new values.
- **Reconnect storm on auth failure.** A WebSocket close due to bad token will retry with exponential backoff (1s, 2s, 4s, ..., 30s capped). If you've configured `MULTIACE_TOKEN` and the browser doesn't have it set, expect noisy reconnect attempts in the server logs until you fix the token.

For exposure beyond the LAN, use a reverse proxy with proper auth (e.g., Cloudflare Access, oauth2-proxy) in front of nginx — do not rely on `MULTIACE_TOKEN` alone for internet-facing deployment.

## Uninstall

```bash
bash /userdata/multiace-web/app/install/uninstall_web.sh
```

## License

GPL-3.0 — same as the parent multiACE project.
