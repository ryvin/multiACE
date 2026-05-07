# Wheel-encoder fallback — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop losing `head_source` bookkeeping when the ACE drive-wheel encoder is stuck-but-filament-actually-reached-the-head.

**Architecture:** Two changes inside `multiace/klipper/extras/ace.py` only. Tier 1 adds `ACE_MARK_HEAD_LOADED` for manual recovery of already-failed loads. Tier 2 catches the `feed_auto_error` exception in `cmd_ACE_LOAD_HEAD` and, if `e<head>_filament == True` (filament physically reached the head), treats the load as successful via the existing SUSPICIOUS+LOAD_HEAD pattern. No changes to the underlying `filament_feed.py` retry loop, no web changes, no installer changes.

**Tech Stack:** Python (Klipper extras module), Klipper gcode framework, `save_variables` for persistence, manual on-printer validation per CLAUDE.md (no automated test harness for firmware).

**Spec:** `docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md`

**Spec deviation acknowledged:** The spec describes coil_freq tracking inside `_feed_loading`'s phase3 retry loop. That loop lives in a separate `filament_feed.py` module (the `FEED_AUTO` Klipper command), not in `ace.py`. Modifying that loop crosses module boundaries. The equivalent user-facing semantic — "filament reached head despite encoder timeout, record the load" — is achieved at `ace.py`'s existing exception handler with a sensor-check fallback. The spec's architectural anchor (post-feed sensor check) is the same; the mechanism is simpler. Audit-log shape (LOAD_HEAD_SUSPICIOUS + LOAD_HEAD) is preserved per the spec's approved decision.

---

## File structure

| File | Status | Purpose |
|---|---|---|
| `multiace/klipper/extras/ace.py` | modify | Tier 1 + Tier 2 — single file, additive changes |
| `multiace/config/extended/ace.cfg` | modify | Optional `ACEC__Mark_Loaded_T<n>` convenience macros (group H) |
| `multiace/README.md` | modify | Document the new `ACE_MARK_HEAD_LOADED` command + the silent-fallback behavior |
| `CLAUDE.md` (repo root) | modify | Add the two new audit-log actions and reasons under firmware section |

No new tests files. Per `CLAUDE.md`: "There are no automated tests, CI, linters, or build steps for `multiace/`. Validation is manual on hardware." Each task that adds code has a manual smoke step on the live printer at `192.168.1.171`, gated on the existing safety check (`print_stats.state` must be `standby` / `complete` / `cancelled` / `error`).

---

## Task 1: Tier 1 — `ACE_MARK_HEAD_LOADED` command

**Files:**
- Modify: `multiace/klipper/extras/ace.py:248-278` (add registration), and add the new `cmd_ACE_MARK_HEAD_LOADED` method near `cmd_ACE_LOAD_HEAD` (~line 1599)

- [ ] **Step 1: Add gcode command registration**

In `_register_commands` (~line 248), after the existing `ACE_LOAD_HEAD` registration block, add:

```python
        self.gcode.register_command(
            'ACE_MARK_HEAD_LOADED', self.cmd_ACE_MARK_HEAD_LOADED,
            desc=self.cmd_ACE_MARK_HEAD_LOADED_help)
```

- [ ] **Step 2: Add the command method body**

Insert near the existing `cmd_ACE_LOAD_HEAD` (around line 1599 of `ace.py`):

```python
    cmd_ACE_MARK_HEAD_LOADED_help = (
        "[multiACE] Manually record a head as loaded when filament physically "
        "reached it but firmware bookkeeping wasn't set (e.g., after wheel-"
        "encoder phase3 timeout). Pre-flight: e<head>_filament must read True. "
        "Usage: ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S"
    )

    def cmd_ACE_MARK_HEAD_LOADED(self, gcmd):
        head = gcmd.get_int('HEAD')
        ace_index = gcmd.get_int('ACE')
        slot = gcmd.get_int('SLOT')
        if head < 0 or head > 3:
            raise gcmd.error('[multiACE] HEAD must be 0-3')
        if ace_index < 0 or ace_index >= len(self._ace_devices):
            raise gcmd.error(
                '[multiACE] ACE %d not configured (have %d devices)'
                % (ace_index, len(self._ace_devices)))
        if slot < 0 or slot > 3:
            raise gcmd.error('[multiACE] SLOT must be 0-3')

        # Honesty gate: refuse if the head sensor disagrees with the assertion.
        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
        if not (sensor and sensor.get_status(0)['filament_detected']):
            raise gcmd.error(
                '[multiACE] Refusing: e%d_filament reads False — head is empty.'
                % head)

        # Idempotency / collision check: if bookkeeping already set, the user
        # should unload first so they don't lose track of what's actually there.
        if self._head_source[head]:
            raise gcmd.error(
                '[multiACE] Head %d already has bookkeeping (%s). '
                'Unload first.' % (head, self._head_source[head]))

        # Source slot metadata if available; fall back to the same defaults
        # the normal cmd_ACE_LOAD_HEAD success path uses (lines 1683-1690).
        slot_info = self._info['slots'][slot] if slot < len(self._info.get('slots') or []) else {}
        self._head_source[head] = {
            'ace_index': ace_index,
            'slot': slot,
            'type': slot_info.get('type', 'PLA') or 'PLA',
            'color': self.rgb2hex(*slot_info.get('color', (0, 0, 0))),
            'brand': slot_info.get('brand', 'Generic') or 'Generic',
        }
        self._save_head_source()

        # Same SET_PRINT_FILAMENT_CONFIG call the normal path makes, so the
        # slicer / autodry / web all see consistent type metadata.
        self.gcode.run_script_from_command(
            'SET_PRINT_FILAMENT_CONFIG '
            'CONFIG_EXTRUDER=%d '
            'FILAMENT_TYPE="%s" '
            'FILAMENT_COLOR_RGBA=%s '
            'VENDOR="%s" '
            'FILAMENT_SUBTYPE=""' % (
                head,
                self._head_source[head]['type'],
                self._head_source[head]['color'],
                self._head_source[head]['brand']))

        # Two-line audit per spec: SUSPICIOUS first (with reason), then LOAD_HEAD.
        # multiace web's tailer treats LOAD_HEAD as the "head bound" event.
        self._audit_state('LOAD_HEAD_SUSPICIOUS', {
            'head': head, 'ace': ace_index, 'slot': slot,
            'reason': 'manual_override',
        })
        self._audit_state('LOAD_HEAD', {
            'head': head, 'ace': ace_index, 'slot': slot,
        })
        self.log_always(
            '[multiACE] head_source[%d] manually marked as ACE %d / slot %d '
            '(via ACE_MARK_HEAD_LOADED)' % (head, ace_index, slot))
```

- [ ] **Step 3: Syntax check + commit**

```bash
python3 -m py_compile multiace/klipper/extras/ace.py
git add multiace/klipper/extras/ace.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(firmware): ACE_MARK_HEAD_LOADED — manual override for phase3 timeouts

When the FEED_AUTO phase3 retry loop times out (feed_auto_error /
move_extrude / etc.) but filament physically reached the head, the
firmware leaves head_source[N] empty — a recurring pain on this rig.

ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S writes head_source from current
slot metadata, persists, and emits the two-line LOAD_HEAD_SUSPICIOUS +
LOAD_HEAD audit pattern so multiace web and downstream consumers see
the binding. Pre-flight gates:
  - e<head>_filament must read True (no lying about empty heads)
  - head_source[head] must be empty (must unload first to overwrite)

Tier 1 of the wheel-encoder fallback spec; Tier 2 (silent fallback in
cmd_ACE_LOAD_HEAD) follows in the next commit.

Spec: docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md
"
```

- [ ] **Step 4: Live smoke — happy path**

Pre-flight: confirm safe state.

```bash
curl -s http://192.168.1.171:7125/printer/objects/query?print_stats \
  | jq -r '.result.status.print_stats.state'
```

Expected: one of `standby`, `complete`, `cancelled`, `error`. If not, abort.

Deploy via the existing pexpect-based path that's been used in this project (see prior conversation; `MULTIACE_DEPLOY_PASS=snapmaker`):

```bash
scp multiace/klipper/extras/ace.py root@192.168.1.171:/home/lava/klipper/klippy/extras/ace.py
ssh root@192.168.1.171 'systemctl restart klipper'
# Wait for Klipper to come back up:
until curl -s --max-time 4 "http://192.168.1.171:7125/printer/info" | grep -q '"state":"ready"'; do sleep 2; done && echo "klipper ready"
```

Find a head where `e<head>_filament=True` but `head_source[head]=null` (the recurring T1 case in this conversation). Then:

```bash
# Replace HEAD/ACE/SLOT with the actual values you want to assert.
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACE_MARK_HEAD_LOADED HEAD=1 ACE=0 SLOT=1"}' \
  | python3 -m json.tool
```

Expected response: `{"result": "ok"}`. Then verify state:

```bash
curl -s "http://192.168.1.171:7125/printer/objects/query?ace" \
  | python3 -c "
import sys, json
hs = json.load(sys.stdin)['result']['status']['ace']['head_source']
print(f\"T1: {hs.get('1')}\")"
```

Expected: `T1: {'ace_index': 0, 'slot': 1, 'type': ..., 'color': ..., 'brand': ...}`.

Verify the audit log entries landed:

```bash
curl -s "http://192.168.1.171:7125/server/files/logs/multiace_state.log" \
  | tail -2
```

Expected last two entries: `LOAD_HEAD_SUSPICIOUS` with `"reason": "manual_override"`, then `LOAD_HEAD`.

- [ ] **Step 5: Live smoke — honesty gate (sensor=False)**

Find a head where `e<head>_filament=False` (head physically empty). Then:

```bash
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACE_MARK_HEAD_LOADED HEAD=2 ACE=0 SLOT=2"}'
```

Expected: HTTP 400 from Moonraker with the `Refusing: e2_filament reads False — head is empty` message. Verify `head_source[2]` is unchanged via the same `/api/state` query.

- [ ] **Step 6: Live smoke — collision gate (head already populated)**

Pick a head with `head_source` already set (e.g., T0=ACE A slot 0). Then:

```bash
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACE_MARK_HEAD_LOADED HEAD=0 ACE=1 SLOT=0"}'
```

Expected: HTTP 400 with `Head 0 already has bookkeeping (...). Unload first.` Verify `head_source[0]` unchanged.

---

## Task 2: Tier 2 — sensor fallback on `feed_auto_error`

**Files:**
- Modify: `multiace/klipper/extras/ace.py:1649-1707` (the `try / except feed_auto_error` block at the start of `cmd_ACE_LOAD_HEAD`'s feed sequence, plus the falls-through-to-success body)

- [ ] **Step 1: Identify the precise existing structure**

Open `multiace/klipper/extras/ace.py` and locate (roughly line 1649):

```python
        try:
            self.gcode.run_script_from_command(
                "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d LOAD=1"
                % (module, channel, head))
        except Exception as e:
            self._audit_state('LOAD_HEAD_FAILED', {'head': head, 'ace': ace_index, 'slot': slot, 'reason': 'feed_auto_error', 'error': str(e)})
            raise
```

The change in step 2 wraps that `except` so it falls through to success when the head sensor reads True after the timeout — instead of unconditionally re-raising.

- [ ] **Step 2: Replace the except block**

Replace the block above with:

```python
        try:
            self.gcode.run_script_from_command(
                "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d LOAD=1"
                % (module, channel, head))
        except Exception as e:
            # FEED_AUTO timed out (wheel-encoder phase3 didn't observe enough
            # motion). On this rig that recurringly happens when filament
            # *did* reach the head — the encoder is the only thing that didn't
            # see it. If the head's filament-motion sensor reads True now,
            # trust it: emit LOAD_HEAD_SUSPICIOUS + fall through to the
            # normal success path (head_source set, SET_PRINT_FILAMENT_CONFIG,
            # LOAD_HEAD audit). If sensor reads False, fail as before.
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            sensor_ok = (sensor
                         and sensor.get_status(0)['filament_detected'])
            if not sensor_ok:
                self._audit_state('LOAD_HEAD_FAILED', {
                    'head': head, 'ace': ace_index, 'slot': slot,
                    'reason': 'feed_auto_error', 'error': str(e),
                })
                raise
            self._audit_state('LOAD_HEAD_SUSPICIOUS', {
                'head': head, 'ace': ace_index, 'slot': slot,
                'reason': 'feed_auto_error_sensor_fallback',
                'error': str(e),
            })
            self.log_always(
                '[multiACE] FEED_AUTO timed out for head %d but '
                'e%d_filament=True; trusting sensor and recording load.'
                % (head, head))
            # Fall through to the existing success path.
```

The existing code below (`wheel_after = ...`, `wheel_delta = ...`, the second `LOAD_HEAD_SUSPICIOUS` for `wheel_not_moving`, the RFID wait, the `_head_source[head] = {...}` assignment, `_save_head_source`, `SET_PRINT_FILAMENT_CONFIG`, the final `LOAD_HEAD` audit) **is unchanged** and runs after the new branch. That gives us identical bookkeeping to a "real" success.

Important caveat: when fallback fires, `wheel_after` is still computed and `wheel_delta` may still flag `wheel_not_moving` and emit a *second* `LOAD_HEAD_SUSPICIOUS` (with a different reason). That's correct: both signals are real diagnostics. The audit log will show:

```
LOAD_HEAD_SUSPICIOUS  reason=feed_auto_error_sensor_fallback
LOAD_HEAD_SUSPICIOUS  reason=wheel_not_moving         (existing line 1669 logic)
LOAD_HEAD                                              (existing line 1707)
```

- [ ] **Step 3: Syntax check + commit**

```bash
python3 -m py_compile multiace/klipper/extras/ace.py
git add multiace/klipper/extras/ace.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(firmware): cmd_ACE_LOAD_HEAD — sensor fallback on feed_auto_error

When FEED_AUTO times out (wheel-encoder phase3 didn't see enough
motion to declare success), check the head filament-motion sensor
before failing. If e<head>_filament=True, the filament physically
reached the head — only the encoder claims it didn't move. Trust the
sensor: emit LOAD_HEAD_SUSPICIOUS reason=feed_auto_error_sensor_fallback,
then fall through to the existing success path (head_source set,
SET_PRINT_FILAMENT_CONFIG, LOAD_HEAD audit).

If sensor reads False (filament really didn't arrive), the load fails
exactly as before with LOAD_HEAD_FAILED.

Tier 2 of the wheel-encoder fallback spec. Tier 1 (manual recovery via
ACE_MARK_HEAD_LOADED) shipped in the previous commit.

Spec: docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md
"
```

- [ ] **Step 4: Live deploy**

Per the same flow as Task 1 step 4:

```bash
scp multiace/klipper/extras/ace.py root@192.168.1.171:/home/lava/klipper/klippy/extras/ace.py
# Pre-flight safe state check first:
curl -s http://192.168.1.171:7125/printer/objects/query?print_stats \
  | jq -r '.result.status.print_stats.state'
ssh root@192.168.1.171 'systemctl restart klipper'
until curl -s --max-time 4 "http://192.168.1.171:7125/printer/info" | grep -q '"state":"ready"'; do sleep 2; done && echo "klipper ready"
```

- [ ] **Step 5: Live smoke — fallback fires on a phase3-timeout-prone slot**

This is the test that proves the fix. T1 has been hitting `feed_auto_error` reliably during this conversation. Pick a slot known to exhibit the pattern (T1 with ACE A or B slot 1 has been the testbed). First unload anything currently on T1:

```bash
curl -s --max-time 600 -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACEC__Unload_T1"}'
```

Wait for completion (poll `/api/state` until `head_source[1]=null` and `e1_filament=false`), then attempt the load that has historically failed:

```bash
curl -s --max-time 1200 -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1"}' \
  -w "\nhttp_code=%{http_code}\n"
```

Two acceptable outcomes:
1. **Clean breakthrough** (encoder eventually unsticks) → HTTP 200, audit shows just `LOAD_HEAD_TIP_REFRESHED` then `LOAD_HEAD`. No fallback needed.
2. **Fallback fires** (encoder times out, sensor=True) → HTTP 200 (this used to be a 400!), audit shows `LOAD_HEAD_TIP_REFRESHED`, then `LOAD_HEAD_SUSPICIOUS reason=feed_auto_error_sensor_fallback`, possibly also `LOAD_HEAD_SUSPICIOUS reason=wheel_not_moving`, then `LOAD_HEAD`.

Verify with:

```bash
curl -s "http://192.168.1.171:7125/server/files/logs/multiace_state.log" | tail -10
curl -s "http://192.168.1.171:7125/printer/objects/query?ace" | python3 -c "
import sys, json
hs = json.load(sys.stdin)['result']['status']['ace']['head_source']
print(f\"T1: {hs.get('1')}\")"
```

Expected: `T1: {'ace_index': 0, 'slot': 1, ...}` populated correctly. The exact failure mode we've been hitting all session is now silently corrected.

- [ ] **Step 6: Live smoke — sensor-False still fails**

To prove the safety net: simulate an empty-head load. With T1 unloaded (`e1_filament=False`), call:

```bash
# Pick a slot known to be empty in firmware state. If all slots are filled,
# this step is impractical to reproduce on a working rig — skip with a note.
# Otherwise, attempt a load and confirm LOAD_HEAD_FAILED still fires.
curl -s --max-time 600 -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=2"}' \
  -w "\nhttp_code=%{http_code}\n"
```

Expected: if FEED_AUTO times out AND sensor reads False (filament did not arrive), the audit shows `LOAD_HEAD_FAILED reason=feed_auto_error` exactly as before. `head_source[1]` stays null. The failure path is preserved when it should fire.

If on this rig every load eventually succeeds (encoder does unstick or filament does arrive), this step is documented as "could not exercise; reasoning preserves the behavior by code inspection." Note in commit message of step 7 below if so.

- [ ] **Step 7: If fallback semantics need adjustment, iterate**

If during smoke testing the sensor-fallback fires when it shouldn't (e.g., sensor reads stale True from a previous load that wasn't fully cleared), tighten the gate. The current gate is `sensor.get_status(0)['filament_detected']` which is the most direct signal the firmware exposes; alternatives include cross-checking `wheel_delta` is non-zero (i.e., SOMETHING moved). For now leave as written; revisit only if false-positives observed.

---

## Task 3: Convenience macros in `ace.cfg`

**Files:**
- Modify: `multiace/config/extended/ace.cfg` (append to end, before the next section)

- [ ] **Step 1: Add convenience macros**

Append to `multiace/config/extended/ace.cfg` (after the existing G group, in a new H group block):

```cfg
# ============================================================
# H — Recovery / manual overrides
# ============================================================

[gcode_macro ACEC__Mark_Loaded_T0]
description: Manually record T0 as loaded after a phase3 timeout. Usage: ACEC__Mark_Loaded_T0 ACE=N SLOT=S (defaults: ACE=0 SLOT=0)
gcode:
  ACE_MARK_HEAD_LOADED HEAD=0 ACE={params.ACE|default(0)} SLOT={params.SLOT|default(0)}

[gcode_macro ACEC__Mark_Loaded_T1]
description: Manually record T1 as loaded after a phase3 timeout. Usage: ACEC__Mark_Loaded_T1 ACE=N SLOT=S (defaults: ACE=0 SLOT=1)
gcode:
  ACE_MARK_HEAD_LOADED HEAD=1 ACE={params.ACE|default(0)} SLOT={params.SLOT|default(1)}

[gcode_macro ACEC__Mark_Loaded_T2]
description: Manually record T2 as loaded after a phase3 timeout. Usage: ACEC__Mark_Loaded_T2 ACE=N SLOT=S (defaults: ACE=0 SLOT=2)
gcode:
  ACE_MARK_HEAD_LOADED HEAD=2 ACE={params.ACE|default(0)} SLOT={params.SLOT|default(2)}

[gcode_macro ACEC__Mark_Loaded_T3]
description: Manually record T3 as loaded after a phase3 timeout. Usage: ACEC__Mark_Loaded_T3 ACE=N SLOT=S (defaults: ACE=0 SLOT=3)
gcode:
  ACE_MARK_HEAD_LOADED HEAD=3 ACE={params.ACE|default(0)} SLOT={params.SLOT|default(3)}
```

The defaults match each head's natural slot index, so a typical call looks like `ACEC__Mark_Loaded_T1 ACE=0` (slot defaults to 1). Operators can override either or both via params.

- [ ] **Step 2: Deploy + verify**

```bash
scp multiace/config/extended/ace.cfg lava@192.168.1.171:/home/lava/printer_data/config/extended/ace.cfg
# Pre-flight safe state check:
curl -s http://192.168.1.171:7125/printer/objects/query?print_stats \
  | jq -r '.result.status.print_stats.state'
# Klipper config-only changes need a soft restart, not a service restart:
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" --data '{"script":"RESTART"}'
# Wait for ready:
until curl -s --max-time 4 "http://192.168.1.171:7125/printer/info" | grep -q '"state":"ready"'; do sleep 2; done
```

Verify the macros are registered:

```bash
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" --data '{"script":"HELP ACEC__Mark_Loaded_T0"}'
```

Expected response includes the description text from step 1.

- [ ] **Step 3: Commit**

```bash
git add multiace/config/extended/ace.cfg
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(firmware): ACEC__Mark_Loaded_T<n> convenience macros

Group H (Recovery / manual overrides). Each macro wraps
ACE_MARK_HEAD_LOADED HEAD=<n> with sensible per-head defaults so
operators can recover from a phase3-timeout bookkeeping gap with one
gcode call: e.g. ACEC__Mark_Loaded_T1 ACE=0 (slot defaults to 1).

Spec: docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md
"
```

---

## Task 4: README updates

**Files:**
- Modify: `multiace/README.md` (Known Limitations section)
- Modify: `CLAUDE.md` (repo root, audit-log actions)

- [ ] **Step 1: Update `multiace/README.md`**

Find the "Known Limitations" section and add a new entry. Read the file first to find the exact location:

```bash
grep -n "Known Limitations\|Audit logging\|## " multiace/README.md | head -30
```

Add this paragraph in the Known Limitations section:

```markdown
### Wheel-encoder phase3 timeouts (mitigated)

The Anycubic ACE Pro drive-wheel encoder occasionally fails to register
filament motion during the FEED_AUTO phase3 retry loop, even when filament
is physically reaching the head. Symptoms: `feed_auto_error` /
`move_extrude` exception from FEED_AUTO; klippy.log shows `cnt_a_1 ==
cnt_a_2` for many consecutive retries while `coil_freq_delta` actively
changes. As of v0.82+:

- **Tier 2 (automatic):** if FEED_AUTO times out but `e<head>_filament`
  reads True, the firmware trusts the sensor and records the load with
  audit reason `feed_auto_error_sensor_fallback`. No user intervention
  needed; `LOAD_HEAD` still fires for downstream consumers.

- **Tier 1 (manual):** for already-failed loads (where the sensor was
  True but bookkeeping wasn't recorded — pre-Tier-2 history), use
  `ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S` to write `head_source[N]`
  manually. Refuses if `e<head>_filament=False` (won't fabricate empty-
  head loads) or if `head_source[N]` is already populated (unload first
  to overwrite).

  Convenience macros: `ACEC__Mark_Loaded_T0` through `_T3` (group H).
```

- [ ] **Step 2: Update repo-root `CLAUDE.md`**

Find the audit-log / firmware section in the repo-root `CLAUDE.md`:

```bash
grep -n "audit\|LOAD_HEAD\|## " CLAUDE.md | head -20
```

If there's an audit-actions list, add three new entries with brief explanations:

- `LOAD_HEAD_SUSPICIOUS` reason `feed_auto_error_sensor_fallback` — FEED_AUTO timed out, but e<head>_filament=True; firmware trusts sensor, records load.
- `LOAD_HEAD_SUSPICIOUS` reason `manual_override` — operator ran `ACE_MARK_HEAD_LOADED` to record a load that bypassed the normal feed path.
- `LOAD_HEAD_SUSPICIOUS` reason `wheel_not_moving` (existing — unchanged) — wheel_delta below min_expected after FEED_AUTO returned OK.

If `CLAUDE.md` doesn't have an explicit audit list, skip this step (the spec doc captures it).

- [ ] **Step 3: Commit**

```bash
git add multiace/README.md CLAUDE.md 2>/dev/null
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "docs: wheel-encoder fallback in README + audit-log actions in CLAUDE.md

Documents both Tiers of the wheel-encoder fallback in the multiACE
README's Known Limitations section, and (if applicable) the new
LOAD_HEAD_SUSPICIOUS reasons in the repo-root CLAUDE.md.

Spec: docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md
"
```

---

## Task 5: Final integration smoke + push

**Files:**
- None — verification only

- [ ] **Step 1: End-to-end check on all four heads**

After all three preceding code commits are deployed, run a small unload+load cycle on each head to confirm:
1. Tier 1 — manual override works for already-broken bookkeeping.
2. Tier 2 — fallback fires when needed, doesn't fire when not.
3. Existing clean loads still produce just `LOAD_HEAD_TIP_REFRESHED` + `LOAD_HEAD` (no spurious SUSPICIOUS entries).

The current rig has T1's bookkeeping empty + sensor=True from earlier in the conversation — perfect Tier 1 test fixture. Run:

```bash
# Tier 1 — recover T1's bookkeeping
curl -s -X POST "http://192.168.1.171:7125/printer/gcode/script" \
  -H "Content-Type: application/json" \
  --data '{"script":"ACEC__Mark_Loaded_T1 ACE=0"}'
curl -s "http://192.168.1.171:7125/printer/objects/query?ace" \
  | python3 -c "
import sys, json
hs = json.load(sys.stdin)['result']['status']['ace']['head_source']
print('T1:', hs.get('1'))"
```

Expected: T1 populated as ACE A slot 1.

- [ ] **Step 2: Tail audit log to confirm event shapes**

```bash
curl -s "http://192.168.1.171:7125/server/files/logs/multiace_state.log" | tail -20
```

Confirm the new `feed_auto_error_sensor_fallback` and `manual_override` reasons appear correctly with the documented payload shape (head, ace, slot present; reason string exact).

- [ ] **Step 3: Push to ryvin**

```bash
git push origin feat/wheel-encoder-fallback
```

If the branch doesn't yet exist on origin:

```bash
git push -u origin feat/wheel-encoder-fallback
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(firmware): wheel-encoder phase3 timeout fallback" --body "$(cat <<'EOF'
## Summary

Two firmware changes in \`multiace/klipper/extras/ace.py\` to stop losing
\`head_source\` bookkeeping when the ACE drive-wheel encoder is stuck-but-
filament-actually-reached-the-head.

- **Tier 1**: \`ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S\` — manual override
  for already-failed loads. Pre-flight gates on \`e<head>_filament=True\`
  and \`head_source[head]=null\`. Emits two-line LOAD_HEAD_SUSPICIOUS
  (reason=manual_override) + LOAD_HEAD audit pattern.
- **Tier 2**: \`cmd_ACE_LOAD_HEAD\` catches \`feed_auto_error\` from
  FEED_AUTO; if \`e<head>_filament=True\`, trusts sensor, emits
  LOAD_HEAD_SUSPICIOUS (reason=feed_auto_error_sensor_fallback), falls
  through to existing success path. If sensor reads False, fails as before.

Convenience macros \`ACEC__Mark_Loaded_T0..T3\` in \`ace.cfg\` (group H).
README + CLAUDE.md updated.

## Test plan

- [ ] Tier 1 happy path on a head with sensor=True, head_source=null
- [ ] Tier 1 honesty gate (sensor=False refuses)
- [ ] Tier 1 collision gate (head_source already set refuses)
- [ ] Tier 2 fallback fires on phase3-timeout-prone slot (T1 historically)
- [ ] Tier 2 sensor-False still fails as before
- [ ] Convenience macros register and dispatch correctly
- [ ] Existing clean loads still emit just LOAD_HEAD_TIP_REFRESHED + LOAD_HEAD

Spec: \`docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

**Spec coverage check:**
- ✅ Tier 1 `ACE_MARK_HEAD_LOADED` (Task 1, with three smoke steps for happy path + two gates)
- ✅ Tier 2 fallback (Task 2, adapted to the actual `cmd_ACE_LOAD_HEAD` structure — see deviation note in plan header)
- ✅ Convenience macros (Task 3 — group H per spec §2 "Components")
- ✅ Documentation (Task 4)
- ✅ Manual on-printer validation per CLAUDE.md (no automated tests for firmware) — every code task has a smoke step
- ✅ Spec §6 deploy steps embedded in Task 1 step 4 + Task 2 step 4
- ✅ Spec §4 error handling — pre-flight gates exercised in Task 1 steps 5-6
- ⚠️ Spec §5 testing item 7 ("constants tuning after one week") — out of scope for this plan (post-deployment observation), captured as future work in spec §7

**Type/method consistency check:**
- `_head_source[head]` access shape consistent across Task 1 and Task 2 (both write the 5-key dict with ace_index/slot/type/color/brand)
- `_audit_state` calls use the same key set across Task 1 (manual_override) and Task 2 (feed_auto_error_sensor_fallback)
- `_save_head_source` invoked in Task 1 (manual override path) — matches existing `cmd_ACE_LOAD_HEAD` line 1691 usage; Task 2 doesn't add a new call because the existing line still runs in the fall-through path

**Spec deviation:** Documented at the top of the plan. Tier 2's mechanism is sensor-check on FEED_AUTO exception, not coil_freq tracking inside a phase3 retry loop. Same user-facing semantic, simpler implementation, single-file change. Approved by spec's own §1 caveat ("the function name `_feed_loading` may differ slightly... implementer reads the source and matches the existing structure").
