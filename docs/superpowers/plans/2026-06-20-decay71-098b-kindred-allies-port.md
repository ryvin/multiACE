# decay71 0.98b "Kindred Allies" — utilization plan

**Date:** 2026-06-20
**Supersedes/extends:** [`2026-05-13-decay71-feature-port.md`](2026-05-13-decay71-feature-port.md) (targeted decay71 **0.95b**)
**Baseline:** our fork `multiace/VERSION` = `0.80b` (running 0.81b) → decay71/main = **0.98b "Kindred Allies"**
**Goal:** Graft decay71 0.98b's wins onto our scaffolding (test suite, vanilla web console, autodry/Govee/HelixScreen stack) without regressing features they lack or our validated USB-reset mitigation.

## What "pull in the latest" means here

decay71's history is opaque bulk uploads ("Add files via upload" / "Update README.md") — a literal merge would clobber our test suite and tooling. So "pull" = **fetch + materialize a reference snapshot**, then graft surgically.

- `git fetch decay71 --tags` ✅ (decay71/main `ed3d847..9191532`; tags `V0.98b_Kindred_Allies`, `V0.97.3b_Hotfix_3`)
- Reference snapshot materialized at `.decay71-ref/` via `git archive decay71/main | tar -x -C .decay71-ref` (scratch dir; refresh with the same command). **Do not commit `.decay71-ref/`.**

## Decisions taken (2026-06-20)

1. **ACE 2 is coming.** The threaded engine + Protocol V2 port is a real scheduled wave (Wave 4), reopening the original Phase 1 (start-ACE pin removal). Headline effort.
2. **TPU is in use.** Manual Heads / TPU bypass is prioritized early (Wave 2).

## Status of the 2026-05-13 plan (carried forward)

| Phase (old plan) | Status | Evidence |
|---|---|---|
| 1 — USB engine + protocol abstraction | **PARTIAL / superseded** | Protocol files vendored (`6969102`), per-ACE serial cache (`5b1e024`,`ba9c182`), `fa_context` (`298579c`). **Start-ACE pin NOT removed** (`ace.py:552,628-650`); replaced by 1Hz keepalive (`ace_keepalive.py`, `47974e2`, instinct #7) + errno-5 reopen (`e9c4736`). |
| 2 — Hardened load/unload | **PARTIAL** | Snapshot/resume landed (`_snapshot_inner_resume_state`, `filament_feed_ace.py:732`, `3a14fe1`). Regrip/past-grip **attempted & reverted** (`filament_feed_ace.py:1068-1078`). |
| 3 — Per-ACE FA toggles | **DONE** | `138bd0c` (#65); `fa_print_disable`/`fa_load_disable`/`fa_debug` in `ace.cfg:41-64`. |
| 4 — Swaptimizer (`--optimize`/`--layer`) | **DONE** | `3ec7ed8` (#66); `multiace_postprocess.py:392,452`, tested. |
| 5 — Web UI parity | **PARTIAL** | Print-queue + Fix-Loadout wizard shipped (`b7e1380`,`app.js:1470`). **No** i18n / wiring overlay / `/api/snapshots`. |
| 6 — Calibration drop | **NOT STARTED** | `default_park_retract_length_mm: 700` (`ace.cfg:103`); gated on Phase-1 pin removal that never happened. |

## What is NEW in 0.98b (beyond the old plan's 0.95b scope)

- **ACE 2 / Protocol V2** (0.97b+): production protobuf-over-serial engine, vendor `1a86:55d3`, **stdlib-only** (hand-rolled varint/CRC), runs side-by-side with V1. `ace_protocol_v2.py` (518 ln). *Our* `ace_protocol_v2.py` is a divergent socket-daemon stub — would be **replaced**, not merged.
- **Manual Heads / TPU** (`ACE_SET_HEAD_MANUAL`, `head_is_manual`) — FA/retract bypass.
- **PTC printer-filament sync** (`_sync_ptc_to_active_ace`, wraps `SET_PRINT_FILAMENT_CONFIG`).
- **Resume/runout bug fixes** (`INNER_RESUME`, `_runout_disp`, `variable_is_runout`).
- **Online update** (`ACE_UPDATE_CHECK/APPLY`, `update_url_base`).
- **Flow calibration** in `extruder_ace.py` (`flow_calibration:*`, manual park-point cal).
- **ttyUSB adapter discovery** (`_read_usb_ids()` sysfs walk) — handles CH340/FTDI, not just CDC-ACM.
- **Swaptimizer v2** (`post_process_virtual_toolheads.py`, 2433 ln): tiered+fuzzy color-name matching, brute-force optimal swap-minimizing layout, Belady layer planner, live material-availability gate, auto-load injection, recommendation report.
- **i18n** grown to **EN/DE/ZH**, 366 keys, `i18n/{en,de,zh}.json`; `MULTIACE_SET_LANGUAGE` keeps Klipper popups in UI language.
- **Vue widgets** (ideas only — no Vue migration): SVG wiring overlay, editable/pausable command queue, saveable loadout snapshots, draggable display mirror, RFID-aware picker + provenance badges.
- **New tools:** `merge_ace_cfg.py` (config-preserving upgrades), `generate_testmatrix.py` (12-color cal plate), `multiace_v2d.py` / `v2_multidev_probe.py` (V2 bring-up).

## Guiding constraints (do not regress)

- **Keepalive mitigation (instinct #7) stays until the threaded engine is *proven* on the rig.** The ACE Pro idle ~5s USB reset is real; their per-ACE continuous reader/writer I/O is the intended replacement but is **unvalidated on Davinci-U1**. Engine swap is feature-flagged with keepalive fallback.
- **Preserve features theirs lacks:** `ace_status.py` (HelixScreen contract), extruder maintenance counters, autodry FSM + Govee bridge, visual-regression tooling, mobile-viewport work, `tip_refresh`, park/autofeed macros.
- **Printer safety:** every rig step pre-checks `print_stats.state`; no Klipper/Moonraker restart during `printing`/`paused` without confirmation.
- **Graft, don't migrate:** keep vanilla web stack and English-hardcoded fallbacks; i18n is a catalog + thin helper, not their `_t()` runtime.

---

## Wave 1 — Standalone low-coupling wins (no engine changes)

Ship independently; each is its own branch + PR.

| # | Item | Source → target | Effort | Risk |
|---|---|---|---|---|
| 1.1 | **ttyUSB adapter discovery** — port `_read_usb_ids()` sysfs walk | `.decay71-ref/.../ace_protocol.py` → `multiace/klipper/extras/ace_protocol.py` | S | low |
| 1.2 | **Config-preserving upgrade tool** | `.decay71-ref/.../tools/merge_ace_cfg.py` → `multiace/tools/` + installer hook | S | low |
| 1.3 | **Swaptimizer color matcher** — tiered + fuzzy color-name matching + "% fewer swaps" recommendation into our tested module (adapt to our `/api/slots` schema; keep greedy as fallback) | `post_process_virtual_toolheads.py` → `multiace_web/tools/multiace_postprocess.py` + new tests | M | low |
| 1.4 | **12-color calibration plate generator** | `.decay71-ref/.../tools/generate_testmatrix.py` → `multiace_web/tools/` | S | low |
| 1.5 | **Localized pause/runout messaging** (English now, string-table-ready) — `_emit_feed_pause`, `_runout_disp` | their `filament_feed_ace.py` / `filament_switch_sensor_ace.py` | S–M | low |

## Wave 2 — Manual Heads / TPU (prioritized)

Built on our shipped Phase-3 FA-toggle infra; runs on the **current** engine, re-validated after Wave 4.

- **2.1** Port `ACE_SET_HEAD_MANUAL` + `head_is_manual` and the FA/retract bypass paths from their `ace.py` / `filament_feed_ace.py`. New `[ace]` state, Config-tab surfacing via `CONFIG_KEY_HINTS`. **Effort M, Risk med** (touches every FA/retract path). Validate with a real TPU head on Davinci-U1.

## Wave 3 — Web parity (vanilla, no Vue)

| # | Item | Effort | Risk |
|---|---|---|---|
| 3.1 | **i18n catalog** — port ~140 `ui.*` keys (EN/DE/ZH) + add `GET /api/i18n` & `/api/i18n/{lang}` to `server.py` + vanilla `t(key,params)` helper + `[data-i18n]` sweep in `app.js`. Defer the ~225 `msg.*` firmware strings. | M | low |
| 3.2 | **SVG wiring overlay** — bezier slot→toolhead lines colored by filament, `ResizeObserver`-driven, on Dashboard | M | low |
| 3.3 | **RFID-aware picker + provenance badges** — cascading material/vendor/color modal from `/api/materials`; saving a value matching the tag clears the override (RFID stays source-of-truth) | M–L | low |
| 3.4 | **Match-tier labels + swap-savings summary** in existing Fix-Loadout wizard (`app.js:1470`) | S | low |

## Wave 4 — ACE 2 / Protocol V2 + threaded engine (HEADLINE — reopens Phase 1)

Feature-flagged behind `enable_threaded_engine` / `enable_ace_v2`; keepalive + pin path remain the default until the rig gate passes.

- **4.1** Replace our socket-daemon `ace_protocol_v2.py` stub with their production protobuf-over-serial engine; fold in the `_read_usb_ids` base from 1.1.
- **4.2** Build per-ACE threaded reader/writer engine in `ace.py` (`_reader_threads`/`_writer_threads`/`_writer_queues`/`_seq_lock`/`_cb_locks`) **behind the flag**, keepalive path as fallback.
- **4.3 🚦 RIG VALIDATION GATE (V1):** prove continuous per-ACE threaded I/O keeps **ACE Pro (V1)** buses warm through the ~5s idle reset on Davinci-U1 — multi-hour standby + repeated cross-ACE toolchanges, watch `multiace_usb.log` for `OSError errno 5`. **Proceed only if it holds.**
- **4.4** Remove the **start-ACE pin** (original Phase 1 deliverable) once 4.3 passes; retire keepalive (or keep as belt-and-suspenders).
- **4.5** Bring up the **ACE 2 (V2)** unit: enable V2 path, use `v2_multidev_probe.py` / `multiace_v2d.py` for device discovery + ID assignment; verify V1+V2 side-by-side.
- **4.6** **Phase 6 calibration drop:** re-evaluate `default_park_retract_length_mm: 700` now that pin-free per-ACE FA is available; relax/remove + README update.

## Wave 5 — Higher-coupling firmware (post-engine)

- **5.1** Wiggle/regrip extrusion-retry incl. ACE 'A' free-pull regrip — revisit the reverted Phase-2 regrip now that the engine supports it (M/med).
- **5.2** Hardened unload retry (pre-cool + heat-soak reset, `_swap_probe_temp`) (M/med).
- **5.3** PTC printer-filament sync (`_sync_ptc_to_active_ace`) (L/high — deep `print_task_config` coupling).
- **5.4** Smart `ACE_SWAP_HEAD` + purge/anti-ooze params (`swap_purge_length`, `swap_anti_ooze_retract`) (M/med).
- **5.5** `INNER_RESUME` + resume/runout bug-fix port (M/med).
- **5.6** Flow calibration in `extruder_ace.py` — reconcile with our maintenance counters (the two forks diverged here; not a clean superset) (M/med).

## Wave 6 — Larger web features (optional / later)

Editable+pausable command queue (`/api/macro-batch`), saveable loadout snapshots (`/api/snapshots/{name}/apply`), draggable display mirror (`/screen` proxy — printer-safety review for touch-forward), online update mechanism (`/api/update`, on-device debug-mode). Each L/med.

## Decision gates

- **G1 (Wave 4.3):** threaded engine must pass the V1 idle-reset rig gate before the pin is removed. Fail → keep keepalive, run V2 engine only on the V2 unit, leave V1 on the current model.
- **G2:** PTC sync (5.3) and online-update (Wave 6) run external scripts / mutate `printer.cfg` — treat as printer-safety-critical, `/ecc:security-scan` before merge.

## Risk register (delta from old plan)

| Risk | Mitigation |
|---|---|
| Threaded engine drops keepalive mitigation | Feature-flag + keepalive fallback + mandatory V1 rig gate (4.3) |
| Their V2 file replaces ours (divergent designs) | Vendor theirs wholesale; delete our socket-daemon stub; pin vendor/product `1a86:55d3` |
| Swaptimizer assumes `T=ace*4+slot` + their `/api/state` | Port *logic* (matcher/solver) into our tested module, not their 2433-ln file |
| i18n key paths ≠ our DOM | Mapping pass; our tabs (Activity/Diag/Hardware/Print-queue) need keys their catalog lacks |
| Wholesale merge regresses our-only features | Graft-not-merge; preserve `ace_status.py`, maintenance counters, autodry/Govee, visual-regression |
| Manual-heads touches every FA/retract path | Land on current engine first, re-validate after engine swap |

## Validation before shipping each wave

1. `pytest` green under `multiace_web/` (and any `multiace/tests/` touched).
2. `/ecc:quality-gate` against the diff; `/ecc:security-scan` for G2 items.
3. Playwright visual-regression / GUI smoke against the live printer for web waves.
4. Manual rig smoke: ≥1 full print with ≥1 cross-ACE toolchange before tagging.

## Out of scope

Full Vue migration; their nginx `auth_request`→Moonraker pipeline (keep bearer-token); their Klipper-side `_t()` runtime (catalog only).
