# HelixScreen multiACE Integration — SP2: Multi-Unit ACE Backend

**Date:** 2026-05-27
**Status:** Design (approved for spec; pending user review before plan)
**Sub-project:** SP2 of the HelixScreen integration (SP1 shipped + live-verified)

---

## Parent program (context)

Run HelixScreen on the Snapmaker U1 display to manage the multiACE rig (up to 4 chained ACE
Pro = 16 colors). Sub-projects:

| # | Sub-project | Repo / lang | Status |
|---|---|---|---|
| SP1 | multiACE multi-unit ACE **status contract** | multiACE (Python) | **DONE** — `ace` object publishes `units[]`, live-verified 2026-05-27 |
| **SP2** | **HelixScreen multi-unit ACE backend** — parse `units[]` → `AmsSystemInfo.units` | helixscreen fork (C++) | **this spec** |
| SP3 | Loadout Check + Recovery panels (+ correct slot-action wiring) | helixscreen fork (C++) | later |
| SP4 | Reversible HelixScreen ⟷ stock-GUI install on the U1 | shell/ops | later |

**Fork strategy (decided):** develop on a **local feature branch** in the existing clone at
`/mnt/e/Code/helixscreen` (origin = upstream `prestonbrown/helixscreen`, no push access). Build
+ test on the dev host; deploy the binary to the U1 under SP4. Offer an upstream PR later, once
proven on hardware. Do not push to origin.

---

## Goal

Extend HelixScreen's ACE backend so that when the `ace` Klipper object carries a `units[]`
array (multiACE, SP1), the backend populates `AmsSystemInfo.units` with one `AmsUnit` per ACE
(N units, 16 slots) following the AFC `populate` pattern — making the existing multi-unit AMS
UI render all units. Non-multiACE firmware (ValgACE/BunnyACE/DuckACE, no `units[]`) keeps the
existing single-unit behavior unchanged.

## Scope

**In scope (data layer + safety):**
- A `parse_units_array()` path in `AmsBackendAce` that builds `AmsSystemInfo.units` from `ace["units"]`.
- Fallback to the existing flat-`slots[]` single-unit parse when `units[]` is absent.
- A **tap-guard**: when `AmsSystemInfo::is_multi_unit()`, `AmsBackendAce::change_tool` no-ops
  (logs; optional toast) instead of issuing the single-unit `ACE_CHANGE_TOOL`.

**Out of scope (deferred):**
- **Correct multi-unit slot *actions*** (load/change mapped to multiACE `ACE_LOAD_HEAD` /
  `ACE_SWITCH`) → **SP3** (the Loadout/Recovery panels are where deliberate loads live).
- **UI/layout changes** — none. SP2 relies on the existing multi-unit AMS UI (verified below).
- **On-device deploy / visual confirmation on the U1** → **SP4**.
- Spoolman/FilamentHub resolution beyond carrying `sku` (same as today's flat path).

## Verified assumption: the multi-unit UI renders ACE units "for free"

Confirmed against the code (not assumed):
- `AmsSystemInfo::is_multi_unit()` exists (`include/ams_types.h:989`, `units.size() > 1`).
- `ui_panel_ams_overview.cpp:314-351` **loops `info.units`**, creating an `ams_unit_card` per
  unit with **dynamic per-unit slot bars** (slot count varies per unit).
- `navigate_to_ams_panel()` routes `is_multi_unit()` → multi-unit overview; else → single detail panel.
- The AMS UI is backend-agnostic: AFC/CFS/Happy-Hare/etc. all flow through the same
  `AmsSystemInfo` → UI path. A units-populated ACE backend rides the same path.

Therefore SP2 needs **no UI code**: populating `AmsSystemInfo.units` is sufficient for the
overview to draw N units. (Visual fit of 16 slots on the U1 screen is an SP4/on-device check —
see Risks.)

---

## Architecture

`AmsBackendAce::parse_ace_object(ace_data)` (the existing parser, invoked from the Moonraker
subscription on `ace`) gains a branch:

```
parse_ace_object(ace):
    parse top-level model/firmware/status as today
    if ace has non-empty array "units":
        parse_units_array(ace)          # NEW — multi-unit path
    else:
        <existing flat slots[] single-unit path, UNCHANGED>   # ValgACE/BunnyACE fallback
    parse top-level total_slots / current_tool / current_slot when present
```

`parse_units_array(ace)`:
- Rebuilds `system_info_.units` from scratch (clear, then one `AmsUnit` per `ace["units"][i]`)
  — must not append onto a previously-emplaced unit.
- Reuses the existing slot helpers (`parse_slot_color`, the slot-status mapping) so multi-unit
  and flat paths produce identical slot semantics.

### Components / files (helixscreen)
- Modify: `src/printer/ams_backend_ace.cpp` — add `parse_units_array`, branch in
  `parse_ace_object`, tap-guard in `change_tool`.
- Modify: `include/ams_backend_ace.h` — declare `parse_units_array`.
- Modify/extend: `tests/unit/test_ams_backend_ace.cpp` — multi-unit + fallback + guard cases.

---

## Data mapping: `units[]` → `AmsSystemInfo`

| SP1 JSON (`ace`) | HelixScreen field | Notes |
|---|---|---|
| `units[i].unit_index` | `AmsUnit.unit_index` | |
| `units[i].name` | `AmsUnit.name` | `"ace_0"` (stable match key) |
| `units[i].display_name` | `AmsUnit.display_name` | `"ACE A"` |
| `units[i].slot_count` | `AmsUnit.slot_count` | |
| `units[i].first_slot_global_index` | `AmsUnit.first_slot_global_index` | from SP1 (AFC convention); do not recompute |
| `units[i].connected` | `AmsUnit.connected` | drives offline/grey rendering |
| `units[i].environment.temperature_c` | `AmsUnit.environment->temperature_c` | set `EnvironmentData` |
| `units[i].environment.has_humidity` | `AmsUnit.environment->has_humidity` | SP1 sends `false` (frame has no humidity) |
| slot `.slot_index` / `.global_index` | `AmsSlot.slot_index` / `.global_index` | verbatim |
| slot `.status` | `AmsSlot.status` | `available`→AVAILABLE, `empty`→EMPTY, `unknown`→UNKNOWN |
| slot `.color` `[r,g,b]` | `AmsSlot.color_rgb` | via existing `parse_slot_color` |
| slot `.type` | `AmsSlot.material` | |
| slot `.brand` | `AmsSlot.brand` | |
| slot `.sku` | spoolman/sku path | same handling as flat path |
| **slot `.mapped_tool`** | **`AmsSlot.mapped_tool`** | **use SP1's value; do NOT apply the "tools ARE slots" 1:1 default** — multiACE has 4 heads / 16 slots |
| `ace.total_slots` | `AmsSystemInfo.total_slots` | |
| `ace.current_tool` / `current_slot` | `AmsSystemInfo.current_tool` / `current_slot` | |
| `ace.model` | `AmsSystemInfo.type_name` | keep existing `"ACE"` semantics |

**The `mapped_tool` correction is the central multi-unit divergence.** The existing single-unit
ACE backend treats tool number == slot index. multiACE provides explicit, sparse `mapped_tool`
(0–3 only for head_source-referenced slots; `-1` otherwise). SP2 must carry SP1's value, never
default to `global_index`.

## Error handling
`parse_units_array` is defensive and never throws into the Moonraker response callback:
- non-array / empty `units` → fall through to the flat path;
- a malformed unit entry → skipped (logged), other units still parsed;
- missing optional slot fields → omitted; unknown slot status → UNKNOWN.
Preserve the surrounding code's existing `weak_ptr`/token + main-thread discipline (no new
bg-thread `this`-capture; this is parse-on-response, so the L081 guard does not newly apply).

## Tap-guard (safety)
In `AmsBackendAce::change_tool(int tool_number)`: if `system_info_.is_multi_unit()`, log and
return without issuing `ACE_CHANGE_TOOL` (optionally surface a toast "manage from the multiACE
panel"). Single-unit path unchanged. Correct multi-unit action wiring is SP3.

---

## Testing (TDD, host-runnable)

**Step 0 (gate):** `make test-run` (build + run the Catch2 suite). If it fails to build in this
environment, run `tests/test_deps.sh`, install missing deps, retry. **If it cannot build here,
stop and escalate** — local TDD is the premise of SP2.

Extend `tests/unit/test_ams_backend_ace.cpp` (Catch2 + Moonraker mock — feed a mock `ace` JSON
in the SP1 shape, 2 units / 8 slots):
- **multi-unit parse** — `units.size()==2`, `total_slots==8`, unit 1 `first_slot_global_index==4`,
  flat global indices `0..7`, per-unit `slot_count==4`.
- **mapped_tool sparse** — only head_source-referenced slots carry a tool (0–3); others `-1`;
  assert `mapped_tool != global_index` for a mapped slot on unit 1.
- **connected/offline unit** — `connected=false` unit retained with `unknown` slots, not dropped.
- **environment** — `temperature_c` carried; `has_humidity==false`.
- **color/material/brand/sku** — carried via the shared helpers.
- **fallback (no regression)** — an `ace` object with flat `slots[]` and **no** `units[]` →
  single unit (`units.size()==1`), existing behavior intact.
- **tap-guard** — multi-unit active → `change_tool` issues **no** gcode (assert the Moonraker
  mock received no `ACE_CHANGE_TOOL`); single-unit → still issues it.

Run targeted: `./build/bin/helix-tests "[ace]"` (or the suite's ACE tag) after the first full build.

## Acceptance criteria
1. `make test-run` is green (incl. new cases) on the dev host.
2. Multi-unit `ace` JSON → `AmsSystemInfo.units` with correct counts, AFC-convention global
   indices, `connected`, `environment`, and sparse `mapped_tool` (not 1:1).
3. Absent `units[]` → unchanged single-unit behavior (regression test passes).
4. `change_tool` is guarded under multi-unit (no wrong command).
5. Diff is minimal/surgical (one new helper + one branch + one guard + tests); the single-unit
   path is untouched. Local branch only; not pushed to origin.

## Risks & mitigations
| Risk | Mitigation |
|---|---|
| **Suite won't build in WSL** (top risk) | Step 0 gate: `make test-run` / `tests/test_deps.sh` before any code; escalate if it can't build. |
| 16 slots (4 cards) overflow the U1 screen | UI is built for variable units/slots (AFC farms) and likely scrolls; visual fit is an SP4/on-device (or SDL) check, not a data-layer blocker. |
| `parse_units_array` double-populates `units` | Rebuild the vector from scratch (clear then emplace per unit); regression test asserts exact `units.size()`. |
| Upstream moves fast → rebase pain | Minimal surgical diff (Approach A) keeps the local branch easy to rebase; offer upstream later to retire the fork. |

## Open questions for SP3/SP4 (not blocking SP2)
- **SP3:** correct slot-action wiring (tap a multi-unit slot → `ACE_LOAD_HEAD HEAD=n ACE=m
  SLOT=k` / `ACE_SWITCH`), plus the dedicated Loadout Check + Recovery panels.
- **SP4:** build the U1 (aarch64) binary, deploy, and run HelixScreen on the framebuffer
  swappable with the stock `/usr/bin/gui` (`/etc/init.d/S99screen`).
- Visual fit of 16 slots on the U1 — confirm on-device; adjust layout in SP3 if needed.
