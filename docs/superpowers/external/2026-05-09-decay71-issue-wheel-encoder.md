# Issue body — file at https://github.com/decay71/multiACE/issues/new

**Title:** Wheel-encoder phase3 timeout: filament reaches head but `cnt_a_1 == cnt_a_2` causes false `feed_auto_error`

## Summary

On the U1 + Anycubic ACE Pro hardware, the wheel-encoder phase3 retry pattern in `_feed_loading` (or the equivalent in `filament_feed_ace.py`) can time out **even when the filament has physically reached the head**. The encoder reads `cnt_a_1 == cnt_a_2` (no wheel motion observed for the configured window) so the load raises `feed_auto_error`, but the head's filament-motion sensor (`e<n>_filament`) is reading True — the filament IS there.

This shows up as recurring `feed_auto_error` failures on otherwise-successful loads. Operators often see the filament at the head and wonder why the load reported failure.

We hit this on a sibling fork (`ryvin/multiACE`) and shipped a two-tier fix that has been live-validated. Sharing here in case it's useful — your `ace.py` may have a similar path that benefits from the same fallback.

## Root cause

The phase3 wheel-encoder check fires too eagerly under specific conditions (filament tip flexible enough that wheel rotation drops below the detection threshold while the filament is still being pushed forward by the upstream feeder). The encoder is the only signal that didn't see the motion; the head sensor saw it just fine.

## Fix on ryvin/multiACE (two tiers)

### Tier 1 — `ACE_MARK_HEAD_LOADED` recovery macro

A new gcode command `ACE_MARK_HEAD_LOADED HEAD=N ACE=M SLOT=S` that lets the operator manually fix bookkeeping after a `feed_auto_error` when the sensor confirms the filament arrived. Pre-flight gates:
- `e<head>_filament` must read True (sensor confirms filament physically present)
- `head_source[head]` must NOT already be set (no overwrite without explicit unload first)

Audit: emits `LOAD_HEAD_SUSPICIOUS` (with `reason: 'manual_override'`) followed by `LOAD_HEAD` for the bookkeeping update. The web log tailer treats `LOAD_HEAD` as the "head bound" event so the UI updates correctly.

Implementation reference: `multiace/klipper/extras/ace.py:1738-1810` in [ryvin/multiACE@4044e09](https://github.com/ryvin/multiACE/blob/4044e09/multiace/klipper/extras/ace.py#L1738-L1810).

### Tier 2 — Sensor fallback in the FEED_AUTO try/except

Inside `cmd_ACE_LOAD_HEAD`, when `FEED_AUTO ... LOAD=1` raises, instead of immediately re-raising:

1. Read the head's `filament_motion_sensor`.
2. If `filament_detected == True`: trust the sensor, emit `LOAD_HEAD_SUSPICIOUS` with `reason: 'feed_auto_error_sensor_fallback'`, and fall through to the normal success path (wheel_delta check, head_source set, audit `LOAD_HEAD`).
3. If `filament_detected == False`: re-raise as before.

This converts a recurring HTTP 400 into a successful load with a `LOAD_HEAD_SUSPICIOUS` audit row that operators can spot-check. On our hardware it eliminated the failures with no operational regressions.

Implementation:

```python
try:
    self.gcode.run_script_from_command(
        "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d LOAD=1"
        % (module, channel, head))
except Exception as e:
    # FEED_AUTO timed out (wheel-encoder phase3 didn't observe enough motion).
    # On this rig that recurringly happens when filament *did* reach the head.
    # If the head's filament-motion sensor reads True now, trust it.
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

Reference: `multiace/klipper/extras/ace.py:1652-1684` in [ryvin/multiACE@4044e09](https://github.com/ryvin/multiACE/blob/4044e09/multiace/klipper/extras/ace.py#L1652-L1684).

## Adaptation notes for decay71/multiACE

Your `ace.py` has a different shape than ours (3584 lines vs ours 2483; orchestration via `ACE_SWAP_HEAD` vs our two-leg web smart-swap), so a verbatim cherry-pick won't apply cleanly. The conceptual changes:

1. **Pick the right point in your `cmd_ACE_LOAD_HEAD`** — wherever you currently invoke `FEED_AUTO ... LOAD=1` (or your equivalent unload/load primitive). Wrap with try/except + sensor check + fall-through-on-sensor-True.
2. **Audit shape** — adapt to your existing audit-event vocabulary. We use `LOAD_HEAD_SUSPICIOUS` with a `reason` field; if you have a different convention, use it.
3. **The recovery macro (Tier 1)** — your `cmd_ACE_SWAP_HEAD` does internal recovery via `_pause_for_recovery()`. The Tier-1 manual macro is for the case where the print is NOT running (operator at the printer, fixing bookkeeping post-failure). It's complementary to your in-print recovery, not a replacement.

## Live validation on ryvin/multiACE

Hardware: Snapmaker U1 + 2× Anycubic ACE Pro daisy-chained.

Before the fix: ~1 in 5 loads from a stale/cool ACE would fail with `feed_auto_error` even though the filament arrived at the head. Operator workaround: notice the failure, manually re-issue `ACE_LOAD_HEAD`, hope it works the second time.

After the fix: zero `feed_auto_error` failures on subsequent loads (Tier 2 path catches them). One `LOAD_HEAD_SUSPICIOUS` audit row per converted failure for spot-checking. No operational regressions in 100+ loads since deployment.

## Why share this here

decay71's `ace.py` is the most mature single-file ACE controller in the multiACE ecosystem (3584 lines vs upstream's 605). If you have already solved this differently (e.g. a longer phase3 window or a different retry shape), I'd love to know and adopt it back. If not, this fix is small, hardware-validated, and low-risk.

Happy to file a focused PR if you accept patches, or just paste the inline diff if you prefer to manually integrate.

— Raul (`ryvin/multiACE` maintainer)

---

## Repo links for context

- ryvin/multiACE main: [github.com/ryvin/multiACE](https://github.com/ryvin/multiACE)
- The two commits where this fix landed:
  - Tier 1 macro: see `ACE_MARK_HEAD_LOADED` in `multiace/klipper/extras/ace.py`
  - Tier 2 sensor fallback: in `cmd_ACE_LOAD_HEAD`, search for `feed_auto_error_sensor_fallback`
- Spec doc: `docs/superpowers/specs/2026-05-07-wheel-encoder-fallback-design.md`
- Plan doc (with the smoke matrix that validated it): `docs/superpowers/plans/2026-05-07-wheel-encoder-fallback.md`
