# FilamentHub Desired-vs-Actual Reconciliation — Design (M1 Phase 1)

**Date:** 2026-07-16
**Status:** approved (brainstorming), ready for implementation plan
**Objective served:** *least errors / ease of use* — never display (or imply) the wrong filament for a slot.
**Scope owner:** the `filamenthub` sidecar plugin only. No firmware, no `multiace_web`, no decay71 backend changes.

## Goal

Make the FilamentHub plugin display the **truth** about each ACE slot by owning its own "desired" state and reconciling it against live physical occupancy — so a pulled label for a not-yet-loaded slot is shown as *"Expected: … (not loaded)"* instead of silently vanishing, and an occupied-but-unidentified slot invites identification instead of reading blank.

## Context / the defect this fixes (verified 2026-07-16)

The deployed backend is **stock decay71** (`.decay71-ref/…/backend/main.py`; the printer does not run the `ryvin/multiace` fork right now). Its state builder runs an eject-debounce: any slot reading `gate == 0` has its slot-override **deleted from disk** 0.5 s later (`EJECT_DEBOUNCE_S = 0.5`, `_drop_override_if_present`). The plugin's Pull writes desired labels into that same override store and then calls `render()`, whose `/api/plugin-api/state` poll triggers the delete. Net: a Pull that reports "applied 2" has its empty-slot labels destroyed within one poll, with no error. Confirmed live — `0_2 = SnapSpeed Red` applied, gone minutes later.

Because decay71 is stock upstream and not editable here, the fix lives entirely in ryvin-owned plugin code: **stop treating decay71's override store as the source of truth for desired state.**

## Non-goals (explicit — deferred to later phases, do not build now)

- **Dry-run / preview-then-commit Pull** (diff table before writing). Phase 2.
- **Any decay71 backend change or upstream PR** to stop the eject-debounce or fix the *dashboard* grid. The decay71 dashboard will still GC empty-slot overrides; only the plugin tab shows reconciliation. A decay71 upstream issue is a phase-2 follow-up.
- **Firmware `head_source` reconciliation / print-start gating** (that is M1's firmware arm, a separate spec).
- **Auto-load actuation** — this feature moves zero filament. Label/display only.
- **Fleet / multi-printer views.**

## Architecture — two layers, never merged destructively

- **Desired** (owned by the plugin, persisted): the FilamentHub winners captured at each Pull, written to a plugin-local JSON file. Survives decay71's GC because it is the plugin's own file, not decay71's override store.
- **Observed** (read live, never persisted): physical occupancy + material/color/RFID from decay71 `GET /api/plugin-api/state`.

Reconciliation is computed **server-side in the plugin backend** (pure function, unit-testable) and rendered by the frontend. The plugin frontend stops depending on decay71's override store to know what was pulled.

## Components

### C1 — Desired-state store (`desired_store.py`, new)

Plugin-local persistence, same pattern as `autodry`'s `persistence.py`.

- Path from config: `Config.desired_state_path`, env `FILAMENTHUB_DESIRED_PATH`, default `.filamenthub_desired.json` (plugin working dir).
- File schema (v1):
  ```json
  {
    "schema": 1,
    "printer": "u1-1",
    "slots": {
      "0_2": {"ace":0,"slot":2,"spool_id":110,"material":"PLA","brand":"Snapmaker","subtype":"SnapSpeed Red","color":"#FF0000"},
      "0_3": {"ace":0,"slot":3,"spool_id":91,"material":"PLA","brand":"Snapmaker","subtype":"SnapSpeed Pearl White","color":"#F8F8FF"}
    }
  }
  ```
  Keyed `"<ace>_<slot>"` (decay71 override-key convention). Values are the existing override-payload shape plus `spool_id`.
- API: `load_desired() -> dict[str, dict]` (missing file → `{}`; corrupt file → log + `{}`, never raise); `save_desired(printer: str, slots: dict[str, dict]) -> None` (atomic write: temp + rename, writes the full `slots` map it is given). The store module is deliberately dumb — the scoped merge (which keys to keep/drop) is computed by the caller in C5, not here, so it stays a trivial roundtrip to test.

### C2 — Observed reader (`MultiAceClient.get_state`, extend existing client)

Add one method to `multiace_client.py`:
`async def get_state(self) -> dict` → `GET {base}/api/plugin-api/state` (decay71 serves it on `:7126`), returns the parsed body; raises `httpx.HTTPError` on failure (callers degrade — see error handling).

### C3 — Pure reconciler (`reconcile_slots`, extend `reconcile.py`)

`reconcile_slots(desired: dict[str, dict], observed_aces: list[dict]) -> list[dict]`

For every slot in the union of (observed slots ∪ desired keys), classify:

| `recon_state` | Condition |
|---|---|
| `VERIFIED` | occupied ∧ desired ∧ (no RFID identity ∨ RFID material+color match desired) |
| `ASSERTED` | occupied ∧ desired ∧ no RFID identity to confirm against |
| `CONFLICT` | occupied ∧ desired ∧ RFID identity present ∧ (material or normalized color differ) |
| `UNKNOWN_LOADED` | occupied ∧ no desired |
| `EXPECTED_NOT_LOADED` | empty ∧ desired |
| `EMPTY` | empty ∧ no desired |

- "occupied" ≡ observed slot `state != "empty"`.
- "RFID identity present" ≡ observed `rfid == 1` **and** the observed `material` (and/or `color`) is non-empty (decay71 fills these from the tag).
- Color comparison uses `normalize_color` on both sides before comparing.
- Each returned row: `{ace, slot, recon_state, display_name, display_material, display_color, desired: {...}|null, observed: {state, material, color, rfid}|null}`. `display_*` prefers desired for the label-bearing states, observed for `UNKNOWN_LOADED`, blank for `EMPTY`.
- Pure: no I/O, fully unit-testable (mirrors the existing `plan_reconcile` discipline).

### C4 — Endpoint `GET /slots` (new, in `app.py`)

Fetches observed (C2) + desired (C1), returns `{ "slots": reconcile_slots(...), "observed_ok": bool }`. On decay71 fetch failure: `observed_ok=false`, and slots are returned from desired alone (every desired slot as `EXPECTED_NOT_LOADED`, occupancy unknown) — never a 5xx to the UI.

### C5 — Pull becomes honest + persists desired (`POST /pull` in `app.py`)

Unchanged: reads FilamentHub ace-state winners, brand-enriches, best-effort writes decay71 overrides for winners (still useful for occupied slots the dashboard can show). **Added:**
1. After computing winners, compute the merged desired map — `load_desired()`, upsert each winner's `payload+spool_id`, then drop only desired keys that fall **within the pruned ACEs** (the `aces_covered` set when present, else the winners' ACE indices) and are no longer winners — and `save_desired(printer, merged)`. This is the durable record that survives decay71's GC, and the scoped-merge mirrors the existing additive/prune rule so uncovered-ACE desired labels are never clobbered.
2. Consume `aces_covered` from the seam for prune scope when present; when a schema-2 payload arrives with empty `aces_covered`, skip clears and include a `warning` in the response ("FilamentHub reported no coverage; clears skipped").
3. Response gains a `reconciliation` summary computed against live observed state: `{verified, asserted, expected_not_loaded, unknown_loaded, conflict}` counts, so the status line can say *"2 desired (1 awaiting load), 2 occupied slots unidentified"* instead of raw `applied/stale`.

Existing `applied/cleared/stale/disputed/errors` fields are retained (back-compat with tests) and augmented, not replaced.

### C6 — Frontend rendering (`static/app.js`, `style.css`)

- Grid renders from `GET /slots` (C4), one card per `recon_state` with distinct styling: `VERIFIED` solid + ✓, `ASSERTED` solid + unverified dot, `EXPECTED_NOT_LOADED` dashed "ghost" card ("Expected: {name} — not loaded"), `UNKNOWN_LOADED` amber ("Filament present — unknown · tap to identify"), `CONFLICT` red ("Tag: {x} · Hub: {y}"), `EMPTY` muted.
- **Show the spool name** (`display_name`, from desired `subtype`) as the primary label — today the grid renders only `material` and the name is never shown.
- **0-based indexing** to match the dashboard: `A0…A3 / B0…B3` (ACE letter A=index 0, slot 0-based). Removes the current `ace+1 / slot+1`.
- Pull status line uses the C5 `reconciliation` summary. Auto-pull-on-open announces itself before writing ("Syncing labels from FilamentHub…").

## Data flow

- **Tab open / refresh:** frontend `GET /slots` → backend fetches observed (decay71) + desired (file) → `reconcile_slots` → render. No dependency on decay71's override store surviving.
- **Pull:** `POST /pull` → winners → best-effort decay71 overrides + `save_desired` (durable) → response with reconciliation summary → frontend re-renders from `/slots`.

## Error handling

- decay71 state fetch fails → `/slots` returns `observed_ok=false`, desired-only view, UI banner "printer state unavailable — showing expected loadout." Never 5xx.
- Desired file missing/corrupt → treated as empty, logged, continue.
- decay71 override write fails during Pull → recorded in `errors[]` as today; desired store still saved (the durable record is independent of decay71 accepting the write).

## Testing

- **`reconcile_slots`**: unit table covering all six states, incl. the live case (Red desired + slot empty → `EXPECTED_NOT_LOADED`; Pearl White desired + occupied+RFID-match → `VERIFIED`; occupied+no desired → `UNKNOWN_LOADED`; RFID mismatch → `CONFLICT`).
- **`desired_store`**: roundtrip, missing file, corrupt file, atomic-write.
- **`GET /slots`**: respx-mocked decay71 state + happy path; decay71-down degrade path (`observed_ok=false`).
- **`POST /pull`**: asserts `save_desired` persisted the winners and the `reconciliation` summary counts; existing pull tests stay green (fields augmented, not removed).
- All existing 54 tests remain green.

## Acceptance criteria (the live defect is fixed when)

Given FilamentHub winners `ACE0 slot2 = SnapSpeed Red` (slot physically empty) and `ACE0 slot3 = SnapSpeed Pearl White` (slot occupied), after a Pull:
1. `GET /slots` returns slot 2 as `EXPECTED_NOT_LOADED` with `display_name = "SnapSpeed Red"` and it **persists across repeated `/slots` polls** (does not vanish).
2. slot 3 returns `VERIFIED` (or `ASSERTED` if no RFID) showing "SnapSpeed Pearl White".
3. occupied slots 0 and 1 (no desired) return `UNKNOWN_LOADED` with an "identify" affordance, not blank.
4. The plugin grid shows all four correctly with 0-based `A0…A3` labels and spool names.
5. The Pull status line reports in reconciliation terms, not raw applied/stale counts.

## Rollout

Plugin-only change. Redeploy via `install/install_plugin.sh` on the printer **only when no print is active** (per Snapmaker U1 safety). The desired-state file is created on first Pull; no migration needed.
