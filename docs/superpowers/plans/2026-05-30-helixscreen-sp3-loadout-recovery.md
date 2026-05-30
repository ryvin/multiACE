# HelixScreen SP3 — Loadout, Slot Actions, Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the HelixScreen multi-unit ACE UI actionable: smart slot tap (3-case load), Loadout Check panel (live mapping), Recovery panel (drift surfacing + targeted fixes). One additive contract change on the SP1 `ace` status object: `head_source[i].sensor`.

**Architecture:** Python (firmware) extends `ace_status.py` to embed per-head sensor truth. C++ (HelixScreen) parses it, runs the smart-swap dispatcher in `ams_backend_ace.cpp` calling existing multiACE Gcode (`ACE_LOAD_HEAD`, `ACE_UNLOAD_HEAD`, `ACEC__Unload_T<n>`, `ACE_CLEAR_HEADS`, `ACE_MARK_HEAD_LOADED`), and presents two LVGL panels (Loadout Check, Recovery) using the existing `Modal`/`AmsContextMenu`/`OverlayBase` primitives. No new firmware Gcode.

**Tech Stack:** Python 3.11 (multiACE firmware), C++17 + LVGL + Catch2 (HelixScreen), Klipper save_variables, pyserial, Moonraker HTTP.

**Spec:** `docs/superpowers/specs/2026-05-30-helixscreen-sp3-loadout-recovery-design.md`

---

## File Structure

### Firmware (Python, `multiace/`)

- **Modify** `multiace/klipper/extras/ace_status.py` — add `sensors_per_head` param to `build_multiace_status`; emit `sensor` field on every `head_source[i]`.
- **Modify** `multiace/klipper/extras/ace.py` `get_status` method (currently ~lines 2957-2992) — lift the existing `sensors = {...}` builder (currently at line 2894 inside the audit path) into `get_status`, pass to the SP1 builder.
- **Modify** `multiace/tests/test_ace_status.py` — add 3 tests for the sensor field (loaded entries, null entries, backward-compat with `sensors_per_head=None`).

### HelixScreen (C++, `/mnt/e/Code/helixscreen/`)

- **Modify** `include/ams_backend_ace.h` — extend the head-source struct with `bool sensor{false}`; declare `request_slot_action`, `classify_loadout_row`, `dispatch_recovery_action`; add `LoadoutRowState` and `RecoveryAction` enums.
- **Modify** `src/printer/ams_backend_ace.cpp` — parse `sensor` field in `parse_units_array`/`parse_ace_object` (additive, default false); implement `request_slot_action` (3-case dispatcher); implement `classify_loadout_row`; implement `dispatch_recovery_action`.
- **Modify** `tests/unit/test_ams_backend_ace.cpp` — 3 new TEST_CASE blocks (sensor parse, classify, dispatcher 3 branches + head-picker fallback + leg-2 sequencer).
- **Modify** `include/moonraker_api_mock.h` — add `std::vector<std::string> sent_gcode_scripts_`, mutex, `get_sent_gcode_scripts()` getter; capture in the overridden `gcode_script(...)`.
- **Create** `include/ams_loadout_check_modal.h` + `src/printer/ams_loadout_check_modal.cpp` — LVGL Modal subclass: 4-row Loadout Check table with symbols.
- **Create** `include/ams_recovery_modal.h` + `src/printer/ams_recovery_modal.cpp` — LVGL Modal subclass: one row per drift, with action buttons and confirm dialogs.
- **Modify** the multi-unit slot tile tap handler in HelixScreen's existing UI panel (find in SP2 unit grid code) to route to `request_slot_action` under multi-unit instead of being neutered.

### Deploy / verification

- Davinci-U1 paths used by deploy: `/home/lava/klipper/klippy/extras/ace_status.py`, `/home/lava/klipper/klippy/extras/ace.py`. HelixScreen binary deploy is SP4-scope; SP3 verification runs the unit tests in Docker container `helixbuild` per the project memory.

---

## Task 1: Extend `ace_status.build_multiace_status` to embed per-head sensor

**Files:**
- Modify: `multiace/klipper/extras/ace_status.py`
- Test: `multiace/tests/test_ace_status.py`

- [ ] **Step 1: Write 3 failing tests**

```python
# Append to multiace/tests/test_ace_status.py

def test_head_source_out_includes_sensor_when_provided():
    out = ace_status._build_head_source_out(
        _head_source_fixture(), sensors_per_head={0: True, 1: False, 2: False, 3: False})
    assert out[0]["sensor"] is True
    assert out[1]["sensor"] is False
    assert out[2] == {"head": 2, "unit": None, "slot": None, "sensor": False}

def test_head_source_out_defaults_sensor_false_when_omitted():
    out = ace_status._build_head_source_out(_head_source_fixture())
    assert all(entry["sensor"] is False for entry in out)

def test_build_status_passes_sensors_through_to_head_source():
    now = 100.0
    last_status = {0: {"recv_ts": now, "result": {"slots": [], "temp": 21}}}
    head_source = {0: {"ace_index": 0, "slot": 0, "brand": "x", "type": "PLA", "color": [1, 2, 3]}}
    sensors = {0: True, 1: False, 2: False, 3: False}
    out = ace_status.build_multiace_status(
        devices=["/dev/x"], active_index=0, head_source=head_source,
        last_status=last_status, now=now, firmware_version="0.81b",
        sensors_per_head=sensors)
    assert out["head_source"][0]["sensor"] is True
    assert out["head_source"][1]["sensor"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `multiace/`: `python3 -m pytest tests/test_ace_status.py -k "sensor" -v`
Expected: 3 FAIL with `KeyError: 'sensor'` or `TypeError: unexpected keyword argument 'sensors_per_head'`.

- [ ] **Step 3: Add `sensors_per_head` to `_build_head_source_out`**

```python
# Replace the existing _build_head_source_out in multiace/klipper/extras/ace_status.py
def _build_head_source_out(head_source, sensors_per_head=None):
    """Emit exactly four head entries; empty heads carry unit/slot = None.
    Every entry carries `sensor: bool` (SP3) — defaults False when not provided."""
    sensors = sensors_per_head or {}
    out = []
    for head in range(DEFAULT_HEAD_COUNT):
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
        else:
            entry = {"head": head, "unit": None, "slot": None}
        entry["sensor"] = bool(sensors.get(head, False))
        out.append(entry)
    return out
```

- [ ] **Step 4: Plumb `sensors_per_head` into `build_multiace_status`**

```python
# Update the signature and the one call site in multiace/klipper/extras/ace_status.py
def build_multiace_status(devices, active_index, head_source, last_status, now,
                          firmware_version, stale_after_s=DEFAULT_STALE_AFTER_S,
                          sensors_per_head=None):
    # everything unchanged until the return dict's "head_source" key:
            "head_source": _build_head_source_out(head_source, sensors_per_head),
    # rest unchanged
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ace_status.py -v`
Expected: all PASS (existing 21 + 3 new = 24).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/sp3-sensor-on-head-source
git add multiace/klipper/extras/ace_status.py multiace/tests/test_ace_status.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(sp1): embed per-head sensor truth on head_source entries

SP3 contract extension: every head_source[i] now carries a 'sensor'
field reflecting the live filament-at-gate reading. Additive — defaults
false when not provided so existing SP2 consumers keep parsing.

See docs/superpowers/specs/2026-05-30-helixscreen-sp3-loadout-recovery-design.md"
```

---

## Task 2: Wire `BunnyAce.get_status` to pass the sensors dict to the SP1 builder

**Files:**
- Modify: `multiace/klipper/extras/ace.py` `get_status` method (~lines 2957-2992)

The audit-log path already builds the right `sensors` dict at line 2894-2898 via `printer.lookup_object('filament_motion_sensor e%d_filament' % h)`. Lift that pattern into `get_status` and pass to `build_multiace_status`.

- [ ] **Step 1: Read current `get_status` to confirm the call shape**

Run: `sed -n '2957,2992p' multiace/klipper/extras/ace.py`
Expected: see the existing `build_multiace_status(devices=..., active_index=..., head_source=..., last_status=..., now=..., firmware_version=...)` call — no `sensors_per_head` arg yet.

- [ ] **Step 2: Edit `get_status` to build sensors and pass to the builder**

```python
# In multiace/klipper/extras/ace.py — inside def get_status(self, eventtime=None):
# After the existing 'status = {...}' block (around line 2969) and before the
# 'try: ... multi = build_multiace_status(...)' block (around line 2973):

        sensors_per_head = {}
        for h in range(4):
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % h, None)
            if sensor is None:
                sensors_per_head[h] = False
            else:
                try:
                    sensors_per_head[h] = bool(
                        sensor.get_status(0).get('filament_detected', False))
                except Exception:
                    sensors_per_head[h] = False

# Then in the build_multiace_status(...) call, add one kwarg:
            multi = build_multiace_status(
                devices=self._ace_devices,
                active_index=self._active_device_index,
                head_source=self._head_source,
                last_status=self._last_status,
                now=now,
                firmware_version=MULTIACE_VERSION,
                sensors_per_head=sensors_per_head,
            )
```

- [ ] **Step 3: Verify the file still parses**

Run: `python3 -c "import ast; ast.parse(open('multiace/klipper/extras/ace.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Run the full multiACE test suite**

Run: `cd multiace && python3 -m pytest tests/ -v 2>&1 | tail -n 5`
Expected: all green (24 from Task 1 + 8 keepalive = 32).

- [ ] **Step 5: Commit**

```bash
git add multiace/klipper/extras/ace.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(sp1): pass per-head sensor truth to multiace status builder

Reuses the filament_motion_sensor lookup already proven in the
audit-log path (ace.py:2894), now also feeding it to
build_multiace_status so HelixScreen sees head_source[i].sensor.

For SP3 Loadout Check / Recovery panels."
```

---

## Task 3: Deploy SP1 extension to Davinci-U1, verify head_source[].sensor in live JSON

**Files:**
- Deploy: `multiace/klipper/extras/ace_status.py`, `multiace/klipper/extras/ace.py`

- [ ] **Step 1: Confirm printer state is safe**

Run: `curl -s --max-time 5 "http://${DAVINCI_U1_HOST:-192.168.1.136}:7125/printer/objects/query?print_stats" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['status']['print_stats']['state'])"`
Expected: `standby`, `complete`, `cancelled`, or `error`. Abort task if `printing` or `paused`.

- [ ] **Step 2: SCP both files to the printer**

```bash
printf 'snapmaker\n' | python3 /tmp/scp_put.py multiace/klipper/extras/ace_status.py /home/lava/klipper/klippy/extras/ace_status.py
printf 'snapmaker\n' | python3 /tmp/scp_put.py multiace/klipper/extras/ace.py /home/lava/klipper/klippy/extras/ace.py
```

Expected: two `OK <local> -> <host>:<remote>` lines.

- [ ] **Step 3: chown to lava and clear stale pyc**

```bash
printf 'snapmaker\n' | python3 /tmp/ssh_run.py 'chown lava:lava /home/lava/klipper/klippy/extras/ace_status.py /home/lava/klipper/klippy/extras/ace.py && rm -f /home/lava/klipper/klippy/extras/__pycache__/ace.cpython-*.pyc /home/lava/klipper/klippy/extras/__pycache__/ace_status.cpython-*.pyc'
```

Expected: no output (success).

- [ ] **Step 4: Restart Klipper**

```bash
printf 'snapmaker\n' | python3 /tmp/ssh_run.py '/etc/init.d/S60klipper restart 2>&1 | tail -n 5'
```

Expected: `Stopping klipper... Starting klipper... All passed Starting klipper with normal config...`

- [ ] **Step 5: Wait ~12s for Klipper ready, then query the ace object**

```bash
sleep 12 && curl -s "http://${DAVINCI_U1_HOST}:7125/printer/objects/query?ace" | python3 -c "import sys,json;d=json.load(sys.stdin);hs=d['result']['status']['ace']['head_source'];import pprint;pprint.pp(hs)"
```

Expected: a list of 4 entries, each with at least `head`, `unit`, `slot`, and the new `sensor` (bool).

- [ ] **Step 6: Spot-check against the legacy sensor source**

```bash
curl -s "http://${DAVINCI_U1_HOST}/multiace/api/state" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('sensors'))"
curl -s "http://${DAVINCI_U1_HOST}:7125/printer/objects/query?ace" | python3 -c "import sys,json;d=json.load(sys.stdin);hs=d['result']['status']['ace']['head_source'];print({e['head']:e['sensor'] for e in hs})"
```

Both dicts must agree key-for-key.

- [ ] **Step 7: Merge branch to main, leave commit local**

```bash
git checkout main && git merge --ff-only feat/sp3-sensor-on-head-source && git log --oneline -3
```

---

## Task 4: HelixScreen — parse `head_source[i].sensor` into `AmsSystemInfo`

**Files:**
- Modify: `helixscreen/include/ams_backend_ace.h`
- Modify: `helixscreen/src/printer/ams_backend_ace.cpp`
- Test: `helixscreen/tests/unit/test_ams_backend_ace.cpp`

SP2 already added `parse_units_array` and the `head_source` parsing runs inside `parse_ace_object`. The struct holding each head needs a new field.

- [ ] **Step 1: Locate the head-source struct**

Run: `grep -n "head_source\|HeadSource\|MappedHead" /mnt/e/Code/helixscreen/include/ams_backend_ace.h | head -n 10`
Expected: at least one struct or member named like `head_source_` with field names matching `head`/`unit`/`slot`/`brand`/`type`/`color`.

- [ ] **Step 2: Write 2 failing Catch2 tests**

```cpp
// Append to helixscreen/tests/unit/test_ams_backend_ace.cpp, inside the
// existing [ams][ace] anonymous namespace + TEST_CASE structure.

TEST_CASE("sensor field on head_source populates AmsSystemInfo", "[ams][ace][sp3]") {
    auto backend = make_test_backend();
    nlohmann::json payload = make_ace_multiunit_payload();
    payload["head_source"][0]["sensor"] = true;
    payload["head_source"][1]["sensor"] = false;
    payload["head_source"][2]["sensor"] = false;
    payload["head_source"][3]["sensor"] = false;
    backend->parse_ace_object(payload);
    auto info = backend->get_system_info();
    REQUIRE(info.head_source[0].sensor == true);
    REQUIRE(info.head_source[1].sensor == false);
}

TEST_CASE("sensor missing on head_source defaults to false (SP2 compat)",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend();
    nlohmann::json payload = make_ace_multiunit_payload();
    // omit sensor field entirely
    backend->parse_ace_object(payload);
    auto info = backend->get_system_info();
    for (auto& hs : info.head_source) { REQUIRE(hs.sensor == false); }
}
```

- [ ] **Step 3: Run tests in Docker `helixbuild` container — verify RED**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_backend_ace.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_backend_ace.cpp helixbuild:/build/src/printer/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 25
```

Expected: build fails with `'sensor' is not a member of …HeadSource…`.

- [ ] **Step 4: Add `bool sensor{false};` to the head-source struct in `ams_backend_ace.h`**

Locate the struct from Step 1 and add the field next to the existing `int unit`, `int slot`, etc. Preserve initializer-list order if the struct uses one.

- [ ] **Step 5: Parse the sensor field in `ams_backend_ace.cpp`**

Find the head-source parsing block inside `parse_ace_object` (or wherever SP2 reads `data["head_source"]`). After populating `unit` / `slot` / `brand` / etc., add:

```cpp
hs.sensor = entry.value("sensor", false);
```

Use `.value(...)` (nlohmann/json) so omission defaults to false — matches the SP2-compat test.

- [ ] **Step 6: Rebuild and re-run, verify GREEN**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_backend_ace.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_backend_ace.cpp helixbuild:/build/src/printer/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 10
```

Expected: SP3 tests PASS, full `[ace]` suite still green (now 46 cases or so).

- [ ] **Step 7: Commit on the helixscreen repo**

```bash
cd /mnt/e/Code/helixscreen
git checkout -b sp3-loadout-recovery
git add include/ams_backend_ace.h src/printer/ams_backend_ace.cpp tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ace): parse head_source[i].sensor from SP3 status extension"
```

---

## Task 5: HelixScreen — `classify_loadout_row` drift classifier

**Files:**
- Modify: `helixscreen/include/ams_backend_ace.h`
- Modify: `helixscreen/src/printer/ams_backend_ace.cpp`
- Test: `helixscreen/tests/unit/test_ams_backend_ace.cpp`

- [ ] **Step 1: Add the enum + free function declaration**

In `helixscreen/include/ams_backend_ace.h`, near the head-source struct:

```cpp
enum class LoadoutRowState {
    HEALTHY_LOADED,    // mapped + sensor true
    HEALTHY_EMPTY,     // unmapped + sensor false
    MAPPED_EMPTY,      // mapped + sensor false (drift)
    LOADED_UNMAPPED,   // unmapped + sensor true (wild filament)
    SUPPRESSED         // swap_in_progress true — ignore
};

LoadoutRowState classify_loadout_row(const HeadSource& hs, bool swap_in_progress);
```

- [ ] **Step 2: Write 5 failing tests**

```cpp
TEST_CASE("classify_loadout_row: mapped + sensor=true is HEALTHY_LOADED",
          "[ams][ace][sp3]") {
    HeadSource hs; hs.unit = 0; hs.slot = 1; hs.sensor = true;
    REQUIRE(classify_loadout_row(hs, false) == LoadoutRowState::HEALTHY_LOADED);
}
TEST_CASE("classify_loadout_row: unmapped + sensor=false is HEALTHY_EMPTY",
          "[ams][ace][sp3]") {
    HeadSource hs; hs.unit = -1; hs.slot = -1; hs.sensor = false;
    REQUIRE(classify_loadout_row(hs, false) == LoadoutRowState::HEALTHY_EMPTY);
}
TEST_CASE("classify_loadout_row: mapped + sensor=false is MAPPED_EMPTY (drift)",
          "[ams][ace][sp3]") {
    HeadSource hs; hs.unit = 0; hs.slot = 1; hs.sensor = false;
    REQUIRE(classify_loadout_row(hs, false) == LoadoutRowState::MAPPED_EMPTY);
}
TEST_CASE("classify_loadout_row: unmapped + sensor=true is LOADED_UNMAPPED (drift)",
          "[ams][ace][sp3]") {
    HeadSource hs; hs.unit = -1; hs.slot = -1; hs.sensor = true;
    REQUIRE(classify_loadout_row(hs, false) == LoadoutRowState::LOADED_UNMAPPED);
}
TEST_CASE("classify_loadout_row: drift during swap_in_progress is SUPPRESSED",
          "[ams][ace][sp3]") {
    HeadSource hs; hs.unit = 0; hs.slot = 1; hs.sensor = false;
    REQUIRE(classify_loadout_row(hs, true) == LoadoutRowState::SUPPRESSED);
}
```

(Use whatever sentinel value `unit` and `slot` carry when "unmapped" — likely `-1` per SP2's `MappedTool` convention. Verify by reading `ams_backend_ace.h`'s comment on the field.)

- [ ] **Step 3: Implement the classifier in `ams_backend_ace.cpp`**

```cpp
LoadoutRowState classify_loadout_row(const HeadSource& hs, bool swap_in_progress) {
    const bool mapped = hs.unit >= 0 && hs.slot >= 0;
    if (swap_in_progress && mapped != hs.sensor) {
        return LoadoutRowState::SUPPRESSED;
    }
    if (mapped && hs.sensor)   return LoadoutRowState::HEALTHY_LOADED;
    if (!mapped && !hs.sensor) return LoadoutRowState::HEALTHY_EMPTY;
    if (mapped && !hs.sensor)  return LoadoutRowState::MAPPED_EMPTY;
    return LoadoutRowState::LOADED_UNMAPPED;
}
```

- [ ] **Step 4: Run, verify GREEN**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_backend_ace.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_backend_ace.cpp helixbuild:/build/src/printer/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 10
```

Expected: 5 new tests PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add include/ams_backend_ace.h src/printer/ams_backend_ace.cpp tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ace): classify_loadout_row identifies drift classes for Recovery panel"
```

---

## Task 6: Extend the Moonraker mock to capture sent gcode strings

**Files:**
- Modify: `helixscreen/include/moonraker_api_mock.h`

The smart-swap dispatcher in Task 7 needs to assert exact Gcode strings in exact order. The mock has `int gcode_script(const std::string& gcode) override;` at line 340 but no capture. Add a minimal capture surface.

- [ ] **Step 1: Read the existing `gcode_script` override declaration and body**

Run: `grep -n "int gcode_script\|gcode_script(const" /mnt/e/Code/helixscreen/include/moonraker_api_mock.h`
Find the declaration around line 340 and its definition (inline in header, or in a corresponding `.cpp` — most mocks in this repo are header-only).

- [ ] **Step 2: Add the capture members near the bottom of the class (around line 944 where `gcode_error_mutex_` lives)**

```cpp
    mutable std::mutex sent_gcode_scripts_mutex_;
    std::vector<std::string> sent_gcode_scripts_;
public:
    std::vector<std::string> get_sent_gcode_scripts() const {
        std::lock_guard<std::mutex> lock(sent_gcode_scripts_mutex_);
        return sent_gcode_scripts_;
    }
    void clear_sent_gcode_scripts() {
        std::lock_guard<std::mutex> lock(sent_gcode_scripts_mutex_);
        sent_gcode_scripts_.clear();
    }
```

- [ ] **Step 3: Hook the capture into `gcode_script(...)` body**

Prepend to the existing body (header or `.cpp`):

```cpp
int MoonrakerApiMock::gcode_script(const std::string& gcode) {
    {
        std::lock_guard<std::mutex> lock(sent_gcode_scripts_mutex_);
        sent_gcode_scripts_.push_back(gcode);
    }
    // existing body unchanged
}
```

- [ ] **Step 4: Smoke test the capture**

Add one tiny TEST_CASE in `test_ams_backend_ace.cpp`:

```cpp
TEST_CASE("moonraker mock captures gcode_script submissions", "[ams][mock][sp3]") {
    auto backend = make_test_backend();
    auto& mock = backend->mock_for_test();
    mock.clear_sent_gcode_scripts();
    mock.gcode_script("M114");
    auto sent = mock.get_sent_gcode_scripts();
    REQUIRE(sent.size() == 1);
    REQUIRE(sent[0] == "M114");
}
```

If `make_test_backend` doesn't expose the mock, add a small `MoonrakerApiMock& mock_for_test()` accessor on the test helper (NOT on the production backend).

- [ ] **Step 5: Build + run, verify GREEN**

```bash
docker cp /mnt/e/Code/helixscreen/include/moonraker_api_mock.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 10
```

- [ ] **Step 6: Commit**

```bash
git add include/moonraker_api_mock.h tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "test(mock): capture gcode_script submissions for SP3 dispatcher tests"
```

---

## Task 7: HelixScreen — `request_slot_action` smart-swap dispatcher

**Files:**
- Modify: `helixscreen/include/ams_backend_ace.h`
- Modify: `helixscreen/src/printer/ams_backend_ace.cpp`
- Test: `helixscreen/tests/unit/test_ams_backend_ace.cpp`

- [ ] **Step 1: Declare the method on the backend in `ams_backend_ace.h`**

```cpp
/// SP3 smart slot tap — picks the right head for target_unit/target_slot,
/// then issues the right multiACE macro sequence (direct / same-ACE swap /
/// cross-ACE 2-leg). Returns AmsError::success() on dispatch (NOT on
/// completion — the underlying gcode is async).
AmsError request_slot_action(int target_unit, int target_slot,
                             std::optional<int> hint_head = std::nullopt);

// Returned when the caller didn't pick a head and there's no obvious free head.
// UI should open a head picker and re-call with hint_head.
constexpr AmsErrorCode NEEDS_HEAD_PICKER = /* pick next free in AmsErrorCode */;
```

Plus add `std::optional<PendingLeg2> pending_leg2_;` and a small struct:

```cpp
struct PendingLeg2 { int head; int target_unit; int target_slot; };
```

in the backend's private section.

- [ ] **Step 2: Write 4 failing tests**

```cpp
TEST_CASE("request_slot_action: empty head -> direct ACE_LOAD_HEAD",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend_with_state(/*all heads unmapped, sensors false*/);
    auto& mock = backend->mock_for_test();
    mock.clear_sent_gcode_scripts();
    REQUIRE(backend->request_slot_action(1, 2).is_success());  // ACE 1 slot 2
    auto sent = mock.get_sent_gcode_scripts();
    REQUIRE(sent.size() == 1);
    REQUIRE(sent[0] == "ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=2");
}

TEST_CASE("request_slot_action: target head already at same ACE -> same-ACE swap",
          "[ams][ace][sp3]") {
    // head 0 currently sourced from ACE 1 slot 1; user taps ACE 1 slot 3
    auto backend = make_test_backend_with_state(
        /*head_source[0]={unit=1,slot=1,sensor=true}*/);
    auto& mock = backend->mock_for_test();
    mock.clear_sent_gcode_scripts();
    REQUIRE(backend->request_slot_action(1, 3, /*hint_head=*/0).is_success());
    auto sent = mock.get_sent_gcode_scripts();
    REQUIRE(sent.size() == 2);
    REQUIRE(sent[0] == "ACEC__Unload_T0");
    REQUIRE(sent[1] == "ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=3");
}

TEST_CASE("request_slot_action: target head at different ACE -> cross-ACE swap leg 1 only",
          "[ams][ace][sp3]") {
    // head 0 sourced from ACE 0; user taps ACE 1 slot 0. Leg 2 fires on a
    // later status update; dispatcher only issues leg 1 immediately.
    auto backend = make_test_backend_with_state(
        /*head_source[0]={unit=0,slot=0,sensor=true}*/);
    auto& mock = backend->mock_for_test();
    mock.clear_sent_gcode_scripts();
    REQUIRE(backend->request_slot_action(1, 0, /*hint_head=*/0).is_success());
    auto sent = mock.get_sent_gcode_scripts();
    REQUIRE(sent.size() == 1);
    REQUIRE(sent[0] == "ACE_UNLOAD_HEAD HEAD=0 LENGTH=600");
}

TEST_CASE("request_slot_action: no hint + no free head -> needs head picker",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend_with_state(/*all heads loaded*/);
    auto err = backend->request_slot_action(1, 0);
    REQUIRE(err.code() == NEEDS_HEAD_PICKER);
}
```

If `make_test_backend_with_state` factory doesn't exist, add a small helper that builds an `AmsSystemInfo` and assigns it to the backend's internal state. The test must NOT round-trip through `parse_ace_object` — SP3 wants direct state injection for these branches.

- [ ] **Step 3: Implement `request_slot_action` in `ams_backend_ace.cpp`**

```cpp
AmsError AmsBackendAce::request_slot_action(int target_unit, int target_slot,
                                            std::optional<int> hint_head) {
    std::lock_guard<std::mutex> lock(mutex_);
    int head = hint_head.value_or(-1);
    if (head < 0) {
        head = pick_target_head_(system_info_);
        if (head < 0) {
            return AmsErrorHelper::error(NEEDS_HEAD_PICKER, "no free head");
        }
    }
    const auto& hs = system_info_.head_source[head];
    const bool mapped = hs.unit >= 0 && hs.slot >= 0;
    const bool same_ace = mapped && hs.unit == target_unit;

    if (!mapped) {
        moonraker_->gcode_script(
            "ACE_LOAD_HEAD HEAD=" + std::to_string(head)
            + " ACE=" + std::to_string(target_unit)
            + " SLOT=" + std::to_string(target_slot));
        return AmsErrorHelper::success();
    }
    if (same_ace) {
        moonraker_->gcode_script("ACEC__Unload_T" + std::to_string(head));
        moonraker_->gcode_script(
            "ACE_LOAD_HEAD HEAD=" + std::to_string(head)
            + " ACE=" + std::to_string(target_unit)
            + " SLOT=" + std::to_string(target_slot));
        return AmsErrorHelper::success();
    }
    moonraker_->gcode_script(
        "ACE_UNLOAD_HEAD HEAD=" + std::to_string(head) + " LENGTH=600");
    pending_leg2_ = PendingLeg2{ head, target_unit, target_slot };
    return AmsErrorHelper::success();
}

int AmsBackendAce::pick_target_head_(const AmsSystemInfo& info) {
    for (int h = 0; h < 4; ++h) {
        const auto& hs = info.head_source[h];
        if (hs.unit < 0 && hs.slot < 0 && !hs.sensor) return h;
    }
    return -1;
}
```

- [ ] **Step 4: Add leg-2 firing in `parse_ace_object`**

At the end of `parse_ace_object` (after `system_info_` is fully updated, still inside the existing lock):

```cpp
if (pending_leg2_) {
    const auto& hs = system_info_.head_source[pending_leg2_->head];
    if (hs.unit < 0 && hs.slot < 0) {  // leg 1 cleared the source
        moonraker_->gcode_script(
            "ACE_LOAD_HEAD HEAD=" + std::to_string(pending_leg2_->head)
            + " ACE=" + std::to_string(pending_leg2_->target_unit)
            + " SLOT=" + std::to_string(pending_leg2_->target_slot));
        pending_leg2_.reset();
    }
}
```

Add a test for it:

```cpp
TEST_CASE("cross-ACE swap: leg 2 fires after next parse with cleared head_source",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend_with_state(/*h0={unit=0,slot=0,sensor=true}*/);
    auto& mock = backend->mock_for_test();
    backend->request_slot_action(1, 0, /*hint_head=*/0);
    mock.clear_sent_gcode_scripts();

    nlohmann::json payload = make_ace_multiunit_payload();
    payload["head_source"][0] = {{"head", 0}, {"unit", nullptr}, {"slot", nullptr},
                                  {"sensor", false}};
    backend->parse_ace_object(payload);

    auto sent = mock.get_sent_gcode_scripts();
    REQUIRE(sent.size() == 1);
    REQUIRE(sent[0] == "ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=0");
}
```

- [ ] **Step 5: Build + run, verify GREEN**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_backend_ace.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_backend_ace.cpp helixbuild:/build/src/printer/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 15
```

Expected: 4 dispatcher tests + leg-2 sequencer test PASS; nothing else regresses.

- [ ] **Step 6: Commit**

```bash
git add include/ams_backend_ace.h src/printer/ams_backend_ace.cpp tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ace): request_slot_action 3-case dispatcher + cross-ACE leg-2 sequencer"
```

---

## Task 8: HelixScreen — Loadout Check LVGL panel

**Files:**
- Create: `helixscreen/include/ams_loadout_check_modal.h`
- Create: `helixscreen/src/printer/ams_loadout_check_modal.cpp`

Existing UI pattern: HelixScreen's `Modal` base class and `AmsEditModal` (both in `include/`). Replicate the construction conventions: constructor taking the parent LVGL screen + a reference to the ACE backend; `show()` builds the table; teardown destroys widgets.

This task is UI-only — no Catch2 coverage (LVGL renders need a real display). Verified on-device in Task 11.

- [ ] **Step 1: Read prior art**

```bash
grep -n "class Modal\|class AmsEditModal\|class AmsContextMenu\|class OverlayBase" /mnt/e/Code/helixscreen/include/*.h
```

Skim each header to understand constructor signature, `show()`/`hide()`/`destroy()` conventions, how they get the AmsSystemInfo update notification (likely an observer registered on the backend).

- [ ] **Step 2: Sketch `ams_loadout_check_modal.h`**

```cpp
#pragma once
#include "modal.h"
#include "ams_backend_ace.h"

class AmsLoadoutCheckModal : public Modal {
public:
    AmsLoadoutCheckModal(lv_obj_t* parent, AmsBackendAce& backend);
    void show() override;
    void hide() override;
private:
    AmsBackendAce& backend_;
    lv_obj_t* table_{nullptr};
    void render_row_(int head_idx, const HeadSource& hs, LoadoutRowState state);
    void on_system_info_update_(const AmsSystemInfo& info);
    int observer_id_{-1};
};
```

- [ ] **Step 3: Implement `ams_loadout_check_modal.cpp`**

LVGL table: 4 rows × 5 cols (Head, Unit, Slot, Filament label, Sensor symbol). Use `classify_loadout_row(hs, info.swap_in_progress)` to pick the symbol:

```
HEALTHY_LOADED  -> ●   (green)
HEALTHY_EMPTY   -> ○   (gray)
MAPPED_EMPTY    -> ✕   (red) + "MAPPED, GATE EMPTY" subtext
LOADED_UNMAPPED -> ?   (yellow) + "WILD FILAMENT" subtext
SUPPRESSED      -> ⟳   (blue) — swap in progress, don't flag drift
```

Toolbar buttons: "Refresh" → `backend.refresh_status()` (which issues `ACE_HEAD_STATUS` via `gcode_script`), "Recover…" → opens `AmsRecoveryModal` and is only enabled when any row is `MAPPED_EMPTY` or `LOADED_UNMAPPED`.

- [ ] **Step 4: Register the panel in HelixScreen's UI controller**

Find the multi-unit ACE panel in HelixScreen (created by SP2 to render the unit grid). Add a "Loadout Check" button that constructs and shows `AmsLoadoutCheckModal`.

- [ ] **Step 5: Build (no test run — UI tests are manual)**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_loadout_check_modal.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_loadout_check_modal.cpp helixbuild:/build/src/printer/
docker cp <ui_controller_file> helixbuild:/build/<dest>
docker exec helixbuild make 2>&1 | tail -n 10
```

Expected: clean build, no warnings on new files.

- [ ] **Step 6: Commit**

```bash
git add include/ams_loadout_check_modal.h src/printer/ams_loadout_check_modal.cpp <ui_controller_file>
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ui): Loadout Check modal renders head/unit/slot/sensor table"
```

---

## Task 9: HelixScreen — Recovery LVGL panel

**Files:**
- Create: `helixscreen/include/ams_recovery_modal.h`
- Create: `helixscreen/src/printer/ams_recovery_modal.cpp`
- Modify: `helixscreen/include/ams_backend_ace.h` (`RecoveryAction` enum, `dispatch_recovery_action` declaration)
- Modify: `helixscreen/src/printer/ams_backend_ace.cpp` (`dispatch_recovery_action` body)
- Test: `helixscreen/tests/unit/test_ams_backend_ace.cpp`

- [ ] **Step 1: Add `RecoveryAction` enum + `dispatch_recovery_action` declaration**

In `ams_backend_ace.h`:

```cpp
enum class RecoveryAction {
    CLEAR_HEAD,        // ACE_CLEAR_HEADS HEAD=<h>
    RETRY_LOAD,        // ACE_LOAD_HEAD HEAD=<h> ACE=<existing.unit> SLOT=<existing.slot>
    MARK_LOADED,       // ACE_MARK_HEAD_LOADED HEAD=<h> ACE=<arg.unit> SLOT=<arg.slot>
    FORCE_UNLOAD,      // ACE_UNLOAD_HEAD HEAD=<h> LENGTH=600
};

struct RecoveryActionParams {
    int head;
    int unit{-1};   // used by MARK_LOADED only
    int slot{-1};   // used by MARK_LOADED only
};

AmsError dispatch_recovery_action(RecoveryAction action,
                                  const RecoveryActionParams& params);
```

- [ ] **Step 2: Write 4 failing tests**

```cpp
TEST_CASE("dispatch_recovery_action: CLEAR_HEAD emits ACE_CLEAR_HEADS HEAD=N",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend();
    auto& mock = backend->mock_for_test();
    backend->dispatch_recovery_action(RecoveryAction::CLEAR_HEAD, {/*head=*/2});
    REQUIRE(mock.get_sent_gcode_scripts().back() == "ACE_CLEAR_HEADS HEAD=2");
}

TEST_CASE("dispatch_recovery_action: RETRY_LOAD uses head's existing source",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend_with_state(
        /*head_source[1]={unit=0,slot=3,sensor=false}*/);
    auto& mock = backend->mock_for_test();
    backend->dispatch_recovery_action(RecoveryAction::RETRY_LOAD, {/*head=*/1});
    REQUIRE(mock.get_sent_gcode_scripts().back()
            == "ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=3");
}

TEST_CASE("dispatch_recovery_action: MARK_LOADED uses provided unit+slot",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend();
    auto& mock = backend->mock_for_test();
    backend->dispatch_recovery_action(RecoveryAction::MARK_LOADED,
                                       {/*head=*/0, /*unit=*/1, /*slot=*/2});
    REQUIRE(mock.get_sent_gcode_scripts().back()
            == "ACE_MARK_HEAD_LOADED HEAD=0 ACE=1 SLOT=2");
}

TEST_CASE("dispatch_recovery_action: FORCE_UNLOAD emits ACE_UNLOAD_HEAD LENGTH=600",
          "[ams][ace][sp3]") {
    auto backend = make_test_backend();
    auto& mock = backend->mock_for_test();
    backend->dispatch_recovery_action(RecoveryAction::FORCE_UNLOAD, {/*head=*/3});
    REQUIRE(mock.get_sent_gcode_scripts().back()
            == "ACE_UNLOAD_HEAD HEAD=3 LENGTH=600");
}
```

- [ ] **Step 3: Implement `dispatch_recovery_action`**

```cpp
AmsError AmsBackendAce::dispatch_recovery_action(RecoveryAction action,
                                                  const RecoveryActionParams& params) {
    std::lock_guard<std::mutex> lock(mutex_);
    const int h = params.head;
    switch (action) {
    case RecoveryAction::CLEAR_HEAD:
        moonraker_->gcode_script("ACE_CLEAR_HEADS HEAD=" + std::to_string(h));
        return AmsErrorHelper::success();
    case RecoveryAction::RETRY_LOAD: {
        const auto& hs = system_info_.head_source[h];
        if (hs.unit < 0 || hs.slot < 0) {
            return AmsErrorHelper::error(AmsErrorCode::INVALID_STATE,
                                          "no existing source to retry");
        }
        moonraker_->gcode_script(
            "ACE_LOAD_HEAD HEAD=" + std::to_string(h)
            + " ACE=" + std::to_string(hs.unit)
            + " SLOT=" + std::to_string(hs.slot));
        return AmsErrorHelper::success();
    }
    case RecoveryAction::MARK_LOADED:
        moonraker_->gcode_script(
            "ACE_MARK_HEAD_LOADED HEAD=" + std::to_string(h)
            + " ACE=" + std::to_string(params.unit)
            + " SLOT=" + std::to_string(params.slot));
        return AmsErrorHelper::success();
    case RecoveryAction::FORCE_UNLOAD:
        moonraker_->gcode_script(
            "ACE_UNLOAD_HEAD HEAD=" + std::to_string(h) + " LENGTH=600");
        return AmsErrorHelper::success();
    }
    return AmsErrorHelper::error(AmsErrorCode::INVALID_STATE, "unknown action");
}
```

- [ ] **Step 4: Build + run backend tests, verify GREEN**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_backend_ace.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_backend_ace.cpp helixbuild:/build/src/printer/
docker cp /mnt/e/Code/helixscreen/tests/unit/test_ams_backend_ace.cpp helixbuild:/build/tests/unit/
docker exec helixbuild make test 2>&1 | tail -n 10
```

- [ ] **Step 5: Sketch the LVGL modal**

`ams_recovery_modal.h`:

```cpp
#pragma once
#include "modal.h"
#include "ams_backend_ace.h"

class AmsRecoveryModal : public Modal {
public:
    AmsRecoveryModal(lv_obj_t* parent, AmsBackendAce& backend);
    void show() override;
private:
    AmsBackendAce& backend_;
    void render_drift_row_(int head_idx, const HeadSource& hs,
                           LoadoutRowState state);
    void confirm_(const std::string& title, const std::string& body,
                  std::function<void()> on_yes);
};
```

Implementation pattern (`ams_recovery_modal.cpp`):

For each head where `classify_loadout_row(hs, system_info_.swap_in_progress) != HEALTHY_*` and `!= SUPPRESSED`:

```
MAPPED_EMPTY:
  - "Clear this head"   -> confirm -> dispatch_recovery_action(CLEAR_HEAD, {h})
  - "Retry load"        -> confirm -> dispatch_recovery_action(RETRY_LOAD, {h})
  - "Mark loaded"       -> confirm -> dispatch_recovery_action(MARK_LOADED, {h, hs.unit, hs.slot})

LOADED_UNMAPPED:
  - "Mark as loaded from..."  -> small slot picker (AmsContextMenu) -> confirm -> MARK_LOADED
  - "Force unload"            -> confirm -> dispatch_recovery_action(FORCE_UNLOAD, {h})
```

Every button routes through `confirm_(...)` first (yes/cancel sub-modal).

- [ ] **Step 6: Build, verify clean compile**

```bash
docker cp /mnt/e/Code/helixscreen/include/ams_recovery_modal.h helixbuild:/build/include/
docker cp /mnt/e/Code/helixscreen/src/printer/ams_recovery_modal.cpp helixbuild:/build/src/printer/
docker exec helixbuild make 2>&1 | tail -n 5
```

- [ ] **Step 7: Commit**

```bash
git add include/ams_recovery_modal.h src/printer/ams_recovery_modal.cpp \
         include/ams_backend_ace.h src/printer/ams_backend_ace.cpp \
         tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ui): Recovery modal — targeted fix-its for loadout drift"
```

---

## Task 10: HelixScreen — wire multi-unit slot tap to `request_slot_action`

**Files:**
- Modify: the SP2 multi-unit unit-grid panel (find by grep).

- [ ] **Step 1: Locate the slot tile tap handler**

```bash
grep -rn "request_slot_action\|change_tool\|on_slot_tap\|slot_tile" /mnt/e/Code/helixscreen/src/printer/ | head -n 10
```

Find the SP2 handler that previously called `change_tool` and was made a no-op under multi-unit.

- [ ] **Step 2: Replace the no-op branch with `request_slot_action`**

```cpp
if (system_info_.is_multi_unit()) {
    auto err = backend_.request_slot_action(target_unit, target_slot);
    if (err.code() == NEEDS_HEAD_PICKER) {
        show_head_picker_(target_unit, target_slot);
    }
    return;
}
```

- [ ] **Step 3: Build, verify no UI regressions**

```bash
docker cp <file> helixbuild:/build/<dest>
docker exec helixbuild make test 2>&1 | tail -n 5
```

- [ ] **Step 4: Commit**

```bash
git add <file>
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit -m "feat(ui): route multi-unit slot tap to request_slot_action"
```

---

## Task 11: Deploy + manual on-device smoke

**Pre-flight:** verify Davinci-U1 print state is safe (standby / complete / cancelled / error). SP4 owns the install/uninstall switching — this manual smoke runs whichever side is currently installed (HelixScreen on the touchscreen, stock Snapmaker GUI on web).

- [ ] **Step 1: Build the HelixScreen binary in `helixbuild`**

```bash
docker exec helixbuild make 2>&1 | tail -n 5
```

- [ ] **Step 2: SCP the binary to the printer**

(Concrete path depends on the SP4-pending install layout. For SP3 smoke, push to `/tmp/helixscreen-sp3` and run manually so SP4 can replace it cleanly.)

```bash
docker cp helixbuild:/build/bin/helixscreen /tmp/helixscreen-sp3
printf 'snapmaker\n' | python3 /tmp/scp_put.py /tmp/helixscreen-sp3 /tmp/helixscreen-sp3
```

- [ ] **Step 3: Stop the running display process and start the new binary**

(Document exact systemd/init.d unit at smoke time — Davinci-U1 is BusyBox sysvinit per memory.)

- [ ] **Step 4: Run the 5-step smoke from the spec**

1. Open Loadout Check. Confirm all four heads match what `curl http://${DAVINCI_U1_HOST}/multiace/api/state` reports for `head_source` and `sensors`.
2. Tap an empty slot on either ACE. Expected: direct ACE_LOAD_HEAD, no banner, `head_source` updates within ~5s on both screen and web console.
3. Tap a different slot on the same ACE as the currently-mapped head. Expected: `ACEC__Unload_T<h>` then `ACE_LOAD_HEAD`, single-step banner, screen reflects new mapping.
4. Tap a slot on the *other* ACE. Expected: two-leg banner "Cross-ACE swap leg 1/2 → leg 2/2", `ACE_UNLOAD_HEAD HEAD=<h> LENGTH=600` first, then after `head_source[h]` clears, `ACE_LOAD_HEAD …`. Sensor flips true at the end.
5. With one head deliberately desynced (`curl -X POST http://${DAVINCI_U1_HOST}:7125/printer/gcode/script -d 'script=ACE_MARK_HEAD_LOADED HEAD=2 ACE=0 SLOT=3'` against an actually-empty slot), open Loadout Check and observe `✕`. Open Recovery → Clear this head → confirm. Loadout Check returns to healthy.

- [ ] **Step 5: Capture before/after `multiace_state.log` snippets**

```bash
printf 'snapmaker\n' | python3 /tmp/ssh_run.py 'tail -n 100 /home/lava/printer_data/logs/multiace_state.log'
```

Keep for the SP3 release commit message.

- [ ] **Step 6: Final cross-fork merge**

In `/mnt/e/Code/multiACE`:

```bash
git checkout main
git log --oneline -5  # confirm SP3 firmware commits present
```

In `/mnt/e/Code/helixscreen`:

```bash
git checkout sp3-loadout-recovery && git log --oneline -10
# Per project memory: HelixScreen SP3 branch stays local; do not push.
```

Mark task #87 / #89 completed.

---

## Self-Review

**1. Spec coverage:**

| Spec section                       | Plan task(s)        |
|------------------------------------|---------------------|
| SP1 contract extension `sensor`    | Task 1, 2, 3        |
| Smart-swap state machine           | Task 4 (parse), 7   |
| Loadout Check panel                | Task 5 (classifier), 8 |
| Recovery panel                     | Task 9              |
| Multi-unit slot-action wiring      | Task 10             |
| Catch2 host tests                  | Tasks 4-7, 9        |
| Manual on-device smoke             | Task 11             |
| `ACE_CLEAR_HEADS [HEAD=N]`         | Task 9 (verified per-head supported in plan-time investigation; spec's "TBD" closed) |
| Moonraker-mock gcode capture       | Task 6 (extends mock minimally — spec's "TBD" closed) |
| Sensor source-of-truth attribute   | Task 2 (lifts existing ace.py:2894 pattern — spec's "TBD" closed) |
| `ACEC__Unload_T<n>` macro coverage | All 4 confirmed at plan-write time (`config/extended/ace.cfg:253-268`) |

All spec sections mapped. All four spec "Open questions" resolved at plan-write time and threaded into the relevant tasks.

**2. Placeholder scan:**

- No "TBD" / "TODO" / "implement later" in any task's mandatory steps.
- Task 8 step 4 says "find" the UI controller file — that's an explicit lookup step with the exact grep, not a placeholder.
- Task 11 step 3 says "Document exact systemd/init.d unit at smoke time" — flagged as a discovery step because SP4's install layout isn't yet decided; SP3 doesn't block on it (the binary can be run from `/tmp` for the smoke).

**3. Type consistency:**

- Python builder param `sensors_per_head: dict[int, bool]` consistent across Tasks 1, 2.
- C++ struct field `bool sensor{false}` on the head-source entry consistent across Tasks 4, 5, 7, 8, 9.
- `LoadoutRowState` enum and `classify_loadout_row(...)` signature consistent across Tasks 5, 8, 9.
- `request_slot_action(int target_unit, int target_slot, std::optional<int> hint_head)` consistent between Tasks 7 and 10.
- `RecoveryAction` enum + `dispatch_recovery_action(RecoveryAction, RecoveryActionParams)` consistent across Task 9.
- `PendingLeg2` struct introduced in Task 7 Step 1, consumed in Task 7 Step 4.

**4. Risk realism:**

- Cross-ACE swap leg-2 sequencing (Task 7 Step 4) is the highest-risk single piece — it races against the periodic poll. The plan ports the web console's documented wait pattern exactly, but the test only covers one tick of advance. Live smoke (Task 11 step 4) is the real verification.
- `ACE_CLEAR_HEADS HEAD=<n>` per-head support is confirmed at the help-string level (`Usage: ACE_CLEAR_HEADS [HEAD=0]`) but the actual `cmd_ACE_CLEAR_HEADS` body should be skimmed at Task 9 implementation time to confirm the parser pops the arg correctly.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-30-helixscreen-sp3-loadout-recovery.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks (spec compliance → code quality), fast iteration. 11 tasks split across two repos (`multiace/` and `helixscreen/`) means each subagent gets a clean context.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batched with checkpoints for review.

Which approach?
