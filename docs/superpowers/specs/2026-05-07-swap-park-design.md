# Swap-park — partial-retract for cross-ACE filament swaps

**Status:** redesigned 2026-05-09 to drop the `ACE_PARK_HEAD` verb in favor of a `LENGTH=` parameter on the existing `ACE_UNLOAD_HEAD` (per decay71 prior art — see Provenance).
**Branch:** `feat/swap-park` off `main`.
**Scope:** Add `LENGTH=` parameter to `cmd_ACE_UNLOAD_HEAD` in firmware. Web smart-swap (already shipped on `main`) uses the parameterized unload for cross-ACE displacement; same chain shape, smaller firmware surface.

## One-line goal

When the user swaps a head's source from ACE A to ACE B, the firmware shouldn't fully retract back to slot A's gate — it should retract only as far as the ACE-side bowden just past the splitter, ready to re-feed quickly if the user swaps back.

## Provenance

The original v1 of this spec (2026-05-07) proposed a new `ACE_PARK_HEAD HEAD=N` gcode verb that internally called `FEED_AUTO LOAD=0` with a length override. Investigation of `decay71/multiACE` (a sibling fork) on 2026-05-09 found a cleaner design: their `ACE_UNLOAD_HEAD` already accepts a `RETRACT_LENGTH` parameter, and their `ACE_SWAP_HEAD` orchestration just passes a shorter length when the workflow wants a "park" effect. No separate park verb. This spec adopts the same approach for ryvin: parameterize the existing unload primitive instead of adding a new verb.

**Why this matters:**
- Smaller firmware surface (one parameter, not a new gcode command + macros + audit shape)
- Aligns conceptually with decay71 — easier cross-fork comparison and contribution
- Web smart-swap state machine (already merged at `498f4f6`) is unchanged — still calls two leg primitives, just passes `LENGTH=600` on the cross-ACE leg-1
- `head_source[N].parked` boolean stays as a runtime marker (no schema break)

## Background — what we observed

During the T1 B1→A1 swap on 2026-05-07, the operator noted: *"you didn't have to fully extract for a swap between aces in case you need to swap back."*

The current `ACEC__Unload_T<n>` macro retracts filament from the head sensor all the way back to the slot's gate retainer (~700+ mm in the U1's bowden setup). When the next operation is "load from the OTHER ACE into the same head," that fully-retracted filament is wasted travel:

- **B1 unload**: ~3 min, retracts B1 from T1 all the way back into ACE B's slot
- **A1 load**: ~3 min, has to feed from ACE A's slot all the way to T1
- **If user swaps back to B1 later**: another ~3 min unload + ~3 min load

The "park" insight: leave the filament tip parked in the ACE-side bowden just past the splitter junction. Subsequent swap-back avoids the long bowden traversal.

## Scope (v1)

**Firmware changes** to `multiace/klipper/extras/ace.py`:
1. Add `LENGTH=` parameter to `cmd_ACE_UNLOAD_HEAD`. Default: full retract (current behavior). When provided: retract that many mm and stop.
2. After the retract, set `head_source[N].parked = True` if `LENGTH` was explicitly provided AND the retract completed without clearing the slot. Otherwise (full unload), `head_source[N] = None` (current behavior).
3. Audit log: `UNLOAD_HEAD` action's existing fields (`{head}`) gain `{length, parked}` when those apply.
4. Add `[ace]` config key `default_park_retract_length_mm` (default 600). Used by `ACEC__Park_T<n>` convenience macros and by the web smart-swap.

**Convenience macros** in `multiace/config/extended/ace.cfg` (group H):
- `ACEC__Park_T0` through `ACEC__Park_T3` — wrap `ACE_UNLOAD_HEAD HEAD=N LENGTH={default_park_retract_length_mm}`. Same operator-facing semantics as the v1 spec, just under the existing verb.

**Web changes:** none required. The smart-swap state machine in `app.js` (already merged at `498f4f6`) calls two legs:
- `_executeSmartSwapLeg1` for cross-ACE: call `ACE_UNLOAD_HEAD HEAD=N LENGTH=600` (replaces the planned `ACE_PARK_HEAD HEAD=N`)
- `_executeSmartSwapLeg2`: existing `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S`

The `usePark` boolean in `initiateSmartSwap` becomes "use parameterized unload"; the time estimate stays at ~4 min.

**Web capability detection:** `_probe_swap_park` in `poller.py` currently looks for `gcode_macro ACEC__Park_T0`. The convenience macros land in this spec, so the probe stays valid as-is. (After this spec ships, the macros exist; the probe flips true; cross-ACE swaps automatically use the short retract.)

## Non-goals (v1)

- **No automatic park** in normal `ACEC__Unload_T<n>`. Existing macro stays as full retract — that's still the right action for "remove this spool entirely."
- **No park-on-print-end**. Print-end always wants the full unload.
- **No mid-print park**. v0.81 firmware can't switch ACEs mid-print anyway.
- **No multi-head park sequencing**. v1 is one head at a time.
- **No park-aware swap-back optimization in firmware**. The web layer doesn't track "this head is parked at A2; if user swaps back to A2, do a short load instead of a full feed." Deferred to v1.5.

## Hardware contract assumptions

1. **Splitter-side park position is calibratable per printer.** Reasonable default: 600 mm. Operator calibrates by trial: too short = filament still in shared bowden = blocks the other ACE; too long = filament back at the gate (defeats the point).

2. **The "park" position must be physically observable.** After a park, `e<head>_filament` should read False. `gate_status[slot]` should still read 1. The park position is inferred from the audit log's `length` field + the wheel encoder having ticked the right amount during retract.

3. **Park calibration value lives in `ace.cfg [ace]` section** as `default_park_retract_length_mm` (default 600). Used as the default `LENGTH` for the `ACEC__Park_T<n>` macros.

## 1. Architecture

### Firmware change to `cmd_ACE_UNLOAD_HEAD`

Current signature: `ACE_UNLOAD_HEAD HEAD=<n>` — full retract via `FEED_AUTO ... UNLOAD=1 STAGE=prepare/doing`.

New signature: `ACE_UNLOAD_HEAD HEAD=<n> [LENGTH=<mm>]` — when LENGTH is provided, the retract stops after that many mm.

The implementation needs to thread `LENGTH` through to `FEED_AUTO`. Two paths depending on what `FEED_AUTO` accepts today:

- **(a) `FEED_AUTO` accepts a `LENGTH=` parameter:** simplest — `cmd_ACE_UNLOAD_HEAD` just passes it through. Audit log gains `length` field when set.
- **(b) `FEED_AUTO` doesn't:** add `LENGTH` plumbing into `filament_feed_ace.py`'s `FEED_AUTO` UNLOAD path. Probably ~30 lines: thread `length` from gcode to the unload state machine, stop the wheel-encoder loop when accumulated length reaches the target.

Implementer's first task: read `multiace/klipper/extras/filament_feed_ace.py` to determine which path applies. The existing `FEED_AUTO ... UNLOAD=1 STAGE=prepare/doing` invocations have NO length parameter; they unload to a fixed default. Path (b) is likely.

### head_source.parked semantics

When `LENGTH` is provided AND the retract completes (sensor clears at head), set `head_source[N].parked = True` and KEEP `head_source[N].ace`/`slot` (filament still belongs to that source).

When `LENGTH` is NOT provided (full unload, default), `head_source[N] = None` (current behavior — filament fully retracted to slot, head is empty).

The web's existing parked-state visual (dashed border + "Parked" badge) reads this flag and renders accordingly. The Unload chevron item still appears on parked heads (clicking it issues `ACE_UNLOAD_HEAD HEAD=N` with NO length — fully retracts the parked filament back to slot).

### Web smart-swap delta (no code change required)

The web's `_executeSmartSwapLeg1` (in `app.js`) currently has:

```js
if (usePark) {
  leg1Ok = await sendScript(`ACE_PARK_HEAD HEAD=${targetHead}`);
} else {
  // ... full unload via ACEC__Unload_T<n> macro ...
}
```

When this firmware change ships, the `usePark` branch's gcode call should change to:

```js
if (usePark) {
  leg1Ok = await sendScript(`ACE_UNLOAD_HEAD HEAD=${targetHead} LENGTH=600`);
}
```

The `LENGTH=600` value should match the `default_park_retract_length_mm` config. Implementer can either hardcode 600 in the JS (simple, fragile) or add a `state.parkRetractLength` field surfaced from `state.py` (clean). Recommend the former for v1; the latter for v1.1 if calibration changes per deployment.

## 2. Components

### `multiace/klipper/extras/ace.py`

- `cmd_ACE_UNLOAD_HEAD_help` updated to mention `LENGTH=` parameter and explain semantics.
- `cmd_ACE_UNLOAD_HEAD` body: read `gcmd.get_int('LENGTH', None)`. Pass to `FEED_AUTO` (or to the underlying unload state machine — path b).
- After successful retract: if `length is not None`, set `self._head_source[head]['parked'] = True` (keep ace/slot/etc). Else clear `head_source[head] = None` as today.
- `_audit_state('UNLOAD_HEAD', ...)` payload gains `length` (when set) and `parked: True/False`.
- New config read in `__init__`: `self.default_park_retract_length_mm = config.getint('default_park_retract_length_mm', 600, minval=100, maxval=2000)`.

### `multiace/klipper/extras/filament_feed_ace.py` (if path b applies)

- Thread a `length` parameter through `FEED_AUTO`'s UNLOAD path
- Stop the unload loop when the accumulated retract distance reaches the target
- If sensor clears before the target distance, that's still a successful park (filament tip is past the splitter, just earlier than expected — operator-side it's fine)

### `multiace/config/extended/ace.cfg`

```cfg
# (in [ace] section)
default_park_retract_length_mm: 600
# Passed to ACE_UNLOAD_HEAD by ACEC__Park_T<n> macros. Operator calibrates per
# printer geometry — default 600mm parks the filament tip in the ACE-side bowden
# just past the splitter Y-junction. Tune up if the parked tip blocks the other
# ACE's feed; tune down if it ends up at the gate.
```

Group H convenience macros:

```cfg
[gcode_macro ACEC__Park_T0]
description: Park T0 — partial retract using default_park_retract_length_mm.
gcode:
  ACE_UNLOAD_HEAD HEAD=0 LENGTH={ printer["gcode_macro _MULTIACE_VARS"].default_park_retract_length_mm | default(600) }

# (T1, T2, T3 analogous)
```

If `gcode_macro _MULTIACE_VARS` doesn't exist (it might not — depends on existing convention in `ace.cfg`), fall back to a literal:

```cfg
gcode:
  ACE_UNLOAD_HEAD HEAD=0 LENGTH=600
```

The literal-600 form ships v1; the printable-config-driven form is a v1.1 polish.

### `multiace_web/src/multiace_web/static/app.js`

Change ONE line in `_executeSmartSwapLeg1`:

```js
// Before:
leg1Ok = await sendScript(`ACE_PARK_HEAD HEAD=${targetHead}`);
// After:
leg1Ok = await sendScript(`ACE_UNLOAD_HEAD HEAD=${targetHead} LENGTH=600`);
```

That's the entire web change. The capability probe (`_probe_swap_park` checking for `gcode_macro ACEC__Park_T0`) stays valid because this spec ships those macros.

### `multiace_web/src/multiace_web/poller.py`

No change. The probe already looks for `ACEC__Park_T0`; this spec adds it.

### `multiace/install/install_multiace.sh` / `uninstall_multiace.sh`

No change. Existing `ace.cfg` install steps cover the new config key + macros.

### Tests

- **Firmware**: per CLAUDE.md, no automated tests. Live validation per §5.
- **Web**: existing `test_state.py` round-trips `head_source[h].parked`. No new tests needed (the JS change is one line; behavior is the same as the existing parked-state path).
- **Calibration drill**: see §5.

## 3. Data flow

### Cross-ACE swap with parameterized unload

```
[user clicks "→ T1" from ACE B slot 1's chevron menu]
       │
       ▼
initiateSmartSwap(targetHead=1, targetAce=1, targetSlot=1, headState="loaded_cross_ace"):
  // currentSrc = state.head_source[1] = {ace: 0, slot: 1}  (sourced from ACE A)
  // usePark = state.swapParkAvailable && headState === "loaded_cross_ace" → true
  // toast countdown → 3 sec → user does not cancel
       │
       ▼
_executeSmartSwapLeg1(usePark=true):
  state.smartSwapPending = { head: 1, leg: 1, ... }
  await sendScript("ACE_UNLOAD_HEAD HEAD=1 LENGTH=600")
       │
       ▼
[Firmware]
  cmd_ACE_UNLOAD_HEAD(HEAD=1, LENGTH=600):
    head_source[1] = {ace:0, slot:1}  (read)
    active_device = 0 (no switch needed for unload)
    FEED_AUTO MODULE=... CHANNEL=... EXTRUDER=1 UNLOAD=1 STAGE=prepare
    FEED_AUTO MODULE=... CHANNEL=... EXTRUDER=1 UNLOAD=1 STAGE=doing  LENGTH=600
    -> Retract 600mm; sensor e1_filament transitions True → False
    head_source[1] = {ace:0, slot:1, ..., parked: True}
    audit: UNLOAD_HEAD {head:1, length:600, parked:true}
       │
       ▼
_executeSmartSwapLeg2:
  state.smartSwapPending = { head: 1, leg: 2, ... }
  await sendScript("ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1")
       │
       ▼
[Firmware standard load path — switches A→B, feeds from ACE B slot 1]
       │
       ▼
audit: SWITCH_TARGET, SWITCH, LOAD_HEAD_TIP_REFRESHED, ..., LOAD_HEAD
state.head_source[1] = {ace:1, slot:1, ...}  (parked marker gone)
state.smartSwapPending = null
       │
       ▼
[Old A1 filament is parked in ACE A bowden at ~600mm; if user later swaps back
to A1, the load operation has to traverse the full bowden again because the
firmware doesn't track "this slot has parked filament waiting." That's the
v1.5 swap-back optimization deferral.]
```

## 4. Error handling

- **`head_source[N]` is None**, full unload requested: refuse with `gcmd.error('Head N is not loaded.')`. (Current behavior; no change.)
- **`head_source[N]` is None**, length unload requested: same refusal.
- **`LENGTH` < 100 or > 2000**: refuse with `gcmd.error('LENGTH must be 100-2000mm.')`. (Sanity bound.)
- **Sensor reads False before retract starts** (head physically empty despite bookkeeping): refuse with the existing tooltip about ACE_MARK_HEAD_UNLOADED for recovery.
- **Sensor still reads True after retract completes** (LENGTH was too short to clear the head): emit `UNLOAD_HEAD_FAILED reason=sensor_still_detecting length=<N>`. Operator should retry with a longer LENGTH or full unload.
- **`FEED_AUTO` raises mid-retract**: emit `UNLOAD_HEAD_FAILED reason=feed_auto_error error=<str(e)>`. Existing recovery behavior. Wheel-encoder Tier-2 fallback (already shipped) catches this gracefully.
- **Print is in progress**: refuse with the existing print-state gate. (No change.)

## 5. Testing

### Firmware (manual on hardware)

1. **Calibration drill**: with T0 loaded from A1, run `ACE_UNLOAD_HEAD HEAD=0 LENGTH=600`. Visually inspect:
   - Filament tip pulled out of T0's hotend (e0_filament=False)
   - Filament tip visible in ACE A's bowden tube ~100mm before the splitter
   - Slot 0 still shows filament backed up to its gate (gate_status[0]=1)
   If filament is past the splitter (in shared bowden, blocking the other ACE), increase LENGTH to 700 and retry. If filament is back at the slot gate, decrease to 500. Pin the calibrated value as `default_park_retract_length_mm` in `ace.cfg`.

2. **Cross-ACE swap with park — timing comparison**: from a state where T1 is loaded from ACE A slot 1, time both paths:

   Path A (parameterized unload, ~600mm):
   ```
   time curl -s -X POST "http://$DAVINCI_U1_HOST:7125/printer/gcode/script" \
     -H "Content-Type: application/json" \
     --data '{"script":"ACE_UNLOAD_HEAD HEAD=1 LENGTH=600\nACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1"}'
   ```

   Path B (full unload, no LENGTH):
   ```
   time curl -s -X POST "http://$DAVINCI_U1_HOST:7125/printer/gcode/script" \
     -H "Content-Type: application/json" \
     --data '{"script":"ACEC__Unload_T1\nACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1"}'
   ```

   Path A should be at least 1 minute faster than Path B.

3. **Bookkeeping marker**: after `ACE_UNLOAD_HEAD HEAD=0 LENGTH=600`, query `/printer/objects/query?ace`. `head_source[0]` should include `parked: True` AND retain its original `ace`/`slot`. After a full unload (`ACE_UNLOAD_HEAD HEAD=0` no length), `head_source[0]` should be `None`.

4. **Web UX**: after a parked state, the slot card should show the dashed "Parked" badge (already shipped). The chevron menu's `↗ Unload T<n>` item on the parked source slot should still appear and, when clicked, fully retract the parked filament back to the slot (`ACE_UNLOAD_HEAD HEAD=0` with no length).

5. **Refusal during print**: start a print, attempt `ACE_UNLOAD_HEAD HEAD=0 LENGTH=600` mid-print. Should refuse with the existing print-state gate.

6. **Smart-swap end-to-end**: after the firmware change ships, click `→ T1` on a cross-ACE slot in the web. The toast should show "~4 min" timing (cross-ACE-with-park branch). Audit log should show `UNLOAD_HEAD {length:600, parked:true}` followed by `LOAD_HEAD`. Total elapsed ~4 min instead of ~6.

## 6. Migration

No data migration. `head_source` already supports the `parked` field (round-trip test landed at `27c2c9a`); old saves without `parked` markers deserialize as `parked` falsy, which is the correct default.

`ace.cfg` operators add `default_park_retract_length_mm` to the `[ace]` section (defaults to 600 if absent). Documentation update to `multiace/README.md` walks through the calibration step.

The original v1 spec's `ACE_PARK_HEAD` verb is NOT shipped — operators who saw that name in earlier docs should use `ACE_UNLOAD_HEAD HEAD=N LENGTH=<mm>` or the `ACEC__Park_T<n>` convenience macros.

## 7. Out-of-scope follow-ups

- **Park-aware swap-back optimization** — track parked filaments in firmware so swap-back can feed only the parked distance. Requires a new `_parked_sources` dict alongside `_head_source` and load-side awareness. Real swap-back optimization gain — but bigger change. Deferred to v1.5.
- **Park calibration auto-discovery** — a `ACE_CALIBRATE_PARK` macro that retracts incrementally and watches `e<head>_filament` transitions. Replaces operator trial-and-error.
- **Multi-head park-and-load chain** — single macro that parks all 4 heads then loads them from a different ACE.
- **Print-end park option** — opt-in macro that parks instead of fully unloads on print end.
- **Mid-print readiness** — see §8 below for patterns to steal from decay71's `ACE_SWAP_HEAD` when web-ops gets lifted from pre-print-only.

## 8. Mid-print readiness — patterns to steal from decay71

When this spec's firmware ships and the web smart-swap moves from pre-print-only to mid-print-aware (a v2 concern, not in scope here), the following patterns from decay71's `cmd_ACE_SWAP_HEAD` (`ace.py:2783–2990` on `decay71/main`) are the right reference:

1. **Heater hold during unload via `KEEP_HEAT=swap_temp` parameter on `ACE_UNLOAD_HEAD`** — prevents nozzle cool-down between legs of a swap.
2. **Position save/restore around the unload+load sequence** — saves printhead position, lifts Z, performs swap, restores.
3. **Toolhead switch (`T<n> A0`)** if the active extruder differs from the swap target — selects the right extruder for the swap legs.
4. **`_pause_for_recovery()` firmware-side recovery** — if leg1 or leg2 fails, pause the print and write a multilingual instruction sheet to the Fluidd log; operator runs follow-up gcode and `RESUME`. Distinct from the current web-side state-aware retry toast (which works for pre-print but NOT for mid-print since the print is paused).
5. **Anti-ooze retract sequencing** (`swap_anti_ooze_retract`) — small retract before un-loading and small extrude after re-loading to manage drool.
6. **Post-load nozzle clean** (`ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD`) — primes the new filament cleanly before resuming the print.

Each pattern is ~10-30 lines in decay71's source. Copy the structure; rewire to ryvin's existing primitives (the heater hold, position save, etc. all have direct ryvin equivalents).

The spec does NOT require these for v1 (pre-print only). They're called out here so the implementer doesn't paint themselves into a corner that would need a rewrite when mid-print lands.

## Open questions

1. **Does `FEED_AUTO`'s UNLOAD path already accept a length parameter?** Implementer's first action: read `multiace/klipper/extras/filament_feed_ace.py`. If yes, path (a) — minimal change. If no, path (b) — thread the parameter through ~30 lines.

2. **Should the literal `LENGTH=600` in `app.js` be parameterized via `state.parkRetractLength`?** v1 says hardcode for simplicity. v1.1 if multi-deployment calibration becomes a real need.

3. **What does `FEED_AUTO ... UNLOAD=1 STAGE=...` actually retract today?** Defaults are buried in the existing implementation; the spec needs to confirm the full-retract distance is roughly 700-800mm so 600mm parking is meaningfully shorter (hence the timing win).

## Plan dependency note

The original plan at `docs/superpowers/plans/2026-05-07-swap-park.md` was written against the v1 (ACE_PARK_HEAD verb) spec. **It needs regeneration** to match this redesigned spec before implementation begins. The plan's scope shrinks meaningfully — fewer firmware lines, no new audit shape, a one-line web change. Re-run writing-plans against this v2 spec.
