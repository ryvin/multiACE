# Pre-load tip refresh

A short retract + re-feed at the ACE gate, run *before* every `LOAD_HEAD`, to
trim a deformed or cooled filament tip left from a previous unload. Eliminates
the most common cause of `move_extrude!raw msg:logic error!` load failures
without changing how the user triggers loads.

## Why it exists

After an `UNLOAD_HEAD`, the filament tip rests at the ACE gate. The shape of
that tip is determined by the unload's tip-shaping retract — primarily the
fluidd `CONTROL_RETRACT_ACTION` macro, which uses `nozzle_diameter` to compute
how far to pull. The tip can be malformed if:

- The hotend cooled too fast at the wrong moment (cone collapses, blob forms).
- `nozzle_diameter` was missing or mismatched (unload retracts the wrong distance).
- The tip sat at the gate long enough for material to creep / sag.

When that filament gets re-fed on the next `LOAD_HEAD`, the toolhead
extruder's grip gear can't bite a malformed tip. You see this as 19+ phase3
retries in `klippy.log`, then `extruder hanging neutral`, then
`move_extrude!raw msg:logic error!`. Total wasted time: ~90 s before the
failure is reported.

The workaround that worked manually was straightforward — back off the bad
tip a little, push fresh filament past the gate, then load. The tip refresh
feature wraps that into the load flow.

## What the macro does

When `tip_refresh_before_load: 1` (the recommended default), each
`ACE_LOAD_HEAD` runs this *before* the main `FEED_AUTO LOAD`:

```
ACE_RETRACT INDEX=<slot>  LENGTH=<retract_len>  SPEED=<retract_speed>
wait_ace_ready()
ACE_FEED    INDEX=<slot>  LENGTH=<feed_len>     SPEED=<feed_speed>
wait_ace_ready()
audit:  LOAD_HEAD_TIP_REFRESHED { head, ace, slot, retract_length, feed_length }
```

Then the normal load proceeds as before. From the user's perspective there's
no new button to press — loads just take ~5 s longer and stop failing on
malformed tips.

## Configuration (`ace.cfg`)

```ini
# Pre-load tip refresh — small retract + re-feed at the gate before each
# load, to trim a deformed/cooled tip left from a previous unload.
tip_refresh_before_load: 1
tip_refresh_retract_length: 30      # mm pulled back through the gate
tip_refresh_feed_length: 90         # mm pushed forward (must be > retract)
tip_refresh_retract_speed: 20       # mm/s — slow, to avoid slip
tip_refresh_feed_speed: 30          # mm/s
```

| Option | Default | Range | Notes |
|---|---|---|---|
| `tip_refresh_before_load` | `False` (in `ace.py`); `1` in shipped `ace.cfg` | bool | Master switch. Set `0` to skip. |
| `tip_refresh_retract_length` | 30 | 0–200 | How far to pull the tip back. Too short = tip stays malformed. Too long = re-feed needs to be proportionally longer. |
| `tip_refresh_feed_length` | 90 | 0–400 | How far to push fresh filament forward after retract. Should be ≥ `retract_length` + ~50 mm so the tip ends up *past* the gate before the main load starts. |
| `tip_refresh_retract_speed` | 20 | 5–100 | mm/s. Slow on retract reduces ACE feed-gear slip. |
| `tip_refresh_feed_speed` | 30 | 5–100 | mm/s. |

If `retract_length: 0` or `feed_length: 0`, the helper is a no-op (no audit
event either).

## When it fires (and when it doesn't)

It runs in `cmd_ACE_LOAD_HEAD` immediately after the gate-status check
confirms the slot has filament, and *before* the toolchange to the target
extruder. Concretely:

```python
if self.gate_status[slot] != GATE_AVAILABLE:
    raise LOAD_HEAD_FAILED slot_empty
self._tip_refresh_at_gate(slot, head, ace_index)   # ← here
self.gcode.run_script_from_command('T%d A0' % head)
self.gcode.run_script_from_command("FEED_AUTO ... LOAD=1")
```

It does *not* run when:
- `tip_refresh_before_load: 0`.
- The slot is empty (the load aborts earlier with `LOAD_HEAD_FAILED slot_empty`).
- A retry path inside `feed_loading` triggers another sub-load — those go
  through `FEED_AUTO` directly, not `ACE_LOAD_HEAD`.

## Audit signal

Every successful tip refresh emits a state event:

```json
{
  "action": "LOAD_HEAD_TIP_REFRESHED",
  "params": {
    "head": 2,
    "ace": 0,
    "slot": 2,
    "retract_length": 30,
    "feed_length": 90
  }
}
```

You'll see this in the Activity tab right before each `LOAD_HEAD`. If you
don't, either tip refresh is disabled in config or the load took a path that
bypasses `ACE_LOAD_HEAD`.

## Tradeoffs

- **Filament cost**: ~60 mm net forward feed per load (`feed_length -
  retract_length`). Negligible — most prints already use orders of magnitude
  more.
- **Time cost**: ~5 s per load at the default speeds.
- **Hardware-issue masking**: if the toolhead extruder gear is worn or the
  hotend isn't reaching temp, tip refresh papers over those failures. Watch
  the load success rate over time — if it trends down even with refresh on,
  the underlying hardware needs attention. The Activity tab's pattern of
  `LOAD_HEAD_TIP_REFRESHED → LOAD_HEAD` (success) vs `LOAD_HEAD_TIP_REFRESHED
  → LOAD_HEAD_FAILED` is the signal.

## Why these defaults

Tested on a Snapmaker U1 + ACE Pro with 80 cm bowdens, after recovering from
~6 hours of malformed-tip failures:

- 30 mm retract @ 20 mm/s — enough to pull the deformed cone past the gate
  without slip. 50 mm worked too but is wasteful.
- 90 mm feed @ 30 mm/s — overshoots the 30 mm retract by 60 mm so the new tip
  ends up well past the gate, ready for the toolhead extruder to bite.

Bowdens shorter than ~50 cm probably don't need tip refresh at all — the tip
doesn't have time to deform sitting at the gate. For longer bowdens (>1 m)
keep the defaults; the tip-shape problem is independent of bowden length.

## Disabling

If you suspect the refresh is interfering with a particular workflow:

```ini
tip_refresh_before_load: 0
```

Save & Restart. Subsequent loads will skip the refresh. Re-enable with `1`.
