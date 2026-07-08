# Auto-Dry → multiACE plugin

A standalone decay71 plugin: per-ACE automatic humidity-triggered drying,
driven entirely over Moonraker (no serial, no import from `multiace_web`).
Ports the `multiace_web` fork's autodry FSM as a bounded, one-time port —
see [Direction A, item #1](../../docs/superpowers/reference/2026-07-07-decay71-vs-ryvin-comparison.md)
in the decay71-vs-ryvin comparison.

Standalone and reloadable — decay71 discovers it by scanning
`MULTIACE_PLUGIN_PORTS` (8089–8098) for a `GET /integration-manifest`, then
renders it as an iframe tab. decay71 upgrades never touch this plugin.
FilamentHub already occupies port 8089; this plugin defaults to **8090**.

## What it does

Each ACE gets its own independent FSM (`IDLE → WATCHING → DRYING → COOLDOWN`,
with a sticky `FAULTED`):

- **WATCHING**: humidity above `target_pct + hysteresis_pp` for N consecutive
  ticks (debounced against sensor blips / lid-open events) → triggers a dry.
- **DRYING**: issues `ACE_DRY ACE=<n> TEMP=<temp> DURATION=<duration_min>`
  via Moonraker. Ends on target-humidity reached, a safety time cap
  (`FAILED_LIMIT`), or insufficient progress after the requested duration
  (`FAILED_DELTA`, which faults the FSM until cleared).
- **COOLDOWN**: rate-limits re-triggering after a cycle ends.
- A daily run-time cap prevents runaway duty cycles.

Config is per-ACE and explicit (`target_pct` / `temp` / `duration_min` /
`enabled`) — this plugin does **not** infer dryer parameters from loaded
filament type/profile the way the source FSM does; that logic depended on
`head_source` data this Moonraker-only sidecar doesn't have a clean way to
read live.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Local Moonraker base URL |
| `MULTIACE_URL` | `http://127.0.0.1:7126` | Local multiACE web (read-only: `swap_in_progress`) |
| `AUTODRY_PLUGIN_PORT` | `8090` | Must be within decay71 `MULTIACE_PLUGIN_PORTS` (8089–8098) |
| `AUTODRY_ROUND_ROBIN` | off | `1/true/yes/on` to also drive non-active ACEs by round-robin `ACE_SWITCH`ing between them at standby. Off by default: only the currently-active ACE gets live telemetry on a single serial connection. |
| `AUTODRY_TICK_SEC` | `30` | Background tick interval. `0` disables the tick loop (status/config/dry endpoints still work; nothing auto-triggers). |
| `AUTODRY_STATE_PATH` | `.autodry_state.json` | Per-ACE FSM persistence file (plugin-local, v2 schema) |
| `AUTODRY_DEFAULT_TARGET_PCT` | `15` | Default target humidity %% for newly-seen ACEs |
| `AUTODRY_DEFAULT_TEMP_C` | `55` | Default dry temperature |
| `AUTODRY_DEFAULT_DURATION_MIN` | `240` | Default cycle duration |
| `AUTODRY_HYSTERESIS_PP` | `5` | Wake threshold = target + hysteresis |
| `AUTODRY_COOLDOWN_MIN` | `30` | Minutes between cycles |
| `AUTODRY_DEBOUNCE_REQUIRED` | `3` | Consecutive above-threshold ticks before triggering |
| `AUTODRY_MAX_RUN_MIN` | `720` | Safety cap on one drying run |
| `AUTODRY_DAILY_DUTY_MAX_MIN` | `1080` | Cap on total drying minutes per 24h, per ACE |
| `AUTODRY_MIN_DELTA_PCT` | `3` | Minimum humidity drop required by end of a full-duration run, else FAULTED |

`load_config()` fails fast (raises) on an out-of-range plugin port, a
negative tick interval, or an out-of-range default target percentage.

## API

- `GET /integration-manifest` → `{"name":"autodry","label":"Auto-Dry","version":"0.1.0","ui_url":"/"}`
- `GET /status` → `{"aces":[{ace, enabled, state, target_pct, temp_c, duration_min, humidity_pct, remaining_min, fault, last_run}, …]}`
- `POST /config {ace, target_pct?, temp?, duration_min?, enabled?}` → set per-ACE params, persisted immediately
- `POST /dry {ace}` → trigger a manual dry now (proxies to Moonraker's `ACE_DRY` macro, using that ACE's configured temp/duration); `409` if already drying
- `POST /reset-fault {ace}` → clear a `FAULTED` FSM back to `IDLE`

## Known limitations (read before relying on multi-ACE auto-trigger)

- **Per-ACE humidity source is unverified against real decay71 hardware.**
  This plugin reads `objects/query?ace` and looks for a `units[]` list with
  `environment.humidity_pct` per ACE (the shape already used by this repo's
  own `ace_status.py` SP2/SP3 contract). If decay71's actual `ace` object
  doesn't expose that shape, only the currently-active ACE will report a
  usable humidity reading (everything else shows `humidity_pct: null` in
  `/status` and its FSM just never arms) — safe degrade, but confirm the
  live shape on hardware before trusting non-active-ACE auto-dry.
- **No external humidity-sensor bridge.** The source fork
  (`multiace_web/autodryer.py` + `poller.py`) also reads an external Govee/
  SwitchBot sensor bridge over HTTP as an alternative to ACE-native
  humidity. That's out of scope here — vendoring it would pull in a whole
  separate HTTP integration surface for a "bounded, one-time" plugin.
- **No filament-type-driven profile selection.** The source FSM picks
  temp/duration from a per-filament-type profile table keyed off what's
  loaded in the ACE's slots (`head_source`). This plugin's `/config`
  instead takes `temp` / `duration_min` directly per ACE — simpler, and
  doesn't need multiACE's toolhead/slot state at all.
- **Round-robin mode is best-effort.** It issues `ACE_SWITCH TARGET=N`
  between ticks for non-active, disabled-during-print ACEs — same
  single-serial-connection constraint the firmware itself has (CLAUDE.md
  instinct #1). It never switches while `print_stats.state` is
  `printing`/`paused`.

## Local dev

```bash
cd multiace_plugins/autodry
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
MOONRAKER_URL=http://127.0.0.1:7125 MULTIACE_URL=http://127.0.0.1:7126 \
  python -m autodry_plugin        # serves 127.0.0.1:8090
```

## Printer install

Copy this folder to the printer, then (only when NO print is active):

```sh
sh install/install_plugin.sh
```

It deploys to `/userdata/autodry-plugin` (venv-free — reuses decay71's
system python3 and its already-installed fastapi/uvicorn/httpx/pydantic),
registers `/etc/init.d/S67autodry-plugin`, ensures an nginx
`location /plugin/` route exists (adds one if decay71 hasn't), starts the
sidecar, and curls the manifest to confirm.

Config defaults are baked into `install/S67autodry-plugin`; edit that file
to change ports/tick interval/round-robin, or export the corresponding env
var before invoking the init script.

Not deployed as part of this change — build + tested in-repo only.
