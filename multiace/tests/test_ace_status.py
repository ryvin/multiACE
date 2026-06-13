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
    assert "type" not in slot
    assert "brand" not in slot
    assert "sku" not in slot


def test_build_environment_temp_only_no_humidity():
    env = ace_status._build_environment({"temp": 24, "status": "ready"})
    assert env == {"temperature_c": 24.0, "humidity_pct": 0.0, "has_humidity": False}


def test_build_environment_uses_humidity_when_present():
    env = ace_status._build_environment({"temp": 25, "humidity": 31})
    assert env == {"temperature_c": 25.0, "humidity_pct": 31.0, "has_humidity": True}


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
    entry = {"result": _frame_fixture(), "recv_ts": 90.0}
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
    assert [s["global_index"] for s in out["slots"]] == list(range(8))
    assert out["slots"][0]["mapped_tool"] == 0
    assert out["slots"][6]["mapped_tool"] == 1
    assert sum(1 for s in out["slots"] if s["mapped_tool"] != -1) == 2


def test_build_status_current_tool_from_active_unit():
    out = ace_status.build_multiace_status(
        devices=["a", "b"], active_index=0, head_source=_head_source_fixture(),
        last_status=_two_device_last_status(), now=100.0, firmware_version="0.81b")
    assert out["active_unit"] == 0
    assert out["current_tool"] == 0
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
    assert out["device_count"] == 1
    assert len(out["units"]) == 1


def test_build_status_active_index_out_of_range_is_minus_one():
    out = ace_status.build_multiace_status(
        devices=["a", "b"], active_index=5, head_source=None,
        last_status=_two_device_last_status(), now=100.0, firmware_version="0.81b")
    assert out["active_unit"] == -1
    assert out["current_tool"] == -1
    assert out["current_slot"] == -1


def test_build_slot_invalid_rfid_is_dropped():
    sf = {"status": "ready", "rfid": "not-an-int", "color": [1, 2, 3]}
    slot = ace_status._build_slot(0, 0, sf, mapped_tool=-1)
    assert "rfid" not in slot
    assert slot["color"] == [1, 2, 3]


def test_sensors_out_emits_list_of_four_bools_when_provided():
    out = ace_status._build_sensors_out({0: True, 1: False, 2: True, 3: False})
    assert out == [True, False, True, False]


def test_sensors_out_defaults_false_for_missing_heads():
    out = ace_status._build_sensors_out(None)
    assert out == [False, False, False, False]
    out = ace_status._build_sensors_out({})
    assert out == [False, False, False, False]
    out = ace_status._build_sensors_out({0: True})
    assert out == [True, False, False, False]


def test_build_status_exposes_top_level_sensors_array():
    now = 100.0
    last_status = {0: {"recv_ts": now, "result": {"slots": [], "temp": 21}}}
    head_source = {0: {"ace_index": 0, "slot": 0, "brand": "x", "type": "PLA", "color": [1, 2, 3]}}
    sensors = {0: True, 1: False, 2: False, 3: False}
    out = ace_status.build_multiace_status(
        devices=["/dev/x"], active_index=0, head_source=head_source,
        last_status=last_status, now=now, firmware_version="0.81b",
        sensors_per_head=sensors)
    assert out["sensors"] == [True, False, False, False]
    # head_source entries do NOT carry sensor field — that lives at top-level
    assert "sensor" not in out["head_source"][0]


def test_minimal_frame_includes_empty_sensors_array():
    out = ace_status.build_multiace_status(
        devices=[], active_index=-1, head_source={}, last_status={}, now=0.0,
        firmware_version="0.81b")
    assert out["sensors"] == [False, False, False, False]
