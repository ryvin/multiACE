# FilamentHub → multiACE Plugin — Design

**Date:** 2026-07-06
**Status:** Draft (awaiting review)
**Target:** decay71/multiACE **v0.99.2b** running on Davinci-U1 (PAXX 12-20)

## Problem

When loading a filament into an ACE slot, the operator knows *what* spool it is (from
their FilamentHub inventory) but has to hand-key the material/brand/color into the
multiACE dashboard — or leave the slot showing a generic/derived color. We want a
**button in the decay71 GUI that pulls the filament identity straight from FilamentHub**
and applies it to the slot, and (per decision) records back in FilamentHub *where* that
spool now physically lives.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Scope of v1 | **Manual picker (MVP)** — operator assigns FilamentHub spools to ACE slots by hand in a dedicated tab. No auto-sync in v1. |
| Direction | **Read + write-back** — pull identity into multiACE *and* set the spool's `location` in FilamentHub on assign. |
| Packaging | **Standalone reloadable plugin** — a sidecar HTTP service decay71 auto-discovers; decay71 updates never touch it. |

## Key finding — decay71 already ships both channels we need

Reading v0.99.2b source (`multiace/web/backend/main.py`) surfaced two facts that shape
the whole design:

1. **Slot identity write channel exists:** `POST /api/slot-override` accepts
   `{ace, slot, material, brand, subtype, color}`, persists to
   `slot_overrides.json`, and the native dashboard renders slot color with precedence
   **override > RFID > derived** (`_parse_state`). So we set slot identity *without*
   touching gcode or `head_source`. (`SlotOverride` model `main.py:559`; handler
   `main.py:1817`; render `main.py:322`.)
   - Companion: `GET /api/slot-override` (list), `DELETE /api/slot-override/{ace}/{slot}` (clear).
   - **Auto-clear (inherited for free):** decay71 drops an override when the slot ejects
     (`gate_status==0` >0.5s) or the head unloads (`head_source → None`). Labels always
     reflect what is physically seated; no stale data.

2. **Plugin system = port-scan + manifest + iframe tab.** decay71 scans
   `MULTIACE_PLUGIN_PORTS` (default `8089-8098`) on `127.0.0.1`, probes each for
   `GET /integration-manifest`, and renders discovered plugins as **iframe tabs**.
   Plugins may also call `GET /api/plugin-api/state`, `GET /api/plugin-api/aces`,
   `POST /api/plugin-api/gcode`. (Discovery `main.py:2033`; proxy `main.py:2093`;
   tab UI `index.html:24/472`.)

## Reuse — ryvin `spoolman.py` is the FilamentHub client, verbatim

`multiace_web/src/multiace_web/spoolman.py` (tested, `tests/test_filamenthub.py`)
already implements exactly what both decisions require:

- `list_spools()` → picker inventory: `[{spool_id, name, material, color, vendor,
  weight_remaining_g, location}]`, where `location` is `{ace, slot}` if the spool is
  placed on *this* printer.
- `assign_spool(spool_id, ace, slot)` → the **write-back**: sets
  `extra.filamenthub.location = {printer, ace, slot}` (handles FilamentHub's
  double-encoded text extra field via `_encode_fh`/`_decode_fh`).
- `unassign_slot(ace, slot)` → clears the location of whatever spool is bound there.

The plugin ports this module (or imports it) — no new FilamentHub integration code.

## Architecture

```
Browser (decay71 SPA at /multiace/)
   └── iframe  /plugin/filamenthub/           ← the FilamentHub tab UI (vanilla HTML/JS)
         │  reads occupancy: GET /multiace/api/plugin-api/state     (same-origin, direct)
         │  assign/unassign:  POST/DELETE /plugin/filamenthub/assign (backend orchestrates)
         ▼
FilamentHub plugin backend  (FastAPI, 127.0.0.1:8089)
   ├── GET  /integration-manifest  → {name:"filamenthub", label:"FilamentHub", ui_url:"/"}
   ├── GET  /            (+ assets) → serve the tab UI
   ├── GET  /spools                 → SpoolmanClient.list_spools()
   ├── POST /assign {spool_id,ace,slot}
   │        (1) SpoolmanClient.assign_spool(...)          → write-back to FilamentHub
   │        (2) POST http://127.0.0.1:7126/api/slot-override
   │              {ace,slot, material, brand:vendor, subtype, color}   → multiACE label
   └── POST /unassign {ace,slot}
            (1) SpoolmanClient.unassign_slot(...)         → clear FilamentHub location
            (2) DELETE http://127.0.0.1:7126/api/slot-override/{ace}/{slot}
   ↕ httpx →  FilamentHub  (base_url, printer_id from env)
```

**Why the backend orchestrates the two writes** (rather than the iframe JS doing both):
one endpoint = one place for error handling and ordering. If FilamentHub write-back
fails, we still surface a clear error and can choose whether to apply the multiACE label
anyway. The backend runs on the printer and reaches multiACE directly at
`127.0.0.1:7126`.

**Reads stay direct:** the iframe is same-origin with `/multiace/`, so occupancy is read
straight from `/multiace/api/plugin-api/state` — no proxy needed.

### Field mapping (FilamentHub spool → slot-override)

| slot-override field | source |
|---|---|
| `ace`, `slot` | chosen by operator in the picker |
| `material` | spool `material` (e.g. "PLA") |
| `brand` | spool `vendor` |
| `color` | spool `color` (`color_hex`), normalized to `#RRGGBB` |
| `subtype` | spool `name` (or blank) — SKU/variant slot; TBD in plan |

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_URL` | *(required)* | FilamentHub/Spoolman base URL |
| `MULTIACE_PRINTER_ID` | *(required)* | this printer's id used in `extra.filamenthub.location.printer` |
| `MULTIACE_URL` | `http://127.0.0.1:7126` | local multiACE web for slot-override writes |
| `FILAMENTHUB_PLUGIN_PORT` | `8089` | must sit in decay71's `MULTIACE_PLUGIN_PORTS` range |

## Deployment

- Own folder in the repo (e.g. `multiace_plugins/filamenthub/`), independent of both
  `multiace/` and `multiace_web/`, so a decay71 upgrade never disturbs it.
- Printer-side install: a BusyBox init script (e.g. `S66filamenthub-plugin`) that starts
  the sidecar on `127.0.0.1:8089`; deploy under `/userdata/` (survives PAXX overlay wipe).
- **nginx gotcha to verify on the printer:** decay71's iframe src is an absolute
  `/plugin/...` at origin root, but the shipped nginx conf only proxies `/multiace/`.
  During install we confirm (or add) an nginx location `/plugin/ → 127.0.0.1:7126` so the
  iframe loads. This is a decay71-core routing concern the plugin depends on; if decay71's
  own plugin tabs already work on this printer, the location already exists.

## Testing

- **Unit (pytest):** field-mapping (spool → slot-override), the manifest response, and the
  `/assign` orchestration with FilamentHub + multiACE both mocked (respx), including the
  failure ordering (FilamentHub 5xx, multiACE 5xx). Reuse the existing
  `test_filamenthub.py` patterns for the ported client.
- **E2E (Playwright, per project rule):** against the live decay71 GUI — the FilamentHub
  tab appears, the picker lists spools, assigning one colors the target slot on the
  dashboard (`source:"override"`), and the spool's location shows bound in FilamentHub.
  Pre-flight `print_stats.state`; abort unless safe (never during `printing`/`paused`).

## Out of scope (v1)

- Auto-sync from FilamentHub location bindings (the "Manual + auto-sync" option) — a clean
  follow-up: a poller that pushes overrides for spools tagged to this printer.
- Physically feeding filament (ACE_LOAD) — this plugin only labels/records identity; the
  filament is already seated by the operator.
- Editing FilamentHub inventory beyond the `location` field.

## Open items to confirm at build/deploy

1. FilamentHub base URL + `printer_id` values (deployment config).
2. Whether decay71's `/plugin/` nginx location already exists on this printer.
3. `subtype` mapping — spool `name` vs a dedicated FilamentHub SKU field.
