# Auto-dry design — humidity-driven, filament-aware

This is a design doc for a feature that **isn't built yet**. It captures the
state machine, safety rules, and config surface so when the BLE humidity
sensor arrives the implementation has a clear target.

## Goal

Watch chamber humidity. When humidity rises above a per-filament-type wake
threshold, automatically run an `ACE_DRY` cycle until humidity drops below a
stop threshold. Keep watching. Repeat as needed. Respect print state.

## Why not "always dry"

- Heat cycles wear the chamber and the filament.
- Drying soft filament near actively-feeding gears can crush it.
- Heat creep — warm filament + warm enclosure + hotend = clogs.
- Endless drying makes some filaments brittle.

So the rule is: dry when needed, stop when dry, never compromise the print.

## What the research says about drying-while-printing

Bambu's AMS 2 Pro and Anycubic's ACE Pro both support drying while a print is
running. The crucial constraint is **temperature is automatically capped below
the loaded filament's softening point during the print** so the feed gears
don't crush soft filament. Bambu's published values:

| Loaded filament | Max dry temp during print |
|---|---|
| PLA | 45 °C |
| PETG | 55 °C |
| TPU / TPE | 50 °C |
| ABS / ASA | 55 °C |
| Nylon (PA) / PC | 60 °C (conservative) |

The auto-dry FSM uses these caps when a print is active and the user has
opted in to during-print drying. When idle, the full per-filament target temp
applies.

## Per-filament thresholds

Reasonable defaults sized for ACE Pro hardware (60 °C effective cap with the
recommended H5104 sensor placed inside the chamber):

| Filament | Wake (start) | Stop (target) | Idle target temp | During-print cap |
|---|---|---|---|---|
| PLA | 35% RH | 25% | 50 °C | 45 °C |
| PETG | 30% | 22% | 60 °C | 55 °C |
| TPU | 30% | 22% | 50 °C | 50 °C |
| ABS / ASA | 25% | 18% | 60 °C | 55 °C |
| Nylon (PA) | 20% | 12% | 60 °C | 60 °C |
| PC | 20% | 12% | 60 °C | 60 °C |
| PVA / BVOH | 20% | 12% | 45 °C | 45 °C |

When multiple slots are loaded with different filaments, use the **strictest**
thresholds — i.e. the lowest stop %, the lowest cap temperature.

## State machine (per ACE)

```
       ┌─────────────────────────────┐
       │   IDLE — auto-dry disabled  │ ◀── user toggle off, sensor offline,
       │                             │     no filament loaded
       └──────────────┬──────────────┘
                      │ enabled, sensor OK, filament loaded
                      ▼
       ┌─────────────────────────────┐
       │ WATCHING — sensor reads RH  │ ◀──┐
       │ every poll (60 s default)   │    │ humidity drops below stop_threshold
       └──────────────┬──────────────┘    │ OR cooldown elapsed
                      │                   │
                      │ humidity > wake_threshold for ≥5 min
                      │ AND not in cooldown
                      │ AND no swap_in_progress
                      │ AND (idle OR during-print mode + safe temp)
                      ▼                   │
       ┌─────────────────────────────┐    │
       │ DRYING — ACE_DRY running    │    │
       │ uses (per-filament target,  │────┘
       │ duration_min from profile)  │
       └──────────────┬──────────────┘
                      │ humidity ≤ stop_threshold
                      │ OR ACE_DRY duration elapsed
                      │ OR max_run_min reached
                      │ OR print starts (graceful stop if not in during-print mode)
                      ▼
       ┌─────────────────────────────┐
       │ COOLDOWN — wait 30 min      │
       │ before re-evaluating        │
       └──────────────┬──────────────┘
                      │ cooldown elapsed
                      ▼
                  WATCHING
```

The 5-minute "above wake" debounce prevents a brief spike (e.g. someone opens
the lid) from kicking off a dry. The 30-minute cooldown prevents flap-flap
near the threshold.

## Guards

These ALL must be satisfied to enter DRYING:

| Guard | Source | What it checks |
|---|---|---|
| `enabled` | env / UI toggle | User opted in |
| sensor available | `/api/print` `humidity.ok` | RH reading is fresh + sane (0–100) |
| filament loaded | `state.head_source` non-empty | Something to dry |
| no swap | `state.swap_in_progress == false` | Mid-toolchange = bad time to heat |
| not in cooldown | local FSM state | Anti-flap |
| safe to run | print state + filament Tg | See below |

**"Safe to run" matrix:**

| Print state | Filament cap reached? | Behavior |
|---|---|---|
| `standby` / `complete` / `cancelled` | n/a | Run at idle target temp |
| `printing` AND user opted in | yes (cap < target) | Run at cap |
| `printing` AND user opted in | no (cap ≥ target) | Run at target |
| `printing` AND user opted out | n/a | Skip; emit `AUTODRY_SKIPPED_PRINT` |
| `paused` (Klipper exception) | n/a | Skip; user attention needed first |
| `error` | n/a | Skip |

## Safety caps

| Cap | Default | Why |
|---|---|---|
| `max_run_min` | 720 (12 h) | If RH still high after 12 h, sensor or seal is bad |
| `cooldown_min` | 30 | Anti-flap |
| `daily_duty_max_min` | 1080 (18 h/day) | Hardware sanity |
| `min_delta_pct` for "useful run" | 3 percentage points | If a 12 h run barely moved RH, fault out |

When a fault triggers (e.g. delta too small after a full run), auto-dry
disables itself for that ACE and emits a banner: "Auto-dry disabled — sensor
or chamber seal suspect. Verify and re-enable in the Dryer tab."

## API additions

### `GET /api/autodry`

Returns the current FSM state and recent transitions per ACE.

```json
{
  "enabled": true,
  "aces": {
    "0": {
      "state": "WATCHING",
      "since": "2026-04-28T10:31:40",
      "last_run": {
        "started": "2026-04-28T08:15:00",
        "ended": "2026-04-28T10:00:00",
        "trigger_rh": 38,
        "end_rh": 23,
        "temp_c_used": 50,
        "duration_min": 105
      },
      "next_evaluation": "2026-04-28T10:32:40"
    }
  }
}
```

### `POST /api/autodry`

Enable / disable / override.

```json
{"action": "enable"}
{"action": "disable"}
{"action": "force_evaluate", "ace": 0}      // skip the debounce, decide now
{"action": "set_during_print", "value": true}
```

## Activity events

New event types appear in `multiace_state.log` and propagate to the dashboard:

| Action | When |
|---|---|
| `AUTODRY_TRIGGERED` | Entering DRYING. params: `{rh, target_temp, duration_min, reason: "wake_threshold"}` |
| `AUTODRY_FINISHED` | Leaving DRYING successfully. params: `{start_rh, end_rh, ran_min}` |
| `AUTODRY_SKIPPED_PRINT` | Wanted to dry but print was running and user opted out |
| `AUTODRY_SKIPPED_SWAP` | Wanted to dry but swap_in_progress |
| `AUTODRY_FAILED_SENSOR` | RH unreadable for ≥3 cycles, FSM paused |
| `AUTODRY_FAILED_LIMIT` | max_run_min reached without hitting stop_threshold |
| `AUTODRY_DISABLED_AUTO` | FSM disabled itself (e.g. min_delta failure) |
| `AUTODRY_DISABLED_USER` | User toggled off |

## Configuration

Per-ACE thresholds live in browser localStorage alongside the existing dryer
profiles (`multiace_dryer_profiles`). Backend defaults via env:

```
MULTIACE_AUTODRY_ENABLED=false
MULTIACE_AUTODRY_TICK_SEC=60
MULTIACE_AUTODRY_DEBOUNCE_MIN=5
MULTIACE_AUTODRY_COOLDOWN_MIN=30
MULTIACE_AUTODRY_MAX_RUN_MIN=720
MULTIACE_AUTODRY_DAILY_DUTY_MAX_MIN=1080
MULTIACE_AUTODRY_DURING_PRINT=false
MULTIACE_AUTODRY_DEFAULT_WAKE_PCT=35
MULTIACE_AUTODRY_DEFAULT_STOP_PCT=25
MULTIACE_AUTODRY_DEFAULT_TARGET_C=50
```

Per-filament overrides in the localStorage profile JSON:

```json
{
  "id": "petg",
  "name": "PETG",
  "temp": 60,
  "duration": 360,
  "wake_pct": 30,
  "stop_pct": 22,
  "during_print_temp": 55
}
```

## UI surface

### Dryer tab

Above the existing manual profile picker, an "Auto mode" panel:

```
┌─ Auto mode ─────────────────────────────────────────┐
│ [   Enabled   ]                                     │
│                                                     │
│ Run during prints: ☐  (capped per filament)        │
│                                                     │
│ Per-filament thresholds   [Edit profiles…]          │
│   PLA  wake 35% / stop 25%  @ 50°C (45°C in print)  │
│   PETG wake 30% / stop 22%  @ 60°C (55°C in print)  │
│   ...                                               │
│                                                     │
│ Last run: PLA, started 08:15, ran 1h45m, 38→23%     │
└─────────────────────────────────────────────────────┘
```

### Dashboard

When auto-dry triggered the current dry, the existing dryer card grows an
"AUTO" badge next to its DRYING pill. When idle but watching, the env strip's
humidity tile shows a small footer line: "Auto-dry: armed · target 30%".

## Implementation outline

```
multiace_web/
  src/multiace_web/
    autodryer.py       # FSM + asyncio.Task tick loop (NEW)
    server.py          # /api/autodry GET/POST (extended)
    static/
      app.js           # auto-mode panel renderer + dashboard badge (extended)
  tests/
    test_autodryer.py  # FSM state transitions, guards, safety caps (NEW)
```

The FSM lives next to `StatusPoller` as a sibling background task. It reads
`/api/print` (already producing humidity, dryer state, print state) and
`app.state.state` (multiACE state for swap_in_progress / head_source). It
emits transitions via the existing event broadcaster so they show up in
Activity.

`autodryer.py` should be ≤300 lines. The FSM is small; most of the volume is
guard evaluation and safety cap enforcement.

## Open questions for v1

- **Multi-filament-loaded selection**: pick the strictest thresholds, but use
  which filament's target temp? Probably the strictest (lowest cap) since
  that's the safest. Document.
- **Sensor placement validation**: a sensor outside the chamber, or in
  ambient-only mode, can't drive auto-dry usefully — it'll never see the
  chamber dry out. Should the FSM detect "RH didn't move during DRYING" and
  warn the user about placement? Yes, the `min_delta_pct` cap covers this.
- **Schedule windows**: should auto-dry respect quiet hours (e.g. don't
  trigger between 11pm–7am)? Probably yes, with a default blackout window.
  Configurable via `MULTIACE_AUTODRY_BLACKOUT_HOURS=23-07`.
- **Manual override**: if the user starts a manual dry from the Dryer tab
  while auto-dry is in WATCHING state, what happens? Right thing: auto-dry
  observes the manual run (treats it as DRYING regardless of who started),
  goes to COOLDOWN when it ends.

## Implementation phases

1. **Phase 1 — read-only:** evaluate guards, log decisions to a new
   `autodryer.log`, but never actually trigger ACE_DRY. Deploy and observe
   for a week. Verify the FSM does what we'd want without committing to
   action.
2. **Phase 2 — idle-only:** enable triggering, but only when print is
   `standby`. The simplest scenario.
3. **Phase 3 — during-print:** add the per-filament cap enforcement. Default
   off; opt-in toggle.

This is the natural rollout. Don't ship Phase 3 first.
