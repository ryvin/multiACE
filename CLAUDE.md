# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

multiACE is a Klipper extension that enables multiple Anycubic ACE Pro filament changers on a Snapmaker U1 3D printer. It extends the SnapACE project with multi-device support, auto-load, RFID handling, per-ACE dryer settings, and print-start safety checks. Current version: 0.81b (Beta).

The repo has **three cooperating subprojects**:

- `multiace/` — Klipper firmware extension (Python modules + Klipper config + installer). Runs on the printer inside Klipper.
- `multiace_web/` — Optional FastAPI web console (`http://<printer-ip>/multiace/`) that observes multiACE state and proxies macros to Moonraker. Runs as a separate uvicorn service on the printer; installed automatically by the main installer.
- `multiace_plugins/` — Optional standalone sidecar plugins (`filamenthub/`, `autodry/`), each its own FastAPI/uvicorn service. On a decay71-based build they are auto-discovered (decay71 scans plugin ports 8089–8098 for `GET /integration-manifest` and renders each as an iframe tab under `/plugin/<name>/`); decay71 upgrades never touch them. Each is installed independently via its own `install/install_plugin.sh`.

The firmware and web console communicate only via the filesystem (multiACE writes `multiace_state.log`, the web console tails it) and via Moonraker HTTP. The sidecar plugins are even more loosely coupled — they never import multiACE Python and talk only over HTTP (Moonraker + the web console's `/api`). There is no direct Python import across any of these boundaries.

## Repository Structure

### Firmware side (`multiace/`)

- `klipper/extras/ace.py` — Core ACE controller (2300+ lines). USB device detection (vendor 28e9, product 018a), multi-device management, Gcode command registration, state persistence, RFID, filament loading/unloading, feed assist, dryer control.
- `klipper/extras/filament_feed_ace.py` — Filament feed system: motor control, LED feedback, sensor input, load/unload state machines.
- `klipper/extras/filament_switch_sensor_ace.py` — Filament detection and runout handling, ACE-aware to avoid false triggers during mid-print swaps.
- `klipper/kinematics/extruder_ace.py` — Extended extruder kinematics with switch-history tracking and maintenance counters.
- `config/extended/ace.cfg` — Klipper config: `[ace]` section + Gcode macros (groups A–G for alphabetical Fluidd ordering).
- `config/extended/multiace/ace_vars.cfg` — Klipper `save_variables` persistence (mode, active device, head→source mapping).
- `config/extended/multiace/ace_mode_switch.sh` — Swaps between ACE and normal (stock) mode by copying the appropriate Python modules and clearing caches.
- `install_multiace.sh` / `uninstall_multiace.sh` — Installer/uninstaller. The installer also invokes `multiace_web/install/install_web.sh`.

### Web console (`multiace_web/`)

- `src/multiace_web/server.py` — FastAPI app: `/health`, `/api/state`, `/api/events`, `/api/print`, `/api/command`, `/api/dry`, `/api/config`, `/api/logs/{kind}`, `/ws`.
- `src/multiace_web/state.py` — In-memory multiACE state model (slots, head_source, sensors, swap_in_progress, last_error). Updated by the tailer + poller, broadcast over WebSocket.
- `src/multiace_web/tailer.py` — `LogTailer` for `multiace_state.log` (handles rotation).
- `src/multiace_web/poller.py` — Periodic `ACE_HEAD_STATUS` poll via Moonraker.
- `src/multiace_web/moonraker.py` — Async Moonraker client (`gcode/script`, `objects/query`, print verbs).
- `src/multiace_web/config_io.py` — Read/write of the `[ace]` block of `ace.cfg`. PUT triggers a Klipper RESTART.
- `src/multiace_web/auth.py` — Optional bearer-token middleware (`MULTIACE_TOKEN`).
- `src/multiace_web/static/` — Vanilla HTML/JS/CSS frontend (no build step, no framework). Tabs: Dashboard, Activity, Dryer, Config, Diag.
- `tests/` — pytest suite (~121 tests, ~13s).
- `install/` — Printer-side install: `install_web.sh`, `S62multiace-web` (BusyBox sysvinit), `nginx-multiace.conf` snippet.
- `tools/visual_regression.py` — Playwright-based read-only screenshot capture across viewports (Dashboard / Activity / Dryer / Config / Diag at 1280×900 and 390×844). Also captures the `Activity → ACE A` filter chip state and the `Diag → ACE B` dropdown selection.
- `tools/e2e_dual_ace.py` — Manual Playwright golden-path for the dual-ACE GUI: verifies both ACE blocks render, the FilamentHub `📖` deep-link opens with `?picker=ace&printer=&ace=&slot=` query params, and issuing `ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=0` via the chevron menu makes `head_source[0]` resolve. Pre-flights `print_stats.state`; aborts unless safe.

### Sidecar plugins (`multiace_plugins/`)

Each plugin is a self-contained FastAPI/uvicorn service with its own `pyproject.toml`, `src/<pkg>/`, `tests/` (pytest + respx), `static/` frontend, and `install/` (BusyBox init script `S6x<name>-plugin` + `install_plugin.sh`). They never import multiACE Python — HTTP only.

- `filamenthub/` — port **8089**. Adds a FilamentHub tab: pick a spool from FilamentHub/Spoolman inventory for an ACE slot (`POST /assign`/`/unassign` write both sides). The Phase-4 **`POST /pull`** mirrors FilamentHub's `GET /fleet/api/ace-state` winners into multiACE `/api/slot-override` labels — **label-only, zero filament motion**. Auto-pull-on-open sends `prune=false` (additive; reports would-be clears as `stale`); the explicit Pull button sends `prune=true` (full reconcile, scoped to ACEs the seam covers). Deployed + live-verified on Davinci-U1.
- `autodry/` — port **8090**. Per-ACE humidity-triggered drying over Moonraker only. Each ACE has its own FSM (`IDLE → WATCHING → DRYING → COOLDOWN`, sticky `FAULTED`); `POST /dry` triggers a manual cycle, `POST /config` sets per-ACE `target_pct`/`temp`/`duration_min`/`enabled`, `POST /reset-fault` clears a fault. Requires an external Govee humidity bridge (`MULTIACE_HUMIDITY_URL`) — the ACE Pro's built-in humidity reading is unusable. Built + tested in-repo; deploy status tracked in project memory.

See each plugin's `README.md` for env vars and install steps. Design/plan docs live under `docs/superpowers/{specs,plans}/`.

## Common commands

### Web console — local dev (Linux/WSL or Windows)

```bash
cd multiace_web
python -m venv .venv
. .venv/bin/activate                       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                                     # full suite
pytest -v tests/test_server.py             # one file
pytest -k "humidity"                       # by keyword
pytest tests/test_server.py::test_state    # one test

MOONRAKER_URL=http://<printer-ip>:7125 \
  MULTIACE_LOG_DIR=/path/to/synthetic/logs \
  uvicorn multiace_web.server:app --port 7126 --reload
# open http://localhost:7126/
```

Two pytest tests skip on Windows because they exercise rotation-detection paths that need POSIX file semantics; they run on Linux/WSL.

### Web console — visual smoke against the live printer

```bash
pip install playwright && playwright install chromium
python multiace_web/tools/visual_regression.py http://<printer-ip>/multiace/
```

The script is hard-coded to navigate read-only — it must NOT click Save & Restart in the Config tab, slot Load/Unload buttons, or `/api/dry`. Per the project's e2e testing rule, use this (or a Playwright session) to verify UI changes against real hardware before declaring a task complete.

### Sidecar plugins — local dev/test

```bash
cd multiace_plugins/filamenthub          # or multiace_plugins/autodry
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                    # filamenthub: 54 tests; autodry: pytest suite
python -m filamenthub_plugin              # serves 127.0.0.1:8089 (autodry: 8090)
```

Printer install (per-plugin, only when no print is active): copy the folder over and run `sh install/install_plugin.sh`. It deploys to `/userdata/<name>-plugin`, registers `/etc/init.d/S6x<name>-plugin`, ensures an nginx `location /plugin/` route, starts the sidecar, and confirms the manifest.

### Firmware side — no build/test/lint

There are no automated tests, CI, linters, or build steps for `multiace/`. Validation is manual on hardware. Iteration loop is:

1. Edit `multiace/klipper/extras/*.py` or `multiace/config/extended/ace.cfg` locally.
2. SCP the changed file(s) to the printer (replacing `/home/lava/klipper/klippy/extras/<name>.py` or the corresponding config path).
3. Restart Klipper (`systemctl restart klipper`) **only if no print is in progress** — see Snapmaker U1 safety rules in the user's global CLAUDE.md.
4. Watch `klippy.log`, `multiace_state.log`, `multiace_usb.log` in `/home/lava/printer_data/logs/`.

### Installation onto a printer

```bash
# Push the multiace/ folder to the printer, then SSH in:
bash /tmp/multiace/install_multiace.sh        # installs firmware AND web console
bash /tmp/multiace/uninstall_multiace.sh      # restores stock files via *_pre_multiace.py backups
```

The web console can be installed independently with `bash /tmp/multiace_web/install/install_web.sh`. It deploys to `/userdata/multiace-web/` (persistent partition; PAXX wipes overlayfs on reboot otherwise) and registers `/etc/init.d/S62multiace-web` plus an nginx snippet at `/etc/nginx/fluidd.d/multiace.conf`.

## Key architectural patterns

- **Klipper module system.** Files under `extras/` and `kinematics/` are loaded by Klipper as extensions. They register Gcode commands, subscribe to events (`klippy:ready`, `klippy:disconnect`, `print_stats:*`), and interact with the Klipper reactor — never use blocking I/O on the main thread.
- **Canonical device mapping.** ACE units are indexed by USB path on startup. The path→index map is **locked** after a 20s startup wait so a USB reset cycle that hides one ACE briefly cannot cause index drift mid-session. `ace_device_count` in `ace.cfg` is required for multi-ACE setups so the lock waits for the full set.
- **Start-ACE pinning during a print.** The ACE that was active when the print started keeps its serial connection open for the entire print. Toolchanges to other ACEs proceed without feed_assist (extruder pulls through bowden) but pay no USB disconnect/reconnect cost. The current limitation is documented in `README.md`'s "Known Limitations" and is targeted to be lifted in v0.82.
- **File-based mode switching.** ACE-mode vs Normal-mode works by `cp`-ing either ACE or stock versions of `filament_feed.py` / `filament_switch_sensor.py` / `extruder.py` into Klipper's paths and clearing `__pycache__`. **Requires reboot.**
- **State persistence.** Klipper `save_variables` holds runtime state (active device, head source, mode). Extruder usage data persists to JSON.
- **Serial protocol.** Communicates with ACE Pro via pyserial at 115200 baud using JSON command/response. Responses processed asynchronously via the Klipper reactor.
- **Audit logging.** Two rotating file loggers (`multiace_state.log`, `multiace_usb.log`) — 1 MB × 3 backups. Controlled by `state_debug` and `usb_debug` flags in `[ace]`. **Keep both enabled** — they are the primary diagnostic surface and the web console depends on `multiace_state.log` being present.
- **Two-process web architecture.** The web console **never** imports multiACE Python. It tails `multiace_state.log`, polls Moonraker every 5s for `ACE_HEAD_STATUS`, polls every 4s for print/cavity/dryer state, and proxies macros via Moonraker's `gcode/script`. Pause/Resume/Cancel skip the proxy and hit Moonraker directly through the same nginx vhost.
- **Frontend is two parallel state models.** multiACE state (slots, head source, sensors) flows from the log tailer + ACE_HEAD_STATUS polls and pushes via WebSocket. Print state (Klipper `print_stats`, virtual_sdcard, toolhead, ace, cavity temp, humidity) is fetched on-demand via `GET /api/print` every 4s. Activity / Dryer / Config / Diag tabs only need the multiACE state stream.

## Dependencies

- **Firmware:** `pyserial`, the Klipper framework (assumed present on the printer), Python stdlib only otherwise.
- **Web console:** `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic` v2, `websockets`. Dev: `pytest`, `pytest-asyncio`, `pytest-mock`, `respx`. Requires Python 3.11+.

## Operating against the live printer

The user's global CLAUDE.md spells out the Snapmaker U1 safety protocol — always check `print_stats.state` before any operation that could disturb a print, and never restart Klipper / Moonraker / multiace-web during `printing` or `paused` without explicit confirmation. That rule applies to every SSH or Moonraker call this repo's tooling drives. Read-only operations (log tail, status query, snapshot, file copy to non-active dirs) are always safe.

### Printer host env var

The multiACE rig (Davinci-U1) is identified by the `DAVINCI_U1_HOST` env var in this repo's docs and tools. Set it once per shell:

```bash
export DAVINCI_U1_HOST=192.168.1.136   # current wired NIC; was 192.168.1.171 on wifi
```

- All plan/spec/reference docs use `$DAVINCI_U1_HOST` in their curl/scp/ssh examples (export the var before running them).
- All `multiace_web/tools/*.py` scripts read `DAVINCI_U1_HOST` with `192.168.1.136` as the fallback default.
- The other U1 (Snapdragon-U1) is unrelated to multiACE; if it ever needs scripting, it gets its own parallel `SNAPDRAGON_U1_HOST` var rather than overloading this one.

When local DNS is set up (e.g. `davinci-u1.local`), update the var and (optionally) the tools' default values; the env-var indirection means no doc/tool churn is needed for IP changes.

## License

GPL-3.0 — all derivative works (firmware and web) must maintain GPL-3.0. License headers required in modified Python files.


## ECC Integration

**Rule packs in effect** (from `~/.claude/rules/ecc/`):
- `common` — coding-style, code-review, security, testing, git-workflow
- `python` — Python-specific patterns, security, testing
- `web` — for `multiace_web` dashboard work (FastAPI + frontend)

**Primary skills to lean on for this project:**
- `verification` and `tdd` — state machine + USB lifecycle bugs hide in races; tests beat intuition
- `documentation-lookup` — Klipper internals and Moonraker API surfaces change; look them up, don't guess
- `python-patterns` and `python-django` adjacent patterns for async/threading
- `frontend-slides` and UI skills for dashboard work
- `search-first` — before changing any state-clearing logic, search audit-log call sites

**ECC commands to run for this project:**
- `/ecc:plan "<task>"` before any refactor of `state.py`, the swap-park flow, or USB reconnect logic
- `/ecc:harness-audit` once after installing — seeds the build/test commands into project memory
- `/ecc:quality-gate` before commits that touch `ace.py`, `state.py`, or the StatusPoller
- `/ecc:security-scan` (AgentShield) before any change that touches printer.cfg writers

**Project-specific instincts (import these once via `/ecc:instinct-import`):**
1. **Start-ACE lock for the duration of a print** (still applies post-keepalive). v0.81b keeps a single USB connection to the ACE that was active when the print started. Other ACEs print without feed_assist. Do not introduce mid-print reconnects without an explicit redesign — this was a deliberate workaround for the ACE Pro USB reset cycle bug. (See instinct #7 for the standby-time variant that's now mitigated by the keepalive.)
2. **Cumulative cross-slot coupling drift.** Retracting slot N mechanically drifts neighboring slots. Over many swap-park cycles, untouched slots can lose grip and need manual reseat. Any new swap logic must consider drift accumulation, not just per-operation correctness.
3. **`default_park_retract_length_mm` is geometry-specific.** Davinci-U1 = 700mm. Too short blocks other ACEs; too long loses ACE drive grip. Never bake a default for arbitrary printers — calibrate per geometry.
4. **State force-clears on SWITCH-family terminal audits + `SERIAL_WRITE_FAILED`.** Firmware emits these while `swap_in_progress` is still True with no matching clear event. Audit-event handlers must force-clear, not wait. `SERIAL_WRITE_FAILED` was added to the terminal list in the 2026-05-17 round-robin work because the ACE Pro USB reset trips the write path mid-swap and the audit lands with `sip=True` baked in.
5. **Leg-2 waits on `head_source[targetHead]` update.** Smart-swap leg 2 races against the audit-log tailer. The fix is in `_waitForSwapLeg1Propagation` — do not remove without replacement.
6. **`swap_park_available` is probed at lifespan startup**, not lazily on first chevron click. Don't move it back to lazy probing — the 5s StatusPoller tick creates a UX dead zone.
7. **ACE Pro idle USB reset every ~5s — needs 1Hz keepalive** (issue #70, fixed in commit 47974e2). The ACE Pro firmware resets its USB interface every 4-5 seconds when it sees no host traffic, regardless of host autosuspend settings. The kernel re-enumerates the device (often with a different ttyACM name), invalidating any cached fd. Holding `serial.Serial` open with no I/O is NOT sufficient — verified 2026-05-17 with a pyserial side-process: within 1s, next `in_waiting` raised `OSError errno 5`. **Any code that caches ACE serials MUST send periodic traffic (≤3s interval).** Our `_keepalive_tick` writes encoded `get_status` every 1s + drains `in_waiting`. Without it, fast-path cached serials are useless and round-robin auto-dry crashes Klipper.
8. **`tick_one_ace` overrides `Inputs.active_device` + `swap_in_progress`** before invoking `tick_fsm`. The poller calls `tick_one_ace(N)` right after `await moonraker.run_gcode("ACE_SWITCH TARGET=N")` returns, but `state.swap_in_progress` / `state.active_device` are updated by the audit-log tailer (async). Without the override, `target_active` would be False and the FSM never advances. Do not remove that `dataclasses.replace(inputs, active_device=ace_idx, swap_in_progress=False)` block in autodryer.py.
9. **autodry persistence has two file shapes (v1 single-FSM, v2 per-ACE manager).** `/userdata/multiace-web/.autodry_state.json` is read by both `load_persisted_state` (legacy, returns `mode=off` if v2-shaped) and `AutoDryer.load_manager` (per-ACE). The v1 reader produces misleading "off" output on v2 files. **Always probe via `/api/autodry?ace=N` (per-ACE view)**, never the bare `/api/autodry` legacy endpoint.
10. **uvicorn under `start-stop-daemon -b` ignores stderr redirection** (`/proc/<pid>/fd/{1,2}` → `/dev/null`). The `>>/var/log/multiace-web.log 2>&1` in S62multiace-web applies to start-stop-daemon's own output, not the daemonized child. `log.exception` is invisible. For diagnostics, either (a) configure a Python `FileHandler` in server.py startup, or (b) write directly to `/tmp/diag.log` from the suspect code path (chmod 666 so user `lava` can write). `multiace_state.log` is the only Python logging that reliably surfaces because it uses its own `RotatingFileHandler`.
11. **Auto-dry round-robin is opt-in via `MULTIACE_AUTODRY_ROUND_ROBIN={1,true,yes,on}`** (poller.py). Per-ACE autodry FSMs only advance via round-robin during standby — so when "auto-dry never fires" comes up, check **both** the env var AND the keepalive log lines (`KEEPALIVE_OPEN`, `KEEPALIVE_INIT`) in `multiace_usb.log`. The `cmd_ACE_SWITCH` catch-all (commit 1c131ba) prevents shutdowns from failed swaps but doesn't make the dryer fire; that requires the keepalive (instinct #7) AND the `run_ace_dry` wiring in server.py.

**Verification before shipping:**
1. Run pytest suite under `multiace/` and `multiace_web/`.
2. `/ecc:quality-gate` against the diff.
3. Manual smoke: at least one full print with at least one cross-ACE toolchange before tagging a release.

**Security notes:**
- This plugin writes to `printer.cfg` and runs Klipper macros. Treat any change that affects file writes or macro emission as printer-safety-critical.
- Never log raw filament profiles or printer serials in shared logs.
