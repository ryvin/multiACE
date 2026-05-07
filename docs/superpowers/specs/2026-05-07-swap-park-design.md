# Swap-park — partial-retract for cross-ACE filament swaps

**Status:** approved 2026-05-07 (per chat alignment; no critical-review pass yet — see §7)
**Branch:** `feat/swap-park` off `main`
**Scope:** Combined firmware (`ACE_PARK_HEAD`) + web (split-button affordance) for cross-ACE filament swaps. Eliminates the unload-everything-then-load-everything waste pattern observed during this session's T1/T3 swap experiments.

## One-line goal

When the user swaps a head's source from ACE A to ACE B, the firmware shouldn't fully retract back to slot A's gate — it should park the filament in ACE A's bowden just past the splitter, ready to re-feed quickly if the user swaps back.

## Background — what we observed

During the T1 B1→A1 swap this session, the operator (on hardware) noted: *"you didn't have to fully extract for a swap between aces in case you need to swap back."*

The current `ACEC__Unload_T<n>` macro retracts filament from the head sensor all the way back to the slot's gate retainer (~700+ mm in the U1's bowden setup). When the next operation is "load from the OTHER ACE into the same head," that fully-retracted filament is wasted travel:

- **B1 unload**: ~3 min, retracts B1 from T1 all the way back into ACE B's slot
- **A1 load**: ~3 min, has to feed from ACE A's slot all the way to T1 (which we know hits phase3 timeouts)
- **If user swaps back to B1 later**: another ~3 min unload + ~3 min load

The "park" insight: leave the filament tip parked in the ACE-side bowden just past the splitter junction. The other ACE feeds into a clear bowden run. Subsequent swap-back from A1 → B1 only needs to retract A1 to its splitter-side park and re-feed B1 from its splitter-side park. Saves ~5 minutes per swap-back.

## Scope (v1)

**Firmware**: a new gcode command `ACE_PARK_HEAD HEAD=N` that:
1. Reads `head_source[N]` to know which (ACE, slot) the head is sourced from
2. Performs a calibrated partial retract — pulling filament back from T<n> to a "park" position in the ACE-side bowden (just past the splitter)
3. Updates `head_source[N]` to indicate "parked" (filament is in bowden, not at gate, not at head)
4. Audit log: emits `PARK_HEAD` action with `{head, ace, slot, retract_length}`

**Web**: a new affordance in the slot row's split-button: when clicking a slot's "Load" or chevron-T<n>, if `head_source[N]` is currently sourced from a *different* ACE than the new target slot, the web invokes `ACE_PARK_HEAD HEAD=N` first, then `ACE_LOAD_HEAD HEAD=N ACE=<new> SLOT=<new>`. Both pre- and post-conditions are observable in the audit log.

## Non-goals (v1)

- **No automatic park** in normal `ACEC__Unload_T<n>`. Existing macro stays as full retract — that's still the right action for "remove this spool entirely from the printer."
- **No park-on-print-end**. Print-end always wants the full unload (filament goes back to slots for storage / dryer).
- **No mid-print park**. v0.81 firmware can't switch ACEs mid-print anyway. Park-park-load is for between-print swaps.
- **No park-multiple-heads chain**. v1 is one head at a time. Multi-head park sequencing is a future concern.
- **No swap-back optimization in firmware** beyond the park. The web layer is responsible for noticing "user swapped back to the parked source" and using a shorter feed sequence — but that's a Tier 2 follow-up.

## Hardware contract assumptions (need verification)

1. **Splitter-side park position is calibratable per printer**. The U1's bowden geometry is fixed at install; the distance from the extruder gear back to the splitter Y-junction is the same for every head. One value per printer — call it `park_retract_length_mm`. Reasonable default: 600 mm (less than the full ~700–800 mm, leaves filament tip in the ACE-side bowden ~100 mm past the splitter). Operator calibrates by trial: too short = filament still in shared bowden = blocks the other ACE; too long = filament back at the gate (defeats the point).

2. **The "park" position must be physically observable**. After a park, `e<head>_filament` should read False (sensor is downstream of the park). `gate_status[slot]` should still read 1 (slot still has filament backed up to the gate). No way to directly observe "filament is in the ACE-side bowden but not at the gate" — the park position is inferred from the audit log + the fact that wheel encoder ticked the right amount during retract.

3. **Park calibration value lives in `ace.cfg [ace]` section**, not hardcoded. New config key `park_retract_length_mm` (default 600).

## 1. Architecture

A small firmware addition + a small web change.

### Firmware (`multiace/klipper/extras/ace.py`)

- New module-level / `[ace]` config key `park_retract_length_mm` (default 600).
- New gcode command `ACE_PARK_HEAD HEAD=N`. Implementation pattern matches `cmd_ACE_UNLOAD_HEAD`:
  - Pre-flight: head must have `head_source[N]` set (can't park empty head). Sensor `e<head>_filament` must read True (must have filament to park).
  - If `active_device != head_source[N].ace_index`, switch via `ACE_SWITCH TARGET=<source.ace>`.
  - Issue partial retract via `FEED_AUTO MODULE=<module> CHANNEL=<channel> EXTRUDER=<head> LOAD=0` — the existing unload feeder, but with a `LENGTH` override (or a similar mechanism) to retract only `park_retract_length_mm` instead of the full distance.
  - Verify `e<head>_filament` reads False post-retract (filament cleared from the head sensor — the success criterion). If True, retract didn't complete; raise.
  - Update `head_source[N]` in place: keep `ace_index` and `slot` for swap-back routing, add a `parked: True` marker. Web reads this marker to render the row distinctly ("Parked at ACE A slot 1").
  - Persist via `_save_head_source`.
  - Audit `PARK_HEAD` with `{head, ace, slot, retract_length, e<head>_filament_post}`.

### Web — slot row split-button auto-park

Add to multiACE web's slot Load click handler in `app.js` (near `pickHeadFor` / `sendLoad` from the dual-ACE GUI work):

```js
async function pickHeadFor(targetAce, targetSlot, targetHead) {
  const currentSrc = state.head_source[targetHead];
  if (currentSrc && currentSrc.ace_index !== targetAce) {
    // Cross-ACE swap — park current source first, then load the new one.
    await sendCommand(`ACE_PARK_HEAD HEAD=${targetHead}`);
  }
  await sendScript(`ACE_LOAD_HEAD HEAD=${targetHead} ACE=${targetAce} SLOT=${targetSlot}`);
}
```

The chained call is one user-facing operation but two gcode events. Audit log shows `PARK_HEAD` then `LOAD_HEAD` (or the standard `LOAD_HEAD_TIP_REFRESHED` etc.).

### Branch

`feat/swap-park` off `main` after the wheel-encoder fallback is merged (which it now is, at f393e29).

## 2. Components

### `multiace/klipper/extras/ace.py` (~70 lines added)

- New config read in `__init__`:
  ```python
  self.park_retract_length_mm = config.getint(
      'park_retract_length_mm', 600, minval=100, maxval=2000)
  ```
- Register `ACE_PARK_HEAD` in `_register_commands` next to `ACE_UNLOAD_HEAD`.
- `cmd_ACE_PARK_HEAD` method: ~50 lines, pattern matching `cmd_ACE_UNLOAD_HEAD` but with the partial-retract length and the new `parked` marker.

### `multiace/config/extended/ace.cfg`

- Optional `park_retract_length_mm = 600` entry in the `[ace]` config section, with a comment explaining how to calibrate.
- Group H (Recovery) gets a new convenience macro `ACEC__Park_T<n>` for each head:
  ```cfg
  [gcode_macro ACEC__Park_T0]
  description: Park T0's filament in ACE-side bowden just past splitter (for swap-back-friendly cross-ACE swaps).
  gcode:
    ACE_PARK_HEAD HEAD=0
  ```

### `multiace_web/src/multiace_web/static/app.js`

- New helper `pickHeadFor(ace, slot, head)` that runs park-then-load when cross-ACE.
- Slot row click handler (already implemented for the dual-ACE GUI feature) updated to call `pickHeadFor` instead of directly sending `ACE_LOAD_HEAD`.

### `multiace_web/src/multiace_web/state.py`

- `head_source[h]` may now contain `parked: True` per the firmware change. State serialization is dict-pass-through, so no schema bump needed — the `parked` field flows through automatically. Frontend reads it.

### `multiace_web/src/multiace_web/static/style.css` (small addition)

- A new `.slot-row.parked` style: subtle visual differentiator (e.g. dashed border or muted-color row) so users can tell a slot is in "parked" state vs fully retracted vs loaded.

### Tests

Firmware: per CLAUDE.md, no automated tests. Live validation per §5.

Web: extend existing `test_state.py` to verify `head_source[h].parked` round-trips via WS payload.

## 3. Data flow

### Cross-ACE swap with park

```
[user clicks "→ T1" from ACE B slot 1's chevron menu]
       │
       ▼
pickHeadFor(targetAce=1, targetSlot=1, targetHead=1):
  currentSrc = state.head_source[1]   // {ace_index:0, slot:1} (sourced from ACE A)
  currentSrc.ace_index (0) !== targetAce (1)  → cross-ACE swap path
       │
       ▼
sendCommand("ACE_PARK_HEAD HEAD=1")
       │
       ▼
[Firmware]
  - read head_source[1]: ACE 0 / slot 1
  - active_device (currently 0 = ACE A) — no switch needed for park
  - issue FEED_AUTO LOAD=0 with LENGTH=park_retract_length_mm
  - filament retracts ~600 mm: T1 → bowden → past splitter → into ACE A's bowden
  - e1_filament transitions True → False (success signal)
  - head_source[1] = {ace_index:0, slot:1, ..., parked: True}
  - _save_head_source
  - audit: PARK_HEAD {head:1, ace:0, slot:1, retract_length:600, e1_filament_post:false}
       │
       ▼
sendScript("ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1")
       │
       ▼
[Firmware standard load path — switches A→B, feeds from ACE B slot 1]
       │
       ▼
audit: SWITCH_TARGET, SWITCH, LOAD_HEAD_TIP_REFRESHED, ..., LOAD_HEAD
       │
       ▼
state.head_source[1] = {ace_index:1, slot:1, ...}  (parked marker gone)
state.head_source[?] for the parked filament — currently nothing tracks
  "filament is parked in ACE A's bowden waiting" — that's a Tier 2 concern
  (see §7 follow-ups: parked-source registry).
```

### Swap-back optimization (deferred — see §7)

A future Tier 2: when swapping back from ACE B's slot to a previously-parked ACE A slot, the firmware could feed only `park_retract_length_mm` instead of full bowden length. Out of scope for v1 — the firmware doesn't currently track "parked filaments" anywhere persistent. v1 just makes the park happen; the swap-back optimization wants a dedicated tracker.

## 4. Error handling

- **head_source[N] is None** when ACE_PARK_HEAD called: refuse with `gcmd.error('Head N is not loaded; nothing to park.')`. No audit.
- **e<head>_filament is False** when ACE_PARK_HEAD called (head physically empty despite bookkeeping): refuse with `gcmd.error('Head N sensor reads no filament; bookkeeping is wrong. Use ACE_CLEAR_HEADS or ACE_MARK_HEAD_LOADED to fix first.')`. No audit (caller error).
- **Source ACE not reachable** (`active_device != source.ace_index` AND switch fails): emit `PARK_HEAD_FAILED reason=switch_failed`. raise.
- **FEED_AUTO LOAD=0 raises** (retract feed failure): emit `PARK_HEAD_FAILED reason=feed_auto_error error=<str(e)>`. raise. **NO sensor-fallback** here — for park, we genuinely need the wheel to spin to pull filament back. If feed fails, retry isn't safe (filament half-out is worse than fully-loaded).
- **e<head>_filament reads True post-retract** (retract didn't complete enough to clear the head): emit `PARK_HEAD_FAILED reason=sensor_still_detecting`. raise. Operator should run `ACE_PARK_HEAD HEAD=N` again with a longer LENGTH or fall back to `ACEC__Unload_T<n>` for a full retract.
- **`park_retract_length_mm` is mis-configured** (too short — filament still in shared bowden after park): user observes the next load from another ACE fails (jam at splitter). Recovery: full unload, recalibrate, retry. The audit log's `retract_length` field tells operator what they tried.
- **Print is in progress** when ACE_PARK_HEAD called: refuse with `gcmd.error('Cannot park during print.')` based on `print_stats.state`. No audit.

## 5. Testing

### Firmware (manual on hardware)

1. **Calibration drill**: print a 4-color test, then run `ACE_PARK_HEAD HEAD=0`. Visually inspect:
   - Filament at T0 sensor cleared (e0_filament=False)
   - Filament tip visible in ACE A's bowden tube (looking down the bowden) ~100 mm before the splitter
   - Slot 0 still shows filament at gate (gate_status=1)
   If filament is past the splitter into the shared bowden, `park_retract_length_mm` is too short. Increase by 50 mm and retry. If filament is back at the slot gate, decrease by 50 mm.

2. **Cross-ACE swap with park**: from a state where T1 is loaded from ACE A slot 1, run:
   ```
   ACE_PARK_HEAD HEAD=1
   ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1
   ```
   Verify total time < (full ACEC__Unload_T1 + ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1). Verify second load doesn't hit splitter friction (no abnormal phase3 retries from the parked filament blocking the path).

3. **Bookkeeping marker**: after a park, query `/printer/objects/query?ace`. `head_source[1]` should include `parked: True`. After the subsequent load, `head_source[1]` should have the new ACE/slot and no `parked` field.

4. **Refusal on empty head**: with T2 not loaded (head_source[2]=None), run `ACE_PARK_HEAD HEAD=2`. Should refuse with the "not loaded" error.

5. **Refusal during print**: start a print, attempt `ACE_PARK_HEAD HEAD=0` mid-print. Should refuse.

### Web (manual + visual)

1. **Cross-ACE swap UX**: with T1 sourced from ACE A, click ACE B slot 1's chevron → "→ T1". The web should:
   - Issue `ACE_PARK_HEAD HEAD=1` automatically (visible in Activity tab as a new `PARK_HEAD` action).
   - Then issue `ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1`.
   - Slot row updates to show T1 sourced from ACE B slot 1, with no flicker showing "T1 empty" in between (state.head_source[1] transitions A1 (parked) → B1 directly).

2. **Same-ACE swap unaffected**: with T1 sourced from ACE A slot 1, click ACE A slot 2 → "→ T1". The web should NOT issue ACE_PARK_HEAD (same-ACE swap, no cross-ACE penalty). Just the standard unload-then-load flow.

3. **Parked state visual**: temporarily induce a parked state by running `ACE_PARK_HEAD HEAD=0` directly. The Dashboard slot row for ACE A slot 0 should show a "parked" visual indicator (dashed border or "parked" badge). Click "→ T0" on ACE A slot 0 again — the load should be FAST (filament feeds from park to T0, not from gate).

### Backend / unit

- `test_state.py`: assert `head_source[h].parked` round-trips via WS payload. Single small test addition.

## 6. Migration

No data migration. `head_source` is reconstructed from `save_variables` on Klipper boot — old saves without `parked` markers just deserialize as no marker, which is the correct default ("not parked").

`ace.cfg` users will need to add `park_retract_length_mm` to the `[ace]` section (defaults to 600 if absent). Documentation update to `multiace/README.md` walks through the calibration step.

## 7. Out-of-scope follow-ups

- **Parked-source registry**: track every parked filament's (ACE, slot, park_distance) in firmware so swap-back can feed only the parked distance, not full bowden length. Requires a new `_parked_sources` dict alongside `_head_source`. Real swap-back optimization gain — but bigger change.
- **Park calibration UX**: a `ACE_CALIBRATE_PARK` macro that auto-discovers the right park length by retracting incrementally and watching `e<head>_filament` transitions. Replaces the trial-and-error in §5 step 1.
- **Multi-head park-and-load chain**: a single macro that parks all 4 heads then loads them from a different ACE, optimized for batch swaps. Would have made tonight's T1/T3 swap chains faster.
- **Print-end park option**: an opt-in macro that parks instead of fully unloads on print end. Useful for "I'm going to print again with the same loadout in an hour" workflows.

## 8. Critical-review note

This spec was approved on the basis of in-chat alignment but did NOT go through a separate code-reviewer / architect critical review pass. Before implementing, recommend a quick critical review focused on:

- Is `park_retract_length_mm` really a per-printer constant, or does it vary per-head (different bowden routing)?
- Does the firmware's existing FEED_AUTO LOAD=0 path accept a LENGTH override, or do we need a different mechanism?
- Is `parked: True` a sufficient marker, or do we need to track the precise park distance per head (in case a future calibration changes mid-session)?

These are the kinds of issues a critical-review pass would surface; flagging them here so the implementer doesn't get bitten.

## Open questions

1. **Per-head vs per-printer park length** — verify by inspection. If U1's bowden routing is symmetric across heads (likely), one printer-wide constant is enough.
2. **FEED_AUTO LOAD=0 LENGTH parameter** — verify by reading filament_feed.py. If unsupported, alternative is to call `ACE_RETRACT INDEX=<source_slot> LENGTH=<park_length>` directly, skipping FEED_AUTO entirely.
