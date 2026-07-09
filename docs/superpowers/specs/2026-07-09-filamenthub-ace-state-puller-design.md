# FilamentHub → multiACE ace-state puller (Phase 4)

**Date:** 2026-07-09
**Branch:** `feat/filamenthub-plugin`
**Status:** Design approved, ready for implementation plan.

## Summary

Add a **puller** to the existing `multiace_plugins/filamenthub/` plugin that reads
FilamentHub's authoritative "which spool should be in which ACE slot" state and
**mirrors it into multiACE's slot labels** — material, brand, subtype, colour per
slot. **Label-only: no motors move.** This is Phase 4 of the multiACE↔FilamentHub
integration; Phases 1–3 already shipped on the FilamentHub side.

## Motivation

FilamentHub is the tracker. When a user physically loads filament into an ACE, they
record *which spool went into which virtual-ACE slot* in FilamentHub (via its
existing virtual-ACE grid / uniform Load flow). multiACE should then be able to
**pull that loaded configuration** and display the correct material/colour per slot
without the operator re-entering it in the multiACE GUI.

Today the `filamenthub` plugin only supports the reverse, per-slot manual push
(`POST /assign` labels one slot at a time and writes back to FilamentHub). There is
no way to say "make multiACE's labels match what FilamentHub already knows."

## Context: what already exists

**FilamentHub side (shipped — do NOT modify from this repo; file an issue on
`ryvin/FilamentHub` if a change is needed):**

- **Phase 1–2:** the virtual-ACE grid (`v2.15.0`) + uniform Load flow (`v2.14.0`) —
  tappable per-ACE slot grid; loading binds a spool to `(printer, ace, slot)` via
  `extra.filamenthub.location`.
- **Phase 3:** the read seam `GET /fleet/api/ace-state?printer=<id>` (sentinel
  server on `:7127`, nginx-proxied at `/fleet/api/`). `scripts/sentinel/ace_state.py:build_ace_state`
  projects the Spoolman `extra.filamenthub.location` records into rows and **resolves
  one winner per `(ace, slot)`** by `asserted_by` priority (`asserted_at` breaks
  ties). Response envelope:
  ```json
  {
    "schema": <int>,
    "printer": "<id>",
    "ts": <epoch>,
    "slots":    [ {"ace":0,"slot":0,"spool_id":42,"material":"PLA","color":"#RRGGBB","name":"...","asserted_by":"user:assign","asserted_at":<epoch>}, ... ],
    "disputed": [ {"ace":0,"slot":1,"spool_id":7, ..., "winner_spool_id":42}, ... ]
  }
  ```
  Status codes: `200` normal; `503` provider unwired (watcher didn't inject it);
  `502` provider raised; `400` missing `printer`.

**multiACE side (shipped):**

- `filamenthub` plugin already has a `MultiAceClient` with
  `set_override(ace, slot, material, brand, subtype, color)` →
  `POST /api/slot-override`, and `clear_override(ace, slot)` →
  `DELETE /api/slot-override/{ace}/{slot}`.
- `SpoolmanClient.list_spools()` returns per-spool `{spool_id, name, material,
  color, vendor, weight_remaining_g, location}` — used to **enrich brand** (the
  ace-state row has no `vendor`).

## Design

Consume the **ace-state seam** (not raw Spoolman) so FilamentHub stays the single
owner of priority/dispute resolution; multiACE never re-derives who-wins.

### New component: ace-state client

`src/filamenthub_plugin/ace_state.py` — async client:

```
get_ace_state(printer_id) -> AceState        # {schema, printer, ts, slots, disputed}
```

- URL: `{ACE_STATE_URL}?printer=<id>` where `ACE_STATE_URL` defaults to
  `{FILAMENTHUB_URL}/fleet/api/ace-state` (new optional config var
  `FILAMENTHUB_ACE_STATE_URL` to override).
- Typed errors distinguishing `503` (seam-not-enabled), `502`, `400`, and
  network/timeout — each maps to a distinct operator-facing message.

### New endpoint: `POST /pull`

Reconcile FilamentHub's desired state into multiACE labels:

1. `get_ace_state(printer_id)`.
2. For each **winning** `slots` row → `set_override` with
   `material=material`, `subtype=name`, `color=color`,
   `brand=<vendor from list_spools() by spool_id, else "">`.
3. **Scoped reconcile:** compute the set of ACE indices ace-state covers. For any
   `(ace, slot)` within that scope that ace-state reports empty but multiACE
   currently labels → `clear_override(ace, slot)`. Slots on ACEs ace-state does
   **not** mention are left untouched (never nuke labels FilamentHub isn't
   authoritative for).
4. Collect results; **partial failures do not abort the loop**. Return:
   ```json
   {"applied": [...], "cleared": [...], "disputed": [...], "errors": [...]}
   ```

Also expose `GET /ace-state` as a thin passthrough (parsed for the UI grid) so the
frontend can render desired vs applied without duplicating the fetch.

### Mapping (ace-state row → slot-override payload)

| slot-override field | source |
|---|---|
| `ace`, `slot` | row `ace`, `slot` |
| `material` | row `material` |
| `subtype` | row `name` |
| `color` | row `color` (declared `filament.color_hex`; normalised to `#RRGGBB`) |
| `brand` | `list_spools()` vendor for `spool_id`, else `""` |

Reuse `mapping.normalize_color`. `disputed` losers are **shown, never written**.

### UI (existing static frontend, FilamentHub tab)

- A **"Pull from FilamentHub"** button.
- A per-ACE grid rendering each slot's desired label + apply result
  (applied / cleared / error).
- A **disputes banner** listing `disputed` rows ("slot A2: spool 7 also claims this
  — resolve in FilamentHub"), pointing at the winner.
- **Trigger:** manual button **plus auto-pull on tab open**. Background polling is
  deferred (YAGNI); a later config flag can add it.

### Safety

Label-only, zero motion. `/api/slot-override` is a web-console label write (no
Klipper restart), so the pull is safe regardless of `print_stats.state`. No new
print-safety gate required.

## Decisions (locked)

- **Reconcile clears vacated slots**, scoped to FilamentHub-known ACEs only.
- **Brand enriched** from the spool list (one extra call) rather than left blank.
- **Manual + on-open trigger**; no background poll in MVP.
- Consume `/fleet/api/ace-state` (priority-resolved) — **not** re-derive from raw
  Spoolman via `list_all_bindings()`.

## Out of scope

- Any physical load/unload of filament (explicitly rejected — label-only).
- Two-way sync (pushing multiACE's sensed/RFID state back to FilamentHub) — the
  FilamentHub watcher already owns that seam.
- Background polling / continuous reconcile.
- FilamentHub-side changes. If the provider is unwired (`503`) or the ace-state row
  needs `vendor` added, open an issue on `ryvin/FilamentHub`.

## Risks / open items

- **ace-state provider must be wired** on the FilamentHub watcher, else the seam
  returns `503`. This is deployment/config on the FilamentHub side — surface the
  `503` clearly; if it needs enabling, that's a `ryvin/FilamentHub` issue, not a
  multiACE change.
- **`schema` version drift:** the client should read `ACE_STATE_SCHEMA` defensively
  and warn (not crash) on an unexpected schema.

## Testing

pytest + respx:

- ace-state client: `200` (slots + disputed), `503`, `502`, `400`, network fail.
- `/pull` reconcile: applied / cleared / scoped-untouched / disputed-shown-not-written
  / partial-error-collected.
- mapping incl. brand enrichment + colour normalisation.

Optional: Playwright read-only smoke against the live printer per the e2e rule
(button visible, grid renders, no motion).
