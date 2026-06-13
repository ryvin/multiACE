# HelixScreen multiACE SP2 — Multi-Unit ACE Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this plan is executed **inline** (TDD, in-session)
> rather than via parallel subagents — all three changes edit the same three files in sequence
> (tightly coupled) and the build runs inside a Docker container reached only via `docker exec`.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the `ace` Klipper object carries a `units[]` array (multiACE/SP1), populate
`AmsSystemInfo.units` with one `AmsUnit` per ACE so the existing multi-unit AMS UI renders all
units; non-multiACE firmware (flat `slots[]`, no `units[]`) keeps its current single-unit path
unchanged; and `change_tool` is tap-guarded under multi-unit.

**Architecture:** One new private method `AmsBackendAce::parse_units_array(const json&)` + a single
early-return branch in `parse_ace_object` + a tap-guard at the top of `change_tool`. The
single-unit flat-`slots[]` path (lines 658–853) is left byte-for-byte unchanged — the branch sits
above it. `parse_units_array` runs under the caller's `mutex_` (no re-lock) and never throws into
the Moonraker callback.

**Tech Stack:** C++17, nlohmann/json (libhv), Catch2 (amalgamated), spdlog. Build = `make test`
inside the `helixbuild` Docker container; tests = `./build/bin/helix-tests "[ace]"`.

**Spec:** `docs/superpowers/specs/2026-05-27-helixscreen-sp2-multiunit-ace-backend-design.md`

---

## Build/test loop (container)

Edits happen on the **host** repo `/mnt/e/Code/helixscreen` (branch `sp2-multiunit-ace`, where
Write/Edit + real commits work). The build runs in the **container** `helixbuild` at `/build`
(fast ext4). Sync the three changed files into the container, then build + run the ACE tests.

Helper (write once to `/tmp/sp2_build.sh`, then `bash /tmp/sp2_build.sh "<catch2 filter>"`):

```bash
#!/usr/bin/env bash
set -euo pipefail
HOST=/mnt/e/Code/helixscreen
for f in src/printer/ams_backend_ace.cpp include/ams_backend_ace.h tests/unit/test_ams_backend_ace.cpp; do
  docker cp "$HOST/$f" "helixbuild:/build/$f"
done
FILTER="${1:-[ace]}"
docker exec helixbuild bash -lc "cd /build && make test -j\"\$(nproc)\" 2>&1 | tail -6 && \
  ./build/bin/helix-tests \"$FILTER\" 2>&1 | tail -12"
```

The build is incremental — only the changed `.cpp` recompiles + relinks (~1–2 min).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `include/ams_backend_ace.h` | Backend class decl | Declare `void parse_units_array(const nlohmann::json&);` in the protected parsing section |
| `src/printer/ams_backend_ace.cpp` | ACE parser + ops | Branch in `parse_ace_object`; new `parse_units_array`; tap-guard in `change_tool` |
| `tests/unit/test_ams_backend_ace.cpp` | Catch2 suite | Multi-unit payload builder + `[ams][ace][multiunit]` cases |

**Confirmed facts (read from source, do not re-derive):**
- Slot type is `SlotInfo` (not "AmsSlot"): fields `slot_index`, `global_index`, `status`,
  `color_rgb` (uint32 0xRRGGBB), `material`, `brand`, `mapped_tool` (default -1), `environment`.
- `SlotStatus`: `UNKNOWN=0, EMPTY=1, AVAILABLE=2, LOADED=3, …`.
- `AmsUnit`: `unit_index`, `name`, `display_name`, `slot_count`, `first_slot_global_index`,
  `std::vector<SlotInfo> slots`, `connected`, `std::optional<EnvironmentData> environment`.
- `EnvironmentData`: `temperature_c`, `humidity_pct`, `has_humidity`.
- `AmsSystemInfo`: `std::vector<AmsUnit> units`, `total_slots`, `current_tool`, `current_slot`,
  `filament_loaded`, `type_name`, `bool is_multi_unit()` (= `units.size() > 1`).
- `parse_ace_object` locks `mutex_` at entry (line 625); `parse_units_array` must NOT re-lock.
- `change_tool` (line 386) currently just `return load_filament(tool_number);`.
- `load_filament` calls `check_preconditions()` first → returns `not_connected` when `!running_`
  (test helper is never started) → never derefs the null `api_`. So an unstarted helper:
  single-unit `change_tool` → `NOT_CONNECTED`; guarded multi-unit `change_tool` → `SUCCESS`.
- Test harness: `class AmsBackendAceTestHelper : public AmsBackendAce` (ctor `(nullptr,nullptr)`),
  `helper.get_test_system_info()`, and `AceTestAccess::parse_ace(helper, json)` drives the parser.
- Existing flat-slot payload builder `make_ace_slot_payload(status, color_rgb, material)` exists.
- SP1 JSON field names (from `multiace/klipper/extras/ace_status.py`): top-level `units`,
  `total_slots`, `current_tool`, `current_slot`; per unit `unit_index`, `name` (`"ace_0"`),
  `display_name` (`"ACE A"`), `slot_count`, `first_slot_global_index`, `connected`, `environment`
  (`temperature_c`/`humidity_pct`/`has_humidity`), `slots`; per slot `slot_index`, `global_index`,
  `status` (`available`/`empty`/`unknown`), `mapped_tool`, optional `color` `[r,g,b]`, `type`,
  `brand`, `sku`.
- **Deliberate omission:** `parse_units_array` does NOT call `apply_overrides` /
  `check_hardware_event_clear`. Those key on a single 0-based slot index and would collide across
  units (every unit has slots 0..3). Correct multi-unit override semantics are SP3.

---

### Task 1: `parse_units_array` — parse `units[]` into `AmsSystemInfo.units`

**Files:**
- Test: `tests/unit/test_ams_backend_ace.cpp` (add builder + 5 cases)
- Modify: `include/ams_backend_ace.h` (declare method)
- Modify: `src/printer/ams_backend_ace.cpp` (branch + method)

- [ ] **Step 1: Write the failing tests**

Add this builder inside the existing anonymous `namespace { … }` in the test file, right after
`make_ace_slot_payload` (before the closing `} // namespace`):

```cpp
// Build a 2-unit multiACE-shaped `ace` payload (SP1 contract). unit0 connected
// with one mapped+colored slot; unit1 connected with a sparse mapped_tool on a
// non-diagonal slot (local 1 / global 5 -> tool 1) so mapped_tool != global_index.
json make_ace_multiunit_payload() {
    auto mk_slot = [](int si, int gi, const std::string& status, int mapped) {
        return json{{"slot_index", si}, {"global_index", gi},
                    {"status", status}, {"mapped_tool", mapped}};
    };
    json u0s0 = mk_slot(0, 0, "available", 0);
    u0s0["color"] = json::array({255, 0, 0});
    u0s0["type"] = "PLA";
    u0s0["brand"] = "Polymaker";

    json unit0 = {
        {"unit_index", 0}, {"name", "ace_0"}, {"display_name", "ACE A"},
        {"slot_count", 4}, {"first_slot_global_index", 0},
        {"connected", true}, {"status", "ready"},
        {"environment", {{"temperature_c", 28.0}, {"humidity_pct", 0.0}, {"has_humidity", false}}},
        {"slots", json::array({u0s0,
                               mk_slot(1, 1, "available", -1),
                               mk_slot(2, 2, "empty", -1),
                               mk_slot(3, 3, "empty", -1)})},
    };
    json unit1 = {
        {"unit_index", 1}, {"name", "ace_1"}, {"display_name", "ACE B"},
        {"slot_count", 4}, {"first_slot_global_index", 4},
        {"connected", true}, {"status", "ready"},
        {"environment", {{"temperature_c", 0.0}, {"humidity_pct", 0.0}, {"has_humidity", false}}},
        {"slots", json::array({mk_slot(0, 4, "available", -1),
                               mk_slot(1, 5, "available", 1),
                               mk_slot(2, 6, "empty", -1),
                               mk_slot(3, 7, "empty", -1)})},
    };
    return json{
        {"model", "ACE Pro"}, {"firmware", "0.81b"}, {"type_name", "multiACE"},
        {"device_count", 2}, {"active_unit", 0},
        {"current_tool", 0}, {"current_slot", 0}, {"total_slots", 8},
        {"status", "ready"},
        {"units", json::array({unit0, unit1})},
    };
}
```

Add these cases at the end of the test file:

```cpp
// ============================================================================
// multiACE multi-unit (SP2)
// ============================================================================

TEST_CASE("ACE multiunit: units[] populates AmsSystemInfo.units", "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_multiunit_payload());
    auto info = helper.get_test_system_info();

    REQUIRE(info.units.size() == 2);
    REQUIRE(info.is_multi_unit());
    REQUIRE(info.total_slots == 8);

    REQUIRE(info.units[0].slot_count == 4);
    REQUIRE(info.units[1].slot_count == 4);
    REQUIRE(info.units[0].first_slot_global_index == 0);
    REQUIRE(info.units[1].first_slot_global_index == 4);

    REQUIRE(info.units[0].name == "ace_0");
    REQUIRE(info.units[0].display_name == "ACE A");
    REQUIRE(info.units[1].name == "ace_1");
    REQUIRE(info.units[1].display_name == "ACE B");
    REQUIRE(info.units[0].connected == true);

    REQUIRE(info.units[0].slots.front().global_index == 0);
    REQUIRE(info.units[1].slots.back().global_index == 7);
}

TEST_CASE("ACE multiunit: mapped_tool is sparse and not 1:1 with global index",
          "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_multiunit_payload());
    auto info = helper.get_test_system_info();

    REQUIRE(info.units[0].slots[0].mapped_tool == 0);
    REQUIRE(info.units[0].slots[3].mapped_tool == -1);
    REQUIRE(info.units[1].slots[1].mapped_tool == 1);
    REQUIRE(info.units[1].slots[1].global_index == 5);
    REQUIRE(info.units[1].slots[1].mapped_tool != info.units[1].slots[1].global_index);
}

TEST_CASE("ACE multiunit: slot status, color, material, brand carried",
          "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_multiunit_payload());
    auto info = helper.get_test_system_info();

    REQUIRE(info.units[0].slots[0].status == SlotStatus::AVAILABLE);
    REQUIRE(info.units[0].slots[2].status == SlotStatus::EMPTY);
    REQUIRE(info.units[0].slots[0].color_rgb == 0xFF0000u);
    REQUIRE(info.units[0].slots[0].material == "PLA");
    REQUIRE(info.units[0].slots[0].brand == "Polymaker");
}

TEST_CASE("ACE multiunit: per-unit environment carried, humidity absent",
          "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_multiunit_payload());
    auto info = helper.get_test_system_info();

    REQUIRE(info.units[0].environment.has_value());
    REQUIRE(info.units[0].environment->temperature_c == 28.0f); // 28.0 is exact in IEEE-754
    REQUIRE(info.units[0].environment->has_humidity == false);
}

TEST_CASE("ACE multiunit: offline unit retained with unknown slots, not dropped",
          "[ams][ace][multiunit]") {
    json payload = make_ace_multiunit_payload();
    payload["units"][1]["connected"] = false;
    payload["units"][1]["status"] = "error";
    for (auto& s : payload["units"][1]["slots"]) {
        s["status"] = "unknown";
        s["mapped_tool"] = -1;
    }
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, payload);
    auto info = helper.get_test_system_info();

    REQUIRE(info.units.size() == 2);
    REQUIRE(info.units[1].connected == false);
    REQUIRE(info.units[1].slots.size() == 4);
    REQUIRE(info.units[1].slots[0].status == SlotStatus::UNKNOWN);
}
```

- [ ] **Step 2: Run the tests, verify they FAIL**

`bash /tmp/sp2_build.sh "[ams][ace][multiunit]"`
Expected: builds, then assertions fail — without the branch, the flat path runs and produces
`units.size()==1` / wrong indices (or the new cases simply don't see populated units).

- [ ] **Step 3: Declare the method (header)**

In `include/ams_backend_ace.h`, immediately after the `parse_ace_object` declaration (line ~184),
still inside the protected parsing section:

```cpp
    /**
     * @brief Parse the multiACE multi-unit `units[]` array into system_info_.units.
     *
     * Invoked from parse_ace_object when the `ace` object carries a non-empty
     * `units` array (multiACE/SP1). Rebuilds system_info_.units from scratch —
     * one AmsUnit per entry — and sets total_slots / current_tool / current_slot
     * from the top-level fields. Non-multiACE firmware (no `units[]`) uses the
     * single-unit flat-`slots[]` path instead. Caller (parse_ace_object) holds mutex_.
     *
     * @param data JSON `ace` object containing `units`
     */
    void parse_units_array(const nlohmann::json& data);
```

- [ ] **Step 4: Add the branch in `parse_ace_object`**

In `src/printer/ams_backend_ace.cpp`, insert immediately after the status→action block (after the
`system_info_.action = action;` closing `}` at line ~656) and BEFORE the `// Parse slots array`
comment (line ~658):

```cpp
    // multiACE multi-unit path (SP2): when the ace object carries a non-empty
    // units[] array, populate system_info_.units from it and skip the
    // single-unit flat-slots[] parse below. Non-multiACE firmware (ValgACE/
    // BunnyACE/DuckACE) has no units[] and falls through to the flat path.
    if (data.contains("units") && data["units"].is_array() && !data["units"].empty()) {
        parse_units_array(data);
        return;
    }
```

- [ ] **Step 5: Implement `parse_units_array`**

In `src/printer/ams_backend_ace.cpp`, insert the full method between the end of `parse_ace_object`
(after its closing `}` at line ~854) and the start of `parse_slot_color` (line ~856):

```cpp
void AmsBackendAce::parse_units_array(const json& data) {
    // Caller (parse_ace_object) holds mutex_. Never throws into the response callback.
    const auto& units_arr = data["units"];

    // Rebuild from scratch so a re-parse never appends onto previously-emplaced units.
    system_info_.units.clear();

    int computed_total = 0;
    for (const auto& uj : units_arr) {
        if (!uj.is_object()) {
            spdlog::warn("[ACE] Skipping non-object entry in units[]");
            continue;
        }

        AmsUnit unit;
        if (uj.contains("unit_index") && uj["unit_index"].is_number_integer())
            unit.unit_index = uj["unit_index"].get<int>();
        if (uj.contains("name") && uj["name"].is_string())
            unit.name = uj["name"].get<std::string>();
        if (uj.contains("display_name") && uj["display_name"].is_string())
            unit.display_name = uj["display_name"].get<std::string>();
        if (uj.contains("first_slot_global_index") &&
            uj["first_slot_global_index"].is_number_integer())
            unit.first_slot_global_index = uj["first_slot_global_index"].get<int>();
        if (uj.contains("connected") && uj["connected"].is_boolean())
            unit.connected = uj["connected"].get<bool>();

        // Per-unit environment: temperature always; humidity only when flagged (SP1 v1 = false).
        if (uj.contains("environment") && uj["environment"].is_object()) {
            const auto& ej = uj["environment"];
            EnvironmentData env;
            if (ej.contains("temperature_c") && ej["temperature_c"].is_number())
                env.temperature_c = ej["temperature_c"].get<float>();
            if (ej.contains("humidity_pct") && ej["humidity_pct"].is_number())
                env.humidity_pct = ej["humidity_pct"].get<float>();
            if (ej.contains("has_humidity") && ej["has_humidity"].is_boolean())
                env.has_humidity = ej["has_humidity"].get<bool>();
            unit.environment = env;
        }

        if (uj.contains("slots") && uj["slots"].is_array()) {
            const auto& slots_arr = uj["slots"];
            unit.slots.resize(slots_arr.size());
            for (size_t i = 0; i < slots_arr.size(); ++i) {
                const auto& sj = slots_arr[i];
                if (!sj.is_object()) continue;
                SlotInfo& slot = unit.slots[i];

                slot.slot_index =
                    (sj.contains("slot_index") && sj["slot_index"].is_number_integer())
                        ? sj["slot_index"].get<int>()
                        : static_cast<int>(i);
                slot.global_index =
                    (sj.contains("global_index") && sj["global_index"].is_number_integer())
                        ? sj["global_index"].get<int>()
                        : unit.first_slot_global_index + static_cast<int>(i);

                // Status: empty->EMPTY, available/loaded/ready->AVAILABLE, else (incl. unknown)->UNKNOWN.
                if (sj.contains("status") && sj["status"].is_string()) {
                    const std::string ss = sj["status"].get<std::string>();
                    if (ss == "empty") {
                        slot.status = SlotStatus::EMPTY;
                    } else if (ss == "available" || ss == "loaded" || ss == "ready") {
                        slot.status = SlotStatus::AVAILABLE;
                    } else {
                        slot.status = SlotStatus::UNKNOWN;
                    }
                }

                if (sj.contains("color"))
                    slot.color_rgb = parse_slot_color(sj["color"]);
                if (sj.contains("type") && sj["type"].is_string())
                    slot.material = sj["type"].get<std::string>();
                if (sj.contains("brand") && sj["brand"].is_string())
                    slot.brand = sj["brand"].get<std::string>();

                // mapped_tool: carry SP1's explicit value verbatim (4 heads / up to 16 slots —
                // NOT the single-unit "tool == slot index" default).
                if (sj.contains("mapped_tool") && sj["mapped_tool"].is_number_integer())
                    slot.mapped_tool = sj["mapped_tool"].get<int>();
            }
        }

        unit.slot_count = (uj.contains("slot_count") && uj["slot_count"].is_number_integer())
                              ? uj["slot_count"].get<int>()
                              : static_cast<int>(unit.slots.size());
        computed_total += static_cast<int>(unit.slots.size());
        system_info_.units.push_back(std::move(unit));
    }

    // total_slots: prefer the top-level aggregate, else the sum of parsed slots.
    if (data.contains("total_slots") && data["total_slots"].is_number_integer())
        system_info_.total_slots = data["total_slots"].get<int>();
    else
        system_info_.total_slots = computed_total;

    // current tool/slot come from the top level in the multi-unit contract.
    if (data.contains("current_tool") && data["current_tool"].is_number_integer())
        system_info_.current_tool = data["current_tool"].get<int>();
    if (data.contains("current_slot") && data["current_slot"].is_number_integer()) {
        system_info_.current_slot = data["current_slot"].get<int>();
        system_info_.filament_loaded = (system_info_.current_slot >= 0);
    }

    spdlog::info("[ACE] multiACE multi-unit: {} units, {} total slots",
                 system_info_.units.size(), system_info_.total_slots);
}
```

- [ ] **Step 6: Run the tests, verify they PASS**

`bash /tmp/sp2_build.sh "[ams][ace][multiunit]"` → all 5 cases pass.

- [ ] **Step 7: Run the full ACE suite (no regression)**

`bash /tmp/sp2_build.sh "[ace]"` → "All tests passed" (≥ prior 36 cases + new).

- [ ] **Step 8: Commit**

```bash
cd /mnt/e/Code/helixscreen
git add src/printer/ams_backend_ace.cpp include/ams_backend_ace.h tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(ace): parse multiACE units[] into AmsSystemInfo.units (SP2)"
```

---

### Task 2: Tap-guard `change_tool` under multi-unit

**Files:**
- Test: `tests/unit/test_ams_backend_ace.cpp` (add 2 cases)
- Modify: `src/printer/ams_backend_ace.cpp` (`change_tool`)

- [ ] **Step 1: Write the failing tests**

Append:

```cpp
TEST_CASE("ACE multiunit: change_tool is guarded (no single-unit tool change)",
          "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_multiunit_payload());
    // Guard short-circuits to SUCCESS without reaching the single-unit load path.
    REQUIRE(helper.change_tool(0).result == AmsResult::SUCCESS);
}

TEST_CASE("ACE single-unit: change_tool still reaches the load path (guard does not fire)",
          "[ams][ace][multiunit]") {
    // Flat slots[] (no units[]) -> single unit. Unstarted helper: the load path
    // hits check_preconditions() and returns NOT_CONNECTED, proving the guard did
    // NOT fire (it would have returned SUCCESS).
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_slot_payload("available", 0x00FF00, "PETG"));
    auto info = helper.get_test_system_info();
    REQUIRE_FALSE(info.is_multi_unit());
    REQUIRE(helper.change_tool(0).result == AmsResult::NOT_CONNECTED);
}
```

- [ ] **Step 2: Run, verify FAIL**

`bash /tmp/sp2_build.sh "[ams][ace][multiunit]"`
Expected: the multi-unit case fails — current `change_tool` calls `load_filament` →
`check_preconditions()` → `NOT_CONNECTED`, so `.result == SUCCESS` fails. (The single-unit case
already passes; that's the regression guard.)

- [ ] **Step 3: Add the guard**

Replace the body of `change_tool` (lines ~386–388):

```cpp
AmsError AmsBackendAce::change_tool(int tool_number) {
    // Tap-guard (SP2): under a multiACE multi-unit setup, a slot tap must not
    // issue the single-unit ACE_CHANGE_TOOL — head<->slot mapping is not 1:1
    // (4 heads / up to 16 slots). Deliberate multi-unit loads are wired in SP3
    // (Loadout/Recovery panels). No-op here so a stray tap can't drive the wrong
    // tool change.
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (system_info_.is_multi_unit()) {
            spdlog::info("[ACE] change_tool({}) ignored under multi-unit multiACE "
                         "— manage from the multiACE panel", tool_number);
            return AmsErrorHelper::success();
        }
    }
    return load_filament(tool_number);
}
```

- [ ] **Step 4: Run, verify PASS**

`bash /tmp/sp2_build.sh "[ams][ace][multiunit]"` → both cases pass.

- [ ] **Step 5: Full ACE suite**

`bash /tmp/sp2_build.sh "[ace]"` → all pass.

- [ ] **Step 6: Commit**

```bash
cd /mnt/e/Code/helixscreen
git add src/printer/ams_backend_ace.cpp tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(ace): tap-guard change_tool under multi-unit multiACE (SP2)"
```

---

### Task 3: Fallback regression guard (flat `slots[]`, no `units[]`)

**Files:**
- Test: `tests/unit/test_ams_backend_ace.cpp` (add 1 case)

- [ ] **Step 1: Add the regression test**

```cpp
TEST_CASE("ACE fallback: flat slots[] with no units[] yields a single unit",
          "[ams][ace][multiunit]") {
    AmsBackendAceTestHelper helper;
    AceTestAccess::parse_ace(helper, make_ace_slot_payload("available", 0x123456, "ABS"));
    auto info = helper.get_test_system_info();
    REQUIRE(info.units.size() == 1);
    REQUIRE_FALSE(info.is_multi_unit());
    REQUIRE(info.total_slots == 1);
    REQUIRE(info.units[0].slots[0].material == "ABS");
}
```

- [ ] **Step 2: Run — should PASS immediately**

`bash /tmp/sp2_build.sh "[ams][ace][multiunit]"` → passes (the flat path is untouched; the branch
only fires for non-empty `units[]`). This case exists to lock the no-regression contract.

- [ ] **Step 3: Full suite + commit**

```bash
bash /tmp/sp2_build.sh "[ace]"
cd /mnt/e/Code/helixscreen
git add tests/unit/test_ams_backend_ace.cpp
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "test(ace): lock single-unit fallback (no units[]) regression (SP2)"
```

---

## Acceptance criteria (from spec)

1. `make test` green incl. new cases; full `[ace]` suite passes in the container.
2. Multi-unit `ace` JSON → `AmsSystemInfo.units` with correct counts, AFC-convention global
   indices (0..7), `connected`, `environment`, and sparse `mapped_tool` (not 1:1).
3. Absent `units[]` → unchanged single-unit behavior (regression test passes).
4. `change_tool` guarded under multi-unit (returns SUCCESS no-op; single-unit reaches load path).
5. Minimal/surgical diff (one branch + one method + one guard + tests); flat path untouched.
   Local branch `sp2-multiunit-ace` only; not pushed to origin/upstream.

## Self-review notes

- **Spec coverage:** every "Testing" bullet maps to a case (multi-unit parse, mapped_tool sparse,
  connected/offline, environment, color/material/brand, fallback, tap-guard). ✓
- **Type consistency:** `SlotInfo`/`AmsUnit`/`AmsSystemInfo`/`EnvironmentData`/`SlotStatus` and
  `AmsErrorHelper::success()`/`AmsResult::{SUCCESS,NOT_CONNECTED}` verified against headers. ✓
- **No placeholders:** all test + impl code is complete and ready to paste. ✓
