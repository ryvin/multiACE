# Wheel-encoder fallback for ACE_LOAD_HEAD — design spec

**Status:** approved 2026-05-07
**Branch:** `feat/wheel-encoder-fallback` off `main`
**Scope:** Two firmware changes inside `multiace/klipper/extras/ace.py`. No
web changes, no config schema bumps, no installer changes.

## One-line goal

Stop losing `head_source` bookkeeping when the ACE drive-wheel encoder is
stuck-but-filament-actually-reached-the-head.

## Background

Observed reproducibly during multiACE GUI dual-ACE testing on
`192.168.1.171` (ACE A & B, slots 0–3):

```
[feed_loading] phase3: extrude[1] retry:18, retry_extrude:0,
  coil_freq_start:1415316, coil_freq_end_min:1415275,
  coil_freq_end_max:1415344, coil_freq_delta:69
[feed_loading] phase3: wheel, cnt_a_1:1, cnt_a_2:1, cnt_b_1:1, cnt_b_2:1
```

Pattern repeats per retry until either timeout (`feed_auto_error` /
`move_extrude` / `LOAD_HEAD_FAILED`) or — sometimes — eventual breakthrough
that records `LOAD_HEAD_SUSPICIOUS` then `LOAD_HEAD`. Two signals:

- **Wheel encoder counts frozen** (`cnt_a_1 == cnt_a_2 AND cnt_b_1 == cnt_b_2`)
  for the whole retry sequence — encoder claims no motion.
- **Coil frequency delta actively changing** (`coil_freq_delta` 50–100 per
  retry) — the inductive load-detection coil senses filament moving past it,
  proving filament IS in motion.

Across the test session: every failure ended with `e<head>_filament == True`
(filament physically reached the head sensor). The wheel encoder is the only
component reporting "stuck"; it's a sensor-trust problem, not a feed problem.

## Scope (v1)

**Tier 1 — manual override gcode**

`ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S`: writes `_head_source[N] =
{ace_index, slot, type, color, brand}` from current slot metadata when the
caller asserts the head is physically loaded. Pre-flight refuses if
`e<head>_filament` reads False (won't lie about empty heads). Emits
`LOAD_HEAD_SUSPICIOUS reason=manual_override` then `LOAD_HEAD` to thread
through the existing audit pipeline.

**Tier 2 — coil_freq fallback in phase3 retry loop**

Per-attempt accumulator `cumulative_coil_delta += abs(coil_freq_delta)`. If
the wheel encoder is stuck (`cnt_a_1 == cnt_a_2 AND cnt_b_1 == cnt_b_2`) for
K consecutive retries AND `cumulative_coil_delta >= COIL_FALLBACK_THRESHOLD`,
treat as success: emit `LOAD_HEAD_SUSPICIOUS reason=
wheel_encoder_stuck_coil_fallback retries=N cumul_coil=M` then proceed to
the existing success path (which emits `LOAD_HEAD`). The existing post-feed
`e<head>_filament == True` check still gates final success.

**Initial constants** (data-driven from observed retries):
- `K = 10` consecutive-stuck-retries
- `COIL_FALLBACK_THRESHOLD = 600` (10 retries × ~60 average delta)

Both tunable as module constants near `_feed_loading`.

## Non-goals (v1)

- Web-side UX changes. multiACE web's existing `head_source` reads pick up
  new entries automatically. No new endpoints, no new buttons.
- Self-healing for already-failed loads in flight. Tier 1 is invoked
  manually; future work could auto-detect and offer.
- Hardware diagnosis / replacement. The fallback makes the firmware tolerant
  of a sensor that's becoming unreliable; physical inspection is a separate
  action the operator takes.
- Per-slot encoder calibration. We treat all slots equally — same K, same
  threshold.
- Cross-ACE differences. Same constants on ACE A and ACE B; failure pattern
  has been seen on both.

## 1. Architecture

Two additive changes in `multiace/klipper/extras/ace.py`:

**Tier 1: gcode command registration**

Register `ACE_MARK_HEAD_LOADED` next to `ACE_LOAD_HEAD` in the
`_register_commands` block. Implementation:

```python
cmd_ACE_MARK_HEAD_LOADED_help = '[multiACE] Manually record a head as loaded when filament physically reached it but firmware bookkeeping wasn\'t set (e.g., after wheel-encoder phase3 timeout). Pre-flight: e<head>_filament must be True. Usage: ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S'

def cmd_ACE_MARK_HEAD_LOADED(self, gcmd):
    head = gcmd.get_int('HEAD')
    ace_index = gcmd.get_int('ACE')
    slot = gcmd.get_int('SLOT')
    if head < 0 or head > 3:
        raise gcmd.error('[multiACE] HEAD must be 0-3')
    if ace_index < 0 or ace_index >= len(self._ace_devices):
        raise gcmd.error('[multiACE] ACE %d not configured' % ace_index)
    if slot < 0 or slot > 3:
        raise gcmd.error('[multiACE] SLOT must be 0-3')

    # Honesty gate: refuse if the head sensor disagrees with the assertion.
    sensor = self.printer.lookup_object(
        'filament_motion_sensor e%d_filament' % head, None)
    if not (sensor and sensor.get_status(0)['filament_detected']):
        raise gcmd.error(
            '[multiACE] Refusing: e%d_filament reads False — head is empty.' % head)

    if self._head_source[head]:
        raise gcmd.error(
            '[multiACE] Head %d already has bookkeeping (%s). Unload first.'
            % (head, self._head_source[head]))

    # Source slot metadata if available; fill defaults otherwise (matches
    # what cmd_ACE_LOAD_HEAD does on a normal-path success).
    slot_data = (self._info.get('slots') or [{}, {}, {}, {}])[slot] or {}
    self._head_source[head] = {
        'ace_index': ace_index,
        'slot': slot,
        'type': slot_data.get('type', ''),
        'color': self._slot_color_hex(slot_data),
        'brand': slot_data.get('brand', 'Generic'),
    }
    self._persist_head_source()

    self._audit_state('LOAD_HEAD_SUSPICIOUS', {
        'head': head, 'ace': ace_index, 'slot': slot,
        'reason': 'manual_override',
    })
    self._audit_state('LOAD_HEAD', {
        'head': head, 'ace': ace_index, 'slot': slot,
    })
    self.log_always('[multiACE] head_source[%d] manually marked as ACE %d / slot %d'
                    % (head, ace_index, slot))
```

`_persist_head_source` and `_slot_color_hex` are existing helpers in the
module; this method just wires them.

**Tier 2: coil_freq fallback in `_feed_loading` phase3**

Locate the phase3 retry loop in `_feed_loading` (the function that emits the
`[feed_loading] phase3: ...` log lines we observed). Modify to track
cumulative coil delta and stuck-retries, and short-circuit to success when
both thresholds met:

```python
# Module-level constants near _feed_loading definition
COIL_FALLBACK_K = 10            # consecutive stuck retries before fallback
COIL_FALLBACK_THRESHOLD = 600   # cumulative |coil_freq_delta| (Hz·retries)

# Inside _feed_loading phase3 (pseudo-diff):
def _feed_loading(self, head, ...):
    ...
    cumulative_coil_delta = 0
    consecutive_wheel_stuck = 0
    for retry in range(MAX_RETRIES):
        ...
        # existing per-retry feed/sense logic
        wheel_moved = (cnt_a_1 != cnt_a_2) or (cnt_b_1 != cnt_b_2)
        coil_delta = abs(coil_freq_end - coil_freq_start)
        cumulative_coil_delta += coil_delta
        if wheel_moved:
            consecutive_wheel_stuck = 0
            # existing success path
        else:
            consecutive_wheel_stuck += 1
            if (consecutive_wheel_stuck >= COIL_FALLBACK_K
                    and cumulative_coil_delta >= COIL_FALLBACK_THRESHOLD):
                self._audit_state('LOAD_HEAD_SUSPICIOUS', {
                    'head': head,
                    'reason': 'wheel_encoder_stuck_coil_fallback',
                    'retries': retry + 1,
                    'cumul_coil': cumulative_coil_delta,
                })
                self.log_always(
                    '[multiACE] phase3 coil-fallback: wheel stuck %d retries, '
                    'cumul_coil=%d, declaring success'
                    % (consecutive_wheel_stuck, cumulative_coil_delta))
                return True   # success — fall through to caller's e<head> sensor check
    # existing timeout / failure path unchanged
```

The function name `_feed_loading` may differ slightly in the actual file —
implementer reads the source and matches the existing structure.

The post-feed sensor check (`e<head>_filament == True` already required for
real success in the existing path) acts as the safety net: even if the
fallback fires erroneously, an empty head won't get bookkeeping.

**Persistence**: `_head_source` already persisted via Klipper's
`save_variables` (line ~1421 of existing ace.py). Both tiers reuse the
existing path; no new persistence concerns.

## 2. Components

**`multiace/klipper/extras/ace.py`** (~80 lines added)

- New `cmd_ACE_MARK_HEAD_LOADED` method (~50 lines including pre-flight)
- Registration in `_register_commands` (1 line)
- New module constants `COIL_FALLBACK_K`, `COIL_FALLBACK_THRESHOLD` (2 lines)
- Modifications inside `_feed_loading` phase3 retry loop (~20 lines)

**`multiace/config/extended/ace.cfg`** (optional convenience macro)

```cfg
[gcode_macro ACEC__Mark_Loaded_T0]
description: Manually mark T0 as loaded (recovery from phase3 timeouts). Usage: pass ACE and SLOT params.
gcode:
  ACE_MARK_HEAD_LOADED HEAD=0 ACE={params.ACE|default(0)} SLOT={params.SLOT|default(0)}

# Repeat for T1/T2/T3
```

Optional — group F or new group H. The raw gcode is the public contract;
macros are convenience wrappers like `ACEC__Load_T0`.

**Documentation**

- Update `multiace/README.md` "Known Limitations" section: note wheel-encoder
  fallback now exists, and how to use `ACE_MARK_HEAD_LOADED` for legacy
  recovery.
- Update repo-root `CLAUDE.md` if there's a relevant section (audit-log
  actions list).

## 3. Data flow

**Tier 1 manual override**:

```
[user observes phase3 timeout, e<head>_filament=True, head_source=None]
        │
        │ user runs (web or Klipper terminal):
        │   ACE_MARK_HEAD_LOADED HEAD=1 ACE=0 SLOT=1
        ▼
cmd_ACE_MARK_HEAD_LOADED:
  - validate args (head/ace/slot in range)
  - check e1_filament == True   (refuse if False)
  - check head_source[1] is None (refuse if already set)
  - read slot 1 metadata from self._info['slots']
  - write _head_source[1] = {ace_index:0, slot:1, type, color, brand}
  - persist via save_variables
  - audit: LOAD_HEAD_SUSPICIOUS reason=manual_override
  - audit: LOAD_HEAD
        │
        ▼
multiace_state.log gets two entries; multiace web tailer picks them up;
state.head_source[1] updates; UI slot row + Hardware Twin reflect.
```

**Tier 2 phase3 coil fallback**:

```
[ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1 invoked]
        │
        ▼
phase 1 (lift_filament) ✓
phase 2 (tip_refresh) ✓ → LOAD_HEAD_TIP_REFRESHED audit
        │
        ▼
phase 3 retry loop:
  retry 0..9:  wheel_moved=False, coil_delta=70   → cumul=700, stuck_count=10
  retry 10:    wheel_moved=False                   ← K threshold reached
               cumul (700) >= 600                  ← coil threshold reached
               → audit: LOAD_HEAD_SUSPICIOUS reason=wheel_encoder_stuck_coil_fallback retries=11 cumul_coil=700
               → return True (declared success)
        │
        ▼
existing post-phase-3 sensor check: e<head>_filament == True ✓
  (if False: fail anyway — fallback can only succeed when filament arrived)
        │
        ▼
existing success path:
  - write _head_source[head] = {...}
  - persist
  - audit: LOAD_HEAD
```

The existing breakthrough behavior (where wheel encoder eventually unsticks
before retry 10) is unchanged — Tier 2 only fires when the encoder stays
stuck through the full window.

## 4. Error handling

**Tier 1**:

- Invalid HEAD/ACE/SLOT range → `gcmd.error` (caller sees Klipper error,
  no audit entry).
- `e<head>_filament == False` → `gcmd.error` with explanatory message.
  Refuses the operation; user must verify physical state first. No audit.
- `head_source[head]` already populated → `gcmd.error` "unload first." No
  audit (avoids polluting the log with rejected attempts).
- `slot` metadata missing or partial → fall back to defaults
  (type='', color='000000', brand='Generic'). Same defaults the existing
  `cmd_ACE_LOAD_HEAD` uses on metadata-empty success.
- `save_variables` write failure → log error, continue. The in-memory state
  is correct; persistence will retry on next state update.

**Tier 2**:

- `coil_freq_start` or `coil_freq_end` missing/garbage from ACE response →
  per-retry exception is caught by existing retry handler; this retry's
  `coil_delta` treated as 0 (doesn't contribute to cumulative). No special
  handling needed.
- Wheel actually starts moving mid-window → `consecutive_wheel_stuck` resets
  to 0 (existing logic). Fallback never fires; normal success path used.
  No regression.
- `e<head>_filament == False` after fallback declares success → existing
  post-phase-3 check fails the load anyway. The fallback is "phase3 is done
  — defer to sensor"; if sensor disagrees, sensor wins.
- Encoder permanently dead (always reads same value) on a slot with empty
  filament → fallback would NOT fire, because cumulative_coil_delta stays
  near zero (no filament = no coil-frequency change). The two thresholds
  together prevent false-success on empty slots.
- Constants need tuning post-deploy → both are module-level, edit + Klipper
  restart. No structural change.

## 5. Testing

Per CLAUDE.md, the firmware side has no automated test harness. Validation
is manual on hardware.

**Pre-deploy desk check**:

- Read the modified `_feed_loading` against the existing structure; verify
  the success-path return matches what the caller expects (boolean? struct?)
- `python -m py_compile multiace/klipper/extras/ace.py` to catch syntax
  errors before SCP'ing to printer.

**On-printer manual validation** (per CLAUDE.md safety: only when
`print_stats.state` is `standby` / `complete` / `cancelled` / `error`):

1. **Tier 1 happy path**: induce a phase3-timeout failure (load from a slot
   with the encoder issue we've been seeing). When `head_source` ends up
   None but `e<head>_filament=True`, run
   `ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S`. Verify
   `head_source[N]` populates correctly via `/printer/objects/query?ace`.
   Verify `multiace_state.log` shows two new entries (SUSPICIOUS +
   LOAD_HEAD).

2. **Tier 1 honesty gate**: with `e0_filament=False` (head empty), run
   `ACE_MARK_HEAD_LOADED HEAD=0 ACE=0 SLOT=0`. Should refuse with
   sensor-False error; head_source unchanged.

3. **Tier 1 collision**: with `head_source[0]` already populated, run
   `ACE_MARK_HEAD_LOADED HEAD=0 ACE=1 SLOT=0`. Should refuse with
   "unload first" error; head_source unchanged.

4. **Tier 2 fallback fires**: load any slot that we've seen exhibit the
   wheel-encoder pattern. Watch klippy.log: should see `LOAD_HEAD_SUSPICIOUS
   reason=wheel_encoder_stuck_coil_fallback retries=N cumul_coil=M` followed
   by `LOAD_HEAD`. multiace web should show the slot bound correctly within
   the next state-update cycle.

5. **Tier 2 doesn't fire on encoder-OK loads**: load a slot where the wheel
   encoder works normally (T0 has historically been clean on first attempt
   for this rig). Should see plain `LOAD_HEAD` audit only — no
   wheel_encoder_stuck_coil_fallback action.

6. **Tier 2 doesn't fire on empty slots**: simulate a slot-empty load (eject
   filament, set gate_status=0, attempt load). Should still fail with
   existing slot_empty error, not fabricate a fallback success. Encoder
   would be stuck (no filament moving) but coil_delta would also be near
   zero, so cumulative threshold isn't met.

7. **Constants tuning**: after one week of mixed use, sample
   `multiace_state.log` for `wheel_encoder_stuck_coil_fallback` events.
   If false positives observed (head sensor disagrees), tighten K or
   threshold. If real failures still slip through (cumul_coil too high),
   loosen.

## 6. Migration

**No data migration**. `_head_source` schema unchanged.

**Deploy steps**:

```bash
# Local edits committed to feat/wheel-encoder-fallback branch.
# SCP changed file to printer:
scp multiace/klipper/extras/ace.py lava@192.168.1.171:/home/lava/klipper/klippy/extras/ace.py
# Verify printer is in safe state first:
curl -s http://192.168.1.171:7125/printer/objects/query?print_stats \
  | jq -r '.result.status.print_stats.state'
# Only restart Klipper if state is standby/complete/cancelled/error:
ssh lava@192.168.1.171 'systemctl restart klipper'
# Watch klippy.log + multiace_state.log for first load to confirm new
# audit entries shape correctly.
```

Rollback: revert ace.py to previous version, restart Klipper. No persisted
state references the new audit reasons (multiace web treats unknown action
strings tolerantly).

## 7. Out-of-scope follow-ups

- **Web-side recovery affordance**: when multiace web detects
  `e<head>_filament=True && head_source[h]==null`, offer a one-click
  "Mark loaded" button that calls `ACE_MARK_HEAD_LOADED` for the most-recent
  attempted (ACE, slot) pair (read from `last_action_at` STATE entries).
  Out of scope for this firmware-only spec; new spec on the multiace web
  side.
- **Per-slot encoder health metric**: track per-(ace,slot) cumulative
  coil-fallback firings; surface a "slot N encoder degrading" hint on
  Diag tab when count exceeds threshold.
- **Auto-tuning**: machine-learn K and COIL_FALLBACK_THRESHOLD per slot from
  observed history.
- **Hardware repair workflow**: if a slot's encoder is permanently dead,
  the operator may want to mark that slot as "force fallback always."

## Open questions

None.
