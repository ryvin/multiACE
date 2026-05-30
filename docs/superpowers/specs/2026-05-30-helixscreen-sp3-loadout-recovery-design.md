# HelixScreen multiACE Integration — SP3: Loadout, Slot Actions, Recovery

> Status: design (brainstorm phase complete and user-approved on 2026-05-29; spec authoring deferred during the keepalive flap debug and resumed 2026-05-30).
>
> Predecessors: [SP1 ACE status contract](2026-05-27-helixscreen-multiace-sp1-status-contract-design.md) — published the `ace` Klipper status object with `units[]`. [SP2 multi-unit ACE backend](2026-05-27-helixscreen-sp2-multiunit-ace-backend-design.md) — taught the HelixScreen ACE backend to parse `units[]` into `AmsSystemInfo` and gate `change_tool` under multi-unit.

## Parent program (context)

HelixScreen running on the Snapmaker U1 (Davinci-U1) built-in display, driving up to four chained Anycubic ACE Pro units (16 colors) via the multiACE Klipper extension. SP1 published the status. SP2 made the screen render it. **SP3 makes it actionable** — the user taps a slot on the screen and the right thing happens, with a panel that shows the live loadout and a panel that recovers from a stuck state.

After SP3, every routine multi-unit operation that the web console at `http://<printer-ip>/multiace/` already supports is reachable from the touchscreen — no laptop, no phone.

SP4 will then own the install/uninstall switching between HelixScreen and the stock Snapmaker GUI.

## Goal

Wire three things in the HelixScreen multi-unit ACE UI, all driven by the existing macro surface — no new firmware Gcode:

1. **Smart slot tap.** Tapping a slot on any ACE Pro unit issues the same three-case load logic the web console performs (direct, same-ACE swap, cross-ACE swap with a 2-leg banner). Mirrors `multiace_web/static/app.js` smart-swap, ported to the C++ ACE backend so the LVGL UI stays declarative.
2. **Loadout Check panel.** A read-only view: every head and which ACE+slot currently feeds it, with the per-head sensor truth alongside. No "expected" or "desired" loadout — purely live mapping. Read the truth, do not invent it.
3. **Recovery panel.** When the loadout diverges from reality (head_source says slot X but the sensor says empty, or two heads claim the same slot), surface the discrepancy and expose targeted fix-it actions: `ACE_CLEAR_HEADS`, `ACE_MARK_HEAD_LOADED`, individual `ACE_UNLOAD_HEAD`, a re-issue of `ACE_HEAD_STATUS`.

SP3 deliberately stays inside what multiACE macros already do. No new firmware code. The single contract change (SP1 extension) is an additive field on `head_source[i]`.

## Scope

In:

- One additive field on the SP1 `ace` status object: `head_source[i].sensor: bool` — the live per-head gate sensor reading.
- Smart-swap state machine in the HelixScreen C++ ACE backend (`ams_backend_ace.cpp`), with TDD via the existing host-runnable Catch2 suite (uses the moonraker mock in `tests/unit/`).
- LVGL Loadout Check panel.
- LVGL Recovery panel.
- Wire the multi-unit slot tap (already neutered under SP2 by the multi-unit guard) to the smart-swap state machine.
- Catch2 tests for parsing + decision tree. Manual on-device smoke covers LVGL panels.

Out:

- No new multiACE Gcode macros. The macros consumed are the ones the web console already drives: `ACE_LOAD_HEAD`, `ACE_UNLOAD_HEAD`, `ACE_CLEAR_HEADS`, `ACE_MARK_HEAD_LOADED`, `ACE_HEAD_STATUS`, and per-ACE `ACEC__Unload_T<n>`.
- No "loadout templating" or "save/restore loadout." That belongs in a later subproject (this turn's "Loadout Check" is read-only).
- No automatic recovery. Recovery requires an explicit user tap — the panel surfaces and proposes, the human commits.
- No SP4 install/uninstall scaffolding.

## SP1 contract extension: `head_source[i].sensor`

The SP1 builder in `multiace/klipper/extras/ace_status.py` emits one entry per head in `head_source` (length 4). Each loaded head is `{head, unit, slot, brand, type, color}`; each unloaded head is `{head, unit: null, slot: null}` (see `_build_head_source_out`).

SP3 adds an additive field:

```jsonc
{
  "head": 1,
  "unit": 1,
  "slot": 2,
  "brand": "eSUN",
  "type": "PETG",
  "color": [31, 119, 180],
  "sensor": true            // NEW: live filament-at-gate reading for head 1
}
```

`sensor` is also present on the null-source entries:

```jsonc
{"head": 3, "unit": null, "slot": null, "sensor": false}
```

Source of truth: the same dict the web console state model already keeps in `multiace_web/src/multiace_web/state.py` as `sensors: dict[int, bool]`, populated from `multiace_state.log` "sensors" events. SP1's `ace_status.py` builder runs inside Klipper and reads directly from the live `BunnyAce` instance — confirm the attribute name (likely `_sensors_per_head`) at plan time and add a small accessor if needed; do not synthesize from `head_source` alone.

Why: the Loadout Check and Recovery panels need to distinguish "head H is mapped to unit U slot S **and** filament is actually at the gate" from "head H is mapped but the gate sensor is empty." Without this field the screen has to extrapolate from `head_source[h] != null`, which falsely confirms an unloaded head.

This is purely additive — SP2's backend ignores unknown fields per its own contract, so the SP2-stable case keeps working until SP3 ships.

## Smart-swap state machine (C++)

Lives in `ams_backend_ace.cpp` alongside the SP2 `parse_units_array` work. The tap action is no longer a single `change_tool(N)` — it's a user-initiated transfer with three cases driven by the live state.

### Inputs

- `targetUnit` (0..3) and `targetSlot` (0..3) — what the user tapped.
- `targetHead` — chosen at tap-time using the same rule the web console uses (`app.js:1864`): the first head H where `head_source[H] == null && !sensors[H]`. If no such head exists, prompt user to pick (or block) — see "Head selection" below.
- The full `AmsSystemInfo` (units, head_source, per-head sensor) — already populated by SP2's `parse_units_array` plus the SP1 extension above.

### Decision tree

1. **Direct load.** `head_source[targetHead] == null` and `sensors[targetHead] == false`.
   - Issue: `ACE_LOAD_HEAD HEAD=<targetHead> ACE=<targetUnit> SLOT=<targetSlot>`.
   - No banner, no two-leg.

2. **Same-ACE swap.** `head_source[targetHead].unit == targetUnit`.
   - Issue: `ACEC__Unload_T<targetHead>` (the per-ACE unload macro the web console uses for same-unit unwinds), then `ACE_LOAD_HEAD HEAD=<targetHead> ACE=<targetUnit> SLOT=<targetSlot>`.
   - Show a single-step "Swapping within ACE U…" toast (LVGL `OverlayBase` subclass).

3. **Cross-ACE swap.** `head_source[targetHead].unit != targetUnit`.
   - Issue leg 1: `ACE_UNLOAD_HEAD HEAD=<targetHead> LENGTH=600`.
   - Wait for leg-1 propagation — the same `head_source[targetHead]` reset that `multiace_web/static/app.js:_waitForSwapLeg1Propagation` watches. Concretely: poll the next `ace` status update until `head_source[targetHead].unit` becomes null. Use the existing HelixScreen periodic status poll (no new timer infra needed).
   - Issue leg 2: `ACE_LOAD_HEAD HEAD=<targetHead> ACE=<targetUnit> SLOT=<targetSlot>`.
   - Banner: "Cross-ACE swap leg 1/2… → leg 2/2…". Two-step `OverlayBase` (`AmsDeviceOperationsOverlay` is the existing model — extend with a two-leg variant).

### Head selection (when user taps but no obvious target head)

If multiple heads are unloaded, prefer the lowest-numbered one (web console parity). If zero heads are free, open a small head-picker modal (LVGL `AmsContextMenu` is the existing primitive) listing the four heads with their current source. The selected head then enters the cross-ACE or same-ACE swap path automatically.

### Backend interface

The C++ ACE backend gets one new method:

```cpp
AmsError request_slot_action(int target_unit, int target_slot,
                             std::optional<int> hint_head = std::nullopt);
```

The implementation is the decision tree above. Issuing the underlying Gcode runs through the existing `MoonrakerClient` interface (`run_gcode`), the same primitive SP2's `change_tool` uses.

The SP2 multi-unit tap-guard on `change_tool` stays — `change_tool(N)` is still a no-op under multi-unit. Slot taps under multi-unit route to `request_slot_action` only.

## Loadout Check panel (LVGL)

Read-only. One row per head (0–3):

```
Head  Unit  Slot  Filament              Sensor
─────────────────────────────────────────────
T0    A     S0    PLA Polymaker green   ● loaded
T1    B     S2    PETG eSUN blue        ● loaded
T2    —     —     —                     ○ empty
T3    A     S3    PLA eSUN red          ✕ MAPPED, GATE EMPTY
```

- `●` = mapped + sensor true (healthy)
- `○` = unmapped + sensor false (healthy)
- `✕` = mapped + sensor false (DRIFT — recovery offered)
- A `?` row appears if `head_source` and sensor disagree the other way (sensor true, no source). That's the "wild filament" case — also surfaced in Recovery.

The panel is pushed by the same status flow SP2 already drives. No polling on the panel's own clock.

UI primitive: `Modal` subclass (existing HelixScreen pattern), titled "Loadout Check", with a "Refresh" button that re-issues `ACE_HEAD_STATUS` and a "Recover…" button that opens the Recovery panel only if any row is `✕` or `?`.

## Recovery panel (LVGL)

Surfaces every row from the Loadout Check that is in drift, plus targeted fix-it actions:

For each drifted head:

- **Mapped but gate empty** → buttons: `Clear this head's mapping` (`ACE_CLEAR_HEADS HEAD=<h>` — see TBD note below), `Retry load` (`ACE_LOAD_HEAD HEAD=<h> ACE=<u> SLOT=<s>` of the existing mapping), `Manually mark loaded` (`ACE_MARK_HEAD_LOADED HEAD=<h> ACE=<u> SLOT=<s>`).
- **Gate loaded but no mapping** → buttons: `Mark as loaded from…` (opens a small slot picker → `ACE_MARK_HEAD_LOADED HEAD=<h> ACE=<picked> SLOT=<picked>`), `Force unload` (`ACE_UNLOAD_HEAD HEAD=<h> LENGTH=600`).
- **Two heads claim same slot** → buttons: a chooser picking which head keeps the mapping, the other becomes `ACE_CLEAR_HEADS HEAD=<other>`.

TBD at plan time: does `ACE_CLEAR_HEADS` accept a `HEAD=<n>` argument or only clear all? Verify against `ace.py` Gcode registrations when writing the plan. If it's all-or-nothing, the per-head clear becomes `ACE_MARK_HEAD_LOADED HEAD=<h> ACE=-1 SLOT=-1` or whatever the firmware's unmap-single-head primitive is. The brainstorm assumed per-head; verify in plan.

Confirm-before-issue dialog on every action — recovery is by definition a moment to slow down.

## Multi-unit slot-action wiring

The path from "user touch on screen" to "Gcode out the door":

```
LVGL slot tile (in SP2 multi-unit unit grid)
       │  tap
       ▼
AmsContextMenu (existing primitive) with a single "Load to head…" action
       │  confirm
       ▼
ACE backend::request_slot_action(target_unit, target_slot)
       │
       ▼ decision tree (see above)
       │
       ▼ Gcode via MoonrakerClient::run_gcode
       │
       ▼ status update arrives over the existing periodic poll
       │
       ▼ panels redraw from new AmsSystemInfo
```

The slot tile already exists in the SP2 unit grid. The only UI change is wiring the existing tap handler past the SP2 multi-unit guard into the new `request_slot_action`.

## Testing

### Catch2 host (TDD)

The existing `tests/unit/test_ams_backend_ace.cpp` harness covers SP2. SP3 adds:

- **Smart-swap dispatcher.** Build an `AmsSystemInfo` payload, call `request_slot_action(targetUnit, targetSlot)`, assert exactly which Gcode strings were captured by the moonraker mock and in which order. One test per decision branch (direct, same-ACE, cross-ACE) and a head-selection variant (no free head → picker required).
- **SP1 sensor field parsing.** Synthesise an `ace` status payload with `head_source[i].sensor` and assert `AmsSystemInfo.head_source[i].sensor` reads through. Backward-compat case: omit the field, assert default `false` and no parse error (preserves SP2 compatibility).
- **Loadout drift detection.** Build `AmsSystemInfo` variants with each drift class (mapped-empty, loaded-unmapped, double-mapped). Assert the helper that classifies a row (`classify_loadout_row(...)`) returns the right enum.

If the moonraker mock doesn't already capture gcode submissions, extend it minimally — small, well-scoped — so the smart-swap tests can assert ordering without coupling to mock internals.

### Manual on-device smoke

Per project rule (global "do e2e testing with playwright using the gui to verify everything works"): HelixScreen runs on the Davinci-U1 display, not a browser, so the equivalent is a recorded touch-walk.

1. Loadout Check shows the four heads matching what the web console shows at `http://<DAVINCI_U1_HOST>/multiace/`.
2. Tap an empty slot → direct load completes, head_source updates within ~5s on both screen and web console.
3. Tap a slot on the *same* ACE as the currently-mapped head → same-ACE swap completes; banner shows one step.
4. Tap a slot on a *different* ACE → cross-ACE swap shows two-leg banner; head_source updates after each leg; sensor flips true after leg 2.
5. With one head deliberately desynced (e.g. `ACE_MARK_HEAD_LOADED` an empty slot, then watch Loadout Check show `✕`), open Recovery and use each fix-it action.

## Acceptance criteria

- `ace_status.py` emits `head_source[i].sensor` for both loaded and null entries; SP1 tests in `multiace/tests/test_ace_status.py` extended with one new test asserting the field is present.
- `ams_backend_ace.cpp` parses `head_source[i].sensor` (additive — missing field defaults `false`). Existing SP2 multiunit tests still pass.
- `request_slot_action` exists; all three branches covered by Catch2 tests with the moonraker-mock capturing the expected Gcode strings.
- LVGL Loadout Check panel renders all four heads with the symbol scheme above, refreshes on status push, opens Recovery when any drift row is present.
- LVGL Recovery panel surfaces all drift classes with confirm-before-issue dialogs.
- Manual smoke (all 5 steps) passes on the live Davinci-U1.
- No regressions in `multiace/tests/` (currently 29 green incl. keepalive) or in the HelixScreen `[ace]` suite (currently 221 assertions / 44 cases green).

## Risks & mitigations

- **Cross-ACE leg-2 race.** Leg 2 mustn't fire before leg 1's `head_source` clear lands on the periodic poll, or it'll find the source still pointing at the old unit and refuse / mis-route. The web console already handles this in `_waitForSwapLeg1Propagation`. Port that wait pattern exactly — do not invent a new timeout.
- **`ACE_CLEAR_HEADS` granularity.** Brainstorm assumed per-head clear; firmware may only support all-or-nothing. Verify before writing the plan; if all-or-nothing, the per-head fix-it becomes `ACE_MARK_HEAD_LOADED HEAD=<h> SLOT=<sentinel>` (whatever the unmap pattern is). Falling back to a global clear in the Recovery panel is acceptable for the first ship; mark it explicitly as a "TODO: per-head clear once firmware supports it."
- **Sensor flapping during a swap.** During the brief window of leg 1 retract, the sensor can show `false` mid-operation. The drift classifier must ignore "drift" while `swap_in_progress` is true (this flag is already on the SP1 status). Tests cover the suppression.
- **Tap latency on cross-ACE swap.** ACE_UNLOAD_HEAD with LENGTH=600 takes 30–60s. The two-leg banner must remain visible and cancel-able — show a Cancel button that issues nothing destructive (no `ACE_RESET` exists; cancel just hides the banner and lets the operation finish). User confirmation is built into the leg-1 confirm.

## Open questions (resolve at plan time, not blocking design)

1. Exact source-of-truth attribute name for per-head sensor in `BunnyAce` — confirm by reading the live attribute set in `ace.py` while writing the plan. Likely `_sensors_per_head` based on the web console state model.
2. Per-head granularity of `ACE_CLEAR_HEADS` — Gcode registration in `ace.py`, confirm during plan.
3. Whether `ACEC__Unload_T<n>` macros exist for all heads or only some. Check `config/extended/ace.cfg`.
4. Whether the moonraker mock in `tests/unit/moonraker_api_mock.h` already captures Gcode submissions, or whether we need to extend it (and how minimally).

## Out-of-scope for SP3 (documented for SP4 and later)

- Reversible install / uninstall of HelixScreen vs stock Snapmaker GUI — SP4.
- Persisted loadout templates ("my normal loadout is unit 0 PLA, unit 1 PETG…") with one-tap restore.
- Per-color material profile lookup / preset overrides.
- Cumulative cross-slot coupling drift surfacing — the Recovery panel currently shows discrete drift; long-term it might also track "slot N has been retracted M times without reseat."
