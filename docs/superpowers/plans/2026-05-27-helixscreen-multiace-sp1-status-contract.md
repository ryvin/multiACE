# SP1: multiACE Multi-Unit ACE Status Contract — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Klipper status object `ace` to multiACE that publishes every ACE unit and slot in a HelixScreen-consumable shape.

**Architecture:** A pure, stdlib-only builder module (`ace_status.py`) assembles the status dict from multiACE's in-memory state; `BunnyAce.get_status()` is a thin, never-raising wrapper. Slot data is sourced **snapshot-on-active** from `self._info` (no keepalive/USB-path change). The object publishes as `ace` (the `[ace]` section name) so HelixScreen's existing subscriber reads it.

**Tech Stack:** Python 3 (Klipper extra + stdlib), pytest. Spec: `docs/superpowers/specs/2026-05-27-helixscreen-multiace-sp1-status-contract-design.md`.

**Conventions for every task:** DRY, YAGNI, TDD. Run tests from the repo root with the venv that has pytest. The pure builder has **no** `serial`/Klipper imports so tests import it directly.

---

## Background the implementer must know

- multiACE's main class is `BunnyAce` in `multiace/klipper/extras/ace.py` (`load_config` returns `BunnyAce(config)`); the `[ace]` config section means a `get_status` method publishes the Klipper object **`ace`**.
- `MULTIACE_VERSION = "0.81b"` is defined at `ace.py:14`.
- In-memory state the builder reads:
  - `self._ace_devices` — list of detected devices (its length = device count).
  - `self._active_device_index` — int index of the active ACE.
  - `self._head_source` — dict `{0..3 -> None | {"ace_index": int, "slot": int, "brand": str, "type": str, "color": [r,g,b]}}`.
  - `self._last_status` — **NEW** (this plan) `{ace_index -> {"result": <frame>, "recv_ts": float}}`.
- The per-device `get_status` frame (`ace_protocol_v1.py:107`) has top-level `status`, `temp`, `dryer_status`, and `slots: [{"index", "status", "sku", "type", "rfid", "brand", "color": [r,g,b]}]`. **No `humidity` key.** Slot `status` may be `"empty1"` → map `startswith("empty")` to `empty`.
- `self._info = response['result']` is set at `ace.py:1264` (active device's get_status callback) — the snapshot point.

---

### Task 1: Scaffold `ace_status.py` + test harness + `_slot_status` / `_coerce_color`

**Files:**
- Create: `multiace/klipper/extras/ace_status.py`
- Create: `multiace/tests/conftest.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Create the test import harness**

Create `multiace/tests/conftest.py`:

```python
import os
import sys

# Make the Klipper-free builder importable as `ace_status` without pulling in
# ace.py (which imports pyserial and Klipper-only relative modules).
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "klipper", "extras")
)
```

- [ ] **Step 2: Write the failing test**

Create `multiace/tests/test_ace_status.py`:

```python
import ace_status


def test_slot_status_maps_empty_variants_to_empty():
    assert ace_status._slot_status("empty") == "empty"
    assert ace_status._slot_status("empty1") == "empty"


def test_slot_status_maps_other_to_available():
    assert ace_status._slot_status("ready") == "available"


def test_slot_status_none_is_empty():
    assert ace_status._slot_status(None) == "empty"


def test_coerce_color_clamps_and_ints():
    assert ace_status._coerce_color([12, 160, 44]) == [12, 160, 44]
    assert ace_status._coerce_color([300, -5, 12.9]) == [255, 0, 12]


def test_coerce_color_bad_input_is_black():
    assert ace_status._coerce_color(None) == [0, 0, 0]
    assert ace_status._coerce_color("nope") == [0, 0, 0]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest multiace/tests/test_ace_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ace_status'`.

- [ ] **Step 4: Create the module with the two helpers**

Create `multiace/klipper/extras/ace_status.py`:

```python
# multiACE — pure, Klipper-free builder for the `ace` status object consumed by
# HelixScreen. No serial/Klipper imports so it is unit-testable standalone.
# Spec: docs/superpowers/specs/2026-05-27-helixscreen-multiace-sp1-status-contract-design.md
# License: GPL-3.0

DEFAULT_SLOT_COUNT = 4
DEFAULT_STALE_AFTER_S = 5.0


def _slot_status(frame_status):
    """Map an ACE frame slot status to the contract value.

    The frame uses 'empty'/'empty1' for unoccupied; anything else is occupied.
    """
    if not frame_status:
        return "empty"
    return "empty" if str(frame_status).startswith("empty") else "available"


def _coerce_color(color):
    """Coerce a frame color into a 3-int [r, g, b] list clamped to 0..255."""
    try:
        r, g, b = color
        return [
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        ]
    except (TypeError, ValueError):
        return [0, 0, 0]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest multiace/tests/test_ace_status.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add multiace/klipper/extras/ace_status.py multiace/tests/conftest.py multiace/tests/test_ace_status.py
git commit -m "feat(ace-status): scaffold pure builder module + slot/color helpers"
```

---

### Task 2: `_build_mapped_tool_index` + `_build_head_source_out`

**Files:**
- Modify: `multiace/klipper/extras/ace_status.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Write the failing test**

Append to `multiace/tests/test_ace_status.py`:

```python
def _head_source_fixture():
    return {
        0: {"ace_index": 0, "slot": 0, "brand": "Polymaker", "type": "PLA", "color": [12, 160, 44]},
        1: {"ace_index": 1, "slot": 2, "brand": "eSUN", "type": "PETG", "color": [31, 119, 180]},
        2: None,
        3: None,
    }


def test_mapped_tool_index_inverts_head_source():
    idx = ace_status._build_mapped_tool_index(_head_source_fixture())
    assert idx == {(0, 0): 0, (1, 2): 1}


def test_mapped_tool_index_empty_when_no_sources():
    assert ace_status._build_mapped_tool_index({0: None, 1: None, 2: None, 3: None}) == {}
    assert ace_status._build_mapped_tool_index(None) == {}


def test_head_source_out_emits_four_heads_with_nulls():
    out = ace_status._build_head_source_out(_head_source_fixture())
    assert len(out) == 4
    assert out[0] == {"head": 0, "unit": 0, "slot": 0, "brand": "Polymaker",
                      "type": "PLA", "color": [12, 160, 44]}
    assert out[2] == {"head": 2, "unit": None, "slot": None}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest multiace/tests/test_ace_status.py -k "mapped_tool or head_source_out" -v`
Expected: FAIL with `AttributeError: module 'ace_status' has no attribute '_build_mapped_tool_index'`.

- [ ] **Step 3: Implement the two functions**

Append to `multiace/klipper/extras/ace_status.py`:

```python
def _build_mapped_tool_index(head_source):
    """Invert head_source (head -> {ace_index, slot}) into {(ace_index, slot): head}."""
    rev = {}
    if not head_source:
        return rev
    for head, source in head_source.items():
        if (source and source.get("ace_index") is not None
                and source.get("slot") is not None):
            rev[(int(source["ace_index"]), int(source["slot"]))] = int(head)
    return rev


def _build_head_source_out(head_source):
    """Emit exactly four head entries; empty heads carry unit/slot = None."""
    out = []
    for head in range(4):
        source = head_source.get(head) if head_source else None
        if (source and source.get("ace_index") is not None
                and source.get("slot") is not None):
            entry = {"head": head, "unit": int(source["ace_index"]),
                     "slot": int(source["slot"])}
            for key in ("brand", "type"):
                if source.get(key):
                    entry[key] = source[key]
            if "color" in source:
                entry["color"] = _coerce_color(source.get("color"))
            out.append(entry)
        else:
            out.append({"head": head, "unit": None, "slot": None})
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest multiace/tests/test_ace_status.py -k "mapped_tool or head_source_out" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace_status.py multiace/tests/test_ace_status.py
git commit -m "feat(ace-status): head_source inversion + head_source output"
```

---

### Task 3: `_build_slot` + `_build_environment`

**Files:**
- Modify: `multiace/klipper/extras/ace_status.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Write the failing test**

Append to `multiace/tests/test_ace_status.py`:

```python
def test_build_slot_full_frame():
    sf = {"index": 0, "status": "ready", "sku": "PM-PLA-GRN", "type": "PLA",
          "rfid": 2, "brand": "Polymaker", "color": [12, 160, 44]}
    slot = ace_status._build_slot(0, 0, sf, mapped_tool=0)
    assert slot == {"slot_index": 0, "global_index": 0, "status": "available",
                    "mapped_tool": 0, "color": [12, 160, 44], "type": "PLA",
                    "brand": "Polymaker", "sku": "PM-PLA-GRN", "rfid": 2}


def test_build_slot_empty_frame_omits_optional_fields():
    sf = {"index": 1, "status": "empty1", "sku": "", "type": "", "rfid": 0,
          "brand": "", "color": [0, 0, 0]}
    slot = ace_status._build_slot(1, 5, sf, mapped_tool=-1)
    assert slot["slot_index"] == 1
    assert slot["global_index"] == 5
    assert slot["status"] == "empty"
    assert slot["mapped_tool"] == -1
    assert slot["color"] == [0, 0, 0]
    assert slot["rfid"] == 0
    assert "type" not in slot      # empty string omitted
    assert "brand" not in slot
    assert "sku" not in slot


def test_build_environment_temp_only_no_humidity():
    env = ace_status._build_environment({"temp": 24, "status": "ready"})
    assert env == {"temperature_c": 24.0, "humidity_pct": 0.0, "has_humidity": False}


def test_build_environment_uses_humidity_when_present():
    env = ace_status._build_environment({"temp": 25, "humidity": 31})
    assert env == {"temperature_c": 25.0, "humidity_pct": 31.0, "has_humidity": True}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest multiace/tests/test_ace_status.py -k "build_slot or build_environment" -v`
Expected: FAIL with `AttributeError: module 'ace_status' has no attribute '_build_slot'`.

- [ ] **Step 3: Implement the two functions**

Append to `multiace/klipper/extras/ace_status.py`:

```python
def _build_slot(slot_index, global_index, slot_frame, mapped_tool):
    """Build one contract slot dict from a frame slot dict.

    Optional string fields (type/brand/sku) are omitted when empty. color and
    rfid are emitted whenever present in the frame.
    """
    sf = slot_frame if isinstance(slot_frame, dict) else {}
    slot = {
        "slot_index": slot_index,
        "global_index": global_index,
        "status": _slot_status(sf.get("status")),
        "mapped_tool": mapped_tool,
    }
    if "color" in sf:
        slot["color"] = _coerce_color(sf.get("color"))
    if sf.get("type"):
        slot["type"] = sf["type"]
    if sf.get("brand"):
        slot["brand"] = sf["brand"]
    if sf.get("sku"):
        slot["sku"] = sf["sku"]
    if "rfid" in sf:
        try:
            slot["rfid"] = int(sf.get("rfid"))
        except (TypeError, ValueError):
            pass
    return slot


def _build_environment(frame):
    """EnvironmentData from the frame: temperature_c from 'temp'; humidity only
    if the frame carries a 'humidity' key (forward-compat — the v1 frame has none).
    """
    try:
        temp = float(frame.get("temp", 0) or 0)
    except (TypeError, ValueError):
        temp = 0.0
    env = {"temperature_c": temp, "humidity_pct": 0.0, "has_humidity": False}
    if "humidity" in frame:
        try:
            env["humidity_pct"] = float(frame.get("humidity") or 0)
            env["has_humidity"] = True
        except (TypeError, ValueError):
            pass
    return env
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest multiace/tests/test_ace_status.py -k "build_slot or build_environment" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace_status.py multiace/tests/test_ace_status.py
git commit -m "feat(ace-status): per-slot + environment builders"
```

---

### Task 4: `_build_unit` (connected, offline/stale, global indexing)

**Files:**
- Modify: `multiace/klipper/extras/ace_status.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Write the failing test**

Append to `multiace/tests/test_ace_status.py`:

```python
def _frame_fixture():
    return {
        "status": "ready",
        "temp": 24,
        "slots": [
            {"index": 0, "status": "ready", "sku": "PM-PLA-GRN", "type": "PLA",
             "rfid": 2, "brand": "Polymaker", "color": [12, 160, 44]},
            {"index": 1, "status": "empty1", "sku": "", "type": "", "rfid": 0,
             "brand": "", "color": [0, 0, 0]},
            {"index": 2, "status": "empty1", "sku": "", "type": "", "rfid": 0,
             "brand": "", "color": [0, 0, 0]},
            {"index": 3, "status": "empty1", "sku": "", "type": "", "rfid": 0,
             "brand": "", "color": [0, 0, 0]},
        ],
    }


def test_build_unit_connected_indices_and_names():
    entry = {"result": _frame_fixture(), "recv_ts": 99.0}
    unit = ace_status._build_unit(
        idx=1, entry=entry, now=100.0, stale_after_s=5.0,
        first_global=4, mapped_index={(1, 0): 1})
    assert unit["unit_index"] == 1
    assert unit["name"] == "ace_1"
    assert unit["display_name"] == "ACE B"
    assert unit["slot_count"] == 4
    assert unit["first_slot_global_index"] == 4
    assert unit["connected"] is True
    assert unit["status"] == "ready"
    assert [s["global_index"] for s in unit["slots"]] == [4, 5, 6, 7]
    assert unit["slots"][0]["mapped_tool"] == 1
    assert unit["slots"][1]["mapped_tool"] == -1


def test_build_unit_offline_when_stale():
    entry = {"result": _frame_fixture(), "recv_ts": 90.0}  # 10s old
    unit = ace_status._build_unit(
        idx=0, entry=entry, now=100.0, stale_after_s=5.0,
        first_global=0, mapped_index={})
    assert unit["connected"] is False
    assert unit["status"] == "error"
    assert unit["slot_count"] == 4
    assert all(s["status"] == "unknown" for s in unit["slots"])
    assert [s["global_index"] for s in unit["slots"]] == [0, 1, 2, 3]


def test_build_unit_offline_when_missing_entry():
    unit = ace_status._build_unit(
        idx=2, entry=None, now=100.0, stale_after_s=5.0,
        first_global=8, mapped_index={})
    assert unit["connected"] is False
    assert unit["display_name"] == "ACE C"
    assert [s["global_index"] for s in unit["slots"]] == [8, 9, 10, 11]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest multiace/tests/test_ace_status.py -k "build_unit" -v`
Expected: FAIL with `AttributeError: module 'ace_status' has no attribute '_build_unit'`.

- [ ] **Step 3: Implement `_build_unit`**

Append to `multiace/klipper/extras/ace_status.py`:

```python
def _unit_display_name(idx):
    return "ACE %s" % chr(ord("A") + idx) if idx < 26 else "ACE %d" % idx


def _offline_unit(idx, first_global, mapped_index, slot_count):
    slots = []
    for s in range(slot_count):
        slots.append({
            "slot_index": s,
            "global_index": first_global + s,
            "status": "unknown",
            "mapped_tool": mapped_index.get((idx, s), -1),
        })
    return {
        "unit_index": idx, "name": "ace_%d" % idx,
        "display_name": _unit_display_name(idx),
        "slot_count": slot_count, "first_slot_global_index": first_global,
        "connected": False, "status": "error",
        "environment": {"temperature_c": 0.0, "humidity_pct": 0.0, "has_humidity": False},
        "slots": slots,
    }


def _build_unit(idx, entry, now, stale_after_s, first_global, mapped_index,
                default_slot_count=DEFAULT_SLOT_COUNT):
    """Build one unit dict. Stale/missing frames yield a connected=False unit
    with `unknown` slots (never dropped, so on-screen indices stay stable)."""
    frame = None
    if isinstance(entry, dict):
        recv_ts = entry.get("recv_ts")
        candidate = entry.get("result")
        if (recv_ts is not None and isinstance(candidate, dict)
                and (now - recv_ts) <= stale_after_s):
            frame = candidate
    if frame is None:
        return _offline_unit(idx, first_global, mapped_index, default_slot_count)

    frame_slots = frame.get("slots") or []
    slot_count = len(frame_slots) if frame_slots else default_slot_count
    slots = []
    for s in range(slot_count):
        sf = frame_slots[s] if s < len(frame_slots) else {}
        slots.append(_build_slot(s, first_global + s, sf, mapped_index.get((idx, s), -1)))
    return {
        "unit_index": idx, "name": "ace_%d" % idx,
        "display_name": _unit_display_name(idx),
        "slot_count": slot_count, "first_slot_global_index": first_global,
        "connected": True, "status": frame.get("status") or "ready",
        "environment": _build_environment(frame),
        "slots": slots,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest multiace/tests/test_ace_status.py -k "build_unit" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace_status.py multiace/tests/test_ace_status.py
git commit -m "feat(ace-status): per-unit builder with offline/stale handling"
```

---

### Task 5: `_derive_current` + `build_multiace_status` (assembly, empty, never-raise)

**Files:**
- Modify: `multiace/klipper/extras/ace_status.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Write the failing test**

Append to `multiace/tests/test_ace_status.py`:

```python
def _two_device_last_status():
    return {
        0: {"result": _frame_fixture(), "recv_ts": 99.0},
        1: {"result": _frame_fixture(), "recv_ts": 99.0},
    }


def test_build_status_assembles_units_and_flat_slots():
    out = ace_status.build_multiace_status(
        devices=["/dev/ttyACM0", "/dev/ttyACM1"],
        active_index=0,
        head_source=_head_source_fixture(),
        last_status=_two_device_last_status(),
        now=100.0,
        firmware_version="0.81b",
    )
    assert out["device_count"] == 2
    assert out["firmware"] == "0.81b"
    assert out["total_slots"] == 8
    assert len(out["units"]) == 2
    assert out["units"][1]["first_slot_global_index"] == 4
    # flat slots == concatenation of unit slots in global order
    assert [s["global_index"] for s in out["slots"]] == list(range(8))
    # mapped_tool sparse from head_source: (0,0)->0 and (1,2)->1 only
    assert out["slots"][0]["mapped_tool"] == 0
    assert out["slots"][6]["mapped_tool"] == 1
    assert sum(1 for s in out["slots"] if s["mapped_tool"] != -1) == 2


def test_build_status_current_tool_from_active_unit():
    out = ace_status.build_multiace_status(
        devices=["a", "b"], active_index=0, head_source=_head_source_fixture(),
        last_status=_two_device_last_status(), now=100.0, firmware_version="0.81b")
    assert out["active_unit"] == 0
    assert out["current_tool"] == 0     # head 0 sourced from active unit 0
    assert out["current_slot"] == 0


def test_build_status_empty_device_list_minimal_frame():
    out = ace_status.build_multiace_status(
        devices=[], active_index=-1, head_source=None, last_status={},
        now=100.0, firmware_version="0.81b")
    assert out["device_count"] == 0
    assert out["units"] == []
    assert out["slots"] == []
    assert out["status"] == "error"


def test_build_status_never_raises_on_malformed_frame():
    bad = {0: {"result": {"slots": "not-a-list"}, "recv_ts": 99.0}}
    out = ace_status.build_multiace_status(
        devices=["a"], active_index=0, head_source=None, last_status=bad,
        now=100.0, firmware_version="0.81b")
    # degraded but valid: a unit exists, no exception
    assert out["device_count"] == 1
    assert len(out["units"]) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest multiace/tests/test_ace_status.py -k "build_status" -v`
Expected: FAIL with `AttributeError: module 'ace_status' has no attribute 'build_multiace_status'`.

- [ ] **Step 3: Implement `_derive_current` + `build_multiace_status`**

Append to `multiace/klipper/extras/ace_status.py`:

```python
def _minimal_frame(firmware_version, status="error"):
    return {
        "model": "ACE Pro", "firmware": firmware_version, "type_name": "multiACE",
        "device_count": 0, "active_unit": -1, "current_tool": -1, "current_slot": -1,
        "total_slots": 0, "head_source": [], "units": [], "slots": [],
        "humidity": 0.0, "status": status,
    }


def _derive_current(head_source, active_index, units):
    """Best-effort current tool/slot: lowest head sourced from the active unit.
    Exact semantics deferred to SP2 (see spec open questions)."""
    if not head_source:
        return -1, -1
    for head in range(4):
        source = head_source.get(head)
        if (source and source.get("ace_index") == active_index
                and source.get("slot") is not None):
            unit_idx = int(source["ace_index"])
            if 0 <= unit_idx < len(units):
                return head, units[unit_idx]["first_slot_global_index"] + int(source["slot"])
    return -1, -1


def build_multiace_status(devices, active_index, head_source, last_status, now,
                          firmware_version, stale_after_s=DEFAULT_STALE_AFTER_S):
    """Assemble the `ace` Klipper status object. Pure; never raises."""
    try:
        device_count = len(devices) if devices else 0
        if device_count == 0:
            return _minimal_frame(firmware_version)

        mapped_index = _build_mapped_tool_index(head_source)
        last_status = last_status or {}
        units = []
        flat_slots = []
        running_global = 0
        for i in range(device_count):
            unit = _build_unit(i, last_status.get(i), now, stale_after_s,
                               running_global, mapped_index)
            running_global += unit["slot_count"]
            units.append(unit)
            flat_slots.extend(unit["slots"])

        active_unit = active_index if 0 <= active_index < device_count else -1
        current_tool, current_slot = _derive_current(head_source, active_index, units)
        top_status = units[active_unit]["status"] if active_unit >= 0 else "ready"

        return {
            "model": "ACE Pro", "firmware": firmware_version, "type_name": "multiACE",
            "device_count": device_count, "active_unit": active_unit,
            "current_tool": current_tool, "current_slot": current_slot,
            "total_slots": running_global,
            "head_source": _build_head_source_out(head_source),
            "units": units, "slots": flat_slots,
            "humidity": 0.0, "status": top_status,
        }
    except Exception:
        return _minimal_frame(firmware_version)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest multiace/tests/test_ace_status.py -v`
Expected: PASS (all tasks' tests; ~20 passed).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace_status.py multiace/tests/test_ace_status.py
git commit -m "feat(ace-status): build_multiace_status assembly + current derivation"
```

---

### Task 6: Wire `_last_status` init + snapshot-on-active into `ace.py`

**Files:**
- Modify: `multiace/klipper/extras/ace.py` (top import; `__init__` near line 136; line 1264)

This task touches printer firmware and is verified live in Task 8 (no pytest — `ace.py` is not importable without Klipper).

- [ ] **Step 1: Add the builder import**

At the top of `ace.py`, alongside the existing `from .ace_protocol_v1 import AceProtocolV1` (line 12), add:

```python
from .ace_status import build_multiace_status
```

- [ ] **Step 2: Initialize the cache**

In `BunnyAce.__init__`, next to `self._callback_map = {}` (line 136), add:

```python
        self._last_status = {}  # ace_index -> {"result": frame, "recv_ts": float} (HelixScreen status)
```

- [ ] **Step 3: Snapshot on active-device frame**

At `ace.py:1264`, where the active device's get_status callback sets `self._info`:

```python
                    self._info = response['result']
```

change it to also record the snapshot keyed by the active device index:

```python
                    self._info = response['result']
                    self._last_status[self._active_device_index] = {
                        "result": response['result'],
                        "recv_ts": self.reactor.monotonic(),
                    }
```

- [ ] **Step 4: Sanity-check the edit compiles (syntax only)**

Run: `python -c "import ast; ast.parse(open('multiace/klipper/extras/ace.py').read()); print('ok')"`
Expected: `ok` (syntax valid; this does not import Klipper).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace.py
git commit -m "feat(ace-status): snapshot active-device frame into _last_status (ace.py:1264)"
```

---

### Task 7: Add `BunnyAce.get_status()`

**Files:**
- Modify: `multiace/klipper/extras/ace.py` (add a method to `BunnyAce`)

- [ ] **Step 1: Add the method**

Add this method to the `BunnyAce` class (place it near `cmd_ACE_HEAD_STATUS`, around line 2391, so related status code lives together):

```python
    def get_status(self, eventtime=None):
        """Publish the `ace` Klipper status object consumed by HelixScreen.

        Pure in-memory assembly (no serial I/O); never raises (build_multiace_status
        returns a minimal frame on any internal error). See SP1 spec.
        """
        now = self.reactor.monotonic() if eventtime is None else eventtime
        return build_multiace_status(
            devices=self._ace_devices,
            active_index=self._active_device_index,
            head_source=self._head_source,
            last_status=self._last_status,
            now=now,
            firmware_version=MULTIACE_VERSION,
        )
```

- [ ] **Step 2: Sanity-check the edit compiles**

Run: `python -c "import ast; ast.parse(open('multiace/klipper/extras/ace.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add multiace/klipper/extras/ace.py
git commit -m "feat(ace-status): BunnyAce.get_status publishes the ace status object"
```

---

### Task 8: Live verification on the printer (read-only) + consumer-shape check

**Files:** none (deployment + verification). Follow the Snapmaker U1 safety protocol — confirm `print_stats.state` is safe before any Klipper restart.

- [ ] **Step 1: Pre-flight — confirm the printer is not printing**

```bash
export DAVINCI_U1_HOST=192.168.1.136
curl -s "$DAVINCI_U1_HOST:7125/printer/objects/query?print_stats" \
  | python -c "import sys,json; print(json.load(sys.stdin)['result']['status']['print_stats']['state'])"
```
Expected: `standby`/`complete`/`cancelled`. If `printing`/`paused`, STOP — do not deploy.

- [ ] **Step 2: Deploy the two files and restart Klipper (only if safe)**

```bash
scp multiace/klipper/extras/ace_status.py root@$DAVINCI_U1_HOST:/home/lava/klipper/klippy/extras/ace_status.py
scp multiace/klipper/extras/ace.py        root@$DAVINCI_U1_HOST:/home/lava/klipper/klippy/extras/ace.py
ssh root@$DAVINCI_U1_HOST "systemctl restart klipper"
```

- [ ] **Step 3: Verify the object publishes with the documented shape**

```bash
curl -s "$DAVINCI_U1_HOST:7125/printer/objects/query?ace" \
  | python -m json.tool
```
Expected: `result.status.ace` contains `units[]` with `first_slot_global_index` (0, 4, …),
`connected`, `environment`; a flat `slots[]` of length `total_slots`; `mapped_tool` non-`-1`
only on slots referenced by `head_source`.

- [ ] **Step 4: Confirm the live humidity question**

Inspect the live `ace` output: if any `units[].slots` source frame contained a `humidity`
key, `environment.has_humidity` is `true`. Record the finding in the spec's "Open questions"
(whether real firmware emits humidity). No code change needed either way.

- [ ] **Step 5: Consumer-shape assertion (ValgACE superset)**

```bash
curl -s "$DAVINCI_U1_HOST:7125/printer/objects/query?ace" | python -c "
import sys, json
ace = json.load(sys.stdin)['result']['status']['ace']
assert isinstance(ace.get('model'), str)
assert isinstance(ace.get('status'), str)
assert isinstance(ace.get('slots'), list)
assert all('status' in s for s in ace['slots'])
print('ValgACE superset OK; slots:', len(ace['slots']))
"
```
Expected: `ValgACE superset OK; slots: <N>`. This proves HelixScreen's unmodified
`parse_ace_object` can render the flat view before SP2.

- [ ] **Step 6: Watch logs for regressions**

```bash
ssh root@$DAVINCI_U1_HOST "tail -n 60 /home/lava/printer_data/logs/klippy.log"
```
Expected: no tracebacks referencing `get_status`/`ace_status`/`_last_status`; ACE detection and keepalive (`KEEPALIVE_*`) behave as before.

---

## Self-review

**Spec coverage:**
- Object name `ace` via `get_status` → Tasks 6–7, verified Task 8. ✅
- Snapshot-on-active sourcing (`ace.py:1264`, no keepalive change) → Task 6. ✅
- `units[]` AFC conventions (`first_slot_global_index`, `global_index`, `connected`, `environment`) → Tasks 3–4, asserted Tasks 4–5/8. ✅
- `mapped_tool` sparse from `head_source` → Tasks 2/5, asserted Task 5. ✅
- Flat `slots[]` aggregate (interim render) → Task 5, asserted Task 8 Step 5. ✅
- `sku` (string) + `rfid` (int); `color` `[r,g,b]`; `status` `startswith("empty")` → Tasks 1/3. ✅
- `environment` temp-only/`has_humidity=false`; live humidity check → Tasks 3/8. ✅
- `get_status` never raises; minimal frame on error → Task 5 (`build_multiace_status` try/except) + Task 7 wrapper. ✅
- pytest builder suite, no hardware → Tasks 1–5. ✅
- No `printer.cfg` writes, no new serial traffic, no USB-path change → Tasks 6–7 (additive read-only). ✅

**Placeholder scan:** none — every code step contains complete code; verification steps contain exact commands.

**Type consistency:** `build_multiace_status(devices, active_index, head_source, last_status, now, firmware_version, stale_after_s)` is identical across Tasks 5/7. `_build_unit(idx, entry, now, stale_after_s, first_global, mapped_index, default_slot_count)` matches between Task 4 def and Task 5 call. `_last_status` entry shape `{"result", "recv_ts"}` matches between Task 6 (writer) and Tasks 4/5 (reader). Slot keys (`slot_index`, `global_index`, `status`, `mapped_tool`, `color`, `type`, `brand`, `sku`, `rfid`) consistent across Tasks 3–5 and the spec.

---

## Execution handoff

This plan produces working, tested software (SP1) on its own. Recommended execution:
**superpowers:subagent-driven-development** — fresh subagent per task, spec-then-quality review between tasks. Tasks 1–5 are pure-Python TDD (cheap model fine); Tasks 6–7 are surgical firmware edits; Task 8 is live hardware verification under the U1 safety protocol (do not restart Klipper while printing).
