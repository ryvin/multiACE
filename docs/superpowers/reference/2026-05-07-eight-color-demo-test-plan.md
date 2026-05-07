# 8-color demo print — test plan

**Purpose**: prove dual-ACE swap-during-print works end-to-end on the hardware, end with a print artifact that visibly demonstrates 8 distinct filament sources.

**Confidence prereqs (✓ achieved this session unless noted)**:
- ✓ Dual-ACE GUI ships per-slot 📖 picker, side-by-side ACE blocks, per-ACE autodry
- ✓ Wheel-encoder fallback (Tier 2) prevents the bookkeeping-gap failure mode
- ✓ `ACE_MARK_HEAD_LOADED` available for any orphan recovery
- ✓ Print-time autodry pin-to-active-ACE works
- ⚠ FilamentHub multi-ACE deep-link picker — **NOT NEEDED for the demo** but nice to have for spool tracking
- ⚠ Pre-v0.82 firmware constraint: only one ACE has feed_assist mid-print; the other ACE feeds via extruder gear pulling through the bowden (slower per-toolchange, but fully functional)

---

## 1. Hardware loadout to prepare

| Head | ACE | Slot | Filament | Color | Notes |
|---|---|---|---|---|---|
| T0 | A | 0 | PLA | (color 1) | Pick something visually distinct from T4–T7's slot |
| T1 | A | 1 | PLA | (color 2) | |
| T2 | A | 2 | PLA | (color 3) | |
| T3 | A | 3 | PLA | (color 4) | |
| T0* | B | 0 | PLA | (color 5) | * = swapped in mid-print |
| T1* | B | 1 | PLA | (color 6) | |
| T2* | B | 2 | PLA | (color 7) | |
| T3* | B | 3 | PLA | (color 8) | |

The U1 has only 4 toolheads, so 8 colors implies **mid-print swaps from ACE A's slots to ACE B's slots** — the test exercise. Suggested layout: print phases 1–2 use ACE A's 4 colors, then prompt-pause for swap, then phases 3–4 use ACE B's 4 colors.

**Color choice**: high-contrast across all 8 (e.g. white / yellow / orange / red / black / blue / green / silver). Easier to verify visually and in photos.

**Material choice**: keep all 8 PLA at first run. Keep things simple — different temps for PETG/ABS introduce variables that aren't part of "does the swap work?" Once PLA passes, repeat with mixed materials if desired.

---

## 2. Slicer setup

### Option A — Standard 8-tool slice (preferred)

Use OrcaSlicer or PrusaSlicer with **8 logical filaments** but slicing only emits T0–T3 in any single phase. Pick a model with 4 distinct color regions per phase, and split the print into two `.gcode` files:

- `phase1.gcode`: uses logical filaments 0–3 → physical T0–T3, draws colors 1–4
- `phase2.gcode`: uses logical filaments 4–7 → physical T0–T3 (after swap), draws colors 5–8

Print phase1 → cancel/pause → swap A→B for all 4 heads → resume with phase2.

### Option B — Single multi-tool job with M0 pause

One `.gcode` file with explicit `M0` (or `PAUSE`) at layer N halfway through. Operator runs the swap macro chain during the pause. Resume.

### Option C — Two separate cubes test (simplest)

Two 4-color test cubes printed back-to-back:

- Cube 1: T0–T3 from ACE A
- After cube 1 completes (auto), printer goes to standby
- Operator runs `swap-all-heads` macro (see §3.2)
- Cube 2: T0–T3 from ACE B (different colors)

This proves the swap mechanic without needing a custom 8-color slice. **Recommended for first run**.

### Color-region pattern in the model

For visibility:
- A simple 8-color **stripe staircase** (each color a vertical band)
- Or **8-segment ring** at print-bed level
- Or **8 letters** (e.g., "ACE A B C D E F") each in a different color

Avoid intricate color mixing — we want visual proof of which color came from which ACE/slot, not a piece of art.

---

## 3. Pre-print procedure

### 3.1 State preparation

1. **Verify safe state**:
   ```bash
   curl -s http://192.168.1.171:7125/printer/objects/query?print_stats | jq -r '.result.status.print_stats.state'
   ```
   Must return `standby` / `complete` / `cancelled` / `error`.

2. **Clean head_source bookkeeping**: confirm all 4 heads have correct `head_source[h]`:
   ```bash
   curl -s "http://192.168.1.171:7125/printer/objects/query?ace" \
     | python3 -c "
   import sys, json
   hs = json.load(sys.stdin)['result']['status']['ace']['head_source']
   for h in (0,1,2,3): print(f'T{h}: {hs.get(str(h))}')"
   ```
   Each head should report `{ace_index, slot, type, color, brand}`. None ⇒ load it before starting.

3. **Verify all 4 heads have filament physically**:
   ```bash
   curl -s "http://192.168.1.171:7125/printer/objects/query?filament_motion_sensor%20e0_filament&filament_motion_sensor%20e1_filament&filament_motion_sensor%20e2_filament&filament_motion_sensor%20e3_filament" \
     | python3 -c "
   import sys, json
   d = json.load(sys.stdin)['result']['status']
   for h in (0,1,2,3):
       sens = d.get(f'filament_motion_sensor e{h}_filament',{}).get('filament_detected')
       print(f'e{h}_filament={sens}')"
   ```
   All four `True`.

4. **Confirm dual-ACE detected**:
   ```bash
   curl -s "http://192.168.1.171:7125/printer/objects/query?ace" \
     | python3 -c "import sys, json; print('device_count:', json.load(sys.stdin)['result']['status']['ace']['device_count'])"
   ```
   Must equal `2`.

5. **Set autodry per ACE** (so humidity is maintained during the print):
   ```bash
   for ace in 0 1; do
     curl -s -X POST "http://192.168.1.171/multiace/api/autodry?ace=$ace" \
       -H "Content-Type: application/json" \
       --data '{"enabled": true, "target_pct": 15, "hysteresis_pp": 5, "default_filament_type": "PLA", "keep_ready": true}'
   done
   ```

6. **Hotend preheat**: bring T0 to print temp (210 °C for PLA). Other heads will warm via toolchange.

### 3.2 Create the swap-all-heads macro chain

For Option C (two cubes), define a one-shot `SWAP_ALL_TO_ACE_B` macro in the printer's gcode terminal or as a Klipper macro:

```cfg
# Optional helper — paste in printer.cfg or include in ace.cfg group H if useful
[gcode_macro SWAP_ALL_HEADS_TO_ACE_B]
description: Unload all 4 heads from ACE A and reload them from ACE B's matching slots. Use only when print_stats.state is safe.
gcode:
  ACEC__Unload_T0
  ACEC__Unload_T1
  ACEC__Unload_T2
  ACEC__Unload_T3
  ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=0
  ACE_LOAD_HEAD HEAD=1 ACE=1 SLOT=1
  ACE_LOAD_HEAD HEAD=2 ACE=1 SLOT=2
  ACE_LOAD_HEAD HEAD=3 ACE=1 SLOT=3
```

This is a **chained macro that takes ~25–35 minutes** to run all 8 operations. Do NOT issue it mid-print on Klipper's main thread without verifying safe state first.

For first-time tests, prefer issuing each `ACEC__Unload_T<n>` and `ACE_LOAD_HEAD ...` one at a time so you can observe each transition.

---

## 4. During-print observability checklist

Run a terminal that polls `/api/state` + tails the audit log every few seconds. Watch for:

| Signal | What "all good" looks like | Red flags |
|---|---|---|
| `print_stats.state` | `printing` throughout phase 1 | `paused` (operator-pause) or `error` |
| `ace.status` | `ready` between toolchanges, `busy` briefly during cross-ACE feeds (post-v0.82 only — pre-v0.82, no feed during print) | stays `busy` for minutes |
| `swap_in_progress` | `False` most of the time; `True` briefly during any active SWITCH | stuck `True` between toolchanges |
| Audit log: `SWITCH_AUTO_PASSIVE` per toolchange | One entry per Tn select with `action: cross_ace_disconnected` (when target ACE != active) or `same_ace_noop` | `SWITCH_FAILED` |
| `e<n>_filament` for each loaded head | `True` whenever that head is the active extruder | `False` mid-print = filament runout / extruder gear lost grip |
| `extruder.position` (active head) | Increasing as the print extrudes | Stuck at 0 = extrude commands not landing |
| `head_source[h]` for active head | Matches what the slicer expects | Mismatch = wrong color being printed |
| Cumulative `LOAD_HEAD_SUSPICIOUS` events | 0 ideal; a few `feed_auto_error_sensor_fallback` acceptable (Tier 2 working) | `LOAD_HEAD_FAILED` = pause + recover |

A simple watcher script:

```bash
while true; do
  clear
  date
  curl -s --max-time 4 "http://192.168.1.171:7125/printer/objects/query?ace&print_stats&toolhead&extruder&filament_motion_sensor%20e0_filament&filament_motion_sensor%20e1_filament&filament_motion_sensor%20e2_filament&filament_motion_sensor%20e3_filament" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)['result']['status']
ps = d['print_stats']
ace = d['ace']
th = d['toolhead']
e = d['extruder']
print(f\"PRINT  state={ps['state']:<8} curr_ext={ps.get('current_extruder')} active_t={th['extruder']:<10} e_temp={e['temperature']:.0f}/{e['target']:.0f} e_pos={e.get('position',0):.1f}\")
print(f\"ACE    status={ace['status']:<6} active={ace['active_device']} dev_count={ace['device_count']} swap={d.get('swap_in_progress')}\")
hs = ace.get('head_source',{})
for h in (0,1,2,3):
    src = hs.get(str(h))
    sens = d.get(f'filament_motion_sensor e{h}_filament',{}).get('filament_detected')
    label = f'ACE {chr(65+src[\"ace_index\"])}/{src[\"slot\"]}' if src else 'empty'
    print(f'  T{h}: {label:<12} e{h}_fil={sens}')"
  sleep 5
done
```

Run in a side terminal during the demo print.

---

## 5. Acceptance criteria

The demo passes if **all** of the following are true:

1. **Phase 1 (ACE A) prints to completion** with all 4 colors visibly distinct and correct (color matches the slot we loaded).
2. **Swap completes cleanly**: 8/8 ACEC__Unload + ACE_LOAD_HEAD return HTTP 200 (or HTTP 200 with `LOAD_HEAD_SUSPICIOUS reason=feed_auto_error_sensor_fallback` audit entries — Tier 2 firing is acceptable).
3. **Phase 2 (ACE B) prints to completion** with the new 4 colors. Each color visually traceable to the (ACE B, slot N) we loaded.
4. **No `LOAD_HEAD_FAILED`** in the multiace_state.log for the duration. (`LOAD_HEAD_SUSPICIOUS` is fine — it's the Tier 2 recovery firing.)
5. **No `SWITCH_FAILED`**.
6. **`head_source[h]` correct** at the end of phase 1 (ACE A) and end of phase 2 (ACE B). Verified via `/printer/objects/query?ace`.
7. **No filament runouts** (`e<n>_filament=False` mid-extrusion on the active head).
8. **Post-print autodry FSM state is sensible** — no FAULT, no stuck IDLE, no unreachable flags set.

If any of 4–7 fails, the demo halts and we recover before proceeding.

---

## 6. Recovery playbook (if something goes wrong)

| Symptom | Recovery |
|---|---|
| `LOAD_HEAD_FAILED` mid-swap, filament physically reached head | `ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S` (Tier 1) — restores bookkeeping without unload/reload |
| `LOAD_HEAD_FAILED` mid-swap, filament did NOT reach head | Standard: ACEC__Unload_T<n> to clear, then retry the load. Inspect splitter for jam. |
| Wrong color printed (head_source mismatch with slicer expectations) | PAUSE the print. Issue `ACE_CLEAR_HEADS` to wipe bookkeeping, then re-load each head from the correct (ACE, slot). RESUME. |
| Print pauses unexpectedly | Check `display_status.message`, `print_stats.message`, klippy.log. Common cause: filament motion sensor triggered runout. |
| ACE B not detected (`device_count=1`) | USB hub flaked — power cycle ACE B + restart Klipper. Recovery is destructive to in-flight prints. |
| Web service down (multiace UI 502) | Recovery in `multiace_web/install/install_web.sh` notes; `S62multiace-web restart`. Klipper continues to drive the print regardless. |

---

## 7. Post-print artifacts to capture

For documentation / portfolio:

- Photos of the printed object showing all 8 colors
- Screenshot of the multiace web Dashboard at end of print (showing `head_source` for all 4 heads bound to ACE B)
- The `multiace_state.log` slice from print start → print end (for the audit timeline)
- The `klippy.log` slice for the same window (for low-level FEED_AUTO traces)
- Any `LOAD_HEAD_SUSPICIOUS` count summary — useful for monitoring wheel-encoder degradation over time

---

## 8. Future variations once base demo passes

- **Mixed materials**: 4× PLA on ACE A, 4× PETG on ACE B. Tests autodry per-ACE and temp-change handling on toolchange.
- **Mid-print autodry trigger**: deliberately raise ambient humidity (or just lower target_pct to 5%) so autodry kicks during the print on the active ACE. Verify dryer fires + completes without disrupting print quality.
- **Cross-ACE mid-toolchange (post-v0.82)**: when v0.82 lifts the single-USB constraint, rerun the demo with toolchanges across ACEs WITHOUT pausing — should be transparent to the slicer.
- **8-color single-pass slice** (Option A above) once we trust the swap mechanic — go from "two cubes back-to-back" to "one model, 8 colors, one print job."
