# multiACE Web Console — Design Spec

**Date:** 2026-04-27
**Target version:** multiACE web v1
**Status:** Approved scope, pending implementation plan

## Purpose

A self-hosted web console running on a Snapmaker U1 with multiACE installed. Provides full operational management of one or more Anycubic ACE Pro filament changers via mobile-responsive UI accessible from any device on the LAN (and externally via the existing Cloudflare tunnel that exposes FilamentHub).

The console replicates and extends the macro buttons currently in Fluidd, but with live state visibility, error persistence, diagnostics, and inline config editing — all without leaving Fluidd's narrow macro-list UX.

## Scope

### In scope (v1)
- Full live-state visibility for ACE Pro hardware: connection status, gate sensors, head sources, toolhead sensors, mode, auto-feed, swap-in-progress, feed-assist.
- Full command surface matching every macro in `ace.cfg` (ACEA__Switch_*, ACEB__Load_*, ACEC__Load_T*, ACEC__Unload_T*, ACEC__Unload_All, ACED__Dry_*, ACEE__Autofeed_*, ACEF__Mode_*) plus underlying gcode commands (ACE_CLEAR_HEADS).
- Activity feed showing the multiACE state-log event stream with filtering and error highlighting.
- Diagnostics view exposing state log, USB log, and klippy.log slices.
- Inline ace.cfg config editor with Klipper RESTART trigger (config reload, not full FIRMWARE_RESTART).
- Mobile-responsive layout (single-column ≤640px, multi-column ≥1024px).
- Real-time updates over WebSocket with auto-reconnect and state refetch.
- Persistent install on the U1 (`/userdata/multiace-web/venv/`) survives reboots via `/oem/.debug` overlay flag and a systemd unit.
- nginx reverse-proxy mount at `http://<printer-ip>/multiace/` (no separate port to remember).
- Optional bearer-token auth via `MULTIACE_TOKEN` env var; LAN-trust default.

### Out of scope (deferred)
- **v2:** Spoolman lookup, manual slot↔spool linker, spool enrichment on cards, FilamentHub deep-links.
- **v2.5:** ACE swap-aware spool tracker (extend FilamentHub's watcher OR multiACE registers its own watcher).
- **v3:** Historical analytics, multi-ACE detail views, pre-print readiness check.
- ACE-side OpenSpool tag reading (requires firmware reverse-engineering — likely never).

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (phone or computer, on LAN or via Cloudflare)       │
│   - vanilla HTML / JS / CSS (no build step)                  │
│   - WebSocket to /ws, REST to /api/*                         │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP / WS
                               ▼
┌──────────────────────────────────────────────────────────────┐
│  multiACE Web (FastAPI + uvicorn) on port 7126               │
│   - Tails multiace_state.log + multiace_usb.log              │
│   - Polls ACE_HEAD_STATUS via Moonraker every 5s             │
│   - Maintains in-memory CurrentState                          │
│   - Broadcasts state diffs to WS clients                      │
│   - Proxies command POSTs to Moonraker                        │
│   - Reads/writes ace.cfg (with FIRMWARE_RESTART)              │
└──────┬─────────────────────────────────────┬─────────────────┘
       │ tail / read / edit                  │ HTTP API
       ▼                                     ▼
   /home/lava/printer_data/                Moonraker (7125)
     logs/multiace_state.log                   │
     logs/multiace_usb.log                     ▼
     logs/klippy.log                       Klipper / multiACE
     config/extended/ace.cfg                   │
                                               ▼
                                            ACE Pro hardware
```

The web app **never talks to ACE hardware directly**. It reads logs multiACE writes, queries Moonraker for printer state, and proxies commands through Moonraker exactly like Fluidd does. It cannot break Klipper or the ACE.

nginx (already running on the U1 for Fluidd) gets a new `/multiace/` location block that proxies to `localhost:7126`.

## Components

### Backend (Python)

```
multiace_web/
├── server.py             # FastAPI app, routes, lifespan, WS broadcaster
├── state.py              # CurrentState dataclass, EventBuffer ring buffer
├── tailer.py             # Async log tailers w/ rotation detection
├── poller.py             # Periodic ACE_HEAD_STATUS poll task
├── moonraker.py          # Async Moonraker client (httpx)
├── auth.py               # Optional bearer token middleware
├── config_io.py          # ace.cfg reader/writer
├── static/
│   ├── index.html        # Single-page shell
│   ├── app.js            # ~500 LoC vanilla JS, no framework
│   └── style.css         # CSS Grid + custom properties for theming
└── install/
    ├── multiace-web.service       # systemd unit
    ├── nginx-multiace.conf        # reverse proxy snippet
    ├── requirements.txt
    └── install_web.sh             # invoked by parent install_multiace.sh
```

**Backend responsibilities:**

| Component | Purpose |
|---|---|
| `server.py` | FastAPI app. Mounts `/static`, exposes `/api/*`, `/ws`. Manages WS client set. |
| `state.py` | `CurrentState`: gates, head_source, sensors, mode, auto_feed, swap_in_progress, per-ACE connection info, last_error. `EventBuffer`: ring buffer of last 200 multiace_state.log entries. Single source of truth for what to push. |
| `tailer.py` | Async tasks `tail_state_log()` and `tail_usb_log()`. Detects rotation via inode-stat polling. Parses each new JSON line, updates state, appends to event buffer, broadcasts to WS. |
| `poller.py` | Every 5s: POSTs `ACE_HEAD_STATUS` to Moonraker, parses gcode response from klippy.log, updates `CurrentState`. Catches drift between actions. |
| `moonraker.py` | Wraps `httpx.AsyncClient`. Centralizes retries, timeouts, gcode/script POSTs, file API calls. |
| `auth.py` | If `MULTIACE_TOKEN` env var set, all `/api/*` and `/ws` require `Authorization: Bearer <token>`. Otherwise open. |
| `config_io.py` | Reads `ace.cfg`, parses key/value pairs, writes back preserving formatting/comments. |

### HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serve index.html |
| `GET` | `/api/state` | Full current state (page load + WS reconnect) |
| `POST` | `/api/command` | `{"macro": "ACEC__Load_T1"}` → proxied to Moonraker |
| `GET` | `/api/events?since=<id>&limit=<n>` | Event slice from ring buffer |
| `GET` | `/api/logs/{kind}?lines=N` | Tail logs: kind ∈ {state, usb, klippy} |
| `GET` | `/api/config` | Current ace.cfg values |
| `PUT` | `/api/config` | Update ace.cfg + trigger FIRMWARE_RESTART |
| `GET` | `/api/aces` | Per-ACE detail: connection state, firmware, USB path, last heartbeat |
| `WS` | `/ws` | Bidirectional. Server pushes `{type: state\|event\|error}`, client pings every 30s |

### Frontend (vanilla JS)

Three files, no build step. Matches FilamentHub's tech stack.

`index.html` — semantic shell. Wires up tabs, panels, navigation. Static markup; JS binds data via standard DOM manipulation.

`app.js` — ~500 LoC. Modules:
- `ws.js`-style section: connection management, exponential backoff reconnect, state refetch on reconnect.
- `render.js`-style: render functions per section (slots, toolheads, activity, dryer, config, diagnostics).
- `commands.js`-style: button handlers, optimistic UI, confirm dialogs for destructive actions.
- Responsive helpers: `isDesktop()` matches `(min-width: 1024px)`; resize listener re-renders on breakpoint cross.

`style.css` — CSS Grid for layout, CSS custom properties for theming (light/dark via `prefers-color-scheme`), 44px+ touch targets, semantic class names.

### UI sections

**Phone layout (≤640):** horizontal swipe nav between sections (same pattern as FilamentHub), dot indicator + tap-to-jump at bottom, persistent action bar with Unload All + Auto-feed toggle.
**Desktop layout (≥1024):** sidebar nav, persistent activity feed, multi-column main pane.

| Section | Phone | Desktop |
|---|---|---|
| **Header** | connection dot, active ACE, mode | same, in sidebar |
| **Slots** (4 cards) | full-width tiles | 1×4 row in main pane |
| **Toolheads** (4 cards) | full-width tiles | 2×2 grid in main pane |
| **Activity feed** | dedicated tab | always-visible right panel |
| **Dryer** | dedicated tab | collapsible card |
| **Config** | dedicated tab | collapsible drawer |
| **Diagnostics** | dedicated tab | collapsible drawer |

### Slot card content
- Slot index (0-3) + active-ACE indicator
- Gate sensor state (filled / empty / reading)
- RFID detection state (none for OpenSpool tags — see deferred work)
- Loaded → Toolhead mapping (or "not loaded")
- Action buttons: Load, Unload, Retry (if last action failed)

### Toolhead card content
- Toolhead index (T0-T3)
- Loaded source (which ACE / slot)
- Sensor state (filament present at toolhead)
- Last error (sticky until next successful load)
- Action buttons: Load, Unload

### Dryer panel
- Per-ACE toggle (start/stop)
- Custom temp slider (default from config; clamped to `max_dryer_temperature`)
- Custom duration slider (default from config)
- Live progress (if multiACE state log emits dryer events)

### Config panel
- Editable fields with current values from ace.cfg
- Per-toolhead override section (load_length_0, load_length_1, ...)
- Per-ACE override section (dryer_temp_0, dryer_duration_0, ...)
- Save button: writes ace.cfg, prompts for FIRMWARE_RESTART confirmation, fires it.

### Diagnostics panel
- State log: last 100 entries, JSON-pretty rendered, filterable by action type
- USB log: last 50 entries
- klippy.log slice: tail of last 200 lines or "since last error" anchor
- Per-ACE status: firmware version, USB serial path, last heartbeat, connection state
- Buttons: "Run ACE_HEAD_STATUS", "Run ACE_LIST" → captures gcode response

## Data flow

### Initial page load
1. Browser GET `/` → static HTML
2. Browser GET `/api/state` → full snapshot
3. Browser opens WS `/ws`
4. Browser renders all sections from snapshot

### Live updates
1. multiACE writes to `multiace_state.log`
2. Backend `tailer.py` reads new JSON line, parses, updates `CurrentState`, appends to ring buffer
3. Backend broadcasts `{type: state, payload: {...}}` and `{type: event, payload: {...}}` to all connected WS clients
4. Browser receives, updates DOM via render functions
5. Periodic `poller.py` triggers `ACE_HEAD_STATUS` every 5s; if state drifted (no recent log activity), updates `CurrentState` and broadcasts.

### Command flow
1. User taps Load button on T1 card
2. Browser optimistically disables button, shows spinner
3. Browser POST `/api/command` with `{macro: "ACEC__Load_T1"}`
4. Backend proxies to Moonraker `POST /printer/gcode/script?script=ACEC__Load_T1`
5. Moonraker returns ok/error → backend returns to browser
6. multiACE actually performs load, writes state log entry on completion
7. tailer picks up entry, broadcasts; browser sees real result, clears spinner

### Reconnection
1. WS drops (network, screen lock, etc.)
2. Browser shows yellow "reconnecting" banner
3. Exponential backoff: retry at 1s, 2s, 4s, 8s, capped at 30s
4. On reconnect: browser GET `/api/state` to refetch (don't trust stale state)
5. WS reattaches; banner clears

### Config edit flow
1. User edits a value in Config panel, taps Save
2. Browser POST `/api/config` with full updated values
3. Backend writes new `ace.cfg`, calls `RESTART` via Moonraker (config-only changes don't need MCU restart, just klippy reload)
4. Browser shows banner "Klipper restarting (~10s)" then refetches state when /printer/info reports ready

## Persistence and install

The U1 wipes `/oem/overlay/*` on every boot unless `/oem/.debug` exists. multiACE's main install already requires this. The web console install:

1. Creates Python venv at `/userdata/multiace-web/venv/` (28GB persistent partition; survives reboots)
2. `pip install -r requirements.txt` → fastapi, uvicorn[standard], httpx, pydantic
3. Copies `multiace_web/` to `/userdata/multiace-web/app/`
4. Drops systemd unit at `/etc/systemd/system/multiace-web.service` (this lives on overlay; relies on `/oem/.debug` to persist)
5. Drops nginx config at `/etc/nginx/conf.d/multiace.conf` proxying `/multiace/` → `localhost:7126`
6. `systemctl enable --now multiace-web.service`
7. `systemctl reload nginx`

The parent `install_multiace.sh` calls `install/install_web.sh` after its existing steps. Uninstall reverses all of the above.

### Environment / config
- `MULTIACE_LOG_DIR` — default `/home/lava/printer_data/logs`
- `MULTIACE_CONFIG` — default `/home/lava/printer_data/config/extended/ace.cfg`
- `MOONRAKER_URL` — default `http://127.0.0.1:7125`
- `MULTIACE_WEB_PORT` — default `7126`
- `MULTIACE_TOKEN` — optional; if set, requires `Authorization: Bearer <token>` on all API/WS

## Failure modes

| Scenario | Detection | UI behavior |
|---|---|---|
| Moonraker unreachable | httpx connection error | Red "printer offline" banner, all command buttons disabled |
| ACE Pro disconnected | `state.connected=false` in log | "ACE offline" badge on slot cards |
| WS dropped | `onclose` event | Yellow "reconnecting" banner, polling fallback every 5s |
| Load failed | LOAD_HEAD_FAILED log entry | Affected toolhead card flips red with error message + "Retry" button. Sticky until next successful action on that toolhead |
| Concurrent action attempted | `state.swap_in_progress=true` | All command buttons disabled with tooltip "swap in progress" |
| Log rotation | inode change in tailer | Re-open file, continue tailing |
| Spoolman unreachable (v2) | API call fails | Slot cards lose enrichment; rest of UI works |
| Klipper restart | `/printer/info.state != ready` | "Klipper restarting" banner; refetch state on ready |

## Concurrency

Multiple browsers connected simultaneously: all receive the same WS broadcasts, see the same state. Two users tapping commands at once: Klipper's gcode queue handles serialization, but multiACE's `swap_in_progress` flag means the second command will see disabled buttons by the time it tries. Race condition window is sub-100ms; acceptable.

## Security

- LAN-trust default, matching Moonraker's posture.
- Optional `MULTIACE_TOKEN` for shared-LAN environments or external Cloudflare tunnel access.
- All commands proxy through Moonraker (which enforces its own trusted-clients list).
- Frontend never holds credentials; if token required, it's prompted on first visit and stored in `localStorage`.
- HTTPS via nginx + existing Cloudflare tunnel cert.

## Testing strategy

No test framework on the printer. Testing is:
1. **Local backend dev**: run `uvicorn server:app --reload` on a workstation pointed at the printer's Moonraker. Use `curl` for HTTP API, `websocat` for WS.
2. **Frontend manual**: load `http://localhost:7126/`, exercise all flows by triggering events server-side (`echo '...' >> multiace_state.log` for synthetic events).
3. **On-printer integration**: deploy via install script, exercise full flow with real ACE Pro hardware. Verify each macro's button works end-to-end.
4. **Regression checklist** (committed to repo):
   - Page load shows current state within 2s
   - Each command button reaches Moonraker and returns response within 1s
   - WS reconnects within 5s of network drop
   - Config edit + save reloads Klipper without manual intervention
   - Failed load surfaces error on the right toolhead card
   - Mobile layout doesn't horizontal-scroll on a 360px viewport

## Open questions for v1 implementation

- **ACE_HEAD_STATUS poll output capture:** Klipper gcode commands print to gcode_response. The poller must subscribe to Moonraker's gcode_response feed (WS subscription) or parse from klippy.log tail. Decision: WS subscription is cleaner.
- **Config write — atomicity:** ace.cfg edit → klippy reload. If file write succeeds but reload fails, user sees partial state. Decision: write to `ace.cfg.tmp`, fsync, atomic rename, then reload. Roll back to `ace.cfg.bak` on reload failure.
- **Event ID scheme for resumption:** ring buffer entries get a monotonic ID per backend lifetime. WS clients can request `since=<last_id>` on reconnect to fill gaps. IDs reset on backend restart; client treats `since` as best-effort.

## Risks

- **U1 firmware update wipes our install.** Mitigated by `/oem/.debug` flag + persistent `/userdata/` location, but a major PAXX update could still break things. Install script must be re-runnable and idempotent.
- **multiACE protocol changes upstream.** State log JSON format is the contract. If multiACE changes field names, we break. Mitigation: pin a multiACE version in install README; CI check would catch this if it existed.
- **WS scaling.** Single backend, single broadcaster. Tens of concurrent clients fine; doesn't need to scale further.
- **Config edit corrupts ace.cfg.** Atomic write + backup mitigates. Manual recovery via SSH always possible.
- **External Cloudflare exposure broadens attack surface.** Token auth handles it; user must opt in by setting `MULTIACE_TOKEN`.

## Future-proofing

- HTTP API designed as the contract; frontend is one consumer. CLI tools, dashboards, or integrations can use the same `/api/*` and `/ws`.
- WS message format includes `type` discriminator; new event types can be added without breaking clients.
- Spoolman / FilamentHub integration (v2) hooks in cleanly: backend gains a `spoolman.py` module, frontend gains a "Spool" badge on each card. No architectural change needed.
- ACE swap-aware spool tracking (v2.5) extends the existing tailer: when a SWITCH event triggers a slot change for an active toolhead, push a `_SPOOL_TRACKER_VARS` update.
