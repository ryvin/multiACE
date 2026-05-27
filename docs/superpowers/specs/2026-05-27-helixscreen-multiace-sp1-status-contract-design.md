# HelixScreen multiACE Integration — SP1: Multi-Unit ACE Status Contract

**Date:** 2026-05-27
**Status:** Design (approved for spec; pending user review before plan)
**Sub-project:** SP1 of the HelixScreen integration (see "Parent program" below)

---

## Parent program (context)

The user wants to run **HelixScreen** (a native C++ LVGL touchscreen Klipper UI) on the
Snapmaker U1's built-in display to manage the multiACE rig (up to 4 chained Anycubic ACE
Pro units = 16 colors) **hands-free at the printer**, while keeping the ability to **roll
back to the stock Snapmaker GUI** easily.

The integration decomposes into four sub-projects coupled only by a data contract:

| # | Sub-project | Repo / language | Depends on |
|---|---|---|---|
| **SP1** | **Multi-unit ACE status contract** — multiACE publishes all N ACEs / 16 slots as a Moonraker-readable Klipper object | multiACE (Python) | — |
| SP2 | HelixScreen multi-unit ACE backend — fork, parse SP1's contract into `AmsSystemInfo` with N units (AFC pattern) | helixscreen fork (C++) | SP1 |
| SP3 | The two priority panels — Loadout Check + Runout/Fault Recovery | helixscreen fork (C++) | SP2 |
| SP4 | Reversible install — HelixScreen ⟷ stock-GUI swap on the U1 | shell/ops | parallel |

Three-layer reversibility model: the **display GUI** (HelixScreen ⟷ stock) is a swappable
service; **multiACE firmware** is unchanged by the display choice; the **web console**
(phone/laptop) keeps full ACE management and survives any HelixScreen rollback. HelixScreen
on the screen is *additive*, never a single point of failure.

**This spec covers SP1 only.** It is independently shippable and testable.

---

## Goal

Add a Klipper status object to multiACE's `ace.py` that publishes the firmware's
already-in-memory multi-ACE state — every ACE unit and every slot — in a JSON shape that
HelixScreen's ACE backend can consume via a Moonraker subscription, mirroring the canonical
multi-unit conventions HelixScreen's AFC/CFS backends already use.

## Non-goals (explicitly out of scope for SP1)

- **No web-console-sourced state.** The autodry FSM phase, Govee *external* humidity, and
  `fault.msg` live in `multiace_web` (`autodryer.py`), a separate process. SP1 is a
  single-process firmware change. ACE-*internal* humidity (reported by the ACE hardware)
  **is** in scope; the Govee external sensor is not.
- **No new C++ in HelixScreen.** That is SP2.
- **No `printer.cfg` writes.** SP1 only reads in-memory state and publishes it.
- **No new serial traffic and no change to the USB/framing path.** SP1 snapshots the frame
  the active device's response path already decodes (`self._info`); it does **not** modify
  `_keepalive_tick`, decode the keepalive drain, or poll non-active ACEs mid-print. Those were
  evaluated and rejected on USB-path safety grounds (#70/#74 stabilized that path). The
  freshness gap for never-activated units is closed by a **guarded, idle-only refresh deferred
  to SP3**.
- **No FilamentHub/Spoolman resolution logic.** The ACE frame carries no UID/SKU — only an
  `rfid` presence code (`0`/`2`) plus brand/type/color. SP1 passes those through; resolving a
  spool against Spoolman/FilamentHub is HelixScreen's existing Spoolman integration (downstream,
  keyed on brand+type+color or a future UID path).

---

## Background: why a new object is needed

`ace.py` exposes state **only** through the `ACE_HEAD_STATUS` *gcode command* (a
human-readable log dump) and the `multiace_state.log` audit log. It has **no
`get_status()` method on the controller**, so nothing appears in Moonraker
`objects/query`/`subscribe` for a touchscreen UI to read.

HelixScreen's ACE backend (`src/printer/ams_backend_ace.cpp`) **subscribes to a Klipper
object** and runs `parse_ace_object(data)` on every update (with a REST `/server/ace/`
fallback for BunnyACE/DuckACE). It expects a ValgACE-shaped object: top-level `model`,
`firmware`, `status`, `humidity`, and a flat `slots[]` array (clamped at 16), each slot with
`status`, `color`, `type`, `sku`. It currently dumps all slots into **one** unit.

HelixScreen's *canonical* model (`include/ams_types.h`), populated by the multi-unit AFC and
CFS backends, is richer: `AmsSystemInfo { vector<AmsUnit> units; int total_slots; … }`, each
`AmsUnit { unit_index, name, display_name, slot_count, first_slot_global_index, connected,
optional<EnvironmentData> environment, … }`, each `AmsSlot { slot_index, global_index,
status, color_rgb, material, brand, mapped_tool, spoolman_id, … }`.

**SP1 publishes a superset** that (a) renders immediately on HelixScreen's *unmodified*
single-unit parser via the flat `slots[]` aggregate (interim win, de-risks SP2), and
(b) carries the full multi-unit structure SP2 maps onto the canonical model.

---

## Architecture

```
ACE hardware ──get_status frame (slots, rfid, humidity, temp)──┐
   active device's response path: _process_response → self._info │ SP1: snapshot-on-active
   (ace.py:1264 self._info = response['result'])                  ┘ → self._last_status[idx]
   [keepalive (#7) keeps idle serials warm but DISCARDS their replies — unchanged by SP1]

ace.py in-memory state:
  _ace_devices, _active_device_index   (device map, locked path→index)
  _head_source[0..3]                   (tool → {ace_index, slot, brand, type, color})
  _last_status[ace_index]              (NEW: {"result": frame, "recv_ts": t} per unit)
        │
        ▼
  build_multiace_status(...)  ── pure module-level function, no I/O ──► dict
        │
        ▼
  Ace.get_status(eventtime)   ── thin wrapper ──► Klipper status object "multiace"
        │
        ▼  Moonraker objects/query?multiace  /  objects/subscribe
  HelixScreen ACE backend (SP2 consumes units[]; unmodified backend consumes flat slots[])
```

### Components

1. **`self._last_status` cache** (new in-memory state). A `dict[int, dict]` keyed by
   `ace_index`, each value `{"result": <decoded frame>, "recv_ts": <monotonic time>}`.
   Populated via **snapshot-on-active**: whenever a device's `get_status` frame is processed
   (the active device's response path already sets `self._info = response['result']` at
   `ace.py:1264`), SP1 also stores that frame under the device's index with a timestamp.
   **No change to the keepalive/USB drain path** (`_keepalive_tick` continues to discard
   non-active responses) and **no new serial traffic**. This is the only new mutable state
   SP1 introduces.

   *Slot-freshness rationale:* a non-active ACE's slot contents are **static while idle**
   (nothing loads/unloads it), so last-known slot data is *accurate*, not merely stale, until
   the unit is next activated or a human manually swaps a spool on it. `connected` reflects
   frame age. Humidity/temp on a non-active unit may be stale — irrelevant to the two priority
   screens (Loadout Check, Recovery). Closing the freshness gap for a never-activated unit (or
   a manual idle swap) is a **guarded, idle-only refresh** gated on `print_stats.state`,
   reusing the tested `_send_request`/`_process_response` decode path, **deferred to SP3
   (Loadout Check)** — the only consumer that needs it. Mid-print polling of non-active ACEs
   and decoding the keepalive drain are explicitly rejected (USB-path safety; see Non-goals).

2. **`build_multiace_status(...)`** — a module-level **pure function**:

   ```
   build_multiace_status(
       devices,            # list: self._ace_devices
       active_index,       # int:  self._active_device_index
       head_source,        # dict: self._head_source {0..3 -> None | {ace_index, slot, ...}}
       last_status,        # dict: self._last_status {ace_index -> {frame, recv_ts}}
       now,                # float: reactor monotonic time
       firmware_version,   # str:  multiACE version constant
       stale_after_s,      # float: keepalive staleness threshold (default 5.0)
   ) -> dict
   ```

   No Klipper objects, no I/O, no exceptions raised — fully unit-testable without hardware.

3. **`Ace.get_status(self, eventtime)`** — a thin wrapper that snapshots the in-memory
   references and calls `build_multiace_status(...)`, wrapped so it can **never raise**
   (returns a minimal valid frame on any internal error).

### Data contract (the published `multiace` object)

```jsonc
{
  "model": "ACE Pro",
  "firmware": "0.81b",
  "type_name": "multiACE",
  "device_count": 2,
  "active_unit": 0,                 // start-ACE pin (multiACE-specific; no AFC analog)
  "current_tool": 0,                // loaded toolchange state (-1 = none)
  "current_slot": 0,                // global slot index feeding current_tool (-1 = none)
  "total_slots": 8,                 // Σ slot_count over units

  "head_source": [                  // multiACE tool→ACE+slot map (4 heads), pass-through
    {"head": 0, "unit": 0, "slot": 0, "brand": "Polymaker", "type": "PLA",  "color": [12, 160, 44]},
    {"head": 1, "unit": 1, "slot": 2, "brand": "eSUN",      "type": "PETG", "color": [31, 119, 180]},
    {"head": 2, "unit": null, "slot": null},                // empty head
    {"head": 3, "unit": null, "slot": null}
  ],

  "units": [
    {
      "unit_index": 0,
      "name": "ace_0",              // internal match name (stable)
      "display_name": "ACE A",      // pretty name for UI
      "slot_count": 4,
      "first_slot_global_index": 0, // Σ slot_count of prior units
      "connected": true,            // false when last frame stale/missing
      "status": "ready",            // ready | loading | unloading | drying | error
      "environment": {              // ACE-internal sensor; omit / has_humidity=false if absent
        "temperature_c": 24.0,
        "humidity_pct": 28.0,
        "has_humidity": true
      },
      "slots": [
        {
          "slot_index": 0,          // 0-based within this unit
          "global_index": 0,        // first_slot_global_index + slot_index
          "status": "available",    // available | empty | unknown
          "color": [12, 160, 44],   // [r,g,b]; parse_slot_color also accepts "RRGGBB"
          "type": "PLA",            // material (HelixScreen field: material)
          "brand": "Polymaker",
          "rfid": 2,                // ACE frame code: 0=none, 2=recognized (NOT a UID/SKU)
          "mapped_tool": 0          // from head_source; -1 when no head references this slot
        },
        { "slot_index": 1, "global_index": 1, "status": "empty",  "mapped_tool": -1 },
        { "slot_index": 2, "global_index": 2, "status": "available", "type": "ABS",
          "color": [128,128,128], "mapped_tool": -1 },
        { "slot_index": 3, "global_index": 3, "status": "unknown", "mapped_tool": -1 }
      ]
    },
    {
      "unit_index": 1, "name": "ace_1", "display_name": "ACE B",
      "slot_count": 4, "first_slot_global_index": 4, "connected": true, "status": "ready",
      "environment": {"temperature_c": 25.0, "humidity_pct": 31.0, "has_humidity": true},
      "slots": [
        {"slot_index": 0, "global_index": 4, "status": "empty", "mapped_tool": -1},
        {"slot_index": 1, "global_index": 5, "status": "empty", "mapped_tool": -1},
        {"slot_index": 2, "global_index": 6, "status": "available", "type": "PETG",
         "color": [31,119,180], "rfid": 2, "mapped_tool": 1},
        {"slot_index": 3, "global_index": 7, "status": "empty", "mapped_tool": -1}
      ]
    }
  ],

  // ── Backward-compat aggregate: HelixScreen's UNMODIFIED single-unit parser renders this ──
  "slots": [ /* flat concatenation of all units' slots, in global_index order, 0..15 */ ],
  "humidity": 28.0,                 // top-level aggregate (active unit's humidity)
  "status": "ready"                 // top-level aggregate
}
```

#### Field semantics & conventions (must match AFC/CFS)

| Field | Convention | Source |
|---|---|---|
| `units[].first_slot_global_index` | `Σ slot_count` of all prior units (AFC line 1980) | computed |
| `slots[].global_index` | `first_slot_global_index + slot_index` (AFC line 2020) | computed |
| `units[].connected` | canonical per-unit offline flag; `false` ⇒ keep unit, mark slots `unknown` | derived |
| `units[].environment` | `EnvironmentData{temperature_c, humidity_pct, has_humidity}`; CFS does per-unit | `_last_status[i].result` |
| `slots[].mapped_tool` | **multiACE-specific:** from `head_source` only; `-1` otherwise (NOT the AFC `=global_index` default) | `_head_source` |
| `slots[].color` | `[r,g,b]` array (ValgACE form `parse_slot_color` accepts); frame's `color` tuple | `_last_status[i].result` |
| `slots[].rfid` | ACE frame code: `0`=none, `2`=recognized. **Not** a UID/SKU — no unique id exists in the frame; downstream Spoolman/FilamentHub matches on brand+type+color | `_last_status[i].result` |
| `active_unit` | start-ACE pin during a print; **no AFC analog** | `_active_device_index` |
| flat `slots[]`, top `humidity`/`status` | interim render path for HelixScreen's unmodified parser | aggregate |

> **Color format note:** both `head_source[].color` and the ACE frame's per-slot `color` are
> already `[r, g, b]` lists in `ace.py` (`_head_source` entries init `'color': [0, 0, 0]` at
> `ace.py:215`; the frame's `color` is the tuple `rgb2hex(*color)` consumes). The builder
> passes them through as `[r, g, b]`, which is exactly what HelixScreen's `parse_slot_color`
> accepts. No hex-string conversion is involved. (`ACE_HEAD_STATUS` only `str()`-formats the
> list for its human log line; that is display-only, not the stored form.)

**The `mapped_tool` distinction is the one deliberate divergence from AFC.** AFC/Box Turtle
defaults `mapped_tool = global_index` because N lanes = N tools. multiACE has **4 toolheads
fed by 16 slots**, so `mapped_tool` is populated only for slots referenced by `head_source`
(value 0–3); all other slots are `-1`. Defaulting to the AFC identity would falsely imply 16
tools.

---

## Field sourcing (from existing `ace.py` state)

| Contract field | In-memory source | New work |
|---|---|---|
| `model`, `firmware`, `device_count`, `units[].name/display_name` | `_ace_devices`, version const | none |
| `active_unit` | `_active_device_index` | none |
| `head_source[]`, `slots[].mapped_tool` | `_head_source` (invert tool→slot to slot→tool) | inversion only |
| `units[].slots[]` status/color/type/brand/rfid | `_last_status[i]["result"]["slots"]` | **snapshot the frame on-active** |
| `units[].environment` (humidity/temp) | `_last_status[i]["result"]` | **snapshot the frame on-active** |
| `units[].status`, `current_tool`, `current_slot` | derived from swap/active/load state | derivation |
| `units[].connected` | `now - _last_status[i]["recv_ts"] <= stale_after_s` | derivation |
| flat `slots[]`, top `humidity`/`status` | aggregate of `units[]` | computation |

The **snapshot-on-active** write happens at the existing `self._info = response['result']`
assignment (`ace.py:1264`): SP1 adds `self._last_status[idx] = {"result": response['result'],
"recv_ts": self.reactor.monotonic()}` for the device whose frame was just processed. No other
code path or serial traffic is added (see Components §1 and Non-goals).

The **only** new mutable state is `self._last_status`, populated by **snapshot-on-active** at
the existing `self._info = response['result']` assignment (`ace.py:1264`) on the active
device's response path. SP1 adds a single dict assignment there; it does **not** touch the
keepalive (`_keepalive_tick` still discards idle-device replies) and adds no serial traffic.

---

## Reactor safety (Klipper)

- `get_status(eventtime)` performs **pure in-memory assembly — zero serial I/O**, mandatory
  under Klipper's reactor model. It reads `_ace_devices`, `_active_device_index`,
  `_head_source`, and `_last_status` and returns a dict.
- `_last_status` is written on the active device's response-processing path (`ace.py:1264`)
  and read by `get_status`. Both run on the Klipper reactor (single-threaded for callbacks),
  so a plain dict assignment is safe; the builder snapshots references at entry and does not
  mutate shared state.
- `get_status` is **defensive**: any internal error returns a minimal valid frame
  (`{"model": "ACE Pro", "device_count": 0, "units": [], "slots": [], "status": "error"}`)
  rather than raising — `ace.py` is printer-safety-critical and `get_status` is called
  frequently by Moonraker.

## Error / edge handling

- **Stale or missing unit frame:** `connected=false`, unit `status="error"`, all its slots
  `status="unknown"`. The unit is **never dropped** from `units[]` — stable indices prevent
  the on-screen grid from reflowing when an ACE blips (the path→index map is already locked
  after the 20 s startup wait, so indices never drift mid-session).
- **No devices detected:** minimal frame with `device_count: 0`, empty `units`/`slots`.
- **Empty head:** `head_source[h]` with `unit: null, slot: null`; no slot gets that
  `mapped_tool`.
- **Malformed cached frame:** the builder treats unparseable slot fields as `unknown` slot
  status and omits color/type rather than raising.

---

## Testing

multiACE firmware has no existing test harness; SP1 introduces one for the pure builder.

### 1. Unit tests (pytest) — `multiace/tests/test_status_contract.py`

Targets `build_multiace_status(...)` (pure, no Klipper). Cases:

- **occupancy mapping** — loaded slot → `available` + color/type; unloaded → `empty`.
- **global indexing** — unit 1 `first_slot_global_index == 4`; slot global indices are
  `first_slot_global_index + slot_index`; flat `slots[]` equals concatenation of units in
  `global_index` order.
- **mapped_tool from head_source** — only the (≤4) slots in `head_source` carry a tool
  number (0–3); all others are `-1`; an empty head maps no slot.
- **offline unit** — frame older than `stale_after_s` ⇒ `connected=false`, unit
  `status="error"`, slots `unknown`, unit still present at its index.
- **environment** — humidity/temp present ⇒ `environment.has_humidity=true`; absent ⇒ key
  omitted or `has_humidity=false`.
- **color formatting** — frame `color` tuple `(r,g,b)` → `[r,g,b]` list.
- **rfid passthrough** — frame `rfid` int (`0`/`2`) carried verbatim to `slots[].rfid`;
  brand/type carried; no `sku` key emitted.
- **empty device list** — returns minimal frame, no exception.
- **never raises** — malformed `last_status` frame returns a degraded-but-valid dict.

### 2. Live verification (printer, read-only)

```bash
export DAVINCI_U1_HOST=192.168.1.136
curl -s "$DAVINCI_U1_HOST:7125/printer/objects/query?multiace" | jq .
# expect: units[] with correct first_slot_global_index / connected / environment,
#         flat slots[] of length total_slots, mapped_tool sparse from head_source.
```
Safe under the U1 safety protocol (query only; no Klipper restart, no swap perturbation).

### 3. Consumer-shape check

Feed the emitted JSON through a small harness asserting it is a valid ValgACE **superset**
(top-level `model`/`status`/`slots[]`/`humidity` present and well-formed) so HelixScreen's
*current* `parse_ace_object` renders the flat 16-slot view without any C++ change — the
interim win that proves the contract before SP2 begins.

---

## Acceptance criteria

1. Moonraker `objects/query?multiace` returns the documented shape against the live Davinci-U1.
2. `units[]` follows AFC conventions: correct `first_slot_global_index`, `global_index`,
   `slot_count`, `connected`; `environment` per unit.
3. `mapped_tool` is sparse and sourced from `head_source` (not the AFC identity default).
4. Flat `slots[]` aggregate renders on HelixScreen's unmodified parser (interim view).
5. `get_status` performs no serial I/O and never raises; no new serial traffic is introduced.
6. pytest suite for `build_multiace_status` passes (≥ the cases above), runnable without hardware.
7. No `printer.cfg` writes; no change to swap/print behavior.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `get_status` called frequently → overhead | Pure dict build from small in-memory structures; no I/O; O(units×slots)=O(16). |
| Cached frame schema varies across ACE firmware | Builder tolerates missing fields → `unknown`/omitted; never raises. |
| `ace.py` is safety-critical | Additive read-only method; defensive wrapper; no writes; no reactor blocking. |
| Index drift if an ACE re-enumerates | Rely on existing locked path→index map (instinct: locked after 20 s); `connected=false` on stale. |
| Divergence from HelixScreen conventions | Field names/semantics mirror `ams_types.h` + AFC populate code verbatim (this spec). |

## Open questions for SP2 (not blocking SP1)

- Does HelixScreen's ACE backend subscribe by the literal object name `ace`? If so, SP2 reads
  `multiace` (new subscription) or we alias. SP1 publishes `multiace` to avoid colliding with
  any single-ACE driver; SP2 decides the subscription name.
- `current_tool`/`current_slot` exact derivation from multiACE's active-toolchange state —
  refine in SP2 against HelixScreen's `AmsSystemInfo` usage.
