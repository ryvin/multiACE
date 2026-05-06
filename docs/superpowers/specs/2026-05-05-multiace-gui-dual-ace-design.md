# multiACE Web — dual-ACE GUI + FilamentHub spool picker integration

**Status:** approved 2026-05-05
**Scope:** Render and control both ACE Pro units from the multiACE web console,
add per-(ACE, slot) spool selection via deep-link to FilamentHub, refactor
autodry to one independent FSM per ACE with round-robin polling.
**Branch:** continuing on `feat/multiace-web-console`.

## Goal

The multiACE firmware has been multi-device-aware since v0.81 and the
Hardware Twin tab already renders per-ACE blocks. The other tabs of the web
console — Dashboard, Activity, Dryer (autodry panel), Diag — still treat
the printer as if it has one ACE, surfacing only the active-connected unit
and dropping per-(ACE, slot) state. This spec brings the rest of the GUI to
parity, and adds a "load a spool from FilamentHub" affordance per slot per
ACE so users can pick filament inventory for any slot of any ACE without
leaving the printer console for more than a tab.

## Non-goals (v1)

- True parallel-USB autodry. Current firmware can only talk to one ACE's
  serial at a time; v0.82 will lift this. Until then we round-robin.
- A native (non-FilamentHub) spool picker inside multiACE web. FilamentHub
  already has a mature NFC-aware picker with conflict resolution; we deep
  link to it instead of duplicating UI.
- Per-ACE Config tab. The `[ace]` section of `ace.cfg` is system-wide by
  design (one ace_device_count, one set of cycle parameters); no UI change
  needed there.
- An ACE Pro labelled "C" or beyond. Firmware supports up to 4 (ACE=0..3 in
  `ACE_LOAD_HEAD`); the GUI design here scales to 4 via the same patterns
  but is presented for 2 ACEs which matches the current hardware. CSS grid
  `auto-fit` handles 3+ without further work.

## Hardware / firmware contract verified live

Empirically checked on 2026-05-05 against the live printer
(`192.168.1.171:7125`) and `multiace/klipper/extras/ace.py`:

- `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S` already supports any source → any
  destination. Verified by issuing `HEAD=0 ACE=1 SLOT=0` from a clean state:
  firmware switched from ACE 0 to ACE 1, fed filament through the splitter
  to T0, eventually recorded `head_source[0] = {ace_index: 1, slot: 0,
  type, color, brand}` in `[ace]` Moonraker status. Phase3 wheel-encoder
  retries are a separate hardware quirk that doesn't block the load.
- The convenience macros `ACEC__Load_T{0..3}` only set `HEAD=N` and let
  `ACE` and `SLOT` default to active-ACE and `slot=N`. They are *not*
  sufficient for explicit (ACE, slot) → head loading. The GUI must issue
  `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S` directly.
- `[ace]` Moonraker object exposes per-active-ACE state: `status`, `temp`,
  `dryer_status`, `gate_status[0..3]`, `active_device` (1-indexed),
  `device_count`, `head_source[0..3]`, `slots[0..3]`. Inactive ACEs are
  invisible from this object until firmware switches to them.
- `ACE_SWITCH TARGET=N` is blocking and takes ~3–8 s (USB disconnect +
  reconnect to the new ACE's serial path). Audit log records `SWITCH`,
  `SWITCH_NOOP`, `SWITCH_TARGET`, `SWITCH_AUTO_PASSIVE` actions.
- `ACED__Dry_Stop` macro currently calls `ACE_STOP_DRYING` (no ACE arg) and
  acts on whichever ACE is currently active. For per-ACE stop, the web
  server will pre-issue `ACE_SWITCH TARGET=N` before `ACE_STOP_DRYING`.

## User-visible model

- **Both ACEs visible at once on Dashboard.** Two slot panels rendered
  side-by-side on wide screens, stacked on narrow. The existing ACE
  switcher pills are removed; the data they used to gate (head_source,
  gate_status, slots) is now rendered for both ACEs simultaneously.
  Inactive ACE shows last-known data with a `stale` badge until the
  next round-robin cycle refreshes it.
- **Dryer status card on Dashboard shows a row per ACE.** Each row:
  `ACE N • <state> • <temp/target>°C • <remaining>` and a per-row Stop
  button. "All idle" collapses gracefully.
- **Slot Load is a split-button.** Default click loads to the lowest free
  head — defined as the lowest index `h` where `head_source[h]` is null AND
  the `e<h>_filament` motion sensor reports no filament. If all four heads
  are busy, the click is suppressed and the chevron menu opens automatically
  with all items disabled and a footer "all heads loaded — unload first".
  The `▾` chevron always opens a menu of T0/T1/T2/T3 with busy heads
  disabled. Each slot also
  has a `📖` button that opens FilamentHub's picker in a new tab/popup
  with `printer`, `ace`, `slot` query params.
- **Dryer tab gets independent autodry rows.** One row per ACE: own
  thresholds (target RH, hysteresis), own filament-type fallback, own
  Start/Stop, own current state. No more single FSM with `target_ace`.
- **Activity tab gets an ACE column and filter chips.** `[ All ] [ ACE 0 ]
  [ ACE 1 ]` pills above the list, ACE column on each row, filter is
  client-side only.
- **Diag tab gets an ACE selector dropdown.** "Show ACE: [ 0 ▾ ]" — every
  per-ACE diag panel (USB, slots, sensors, autodry FSM, ACE_HEAD_STATUS
  slice) re-renders for the chosen ACE. Global panels (klippy.log tail,
  ACE_LIST output, raw JSON) stay above the dropdown unchanged.

## Architecture

Two repos, three pieces:

```
            ┌─────────────────────────┐                    ┌──────────────────────────┐
            │   multiACE web (this    │                    │   FilamentHub (separate  │
            │   repo)                 │                    │   repo, separate spec)   │
            │                         │                    │                          │
 user ─────►│   Dashboard / Dryer /   │   deep-link        │   ACE Load Picker sheet  │
            │   Activity / Diag       │   /?picker=ace     │   (existing, extended    │
            │                         │   &printer=&ace=   │    to read+write `ace`   │
            │   "📖 Load" buttons     │ ──&slot= ─────────►│    field on location)    │
            │                         │                    │                          │
            │   round-robin poller    │                    │   writes Spoolman:       │
            │   per-ACE autodry FSMs  │                    │   extra.filamenthub      │
            │   spoolCache (5 s poll) │ ◄── Spoolman GETs ─│    .location =           │
            │                         │   via FH nginx     │    {printer, ace, slot}  │
            └────────┬────────────────┘                    └──────────────────────────┘
                     │ Moonraker HTTP
                     ▼
            ┌──────────────────────────┐
            │  Klipper + multiACE      │
            │  ACE_LOAD_HEAD,          │
            │  ACE_DRY, ACE_SWITCH,    │
            │  ACE_STOP_DRYING         │
            └──────────────────────────┘
```

The two repos communicate only by the FilamentHub deep-link URL contract
and by Spoolman's `extra.filamenthub.location` schema. multiACE web does
not import FilamentHub. FilamentHub does not call multiACE.

## Components — multiACE web

### Backend (`multiace_web/src/multiace_web/`)

**`autodry.py` (refactor).** Replace single FSM with `AutodryManager` owning
`fsms: list[AutodryFSM]`, length = `device_count`. Each FSM has its own
target RH, hysteresis, filament-type fallback, last-RH reading, last-tick
time, `locked: bool`, and `unreachable: bool`. Construction reads existing
single-FSM persisted state (in `~/printer_data/config/multiace_autodry.json`
or wherever it lives now) and migrates: `target_ace` becomes the index of
the surviving FSM; the second FSM is constructed with defaults and
`enabled = False`. New persistence shape:

```json
{
  "fsms": [
    {"ace": 0, "enabled": true, "target_rh": 15, "hysteresis": 5, "default_filament_type": "PLA", ...},
    {"ace": 1, "enabled": false, "target_rh": 15, "hysteresis": 5, "default_filament_type": "PLA", ...}
  ]
}
```

**`poller.py` (extend → `MultiAcePoller`).** Loop body:

```python
async def tick():
    print_state = await mr.print_stats()
    if print_state == "printing":
        active = derive_active_ace_from_head_source(state)
        ace = await mr.query("ace")
        manager.fsms[active].tick(ace, locked=False)
        for other in range(device_count):
            if other != active:
                manager.fsms[other].locked = True
    else:
        target = (last_polled + 1) % device_count
        if state.active_device != target:
            ok = await switch_ace(target)
            if not ok:
                manager.fsms[target].mark_unreachable()
                last_polled = target
                return
        ace = await mr.query("ace")
        manager.fsms[target].tick(ace, locked=False)
        last_polled = target
```

`switch_ace` calls `gcode/script` with `ACE_SWITCH TARGET=N` and waits for
`active_device` to settle (poll every 500 ms, max 10 s). Two consecutive
failures on the same target mark `unreachable = True`; recovery is the
next successful switch.

**`spoolman.py` (new, ~80 lines).** Async client:

```python
class SpoolmanClient:
    def __init__(self, base_url: str, printer_id: str): ...
    async def list_spools_for(self, ace: int, slot: int) -> Spool | None: ...
    async def list_all_bindings(self) -> dict[int, dict[int, Spool]]:
        """Returns {ace: {slot: spool}} for spools with location.printer == self.printer_id.
        Server-side keying is nested-dict so JSON-serialization for the WS payload
        round-trips cleanly (no tuple-key encoding issues)."""
```

Polled every 5 s by a background task; result merged into `state.spool_cache`
as `state.spool_cache[ace][slot]`.
Tolerates absent `ace` field on legacy entries (treats as `ace=0`). On
timeout or 5xx, keeps last cache for up to 5 minutes then ages to `null`.

**`server.py` (extend).**
- New env vars: `FILAMENTHUB_URL` (e.g. `https://filamenthub.local`),
  `FILAMENTHUB_PRINTER_ID` (e.g. `u1-1`). Both optional; absent → spool
  picker UI disabled.
- New endpoints:
  - `GET /api/slots` — returns `{aces: [{index, slots: [{slot, gate_status, spool}, ...], status, dryer}]}`
  - `POST /api/dry/stop` body `{ace: int}` — switches to ACE if needed, then `ACE_STOP_DRYING`
  - `GET /api/autodry?ace=N` — returns one FSM's state
  - `POST /api/autodry?ace=N` body `{enabled, target_rh, hysteresis, default_filament_type}`
- Extend:
  - `/api/print` fetches `[ace]` for currently-active and merges last-known
    cached state for the inactive ACE; UI uses staleness flags.
  - `/api/command` — pre-flight rejects load-into-busy-head or load-from-empty-slot
    with 409 + `{"error": "<reason>"}`. The endpoint stays a thin proxy for
    everything else.

### Frontend (`multiace_web/src/multiace_web/static/`)

**`app.js`** (~150 lines net change). New functions:
- `renderSlotsPanelMultiAce()` — emits `<div class="slots-panel"><div class="ace-block" data-ace="0">…</div><div class="ace-block" data-ace="1">…</div></div>`. CSS `auto-fit` handles wrap.
- `renderSlotRow(ace, slot, gate, spool)` — emits the row with the 📖 deep-link button, the split-button Load + chevron menu, and the Unload button. Disabled-state logic: `gate != AVAILABLE` → no Load; `head_source[h]` set → that head item disabled in the chevron menu.
- `pickHeadFor(ace, slot, head)` — POSTs `/api/command {script: "ACE_LOAD_HEAD HEAD=<head> ACE=<ace> SLOT=<slot>"}`. The default Load click computes `head = lowestFreeHead()` first.
- `buildFilamentHubPickerUrl(printerId, ace, slot)` — returns `${FILAMENTHUB_URL}/?picker=ace&printer=${printerId}&ace=${ace}&slot=${slot}` and `window.open()`s it with `noopener`.
- `renderDryerCardMultiAce()` — stacked rows, per-ACE Stop wired to `POST /api/dry/stop {ace}`.
- `renderActivity()` — adds ACE column and filter chips. State: `state.activityFilter ∈ {null, 0, 1, …}`.
- `renderDiag()` — adds `<select id="diagAce">` whose `change` event calls `setDiagAce(ace)` which re-renders only the per-ACE blocks.
- Removes the existing ACE switcher pills (`device_count > 1` block) — slots panel is now always all-ACE.

**`hardware-twin.js`** — no change. Already multi-ACE aware.

**`style.css`** (~80 lines added). Tokens reused. Key rules:
```css
.slots-panel { display:grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap:1rem; }
.ace-block { background:var(--surface); border-radius:var(--radius); padding:1rem; }
.ace-block.is-stale::after { content:"stale"; ... }
.ace-block.is-unreachable { opacity:.5; ... }
.dryer-row { display:flex; align-items:center; gap:.75rem; }
.activity-ace-chip { display:inline-block; padding:2px 10px; border-radius:999px; ... }
.slot-load-split { display:inline-flex; }
.slot-load-split > button:first-child { border-radius:6px 0 0 6px; }
.slot-load-split > button:last-child { border-radius:0 6px 6px 0; padding:0 6px; }
.head-target-menu { position:absolute; ... }
```

## Components — FilamentHub (separate spec, summarized here for the contract)

The FilamentHub repo will need its own design doc and PR. The contract this
spec depends on:

1. The ACE Load Picker accepts query params `printer`, `ace`, `slot`. When
   present, sheet opens directly bound to that target.
2. After NFC scan + spool match + assignment, `extra.filamenthub.location`
   is written with `{printer, ace, slot, asserted_at, asserted_by}`. The
   `slot` field stays as an integer 0..3 (per-ACE slot index, not extruder
   index).
3. `spool_watcher.py` reads `ace` from location; absent = 0. When syncing
   `SET_EXTRUDER_SPOOL`, it uses multiACE's `head_source[h] = {ace, slot}`
   to find which extruder a spool is bound to (a spool at ACE 1 / slot 0
   that's loaded into T0 still maps to extruder 0 for the macro).
4. Fleet card slot row displays slots grouped by ACE for printers whose
   capability flags include `multi_ace: true` (new flag, defaults false).

That's the integration surface. The FilamentHub spec covers UI specifics.

## Data flow

### Slot panels

```
WebSocket /ws  ──►  app.js renderSlotsPanelMultiAce()
                       │
                       │ for ace in 0..device_count-1:
                       │   for slot in 0..3:
                       │     read state.aces[ace].slots[slot] (gate/type/color)
                       │     read state.spool_cache[ace][slot] (Spoolman binding)
                       ▼
                  <div class="ace-block" data-ace="N">
                       │
   click 📖 ──► window.open(${FH}/?picker=ace&printer=…&ace=N&slot=S)
   click Load ──► POST /api/command {script:"ACE_LOAD_HEAD HEAD=<auto> ACE=N SLOT=S"}
   chevron ─┬── menu shown
            └─► POST /api/command {script:"ACE_LOAD_HEAD HEAD=<chosen> ACE=N SLOT=S"}
```

`spool_cache` is built on the server by `SpoolmanClient.list_all_bindings()`
every 5 s and pushed in WebSocket frames as `state.spool_cache`.

### Round-robin autodry

See `MultiAcePoller.tick()` pseudocode under Components above. Cycle period
is ~10–15 s per ACE in idle (poll cadence × device_count + switch time).

### FilamentHub callback

FilamentHub writes the new binding to Spoolman directly. multiACE web
discovers it on the next 5 s Spoolman poll. No webhook needed. Worst-case
visible lag from picker-close to UI-update: ≤5 s.

### Spoolman `extra.filamenthub.location` schema

| Today | Proposed |
|---|---|
| `{printer, slot, asserted_at, asserted_by}` | `{printer, ace, slot, asserted_at, asserted_by}` |

`ace` defaults to `0` when missing. Single-ACE printers unaffected.

## Error handling

- **USB switch fails.** Log WARN; skip this round-robin tick; retry next
  cycle. Two consecutive failures on the same target → mark `unreachable`,
  UI greys the ACE block, autodry row reads "paused — ACE unreachable".
  Auto-recovers on the next successful switch.
- **FilamentHub unreachable.** 5 s poll uses `httpx.AsyncClient(timeout=3)`.
  Cache ages but doesn't clear for 5 min; "⚠ FilamentHub offline" badge
  appears. Deep-link still works (user just sees FilamentHub's own error).
- **Spoolman spool missing `ace` field.** Treat as `ace=0`. One-time INFO
  log per spool.
- **Concurrent autodry start while idle.** Round-robin guarantees one ACE
  active at a time; FSM that wants to start drying queues until its turn.
  UI shows `state = "starting (queued)"` briefly.
- **Per-ACE Stop Drying on inactive ACE.** Server pre-issues
  `ACE_SWITCH TARGET=N` then `ACE_STOP_DRYING`. Switch failure → 502 +
  `{"error":"could not switch to ACE N"}`, UI toast.
- **Pre-flight refusals on Load.** Server returns 409 with reason for
  busy-head / empty-slot / unreachable-ACE; UI toast and reopens the
  chevron menu.
- **Autodry during print on inactive ACE.** Marked locked; no commands
  issued; last-known data preserved. Stop button disabled with tooltip
  "ACE locked during print".

## Testing

### Backend (pytest)

- `test_autodry_per_ace.py` — new. Two FSMs, independent thresholds,
  locked-during-print, switch-fail → unreachable, queued start across
  round-robin.
- `test_poller_roundrobin.py` — new. Idle target alternation, switch
  issued only when needed, printing-path stickiness, two failures →
  unreachable.
- `test_spoolman_client.py` — new (`respx`). With/without `ace`,
  fallback to 0, 5xx/timeout doesn't clear cache.
- `test_server.py` extensions — `/api/dry/stop?ace=`,
  `/api/autodry?ace=` GET/POST, `/api/slots` shape, busy-head 409.
- Audit existing tests for hardcoded `ace=0` assumptions.

Target: ~15–20 new tests; suite stays under ~20 s.

### Frontend

- Extend `tools/visual_regression.py` with new screenshots at 1280×900
  and 390×844 for: Dashboard side-by-side, stacked dryer card, Activity
  with ACE chips, Diag with dropdown=1. Stays read-only.
- New `tools/e2e_dual_ace.py` (Playwright, manual) for click flows: Load
  via chevron, deep-link popup verification, Diag dropdown re-render.
  Honors print-state safety check.

### Live-printer e2e

Per the user's project rules: validate against the real printer with a
Playwright session. Golden path: render two ACEs, perform one Load via
chevron, observe `head_source` update over WebSocket, screenshot. Edge:
unplug ACE B briefly, see unreachable badge ≤10 s, replug, recovery on
next round-robin tick. Run only when no print is in progress.

### Firmware

No automated tests (per CLAUDE.md). Manual on-printer validation via
`multiace_state.log` action stream.

## Migration

1. **Persistence migration.** First boot of new code reads the old
   single-FSM autodry state and writes the new `fsms[]` shape. Old key
   left in place for one release as fallback.
2. **Spoolman migration.** Existing spools with `extra.filamenthub.location`
   without `ace` continue to work (treated as `ace=0`). FilamentHub spec
   covers the writer-side migration.
3. **No firmware change required.** All needed gcode commands
   (`ACE_LOAD_HEAD`, `ACE_DRY`, `ACE_SWITCH`, `ACE_STOP_DRYING`) already
   exist in v0.81.

## Open questions

None. All design decisions captured. The FilamentHub-side spec is a
separate workstream and does not block this one — multiACE web will ship
the deep-link buttons even before FilamentHub picks up the `ace` query
param, which simply means the picker opens to `slot=N` ignoring `ace`
until FilamentHub is updated.

## Out-of-scope follow-ups

- v0.82 firmware lifts the "one ACE serial open at a time" rule. When that
  ships, drop the round-robin and poll both ACEs continuously. Code path
  is small: the `idle` branch of `MultiAcePoller.tick()` becomes a parallel
  query of all `[ace 0..N]` objects. UI needs no change.
- Replace `ACEC__Load_T{0..3}` macros with parameterized variants once we
  control all callers (Fluidd custom buttons, etc.). Until then they
  remain for backwards compat with hand-typed gcode.
- Per-ACE config sub-section (cycle parameters tuned per device) — only if
  hardware variation across ACE units becomes a real complaint.
