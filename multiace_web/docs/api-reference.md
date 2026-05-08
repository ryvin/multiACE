# multiACE Web Console — API Reference

All endpoints are under `/api/` (or `/ws` for the WebSocket). When the console
is mounted at `/multiace/` behind nginx, the actual URL is e.g.
`http://$DAVINCI_U1_HOST/multiace/api/state`. Same-origin under nginx, so browser
fetches work without CORS configuration.

If `MULTIACE_TOKEN` is set in `.env`, every endpoint under `/api/*` and the
`/ws` WebSocket require an `Authorization: Bearer <token>` header (or
`?token=<token>` query parameter for the WebSocket; browsers can't set headers
on WS handshakes).

---

## `GET /health`

Liveness probe. Always 200, no auth required.

```json
{"ok": true}
```

---

## `GET /api/state`

Current multiACE state — what's loaded where, gate status, sensors, last error.

**Response shape:**

```json
{
  "active_device": 0,
  "device_count": 1,
  "connected": true,
  "serial": "/dev/serial/by-path/...",
  "mode": "multi",
  "swap_in_progress": false,
  "auto_feed": false,
  "feed_assist": 1,
  "gate_status": [1, 1, 1, 1],
  "head_source": {
    "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "FFB400"},
    "1": {"ace": 0, "slot": 1, "type": "PLA", "color": "..."},
    "2": null,
    "3": null
  },
  "sensors": {"0": true, "1": true, "2": false, "3": false},
  "print_task_config": {
    "0": {"type": "PLA", "color": 4294506524, "vendor": "Snapmaker"},
    "1": {"type": "PLA", "color": 4293340957, "vendor": "Generic"},
    "...": "..."
  },
  "last_error": null,
  "last_action_at": "2026-04-28 10:31:16"
}
```

`color` is 0xAARRGGBB packed in the uint32 `print_task_config[h].color`. `4294967295`
(`0xFFFFFFFF`) is multiACE's "no color" sentinel; the frontend renders it as a
checkered swatch.

---

## `GET /api/events`

Recent activity events from `multiace_state.log`. Newest first.

**Query:** `?limit=N` (default 200, max 200)

**Response:**

```json
{
  "events": [
    {
      "id": 42,
      "ts": "2026-04-28 10:31:40",
      "action": "LOAD_HEAD",
      "params": {"head": 1, "ace": 0, "slot": 1},
      "active_device": 0,
      "device_count": 1,
      "...": "...full state snapshot at the time..."
    }
  ]
}
```

Each event embeds the full state at its timestamp, so the activity log doubles
as a state journal.

---

## `GET /api/print`

Summary of Klipper print state, multiACE dryer state, U1 cavity temperature,
and (when configured) external humidity. The single source of truth for the
Dashboard's Print panel, dryer card, and environment strip.

**Implementation:** proxies Moonraker
`/printer/objects/query?print_stats&virtual_sdcard&toolhead&ace&temperature_sensor cavity`,
extracts the meaningful fields, normalizes units, and folds in the cached
external humidity reading.

**Response:**

```json
{
  "state": "printing",
  "filename": "1234-abc_plate_1.gcode",
  "progress": 0.328,
  "print_duration": 24713.4,
  "total_duration": 25895.1,
  "eta_sec": 50603.5,
  "layer": 114,
  "total_layer": 417,
  "current_extruder": 0,
  "exception": null,
  "message": null,
  "dryer": {
    "status": "drying",
    "target_temp": 50,
    "duration_min": 240,
    "remain_min": 238,
    "remain_sec": 14289
  },
  "cavity_temp_c": 46.5,
  "humidity": {
    "configured": false
  }
}
```

**Field notes:**

- `state` — `printing`, `paused`, `complete`, `cancelled`, `standby`, `error` (Klipper's vocabulary).
- `progress` — 0.0–1.0 from `virtual_sdcard.progress`.
- `eta_sec` — `print_duration / progress - print_duration`. `null` when `progress < 0.001`.
- `current_extruder` — head index (0–3) computed from Klipper's `toolhead.extruder` (`extruder` → 0, `extruder1` → 1, etc.). `null` when unknown.
- `exception` — `null` when print is healthy. When Klipper has a fault: `{"code": 45, "message": "Extruder pickup failed", "level": 2}`. Klipper sometimes returns `{}` (empty); we normalize that to `null`.
- `dryer.duration` is in **minutes** (Klipper convention); `dryer.remain_time` from Klipper is in **seconds**. The endpoint normalizes to `duration_min` and `remain_min` for the dashboard, and exposes the raw `remain_sec` too if you want sub-minute precision.
- `cavity_temp_c` — from the U1's existing `temperature_sensor cavity` Klipper object. `null` if the sensor isn't reporting.
- `humidity` — see "External humidity" below.

**Errors:** 502 if Moonraker is unreachable or the query fails.

---

## `POST /api/command`

Run a multiACE Gcode macro by name. Validation: macro must match
`^[A-Z][A-Za-z0-9_]{0,63}$`. The strict regex prevents arbitrary G-code
injection through this endpoint.

**Body:**

```json
{"macro": "ACEC__Load_T1"}
```

**Response:** `{"ok": true, "result": "ok"}` (the `result` is whatever
Moonraker returned).

**Errors:**
- 422 — macro name doesn't match the regex
- 502 — Moonraker rejected the macro or is unreachable

For parameterized commands like `ACE_DRY ACE=0 TEMP=50 DURATION=240`, use
`/api/dry` instead (which is the only parameterized endpoint we expose).

---

## `POST /api/dry`

Start a parameterized ACE drying cycle. Maps to
`ACE_DRY ACE=N TEMP=T DURATION=D` and runs through Moonraker.

**Body:**

```json
{"ace": 0, "temp_c": 50, "duration_min": 240}
```

| Field | Type | Range |
|---|---|---|
| `ace` | int | 0..7 |
| `temp_c` | int | 30..120 |
| `duration_min` | int | 1..2880 (48 h cap) |

The backend's bounds are intentionally loose — Klipper / multiACE enforces its
own `max_dryer_temperature` (default 70 °C, in `ace.cfg`) and rejects requests
above that.

**Response:** `{"ok": true, "result": "ok", "gcode": "ACE_DRY ACE=0 TEMP=50 DURATION=240"}`

**Errors:**
- 422 — Pydantic validation (any field missing or out of range)
- 502 — Moonraker rejected the gcode (check `max_dryer_temperature`)

To stop a running dryer: `POST /api/command {"macro": "ACED__Dry_Stop"}`.

---

## `GET /api/config`

Read the current `[ace]` section of `ace.cfg`.

**Response:**

```json
{
  "values": {
    "feed_speed": "80",
    "retract_speed": "30",
    "load_length": "1500",
    "...": "..."
  }
}
```

All values are strings — type coercion is the editor's responsibility.

---

## `PUT /api/config`

Write changes to the `[ace]` section of `ace.cfg`, then trigger a Klipper
`RESTART` so the new config takes effect.

**⚠ This restarts Klipper, which interrupts any active print.** The frontend
shows a confirm dialog before submitting.

**Body:**

```json
{"values": {"feed_speed": "100"}}
```

Validation:
- Keys must already exist in `ace.cfg` (whitelist; prevents injection of new keys).
- Keys match `^[a-zA-Z_][a-zA-Z0-9_]{0,63}$`.
- Values are strings, max 256 chars, no `\n` `\r` or `#` (would inject lines / comments).

**Response on success:** `{"ok": true, "restarted": true}`

**Errors:**
- 400 — unknown config key
- 422 — Pydantic validation
- 500 — couldn't read/write the file
- 502 — file written but Klipper RESTART failed (config is on-disk, restart manually to apply)

---

## `GET /api/logs/{kind}`

Tail `<MULTIACE_LOG_DIR>/{kind}.log` via Moonraker's file API. Currently used
by the Diag tab to show the klippy.log slice.

**Path:** `kind` matches `^[a-z_]+$` (path traversal blocked).

**Query:** `?lines=N` (default 200)

**Response:**

```json
{"lines": ["2026-04-28 09:44:55 ...", "..."]}
```

---

## `WebSocket /ws`

Live state stream. On connect, the server sends an initial `state` message,
then pushes:

- `{"type": "state", "payload": <full state>}` — when multiACE state changes (poller, log tailer, manual refresh).
- `{"type": "event", "id": N, "ts": "...", "payload": <event>}` — when a new event lands in `multiace_state.log`.

The client sends `"ping"` text frames every 30 s to keep NAT/proxies happy;
the server replies `"pong"`. JSON messages from the client are ignored.

**Auth (when `MULTIACE_TOKEN` is set):** include `?token=<token>` in the URL,
or set the `Authorization: Bearer <token>` header (browsers can't set headers
on WS handshakes, so the query param is the practical path).

---

## External humidity adapter

`GET /api/print` returns a `humidity` field. When `MULTIACE_HUMIDITY_URL` is
unset:

```json
{"configured": false}
```

When set, the adapter fetches the URL (with optional `MULTIACE_HUMIDITY_AUTH`
header), caches the result for 30 s, and returns:

```json
{
  "configured": true,
  "ok": true,
  "humidity_pct": 47.2,
  "temp_c": 23.1,
  "label": "ACE Pro",
  "fetched_at": 1714326711.41
}
```

On fetch failure:

```json
{
  "configured": true,
  "ok": false,
  "error": "ConnectError: nope"
}
```

The adapter auto-detects three common payload shapes:

1. **Generic:** top-level `humidity`, `humidity_pct`, `rh`, `RH`, `temperature`, `temperature_c`, `temp`, `temp_c`.
2. **Home Assistant** (`/api/states/<entity>`): `state` + `attributes.device_class=humidity` (or `attributes.unit_of_measurement` containing `%`).
3. **SwitchBot Cloud** (`/v1.0/devices/<id>/status`): `body.humidity` and `body.temperature`.

For unusual shapes, set `MULTIACE_HUMIDITY_HUM_PATH` and
`MULTIACE_HUMIDITY_TEMP_PATH` to dot-paths (e.g.
`MULTIACE_HUMIDITY_HUM_PATH=readings.0.h`). List indices are supported as
numeric path components.

---

## Frontend → Moonraker direct calls

A few actions bypass this app and call Moonraker directly via the same nginx
origin:

| Frontend action | Moonraker endpoint | Reason |
|---|---|---|
| Pause | `POST /printer/print/pause` | Simpler than proxying |
| Resume | `POST /printer/print/resume` | Simpler than proxying |
| Cancel | `POST /printer/print/cancel` | Simpler than proxying |

These don't go through `MULTIACE_TOKEN` auth. Moonraker has its own API key /
local trust system; configure it there if you need auth for pause/resume.

---

## Curl recipes

```bash
# Health
curl http://printer/multiace/health

# State
curl http://printer/multiace/api/state | jq

# Run a macro
curl -X POST http://printer/multiace/api/command \
  -H 'Content-Type: application/json' \
  -d '{"macro":"ACE_HEAD_STATUS"}'

# Start a dry: ACE 0, 50°C, 4h
curl -X POST http://printer/multiace/api/dry \
  -H 'Content-Type: application/json' \
  -d '{"ace":0,"temp_c":50,"duration_min":240}'

# Read print state
curl http://printer/multiace/api/print | jq

# Tail klippy.log (last 500 lines)
curl 'http://printer/multiace/api/logs/klippy?lines=500'
```

With `MULTIACE_TOKEN` set, add `-H 'Authorization: Bearer <token>'` to every
call.
