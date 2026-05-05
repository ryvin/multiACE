# multiACE Web Console

Mobile-responsive web console for managing Anycubic ACE Pro filament changers
on a Snapmaker U1 running [multiACE](../multiace/).

Reach it at `http://<printer-ip>/multiace/` from any browser on your LAN.

## What it does

| Tab | What you'll see |
|---|---|
| **Dashboard** | Print state (filename, progress, layer, ETA, Pause/Resume/Cancel), filament grid (4 toolheads with color band, source slot, status), ACE slot strip, recent activity, dryer status (when active), environment strip (cavity temp + humidity) |
| **Activity** | Full multiACE event log, latest first, with red highlighting on `*_FAILED` events |
| **Dryer** | Per-ACE dryer profile picker (PLA / PETG / TPU / ABS / Nylon / PC / PVA / Quick / Custom), temp + duration overrides, Start / Stop. Profiles persist in browser localStorage |
| **Config** | Live `ace.cfg` editor — saving triggers a Klipper `RESTART` |
| **Diag** | Current state JSON, klippy.log tail, raw `ACE_HEAD_STATUS` / `ACE_LIST` / `ACE_CLEAR_HEADS` buttons |
| **Hardware** | Schematic SVG twin of the ACE Pro stack and the Snapmaker U1 — bowden tubes, couplers, slots, toolheads — with live state, animation during loads/unloads, and per-block Load/Unload buttons |

- **Hardware tab.** A schematic SVG twin of the ACE Pro stack and the Snapmaker U1.
  Each ACE slot has a bowden tube; tubes meet at per-toolhead couplers; one tube
  continues from each coupler to the U1. Source slots and destination toolheads
  pulse during load/unload, and the source tube animates as filament moves.
  Per-block Load/Unload buttons mirror the existing Dashboard buttons. The
  existing Dashboard tab is unchanged.

The dashboard is the home view and answers the five things you open the app to ask:
*is everything OK*, *what's printing*, *what's loaded where*, *what just happened*,
*what can I do next*. Action buttons reveal contextually — Resume only when paused,
Cancel only during a print, Stop drying only when a dry is running.

See [`docs/dashboard-guide.md`](docs/dashboard-guide.md) for screenshots and a
walkthrough. In-app, the `?` button next to the tabs opens a context-keyed
help modal with a one-liner for every control on every tab.

Other docs:

- [`docs/troubleshooting.md`](docs/troubleshooting.md) — field-tested
  recipes for the failure modes we've actually hit (load `move_extrude logic
  error`, stranded filament, ACE USB hang, Klipper soft-restart not picking
  up new `ace.py` options, etc.).
- [`docs/tip-refresh.md`](docs/tip-refresh.md) — the pre-load tip refresh
  feature: what it does, when it fires, config knobs.
- [`docs/api-reference.md`](docs/api-reference.md) — REST + WS surface.
- [`docs/auto-dry-design.md`](docs/auto-dry-design.md) — drying profiles
  and the per-ACE workflow.
- [`docs/hardware-bluetooth.md`](docs/hardware-bluetooth.md) — humidity
  sensor adapter integrations (Govee BLE, Home Assistant, SwitchBot).

## Architecture

```
Browser ←──HTTPS/WS──→ nginx (port 80, fluidd site)
                          │
                          ├─ /multiace/*   →  uvicorn :7126 (this app)
                          ├─ /printer/*    →  Moonraker :7125
                          └─ /server/*     →  Moonraker :7125
                          
uvicorn :7126 ──┬─→ tails  /home/lava/printer_data/logs/multiace_state.log
                ├─→ polls  Moonraker /printer/gcode/script (ACE_HEAD_STATUS) every 5s
                ├─→ polls  Moonraker /printer/objects/query (print_stats, ace, cavity)
                ├─→ reads  ace.cfg  for the Config editor
                └─→ optionally fetches an external humidity URL (see below)
```

Frontend is **vanilla HTML / JS / CSS** — no build step, no framework. Runtime
state is split into two parallel models:

- **multiACE state** (slots, head_source, sensors, swap_in_progress, last_error)
  flows from `multiace_state.log` via the `LogTailer`, and from periodic
  `ACE_HEAD_STATUS` polls. Pushed to clients over WebSocket.
- **Print state** (Klipper print_stats, virtual_sdcard, toolhead, ace,
  temperature_sensor cavity, humidity) is fetched on-demand via `GET /api/print`
  and polled by the dashboard every 4s.

Everything else (Activity, Dryer, Config, Diag) runs on the multiACE state alone.

## API

Quick reference; full spec in [`docs/api-reference.md`](docs/api-reference.md).

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | `{"ok": true}` liveness probe |
| `/api/state` | GET | Current multiACE state snapshot |
| `/api/events` | GET | Recent activity events (`?limit=200` default) |
| `/api/print` | GET | Print, dryer, cavity temp, humidity (Moonraker proxy) |
| `/api/command` | POST | `{"macro": "ACEC__Load_T1"}` — runs a multiACE Gcode macro |
| `/api/dry` | POST | `{"ace": 0, "temp_c": 50, "duration_min": 240}` — start a parameterized ACE_DRY |
| `/api/config` | GET / PUT | Read or write the `[ace]` section of `ace.cfg` (PUT triggers Klipper RESTART) |
| `/api/logs/{kind}` | GET | klippy.log slice (`?lines=200`) |
| `/ws` | WebSocket | Live state + event stream |

Pause / Resume / Cancel a print: the dashboard hits Moonraker's
`/printer/print/{verb}` endpoints directly (same origin via nginx). No multiACE
proxy in the way.

## Local development

```bash
cd multiace_web
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                            # 121 tests, ~13s
MOONRAKER_URL=http://192.168.1.171:7125 \
  MULTIACE_LOG_DIR=/path/to/synthetic/logs \
  uvicorn multiace_web.server:app --port 7126 --reload
```

Open <http://localhost:7126/>.

For frontend testing without a real printer, append synthetic events to your
`MULTIACE_LOG_DIR/multiace_state.log` and watch the UI update in real time.
The plan in `docs/superpowers/plans/2026-04-27-multiace-web-console.md` has
sample events you can paste in.

### Visual regression with Playwright

A headless Chromium can drive the live UI for end-to-end smoke testing:

```bash
pip install playwright
playwright install chromium
python tools/visual_regression.py http://192.168.1.171/multiace/
```

The script captures Dashboard / Activity / Dryer / Config / Diag at 1280×900 and
390×844 (iPhone 12 Pro), with explicit guards against clicking any action
button — read-only navigation only.

> **Safety note for testing on hardware.** Never trigger `Save & Restart` in
> the Config tab, never click slot/toolhead Load/Unload buttons, and never call
> `/api/dry` against a real printer mid-print without explicit reason. They
> work; that's the point. They also affect physical hardware.

## Install on the printer

`install_multiace.sh` runs `install/install_web.sh` automatically as part of
its flow. To install just the web console without the rest of multiACE:

```bash
ssh root@<printer-ip>
bash /tmp/multiace_web/install/install_web.sh
```

Prerequisites:
- `/oem/.debug` exists. The PAXX firmware wipes the overlay on boot otherwise;
  the main multiACE installer ensures it.
- nginx is running (default on PAXX).

The installer:
1. Copies the package into `/userdata/multiace-web/app/` (persistent partition).
2. Creates a Python venv at `/userdata/multiace-web/venv/` and `pip install -e`s
   the package into it.
3. Drops a BusyBox sysvinit script at `/etc/init.d/S62multiace-web`. The
   Snapmaker U1 PAXX firmware is Buildroot, not systemd; the script wraps
   `start-stop-daemon` in `setsid` so the daemon survives the install shell.
4. Drops an nginx snippet at `/etc/nginx/fluidd.d/multiace.conf`. The fluidd
   site already includes `fluidd.d/*.conf` so our `location /multiace/` block
   ends up inside the existing `server { listen 80; }`.
5. Reloads nginx and starts the service.

Manage the running service:

```bash
/etc/init.d/S62multiace-web {start|stop|restart|status}
```

Logs go to `/var/log/multiace-web.log` (uvicorn output is captured there by the
init script).

### Watchdog (auto-restart on crash)

A sister init script `S63multiace-web-watchdog` polls `S62multiace-web status`
every 60 s and restarts the daemon if it has stopped. Installed and started
automatically by `install_web.sh`. The PAXX firmware has no `crond` and no
service supervisor, so without this the daemon stays stopped until the next
reboot if it ever exits unexpectedly.

```bash
/etc/init.d/S63multiace-web-watchdog {start|stop|restart|status}
```

Watchdog activity is logged to `/var/log/multiace-web-watchdog.log`.

### Multi-host deploys

The console is single-tenant — one console per Snapmaker U1. To run the same
codebase against multiple printers, deploy independently to each. There's no
cross-printer view today.

### Embed in Mainsail / Fluidd

Both UIs read Moonraker's `[webcam]` blocks and render any with
`service: iframe` as a dashboard panel, so a single config entry surfaces the
console in **both** at once. Append `?embed=1` to strip multiACE's own
top bar + tab nav so it fits the panel slot; `?tab=hardware` (default)
picks which tab renders.

Add to `/home/lava/printer_data/config/moonraker.conf`:

```ini
[webcam multiACE]
service: iframe
stream_url: /multiace/?embed=1&tab=hardware
```

Reload Moonraker (`SAVE_CONFIG` is not needed — `[webcam]` is dynamic):

```bash
curl -X POST http://<printer-ip>:7125/server/restart
```

The "multiACE" panel then appears in both Fluidd's and Mainsail's
*Webcam* layout. Drag it where you want; each UI tracks panel layout
independently. Other valid `?tab=` values: `dashboard`, `activity`,
`dryer`, `config`, `diag`.

## Configuration

Edit `/userdata/multiace-web/app/.env` (sourced via `set -a; . .env; set +a`
so any new variables are auto-exported to the uvicorn process):

| Variable | Default | Purpose |
|---|---|---|
| `MULTIACE_LOG_DIR` | `/home/lava/printer_data/logs` | Where multiACE writes its logs |
| `MULTIACE_CONFIG` | `/home/lava/printer_data/config/extended/ace.cfg` | Path to ace.cfg |
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Moonraker base URL |
| `MULTIACE_WEB_PORT` | `7126` | Port the FastAPI app binds (uvicorn `--host 127.0.0.1`) |
| `MULTIACE_TOKEN` | (unset) | If set, requires `Authorization: Bearer <token>` on `/api/*` and `/ws` |
| `MULTIACE_HUMIDITY_URL` | (unset) | External JSON endpoint for humidity / ambient temp |
| `MULTIACE_HUMIDITY_AUTH` | (unset) | Optional `Authorization` header for the URL |
| `MULTIACE_HUMIDITY_HUM_PATH` | (auto) | Dot-path into the JSON for the humidity number |
| `MULTIACE_HUMIDITY_TEMP_PATH` | (auto) | Dot-path for ambient temperature |
| `MULTIACE_HUMIDITY_LABEL` | `Sensor` | Display label on the dashboard tile |

### Wiring a humidity sensor

The dashboard reads humidity from any HTTP+JSON source. Drop the values into
`.env` and restart the service. See
[`docs/hardware-bluetooth.md`](docs/hardware-bluetooth.md) for the full guide
to a Govee BLE sensor + USB BT dongle setup.

**Home Assistant** (any humidity entity):

```
MULTIACE_HUMIDITY_URL=http://homeassistant.local:8123/api/states/sensor.ace_pro_humidity
MULTIACE_HUMIDITY_AUTH=Bearer <long-lived-access-token>
MULTIACE_HUMIDITY_LABEL=ACE Pro
```

The auto-detector recognizes HA's `state` + `attributes.device_class=humidity` shape.

**SwitchBot Cloud** (Meter / Meter Plus):

```
MULTIACE_HUMIDITY_URL=https://api.switch-bot.com/v1.0/devices/<deviceId>/status
MULTIACE_HUMIDITY_AUTH=<your-switchbot-token>
MULTIACE_HUMIDITY_LABEL=ACE Pro
```

**Generic / DIY** (ESP32 + AHT20, Tasmota, anything that returns JSON):
`{"humidity": 47.2, "temperature": 24.1}` works out of the box. For nested
shapes use the `*_PATH` vars (`MULTIACE_HUMIDITY_HUM_PATH=sensor.0.h`).

The tile shows only when configured; on fetch failure it shows "sensor
offline" without disturbing anything else.

## Dryer profiles

The Dryer tab ships with sensible defaults sized for ACE Pro's 70 °C cap (60 °C
when paired with a non-rated humidity sensor — see hardware doc):

| Profile | Temp | Duration |
|---|---|---|
| PLA | 50 °C | 4 h |
| PETG | 65 °C | 6 h |
| TPU / TPE | 50 °C | 8 h |
| ABS / ASA | 70 °C | 8 h |
| Nylon (PA) | 70 °C | 12 h |
| PC | 70 °C | 8 h |
| PVA / BVOH | 45 °C | 6 h |
| Quick freshen | 50 °C | 1 h |

Profiles persist in browser localStorage under `multiace_dryer_profiles`. Use
the **Edit profiles…** button on the Dryer tab to customize (validated JSON;
errors don't clobber the saved values). **Reset to defaults** wipes the saved
profiles back to ship state.

The future "auto-dry" mode design (humidity-driven, filament-aware,
print-state-aware) is captured in
[`docs/auto-dry-design.md`](docs/auto-dry-design.md).

## Security & known limitations

LAN-only console. **Do not expose to untrusted networks.** For
internet-facing deployment, put a real reverse proxy with auth in front of
nginx (Cloudflare Access, oauth2-proxy, etc.) — `MULTIACE_TOKEN` is a fence
not a wall.

- **`renderToolheads` interpolates `cfg.vendor` / error fields into `innerHTML`.** Hostile filament metadata or G-code macro output would render as HTML. The Config, Activity, and Diagnostics renderers all use `textContent` or DOM construction; only the toolhead card has this surface. Threat model: collaborator-supplied gcode, not internet attacker.
- **Token reload required.** Bearer token is read from `localStorage` at page load. Update via DevTools and refresh.
- **Config tab caches once.** External edits to `ace.cfg` via SSH require a page reload to show in the editor. Saves go through correctly either way.
- **Reconnect storm on bad token.** WebSocket exponential backoff caps at 30 s but doesn't detect the close-code-1008 fatal-auth case. Set a token correctly and don't worry about it.

## Uninstall

```bash
bash /userdata/multiace-web/app/install/uninstall_web.sh
```

Removes the app, venv, init script, and nginx snippet. nginx is reloaded.
Klipper / Moonraker / multiACE are untouched.

## Tests

121 tests covering parser, config IO, log tailer, Moonraker client (incl.
`query_objects` for the dashboard), the optional bearer-token middleware, the
status poller, the FastAPI server (every endpoint plus the WebSocket auth
flow), and the humidity adapter (path resolution, common-shape detection,
caching, gating, error paths).

```bash
cd multiace_web
. .venv/bin/activate
pytest -v
```

2 tests skip on Windows because they exercise inotify-style file rotation
detection. They run on Linux.

## License

GPL-3.0 — same as the parent multiACE project.

## Changelog

## 0.7.0 — 2026-05-04

- New **Auto-dry** feature — humidity-driven, filament-aware filament
  maintenance. Watches chamber RH and runs `ACE_DRY` cycles when needed,
  with per-filament temp/duration profiles, single global RH target
  (default 15%), and a sticky FAULTED state for failed cycles.
- New `MULTIACE_AUTODRY_MODE` env (off/log/active) plus dry-run logging
  mode for safe rollout.
- Mainsail/Fluidd toasts for triggered/finished/failed transitions via
  Moonraker `[server_announcements]`.
- New endpoints: `GET /api/autodry`, `POST /api/autodry`.
- Persisted FSM state across multiace-web restarts.

## 0.6.1 — 2026-05-03

- Hardware tab: text contrast fix — labels on top of filament colors
  (white/yellow/light) now switch to dark text via luminance threshold.
- Hardware tab: dual drop-shadow halo on tubes so white/dark filaments
  remain visible on either light or dark page backgrounds.
- Embed mode (`?embed=1&tab=<view>`) — strips top bar + tab nav so the
  console can be iframed inside Mainsail/Fluidd as a webcam panel.

## 0.6.0 — 2026-05-02

- New Hardware tab with animated SVG twin of the ACE-U1 system.
