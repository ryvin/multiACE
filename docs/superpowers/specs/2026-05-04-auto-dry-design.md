# Auto-dry — humidity-driven, filament-aware filament maintenance

**Status:** approved 2026-05-04
**Scope:** Phase 1 (read-only logging FSM) + Phase 2 (idle-only triggering).
Phase 3 (during-print) deferred to a later spec.
**Supersedes:** the older design at `multiace_web/docs/auto-dry-design.md`,
which predated the BLE humidity sensor wiring and used per-filament humidity
thresholds. This spec keeps the FSM and safety model from that doc but
revises the user-facing model to a **single global target** with per-filament
*means* (temp + duration), based on web research of how Bambu / Polymaker /
Prusa active-drying chambers manage filament.

## Goal

Watch chamber humidity. Once humidity rises above (target + hysteresis), run
an `ACE_DRY` cycle until it drops below the target, with per-filament
temperature and duration. Respect print state: do not trigger while a print
is running in this version. Surface state through the existing dashboard
*and* push transitions as native toasts to Mainsail/Fluidd via
Moonraker `[server_announcements]`.

## Non-goals (v1)

- During-print drying with per-filament temperature caps. Deferred — Phase 3
  in the older doc; revisit only after Phase 1+2 has run for at least one
  print cycle without surprises.
- Per-filament humidity targets. Web research and hardware specs (Bambu AMS
  2 Pro, Polymaker PolyDryer, Prusa Pro ACU) all converge on a single
  chamber humidity target with per-filament drying *means*; the user
  confirmed this matches their mental model ("shoot for 15% for them all").
- Quiet hours / blackout windows. YAGNI — cooldown + max_run already prevent
  the only failure modes blackout would catch. Add later if real complaints
  emerge.

## Hardware contract assumptions (verified live)

Empirically checked against the running printer (`/api/print`,
`/api/state`) + `multiace/klipper/extras/ace.py` +
`multiace/config/extended/ace.cfg` on 2026-05-04 before finalizing this
spec:

- **`ACE_DRY` macro signature:** `ACE_DRY ACE=N [TEMP=T] [DURATION=D]`.
  `ACE` is the device index (0–3 — per device, not global). `TEMP` is °C.
  `DURATION` is **minutes** (verified at `ace.py:1966`:
  `'Drying ACE %d at %d°C for %d min'`).
- **DURATION upper bound:** Klipper config `dryer_duration` is capped at
  `maxval=480` (8 h). The gcode invocation itself doesn't enforce a max,
  but the ACE Pro firmware's drying job is an 8 h cycle by design.
  **Auto-dry never asks for more than 480 min per single cycle.** Longer
  drying for hygroscopic filaments (Nylon, PC — want 24 h) is handled by
  the FSM's natural retry loop: each 8 h cycle drops RH some, COOLDOWN
  resets, WATCHING re-evaluates, next cycle starts if still above wake.
  Cycle until target reached or `daily_duty_max_min` consumed.
- **`/api/dry`** in `server.py` already proxies the macro:
  `gcode = f"ACE_DRY ACE={body.ace} TEMP={body.temp_c} DURATION={body.duration_min}"`.
  Auto-dry calls this same endpoint internally — no new Klipper surface.
- **`/api/print` exposes the active-ACE's dryer state** (verified live):

  ```json
  "dryer": {
    "status":       "stop",   // "stop" when idle; "drying" while a cycle runs
    "target_temp":  0,
    "duration_min": 0,
    "remain_min":   0,
    "remain_sec":   0
  }
  ```

  Note: `status` (not `state`); values seen are `"stop"`/`"drying"`. The
  field is single-instance — it shows whichever ACE is currently
  `active_device`. We use `dryer.status == "drying"` for OBSERVED_DRYING
  detection.
- **`gate_status`** on `/api/state` is `list[int] of length 4`, where each
  index is `0` (empty) or `1` (loaded), for the **active ACE only** (not
  per-device).
- **`head_source`** on `/api/state` is `dict[str, {ace, slot, type, color}]`
  keyed by toolhead index 0–3. `src.ace` identifies which physical ACE
  feeds that toolhead — so this is the right field for "is any toolhead
  sourced from ACE N" multi-ACE-aware check.

### Single-FSM consequence for v1

The H5104 humidity sensor sits in **one** physical ACE chamber, and
`/api/print` exposes the dryer state of only the **active** ACE. The
predecessor doc's per-ACE FSM model isn't supported by these contracts —
v1 runs a **single FSM** that auto-dries one configured ACE. Multi-ACE
auto-dry (one sensor per ACE, observability for inactive ACEs) is a v2
concern with its own spec.

Configuration:

```
MULTIACE_AUTODRY_TARGET_ACE=0    # which physical ACE to manage; default 0
```

The FSM only operates when this ACE is `active_device` and contains the
configured humidity sensor. If `active_device != target_ace`, the FSM
goes to `IDLE` (state field exposes `reason: "not_active_ace"`) and the
toast / dashboard footer say so.

## Architecture

```
multiace_web/src/multiace_web/
  autodryer.py          # NEW — FSM + asyncio tick loop, ≤300 lines
  server.py             # +/api/autodry GET/POST, lifespan starts AutoDryer
  state.py              # unchanged
  static/
    app.js              # +Auto-dry panel + dashboard footer + Activity events
    style.css           # +panel styling
multiace_web/tests/
  test_autodryer.py     # FSM transitions, guards, safety caps, multi-slot
  test_announcements.py # Moonraker server_announcements posting
```

### Single FSM instance

`AutoDryer` holds **one FSM instance** managing the configured
`target_ace`. There is no per-ACE FSM in v1 — the hardware contract
(single sensor + single observable dryer status) doesn't support it. If
`device_count > 1`, the FSM still only manages `target_ace`; other ACEs
are untouched. Multi-ACE expansion (per-ACE sensors, per-ACE FSM,
multi-sensor reconciliation) is a v2 spec.

### Persistent state across restarts

`AutoDryer` writes a JSON snapshot to
`/userdata/multiace-web/app/.autodry_state.json` (mode 0644, owned by
lava). Two write modes:

- **Immediate (synchronous fsync before returning):** mode flips, target
  changes, FAULTED transitions, fault clears. Losing these in a crash
  causes the failure modes the persistence is designed to prevent.
- **Debounced (≤1 write/sec):** `daily_duty` rollover ticks, last_run
  bookkeeping, entry_id capture. Sub-second loss is acceptable.

Schema:

```json
{
  "mode": "log",
  "target_pct": 15,
  "hysteresis_pp": 5,
  "fsm": {
    "state": "FAULTED",
    "fault": {"code": "FAILED_DELTA", "since_ts": 1714780000.0,
              "msg": "12h cycle moved RH 22→21 (Δ=1pp)"},
    "trigger_announcement_id": null,
    "last_run": {...},
    "daily_duty": [
      {"started_ts": 1714756800.0, "ran_min": 480}
    ]
  }
}
```

The lifespan `startup` hook reads this file (if present) and applies it
**before** the first FSM tick:

- `mode` / `target_pct` / `hysteresis_pp` override their corresponding
  env vars (env is the *initial-deploy default*; once a user POSTs to
  change a value, the persisted value wins for subsequent restarts).
- `FAULTED` survives restart — the fault stays until user clears via
  `reset_fault`. Solves the "broken seal causes infinite re-trigger"
  hole.
- `trigger_announcement_id` survives restart so we can dismiss matched
  TRIGGERED ↔ FINISHED toast pairs even after a multiace-web restart
  during a long dry.
- `daily_duty` is a 24-hour rolling list of completed runs; on restart
  we drop entries older than 24 h before computing the cap.

### Boot-time DRYING reconciliation

`dryer.status == "drying"` is the "is currently drying" check; values seen
live are `"stop"` (idle) and `"drying"`.

If on boot the persisted state was DRYING but `/api/print` `dryer.status`
also reports `"drying"`, we adopt the in-flight cycle (don't restart it,
don't double-trigger). On the FSM's first tick where `dryer.status`
transitions back to `"stop"` → we move to COOLDOWN. If on boot the
persisted state was DRYING but Klipper reports `"stop"`, the cycle ended
during the restart window — go straight to COOLDOWN with
`last_run.ended_ts = now` and emit `AUTODRY_FINISHED_AFTER_RESTART`
(informational, no toast).

### Tick loop

`AutoDryer` is started by `server.py`'s lifespan as a sibling background
task to `StatusPoller`. Each tick (60 s default) it reads:

- In-process `app.state.state` (multiACE state model) — `active_device`,
  `head_source`, `gate_status`, `swap_in_progress`, `print_task_config`
- Cached `/api/print` payload — `humidity` (already wired to the Govee
  bridge), `state` (Klipper `print_stats.state`), `dryer.status`,
  `cavity_temp_c`
- Local FSM state — current state, cooldown timer, rolling humidity
  buffer, daily_duty list

…and runs the FSM. Transitions are emitted via the existing event
broadcaster (Activity tab) and, for user-relevant ones, posted as toasts
to Moonraker `[server_announcements]` (Fluidd/Mainsail bell).

`autodryer.py` should be ≤300 lines. The FSM is small; most of the volume
is guard evaluation and announcement plumbing.

## Runtime mode (single env var, three modes)

`MULTIACE_AUTODRY_MODE` controls behavior end-to-end:

| Mode | Behavior |
|---|---|
| `off` (default) | FSM doesn't run. Endpoints return `{"mode": "off"}`. |
| `log` | FSM evaluates, logs decisions to `multiace_state.log`, **never POSTs `/api/dry`**. Phase 1. |
| `active` | FSM evaluates *and* triggers dry cycles, idle-only. Phase 2. |

This means Phase 1 vs Phase 2 is a config flip, not a code branch. The
implementation is one path; mode just gates the action at the moment we
would call `/api/dry`.

`MULTIACE_AUTODRY_DURING_PRINT` is **not honored** in this version. The
"safe to run" check always returns False during `printing`/`paused`, and
the corresponding transition emits `AUTODRY_SKIPPED_PRINT`.

## State machine

States (also the values exposed in `/api/autodry` `state` field):
`IDLE | WATCHING | DRYING | OBSERVED_DRYING | COOLDOWN | FAULTED`.

```
       ┌─────────────────────────────┐
       │   IDLE                      │ ◀── mode=off, sensor offline,
       │                             │     active_device != target_ace,
       │                             │     no filament loaded
       └──────────────┬──────────────┘
                      │ mode in (log,active), sensor OK,
                      │ active_device == target_ace,
                      │ ≥1 head_source.ace == target_ace
                      ▼
       ┌─────────────────────────────┐
       │ WATCHING — tick every 60s   │ ◀──────┐
       │ rolling RH buffer 5 samples │        │ humidity ≤ target_pct
       └──────────────┬──────────────┘        │ OR cooldown elapsed
                      │                       │
                      │ user starts manual dry │
                      │ (dryer.status="drying" │
                      │  while in WATCHING)    │
                      │ ──────────────┐        │
                      │               ▼        │
                      │      ┌─────────────────────────────┐
                      │      │ OBSERVED_DRYING — observed  │
                      │      │ user-initiated cycle. No    │
                      │      │ trigger event, no toast.    │
                      │      └──────────────┬──────────────┘
                      │                     │ dryer.status="stop"
                      │                     ▼
                      │                COOLDOWN ───────────┘
                      │
                      │ humidity > wake_threshold for ≥ debounce_min
                      │ AND not in cooldown
                      │ AND not over daily duty cap
                      │ AND no swap_in_progress
                      │ AND print_state in {standby,complete,cancelled,error}
                      │ AND dryer.status == "stop"
                      ▼
       ┌─────────────────────────────┐
       │ DRYING — ACE_DRY running    │
       │ (mode=active only; mode=log │ ──┐ humidity ≤ target_pct
       │  emits AUTODRY_DRY_RUN and  │   │ OR ACE_DRY duration elapsed
       │  immediately COOLDOWN)      │   │ → COOLDOWN (success)
       └──────────────┬──────────────┘   │
                      │                   │ OR max_run_min reached
                      │                   │ OR min_delta_pct not met
                      │                   │ → FAULTED
                      │                   │
                      │                   │ OR print starts mid-cycle
                      │                   │ → COOLDOWN + AUTODRY_SKIPPED_PRINT
                      ▼                   ▼
       ┌─────────────────────────────┐
       │ COOLDOWN — wait 30 min      │
       └──────────────┬──────────────┘
                      │ cooldown elapsed
                      ▼
                  WATCHING

   FAULTED — sensor or seal failure detected. Self-disabled until user
   clears via POST /api/autodry {"action":"reset_fault"}.
```

The 5-minute "above wake" debounce prevents lid-opening spikes from kicking
off a dry. The 30-minute cooldown prevents flap near the threshold.

### Debounce semantics

The debounce is implemented as a **rolling sample buffer**, not a wall-clock
timestamp. Required: at least `ceil(debounce_min / tick_sec * 60)` consecutive
ticks each reading RH > wake_threshold. With defaults (5 min debounce, 60 s
tick) that's 5 samples. A single dip below the wake line resets the count.
This is more robust than `now > then + 5min` against the cache staleness
described below.

The buffer is **always empty at FSM boot** — even if persisted state shows
WATCHING, we wait for a fresh 5-sample window before considering trigger.
Prevents an immediate post-restart trigger.

### Sensor cache staleness note

`/api/print` humidity is cached 30 s; FSM ticks every 60 s. Worst-case a
sample is 90 s old when evaluated. The 5-sample debounce mitigates this:
even if a couple ticks read the same cached value, you still need 5
distinct sustained readings for a trigger. If implementation finds this
too coarse, the cache TTL is overridable via `_HUMIDITY_TTL_SEC` (already
test-exposed in server.py).

### Manual dry interaction (OBSERVED_DRYING)

When the user starts a dry from the Dryer tab while the FSM is in
WATCHING, the FSM transitions to **OBSERVED_DRYING** (a real state in
the enum, not a flag on DRYING). This is identical to DRYING for RH
tracking and timer purposes but:

- No `AUTODRY_TRIGGERED` event, no toast (the user already knows they
  started this).
- `last_run.kind = "manual"` in the JSON.
- The FSM never issues its own `ACE_DRY` while in OBSERVED_DRYING — we're
  observing, not driving.
- On `dryer.status == "stop"`, transition to COOLDOWN normally.

The DRYING-entry guard `dryer.status != "drying"` blocks the FSM from
issuing ACE_DRY while one is already running; OBSERVED_DRYING handles
the case where one started after the FSM was already in WATCHING.

### Mixed-filament loads in one ACE

If two slots in the same ACE have *different* filament types (e.g., PLA
and Nylon), the FSM picks:

- **Strictest temp cap:** `min(temp_c)` across loaded slots → don't crush
  the soft one.
- **Shorter duration:** `min(duration_min)` across loaded slots → never
  bake the heat-sensitive one. (Industry warning: drying PLA at 50°C for
  24 h causes brittleness. Better to give Nylon less drying than to
  damage PLA.)

The UI flags this case explicitly:

> ACE 0 has PLA + Nylon loaded. Auto-dry will use a single 50°C × 6 h
> cycle (PLA-safe). Nylon needs longer to fully dry — re-run with only
> the Nylon slot loaded for full effect.

The strictest-rule is computed per-tick (in case the user swaps a slot
mid-WATCHING).

## Guards (all must hold to enter DRYING)

| Guard | Source | What it checks |
|---|---|---|
| Mode is `log` or `active` | persisted state, falls back to env | User opted in |
| Target ACE is active | `state.active_device == target_ace` | Sensor + dryer observability requires the ACE to be active |
| Sensor available | `/api/print` `humidity.ok && 0 ≤ humidity_pct ≤ 100` | Fresh + sane reading |
| Filament loaded in target ACE | `any(src.ace == target_ace for src in state.head_source.values() if src)` | Something to dry. (`gate_status` is for the active ACE only and would also work here, but `head_source.ace` is the direct multi-ACE-aware check.) |
| Not in swap | `state.swap_in_progress == false` | Toolchanges are bad timing |
| Not in cooldown | local FSM | Anti-flap |
| Not over daily duty cap | local FSM (rolling 24h sum) | Hardware sanity |
| Print state safe | `/api/print` `state in {standby, complete, cancelled, error}` | Don't fight a running print |
| Dryer not already busy | `/api/print` `dryer.status == "stop"` | Don't ACE_DRY on top of running cycle (instead → OBSERVED_DRYING) |

If any guard fails, FSM stays in WATCHING and emits the corresponding
`AUTODRY_SKIPPED_*` event (rate-limited: at most one of the same skip-type
per cooldown period).

## Single global humidity target with hysteresis

User-facing knobs:

| Setting | Default | Range | Meaning |
|---|---|---|---|
| `target_pct` | **15** | 5–60 | Target chamber RH the FSM tries to maintain |
| `hysteresis_pp` | 5 | 1–15 | Wake threshold = target + this |

So with defaults: dry starts when RH > 20% sustained for 5 min, stops when
RH ≤ 15%. User can lower to 10% if their seal supports it; warning surfaced
in UI if they go below 10% ("near H5104 sensor floor — may not stop").

Web research (Bambu / Polymaker / Prusa) shows commercial active-drying
chambers aim for 10–15% RH. 15% is a pragmatic middle ground — meaningful
print-quality improvement over "ambient" filament without sitting on top
of the H5104's ±3% accuracy floor.

## Per-filament drying *means* (cycle params)

These come from cross-referenced manufacturer recommendations (Bambu wiki,
Polymaker, Prusa, Anycubic) and the ACE Pro's 70°C max temp:

| Filament | Dry temp °C | Duration h | Notes |
|---|---|---|---|
| PLA | 50 | 6 | Don't exceed 55°C — softens |
| PETG | 60 | 8 | |
| TPU | 50 | 12 | Soft at temp; long time |
| ABS | 65 | 6 | |
| ASA | 65 | 6 | Same family as ABS |
| Nylon (PA) | 70 | 24 | Wants 80°C; ACE caps at 70 → longer cycle |
| PC | 70 | 24 | Same as nylon |
| PVA | 45 | 8 | Never exceed 50°C — heat-sensitive |
| (unknown) | 50 | 6 | Conservative PLA-equivalent fallback |

Stored in localStorage `multiace_dryer_profiles` (already exists for the
manual Dryer tab) under each profile's `temp` and `duration` keys. The
auto-dry feature *reuses* the existing profile shape — no new schema for
the means.

**Profile lookup fallback chain** when the FSM needs cycle params for a
loaded slot's filament `type` string:

1. localStorage profile whose `id` (case-insensitive) matches `type`
2. Defaults table above (this spec)
3. If `type` is unknown to both, use the "(unknown)" fallback row
   (50°C × 360 min — conservative PLA-equivalent)

Per-cycle DURATION is hard-clamped at 480 min before the macro call (see
the hardware contract section). Profiles can store any value; the FSM caps
at issue time.

(Mixed-filament reconciliation rules are spelled out in the FSM section
above — strictest temp cap + shortest duration + UI warning. Not repeated
here.)

## Safety caps

| Cap | Default | Why |
|---|---|---|
| `max_run_min` | 720 (12 h) | If RH still high after 12 h, sensor or seal is bad |
| `cooldown_min` | 30 | Anti-flap |
| `daily_duty_max_min` | 1080 (18 h/24h window) | Hardware sanity |
| `min_delta_pct` for "useful run" | 3 percentage points | If a 12 h run barely moved RH, fault out |
| `sensor_floor_warn_pct` | 13 | If target_pct < this, UI shows "near H5104 sensor floor — auto-dry may not stop reliably." Doesn't block; informational only. (Default 13 means the warning shows whenever the user lowers target below the H5104's ±3% accuracy band around 10%.) |

Faults that auto-disable the FSM (set state to FAULTED, emit event, post
toast):

- `AUTODRY_FAILED_LIMIT` — `max_run_min` reached without crossing target
- `AUTODRY_FAILED_DELTA` — full run completed but `end_rh - start_rh < min_delta_pct`
- `AUTODRY_FAILED_SENSOR` — RH unreadable for ≥3 consecutive ticks
- `AUTODRY_SKIPPED_DAILY` — would exceed `daily_duty_max_min`; skipped, not a fault per se but rate-limited (see Activity events table)

User clears with `POST /api/autodry {"action":"reset_fault"}`.

## API additions

### `GET /api/autodry`

```jsonc
{
  "mode": "log",                          // off | log | active
  "target_ace": 0,
  "target_pct": 15,
  "hysteresis_pp": 5,
  "fsm": {
    "state": "WATCHING",                  // IDLE|WATCHING|DRYING|OBSERVED_DRYING|COOLDOWN|FAULTED
    "since": "2026-05-04T03:12:00Z",
    "next_evaluation": "2026-05-04T03:13:00Z",
    "current_rh": 36.4,
    "wake_threshold_pct": 20,             // target + hysteresis (computed)
    "stop_threshold_pct": 15,             // = target (computed)
    "effective_temp_c": 50,               // strictest temp cap across loaded slots
    "effective_duration_min": 360,        // shortest duration across loaded slots
    "loaded_types": ["PLA", "PLA"],
    "mixed_filament_warning": false,      // true when loaded_types has >1 distinct type
    "idle_reason": null,                  // when state==IDLE: "mode_off"|"no_sensor"|"not_active_ace"|"no_filament"
    "fault": null,                        // or {"code": "FAILED_DELTA", "since_ts": ..., "msg": "..."}
    "last_run": {
      "kind":         "auto",             // "auto" | "manual" | "auto_after_restart"
      "outcome":      "success",          // "success" | "failed"
      "started":      "2026-05-04T01:30:00Z",
      "ended":        "2026-05-04T03:00:00Z",
      "trigger_rh":   22.0,
      "end_rh":       14.5,
      "temp_c_used":  50,
      "duration_min": 360,                // requested (after profile lookup + cap)
      "ran_min":      90                  // actual elapsed
    }
  }
}
```

### `POST /api/autodry`

```jsonc
// Global actions (apply to all ACEs):
{"action": "set_mode",       "value": "log" | "active" | "off"}
{"action": "set_target",     "value": 15}                       // 5–60
{"action": "set_hysteresis", "value": 5}                        // 1–15

// Single-FSM actions (no `ace` param needed in v1; FSM manages target_ace):
{"action": "force_evaluate"}                                    // bypass debounce AND cooldown for this tick
{"action": "reset_fault"}                                       // clear FAULTED state
```

All POSTs validate and `400` on out-of-range values; the body returns the
new full state (same shape as GET).

`force_evaluate` bypasses both the debounce buffer and cooldown — it
means "user is asking us to decide right now". This is the only API that
can shorten cooldown.

Idempotent — reposting the same `set_mode` is a no-op. State mutations
(immediate-write fields per the persistence section) write through to
`/userdata/multiace-web/app/.autodry_state.json` *before* the API
returns.

## Activity events (existing event broadcaster)

| Action | When | Params |
|---|---|---|
| `AUTODRY_TRIGGERED` | WATCHING → DRYING (only when mode=active) | `{ace, target_temp, duration_min, trigger_rh, reason}` where `reason ∈ {wake_threshold, force_evaluate, post_cooldown_resume}` |
| `AUTODRY_DRY_RUN` | mode=log decision (would have triggered) | same fields, marked `dry_run=true` |
| `AUTODRY_FINISHED` | DRYING → COOLDOWN (success) | `{ace, start_rh, end_rh, ran_min}` |
| `AUTODRY_SKIPPED_PRINT` | Wanted to dry but print active | rate-limited 1/cooldown |
| `AUTODRY_SKIPPED_SWAP` | Wanted to dry but swap in progress | rate-limited |
| `AUTODRY_SKIPPED_DAILY` | Daily duty cap reached | rate-limited |
| `AUTODRY_FAILED_SENSOR` | RH unreadable ≥3 ticks | once per FAULTED transition |
| `AUTODRY_FAILED_LIMIT` | max_run_min reached | once |
| `AUTODRY_FAILED_DELTA` | min_delta not met | once |
| `AUTODRY_FAULT_CLEARED` | User reset_fault | once |
| `AUTODRY_FINISHED_AFTER_RESTART` | Boot found persisted DRYING but Klipper idle | once on boot |

## Mainsail/Fluidd toast integration

`autodryer.py` posts to Moonraker on user-relevant transitions only —
**not** every 60 s tick:

```http
POST http://127.0.0.1:7125/server/announcements/post
Content-Type: application/json

{
  "title":       "Auto-dry triggered: ACE 1",
  "entry_type":  "info",
  "priority":    "normal",
  "description": "Humidity 22%, drying T1/T2 to 15% at 50°C. ETA ~6h."
}
```

Posted on:

- `AUTODRY_TRIGGERED` (info)
- `AUTODRY_FINISHED` (info)
- All `AUTODRY_FAILED_*` (warning)
- `AUTODRY_FAULT_CLEARED` (info)

Mode=`log` posts the same toasts but with `[DRY-RUN]` prefix in the title
so you can see what the FSM *would* have done in your normal Fluidd
notification bell.

The `entry_id` returned by Moonraker is stored on the FSM state (and
persisted to disk) so we can auto-dismiss it on the matching transition:

- `DRYING → COOLDOWN` (success): dismiss the TRIGGERED entry_id, post
  FINISHED (which is left in the bell — informational).
- `DRYING → FAULTED` (any FAILED_*): dismiss the TRIGGERED entry_id (the
  "drying to 15%" message is now stale and lying), post the FAILED toast
  (kept — user attention needed).
- `OBSERVED_DRYING → COOLDOWN`: nothing to dismiss (we never posted a
  toast for a manual cycle).

The mode=`log` toasts use `[DRY-RUN]` prefix in **both title and
description** so users seeing them in the bell can't mistake them for
real action: `"[DRY-RUN] Auto-dry would trigger: ACE 0"` /
`"[DRY-RUN] Humidity 22%, would dry T1/T2 to 15% at 50°C..."`.

If `device_count > 1` and SKIP toasts are emitted (e.g., during a print)
each transition produces exactly one toast — there is only one FSM in v1.

## UI surface

### Dryer tab

A new "Auto-dry" panel above the existing manual profile section:

```
┌─ Auto-dry ───────────────────────────────────────────────┐
│ Mode  ( Off | Log | Active )                             │
│                                                          │
│ Global target humidity         [───────●  15%]           │
│ ▾ Advanced                                               │
│   Hysteresis                   ±5 pp                     │
│   Max run                      12h                       │
│   Cooldown                     30m                       │
│   Daily cap                    18h                       │
│                                                          │
│ Per-filament dry settings   [Edit profiles…]             │
│   PLA    50°C  · 6h                                      │
│   PETG   60°C  · 8h                                      │
│   TPU    50°C  · 12h                                     │
│   ABS    65°C  · 6h                                      │
│   …                                                      │
│                                                          │
│ ACE 0 — Watching · target 15% · current 36% (PLA)        │
│ Last run 1h45m ago: 22% → 14%, ran 90m                   │
└──────────────────────────────────────────────────────────┘
```

The "Edit profiles…" button opens the same modal already used for the
manual Dryer tab profiles (only `temp` + `duration` are auto-dry-relevant;
the modal shows them in a grouped section).

### Dashboard humidity tile (footer)

The existing humidity tile in the env-strip gets a small footer line that
mirrors FSM state:

| FSM state | Footer text |
|---|---|
| IDLE / mode=off | (no footer) |
| IDLE / `idle_reason=not_active_ace` | `Auto-dry idle · ACE 0 not active` |
| IDLE / `idle_reason=no_filament` | `Auto-dry idle · no filament loaded` |
| IDLE / `idle_reason=no_sensor` | `Auto-dry idle · sensor offline` |
| WATCHING | `Auto-dry: armed · target 15%` |
| DRYING (mode=active) | `Drying… 22 → 14%` (live current_rh) |
| DRYING (mode=log) | `Would dry [log-only] · 22%` |
| OBSERVED_DRYING | `Manual dry running · 22 → 14%` |
| COOLDOWN | `Cooldown · resumes 14:32` |
| FAULTED | `Auto-dry paused · check chamber seal` (clickable to reset) |

### Activity tab

Existing event renderer handles the new actions automatically once the
labels are added to the event-name → human-text dictionary.

## Configuration

Backend env (defaults):

```
MULTIACE_AUTODRY_MODE=off                    # off | log | active
MULTIACE_AUTODRY_TARGET_ACE=0                # which physical ACE the FSM manages (0–3)
MULTIACE_AUTODRY_TICK_SEC=60
MULTIACE_AUTODRY_DEBOUNCE_MIN=5
MULTIACE_AUTODRY_COOLDOWN_MIN=30
MULTIACE_AUTODRY_MAX_RUN_MIN=720
MULTIACE_AUTODRY_DAILY_DUTY_MAX_MIN=1080
MULTIACE_AUTODRY_MIN_DELTA_PCT=3
MULTIACE_AUTODRY_DEFAULT_TARGET_PCT=15
MULTIACE_AUTODRY_DEFAULT_HYSTERESIS_PP=5
```

Env values are *initial-deploy defaults* — the persisted state file
overrides them once a user POSTs a change.

Per-filament cycle params live in localStorage `multiace_dryer_profiles`
(no new schema). User-set values override the defaults table above on a
per-type basis.

## Testing

`tests/test_autodryer.py` covers:

- All FSM transitions (IDLE → WATCHING → DRYING → COOLDOWN → WATCHING)
- All guards individually (sensor missing, swap in progress, print active,
  cooldown active, daily cap, target unreachable)
- Multi-slot strictest-rule: PLA + Nylon → uses 50°C × shorter duration
  (PLA's 360 min, NOT Nylon's 1440), with `mixed_filament_warning=true`
  flag on the slot status
- Per-cycle DURATION clamp at 480 min even when profile asks for more
- Mode flips (off ↔ log ↔ active) at every state
- Debounce: 5-sample rolling buffer; single dip below wake resets count;
  buffer empty on FSM boot regardless of persisted state
- Cooldown elapse → re-evaluate
- Faults (FAILED_LIMIT, FAILED_DELTA, FAILED_SENSOR) → FAULTED, persisted
  to disk, survives FSM restart
- `reset_fault` API clears state, removes from persisted file, and re-arms
- `force_evaluate` API bypasses debounce for one tick
- Single FSM behavior: in a multi-ACE setup with `target_ace=0` and
  `active_device=1`, FSM stays IDLE with `idle_reason="not_active_ace"`.
  Switching `active_device` to 0 transitions to WATCHING. Switching back
  during DRYING transitions to FAULTED? — actually no, this can't happen
  during active drying because Klipper holds the active serial. Test:
  switching active_device while FSM is in COOLDOWN → IDLE.
- `OBSERVED_DRYING`: user starts manual dry while FSM in WATCHING; FSM
  enters OBSERVED_DRYING (no toast, no AUTODRY_TRIGGERED), transitions to
  COOLDOWN on dryer.status going `"stop"`; never issues its own ACE_DRY.
- Boot-time recovery cases:
  - Persisted DRYING + Klipper `dryer.status == "drying"` → adopt cycle,
    COOLDOWN on finish
  - Persisted DRYING + Klipper `dryer.status == "stop"` → COOLDOWN with
    `AUTODRY_FINISHED_AFTER_RESTART`
  - Persisted FAULTED → stays FAULTED, no auto-clear
  - Persisted mode beats env on conflict
  - Persisted target_ace beats env on conflict
- Immediate-write semantics: kill the process between a fault detection
  and the next debounced flush; verify on relaunch the FAULTED state is
  loaded from disk.
- TRIGGERED-then-FAILED dismiss: when DRYING → FAULTED via FAILED_LIMIT,
  the TRIGGERED entry_id is dismissed (mock Moonraker check) and the
  FAILED entry_id is kept.
- `force_evaluate` while in COOLDOWN: skips both debounce and cooldown,
  evaluates against current RH.
- Idle reasons: each value of `idle_reason` reachable through its own
  test scenario.

`tests/test_announcements.py` covers:

- Toast posted exactly once per user-relevant transition
- `[DRY-RUN]` prefix in mode=log
- entry_id captured, **persisted to disk**, and used for dismiss
- entry_id survives FSM restart so a TRIGGERED→FINISHED pair started before
  restart still gets dismissed cleanly
- Failed POST to Moonraker doesn't crash the FSM (logged + retried next transition)
- 60s ticks don't post (only transitions do)

Existing API tests (`test_server.py`) extend for the new endpoints — round-
trip set/get for mode/target/hysteresis, validation errors for out-of-range.

## Implementation phases

| Phase | What | Acceptance |
|---|---|---|
| 1 — `mode=log` | Full FSM evaluation; logs but never triggers | Run for one print + one idle window. Logs show plausible decisions. No false `AUTODRY_TRIGGERED`. |
| 2 — `mode=active` | Same code, gate flipped | Run on idle. First real triggered cycle drops RH from above-wake to ≤target. No spurious triggers during prints. |
| 3 (deferred) | During-print w/ caps | New spec when 1+2 are stable |

Phases 1 and 2 ship together as a single implementation; the user picks
which mode to operate in via env. The plan schedules a Phase 1 soak (run
in `mode=log` for a real-world period) before flipping to `mode=active`.

## Resolved open items from the predecessor doc

- **Manual override interaction** (q4): see `OBSERVED_DRYING` sub-state
  in the FSM section — the FSM does not credit user-initiated cycles as
  its own and never issues ACE_DRY on top of a running cycle.
- **Sensor placement validation** (q2): covered by `min_delta_pct` (faults
  on broken seals) and `sensor_floor_warn_pct` (UI hint when user targets
  near sensor accuracy). No new logic beyond these caps.
- **Multi-filament target reconciliation** (q1): N/A — single global
  target. Cycle params reconciled by strictest-cap + shortest-duration
  rule with explicit UI warning for mixed loads.
- **Quiet hours / blackout windows** (q3): dropped. Cooldown +
  `daily_duty_max_min` already prevent the failure modes blackout would
  catch. Add as a YAGNI follow-up if real complaints emerge.

## Security / reliability notes

- AutoDryer never directly invokes Klipper. Every action goes through the
  existing `/api/dry` proxy, which goes through Moonraker's
  `gcode/script` — same path as the user's manual dry button. So the
  Snapmaker U1 safety protocol (don't restart Klipper while printing) is
  honored automatically: the FSM checks `print_stats.state` before issuing
  a dry, and `ACE_DRY` itself is a non-blocking macro that just sets temps.
- Restarting `multiace-web` mid-print blinks the iframe panel briefly but
  doesn't disturb the print or camera streams (verified during the 0.6.x
  deploys). The auto-dry FSM persists its critical state to
  `.autodry_state.json` on every transition so on restart it resumes
  knowing whether it was DRYING (boot reconciliation), FAULTED (stays
  FAULTED), or what the user-set target/mode were.
- The Moonraker `/server/announcements/post` endpoint requires Moonraker's
  default no-auth (LAN-only) configuration. If the user enables auth, the
  bridge will need a token in env — flag in `hardware-bluetooth.md`-style
  troubleshooting.

## Out-of-scope explicit list (so reviewer doesn't flag)

- Charts / RH history graphs
- Scheduling (don't run between 23:00 and 07:00) — see "Non-goals"
- Cross-ACE coordination (e.g., dry ACE 0 and ACE 1 sequentially)
- Predictive / ML-based humidity targeting
- Auto-detection of filament type from RFID — we already trust
  `print_task_config` for this
- The Klipper `[temperature_sensor]` integration discussed during
  brainstorming — needs a Klipper restart, deferred

## Versioning

Ships as `multiace-web` 0.7.0. The Govee bridge (currently part of the
0.6.x series) is unchanged by this work. README gets a "0.7.0" changelog
entry summarizing: new Auto-dry tab section, new env var, new API,
Moonraker toasts.
