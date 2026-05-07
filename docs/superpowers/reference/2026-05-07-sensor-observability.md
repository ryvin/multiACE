# Sensor observability — multiACE filament swap

**Purpose**: catalog every observable signal during a cross-ACE filament swap on a Snapmaker U1 with two ACE Pro units, plus annotated timelines so an operator (or an automation) can tell what's actually happening at each step.

**Scope**: filament motion from spool → ACE drive wheel → bowden → splitter → extruder gear → hotend nozzle. Includes both the firmware's bookkeeping signals and the physical sensors. Excludes printer XYZ kinematics, bed/chamber heaters not directly involved in the swap.

---

## 1. The sensor map

```
                                  ┌─────────────────────────────────┐
                                  │       Snapmaker U1 toolhead     │
                                  │                                 │
   ┌──────────┐                   │   ┌──────┐                      │
   │ ACE A    │                   │   │ T0   │ extruder + hotend    │
   │ slots    │                   │   │      │ ┌─────┐              │
   │ 0 1 2 3  │                   │   └──┬───┘ │ NZL │ ◄── nozzle   │
   │  └─┬─┘   │                   │      │     └─────┘              │
   │    │     │                   │   e0_filament motion sensor      │
   └────┼─────┘                   │      ▲                          │
        │                         │      │ (downstream of gear)     │
        │ bowden A                │   ┌──┴───┐                      │
        │                         │   │ T1   │                      │
   ┌────┴────┐                    │   └──┬───┘  e1_filament          │
   │SPLITTER │  ◄── Y-junction:   │      │                          │
   │ (Y-jct) │      where bowden  │   ┌──┴───┐                      │
   │         │      from A and B  │   │ T2   │                      │
   └────┬────┘      converge      │   └──┬───┘  e2_filament          │
        │                         │      │                          │
   ┌────┼─────┐                   │   ┌──┴───┐                      │
   │ ACE B    │                   │   │ T3   │                      │
   │ slots    │                   │   └──┬───┘  e3_filament          │
   │ 0 1 2 3  │                   │      │                          │
   │  └─┬─┘   │                   │   bed                           │
   │    │     │                   │                                 │
   │ bowden B │                   └─────────────────────────────────┘
   └──────────┘

   ACE side                       printer side
   (one of A/B "active" at a time   (sees the live filament that
    via USB serial — pre-v0.82       any active ACE pushed through)
    only one ACE talks to firmware)
```

The dual-ACE topology has **one shared bowden + extruder side** (the printer) and **two parallel ACE sides**, joined at the splitter. Only one ACE drives at a time pre-v0.82.

---

## 2. Sensor catalog

### 2.1 Per-slot ACE-side signals

| Signal | Source | What it tells you | Where to query |
|---|---|---|---|
| `slot.<n>.status` | ACE serial response | `ready` / `busy` / `unwinding` — ACE's own state machine | `printer/objects/query?ace` → `result.status.ace.slots[n].status` |
| `slot.<n>.rfid` | ACE NFC reader, parsed by firmware | `1` if RFID tag detected at the slot, else `0`. **Does NOT mean OpenSpool data parsed** — Anycubic format only. | same path, `.slots[n].rfid` |
| `slot.<n>.type` / `.color` / `.sku` / `.brand` | Anycubic-tag parser | Filament metadata when tag is recognized; empty string for OpenSpool tags. | `.slots[n].type` etc. |
| `gate_status[n]` | ACE gate sensor | `1` if filament tip is at the slot's gate retainer (loaded into the gate), `0` if fully retracted/empty. **ONLY for the active ACE** — inactive ACE's gate_status is unobservable today. | `.gate_status` (4-element array) |
| `wheel encoder counts a/b` | ACE drive-wheel rotation | Two-axis tick counts; pre/post-feed deltas tell whether the drive wheel actually moved during a load. **The flaky signal that drove our wheel-encoder fallback work.** | Internal — exposed via `_read_wheel_counts(module, channel)` in `ace.py`. Surfaced at audit time as `wheel_delta_a/b` in `LOAD_HEAD_SUSPICIOUS reason=wheel_not_moving`. |
| `coil_freq_start/end_min/max` | ACE inductive load-detection coil | Frequency drift while filament moves through the coil zone. Per-retry value in klippy.log `[feed_loading] phase3:` lines. Real motion shows ~50–100 Hz delta per retry. | Not directly via Moonraker — only in `klippy.log` lines emitted by FEED_AUTO. |
| `ace.status` | ACE state machine | `ready` (idle), `busy` (drive wheel running), `unwinding` (long retract), errors. | `result.status.ace.status` |
| `ace.temp` | ACE chamber thermistor | Internal ACE Pro temp — not the dryer setpoint, the actual reading. | `.temp` |
| `dryer_status` | ACE drying sub-state | `status: stop|drying`, `target_temp`, `remain_time` | `.dryer_status.{status,target_temp,remain_time}` |

### 2.2 Per-head printer-side signals

| Signal | Source | What it tells you | Where to query |
|---|---|---|---|
| `e<n>_filament` | filament_motion_sensor at each head | **Single most reliable signal that filament is physically at the toolhead.** Triggers on motion through the sensor; goes True when filament threads through; reads stale (last-seen) state when stationary. | `filament_motion_sensor e<n>_filament` Moonraker object → `.filament_detected` |
| `extruder` / `extruder1..3` temperature | hotend thermistor per head | `temperature`, `target`, `power`. Min ~170°C required for any extrude move (Klipper `min_extrude_temp`). | `printer.objects.query?extruder&extruder1&extruder2&extruder3` |
| `extruder.position` | step-counted E axis | Cumulative E motion. Stays at 0 if the E driver isn't engaged or if commands are silently dropped. Useful for "did the extruder gear actually move?" answers. | `.extruder.position` |
| `toolhead.extruder` | active toolhead object | Which extruder is currently the target of `G1 E...` commands (`extruder`, `extruder1`, `extruder2`, `extruder3`). Determines what `G1 E10` actually moves. | `printer.objects.query?toolhead` → `.toolhead.extruder` |
| `toolhead.homed_axes` | homing state | XYZ homing status. Empty string = not homed. Some Klipper configs gate motion (including E in some setups) on this. | `.toolhead.homed_axes` |

### 2.3 multiACE bookkeeping signals

These are firmware-managed dictionaries, not physical sensors — but they're the persistent record of "what's where" that the web UI and downstream automation rely on.

| Signal | Source | Meaning |
|---|---|---|
| `head_source[h]` | multiACE Python state, persisted via `save_variables` | `{ace_index, slot, type, color, brand}` for head h. None ⇒ "head is empty per firmware." Authoritative for the GUI's "what's loaded where" answer. |
| `active_device` | multiACE state | 1-indexed (1 = ACE A, 2 = ACE B) per the get_status path. The ACE whose USB serial is currently open. |
| `device_count` | multiACE state | Number of ACE Pros enumerated at boot. |
| `swap_in_progress` | multiACE state from audit-log entries | True between SWITCH start and the next event that resets it. **Important UX caveat**: the firmware has no explicit "swap done" event for autodry-driven SWITCHes, so this can stick True after a SWITCH from MultiAcePoller (which is why the web's MultiAcePoller idle round-robin is gated off by default — see `feat/multiace-web-console`'s `MULTIACE_AUTODRY_ROUND_ROBIN` env var). |
| `print_task_config[h]` | slicer-set via `SET_PRINT_FILAMENT_CONFIG` | Per-head `{type, color, vendor}` for the current print job. Cleared between prints; not persistent. |
| Audit log line (`multiace_state.log`) | multiACE `_audit_state(action, params)` | Append-only event stream of every state-changing action. Source of truth for "what happened, when." Tailed by multiace web. |

### 2.4 Print-context signals

| Signal | Source | Meaning |
|---|---|---|
| `print_stats.state` | Klipper | `standby`, `printing`, `paused`, `complete`, `cancelled`, `error`. Gates whether a swap is safe. |
| `print_stats.current_extruder` | Klipper | Which T-index the slicer most recently selected. Useful for confirming a tool change happened. |
| `display_status.message` | Klipper | Operator-visible status message (e.g. "Loading T1…"). Currently not used by multiACE for swap progress. |
| `gcode_move.absolute_extrude` | Klipper | E mode (M82 absolute / M83 relative). Affects how `G1 E…` commands are interpreted. |

---

## 3. Audit-log actions during a swap (vocabulary)

Tail `multiace_state.log` to see these as they fire. The order tells you exactly where in the swap you are.

| Action | Emitted when | Useful payload |
|---|---|---|
| `SWITCH_TARGET` | An `ACE_LOAD_HEAD` requested an ACE different from the active one | `target_ace` |
| `SWITCH` | Active ACE actually changed via `ACE_SWITCH TARGET=N` | `target`, `autoload` |
| `SWITCH_NOOP` | `ACE_SWITCH TARGET=N` where N is already active | `target`, `reason: already_active` |
| `SWITCH_AUTO_PASSIVE` | Print-time toolchange to a head sourced from a non-active ACE | `head`, `target_ace`, `target_slot`, `action: cross_ace_disconnected` / `same_ace_noop` |
| `SWITCH_FAILED` | `ACE_SWITCH` couldn't reach target | `target`, `reason` |
| `UNLOAD_HEAD` | `ACEC__Unload_T<n>` started | `head` |
| `UNLOAD_HEAD_FAILED` | unload exception | `head`, `reason`, `error` |
| `UNLOAD_ALL` / `UNLOAD_ALL_STEP` | `ACEC__Unload_All` flow | per-head step events |
| `LOAD_HEAD_TIP_REFRESHED` | Phase 2 of load complete (tip retract + re-feed inside the slot) | `head`, `ace`, `slot`, `retract_length`, `feed_length` |
| `LOAD_HEAD_SUSPICIOUS reason=wheel_not_moving` | Existing diagnostic — wheel encoder reported < 5 ticks delta after FEED_AUTO returned OK | `wheel_delta_a`, `wheel_delta_b` |
| **`LOAD_HEAD_SUSPICIOUS reason=feed_auto_error_sensor_fallback`** (new — Tier 2) | FEED_AUTO raised exception, but `e<head>_filament=True` so we trust the sensor | `error: <FEED_AUTO message>` |
| **`LOAD_HEAD_SUSPICIOUS reason=manual_override`** (new — Tier 1) | Operator ran `ACE_MARK_HEAD_LOADED` | — |
| `LOAD_HEAD` | Load completed and `head_source[h]` populated | `head`, `ace`, `slot` |
| `LOAD_HEAD_FAILED` | FEED_AUTO raised AND `e<head>_filament=False` (real failure) | `head`, `reason: feed_auto_error`, `error` |

---

## 4. Annotated timeline — successful B1 → A1 swap

This is what you see when the swap works cleanly. Phase headers map to the audit-log entries; the right column shows the sensors you can poll to verify each phase from outside the firmware.

```
Time   Audit-log entry                                      Verifiable by external poll
─────  ───────────────────────────────────────────────────  ─────────────────────────────
T0     UNLOAD_HEAD {head=1}                                 ace.status=busy
                                                            slot.<B1>.status=unwinding
                                                            (extruder e1 retracts)

T+5s   (ACE B drive wheel pulls filament back through       e1_filament still True
        bowden B; extruder gear pulls filament out of T1)   wheel encoder counts climbing

T+30s  (filament cleared from extruder; bowden B emptying)  e1_filament transitions True→False
                                                            wheel encoder still climbing

T+60s  (filament tip retracts back to ACE B's slot gate)    slot.<B1>.status=ready
                                                            gate_status[B,1]=1  (parked at gate)
                                                            ace.status=ready

T+60s  SWITCH_TARGET {target_ace=0}                         active_device=1  (was 2)
T+62s  SWITCH {target=0, autoload=0}                        ace.status=busy briefly
                                                            (USB reconnect to ACE A)

T+72s  (ACE A active; ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1     gate_status (now A's) = [1,1,1,1]
        starts feeding)

T+85s  LOAD_HEAD_TIP_REFRESHED {h=1, a=0, s=1}              slot.<A1>.status=busy
       (phase 2: tip-refresh inside slot)

T+90s  (FEED_AUTO LOAD=1 begins phase3 retry loop)          klippy.log [feed_loading] phase3:
                                                            extrude[1] retry:N

T+90s  ─── two outcomes ───
       (a) clean breakthrough: wheel encoder ticks rise
           past min_expected after FEED_AUTO returns OK     wheel_delta_a/b ≥ 5

       (b) Tier 2 fallback: FEED_AUTO times out, but
           e1_filament=True
           LOAD_HEAD_SUSPICIOUS reason=                     audit log shows
             feed_auto_error_sensor_fallback                LOAD_HEAD_SUSPICIOUS twice:
           (the existing wheel_not_moving diagnostic        once for sensor-fallback,
            also fires)                                     once for wheel_not_moving

T+~5min  e1_filament=True confirmed in success path         e1_filament=True
         _head_source[1] = {ace_index:0, slot:1, ...}
         SET_PRINT_FILAMENT_CONFIG sent to slicer
         LOAD_HEAD {h=1, a=0, s=1}                          head_source[1] populated in
                                                            web UI (5s WS push)
```

### 4.1 Failure modes and how to recognize them from sensors

| Failure | Audit-log signature | External symptoms |
|---|---|---|
| Splitter jam (filament can't pass Y-junction) | `LOAD_HEAD_FAILED reason=feed_auto_error` after long phase3 retries | klippy.log shows `cnt_a_*` ticking but `e<n>_filament` never goes True |
| Wheel encoder broken (filament arrives, encoder doesn't see it) | `LOAD_HEAD_SUSPICIOUS reason=feed_auto_error_sensor_fallback` then `LOAD_HEAD` | klippy.log shows `cnt_a_1==cnt_a_2` for many retries; `coil_freq_delta` actively changes; `e<n>_filament=True` |
| Slot empty / no_filament error | `LOAD_HEAD_FAILED` HTTP 400 message contains `no_filament` | gate_status[n]=0 for that slot; slot.<n>.rfid may also be 0 |
| Slot drive motor stuck | Fast `LOAD_HEAD_FAILED reason=feed_auto_error` (under 90s) | wheel encoder counts NEVER advance; coil_freq stays static |
| Extruder gear can't grip (cold hotend or worn gear) | Phase3 retries climb but `extruder.position` stays at 0 | `extruder.target` = 0 OR `extruder.temperature` < min_extrude_temp |
| Bookkeeping gap (post-Tier-2 — should be rare now) | `LOAD_HEAD_FAILED` with `e<n>_filament=True` (paradoxical) | Recover via `ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S` |

---

## 5. Live observability commands

### One-shot snapshot

```bash
curl -s "http://192.168.1.171:7125/printer/objects/query?ace&filament_motion_sensor%20e0_filament&filament_motion_sensor%20e1_filament&filament_motion_sensor%20e2_filament&filament_motion_sensor%20e3_filament&extruder&print_stats&toolhead" \
  | python3 -m json.tool
```

### Tail the audit stream

```bash
curl -s "http://192.168.1.171:7125/server/files/logs/multiace_state.log" \
  | tail -30 \
  | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('20'): continue
    try:
        ts, rest = line.split(' STATE ', 1)
        d = json.loads(rest)
        params = json.dumps(d.get('params',{}))[:90]
        print(f'{ts}  {d[\"action\"]:<25}  {params}')
    except (ValueError, KeyError, json.JSONDecodeError): pass
"
```

### Watch klippy.log phase3 retries (the wheel-encoder pattern)

```bash
curl -s "http://192.168.1.171:7125/server/files/logs/klippy.log" \
  | grep "phase3" | tail -20
```

### Streaming view over WebSocket (multiace web)

Open `http://192.168.1.171/multiace/` — Dashboard slot rows + Hardware Twin reflect `head_source`/sensor state in real-time via WS broadcasts. Diag tab shows raw JSON of the full state object.

---

## 6. What the web UI surfaces vs what's hidden

| Layer | Visible in multiace web today | Hidden / requires raw query |
|---|---|---|
| Per-slot `gate_status` (active ACE) | ✓ Dashboard slot rows show "Filled" / "Empty" | Inactive ACE's gate_status — not readable until firmware switches to it |
| `head_source[h]` | ✓ Hardware Twin + Dashboard slot rows | — |
| `e<n>_filament` motion sensor | ✓ Dashboard toolhead cards ("No Filament" warning when sensor=False but head_source set) | — |
| `slot.<n>.rfid` (tag presence) | ✗ Not surfaced today | Diag tab raw JSON, or `/printer/objects/query?ace` |
| `slot.<n>.type`/`color`/`brand` | ✓ When non-empty (Anycubic-format tags) | OpenSpool tags don't populate; FilamentHub picker fills the gap |
| `wheel_delta_a/b` | ✗ Audit-log only (`LOAD_HEAD_SUSPICIOUS reason=wheel_not_moving`) | Activity tab shows the audit entry; details in raw JSON params |
| `coil_freq_delta` | ✗ klippy.log only | Diag tab → klippy.log tail |
| FEED_AUTO timeouts → fallback fires | ✓ Audit log shows new `feed_auto_error_sensor_fallback` action | — |
| ACE chamber temp + dryer state | ✓ Dryer card on Dashboard (active ACE only) | Inactive ACE's dryer until v0.82 lifts the single-USB constraint |
| Switch in progress | ✓ `swap_in_progress` flag on state (gates Load buttons during a real swap) | — |
| `current_extruder` | ✓ Toolhead cards show "Extruding" pill | — |

**Biggest observability gap**: inactive ACE's live state. With v0.81's single-USB constraint, anything inside the inactive ACE (gate_status, dryer, slot.<n>.status) is "last known" until the next switch. v0.82 lifts this.

---

## 7. Sensor checklist for the upcoming 8-color demo print

When the demo runs, watch these in real-time. Anything red = pause and diagnose before continuing:

- [ ] All 4 `e<n>_filament=True` (heads loaded) AND `head_source[n]` matches expected (ACE, slot) tuple before print start
- [ ] `print_stats.state=printing` — confirms job actually started
- [ ] On every toolchange (Tn select), watch for `SWITCH_AUTO_PASSIVE` audit entry — confirms multiACE saw the change
- [ ] If a toolchange targets a head sourced from the non-active ACE, expect `cross_ace_disconnected` action — feed_assist OFF for that head, extruder pulls filament through bowden manually. Slower but functional pre-v0.82.
- [ ] Any `LOAD_HEAD_FAILED` mid-print = problem. Pause the print, inspect, recover via ACEC unload + reload.
- [ ] Any `SWITCH_FAILED` = serious — USB hub flaked, ACE rebooted. Pause, recover, restart job.
- [ ] Cumulative `LOAD_HEAD_SUSPICIOUS` count after print: fewer is better; many = wheel encoder degrading.

See `2026-05-07-eight-color-demo-test-plan.md` for the full demo checklist.
