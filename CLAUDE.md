# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

multiACE is a Klipper extension that enables multiple Anycubic ACE Pro filament changers on a Snapmaker U1 3D printer. It extends the SnapACE project with multi-device support, auto-load, RFID handling, per-ACE dryer settings, and print-start safety checks. Current version: 0.81b (Beta).

The repo has **two cooperating subprojects**:

- `multiace/` — Klipper firmware extension (Python modules + Klipper config + installer). Runs on the printer inside Klipper.
- `multiace_web/` — Optional FastAPI web console (`http://<printer-ip>/multiace/`) that observes multiACE state and proxies macros to Moonraker. Runs as a separate uvicorn service on the printer; installed automatically by the main installer.

The two communicate only via the filesystem (multiACE writes `multiace_state.log`, the web console tails it) and via Moonraker HTTP. There is no direct Python import across the boundary.

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
