"""Tests for filament_switch_sensor_ace.build_runout_message — the Klipper-free
builder that turns a bare '<name> runout' into an actionable, ACE/slot-aware
pause message. ACE/slot labels are 0-based to match the firmware SLOT param."""
import filament_switch_sensor_ace as fss

build = fss.build_runout_message


def test_includes_ace_and_slot_when_source_known():
    head_source = {0: {"ace_index": 1, "slot": 2}}
    msg = build("e0_sensor", 0, head_source)
    assert "e0_sensor runout" in msg
    assert "(ACE 1 / Slot 2)" in msg
    assert "RESUME" in msg
    assert "Reload" in msg


def test_zero_based_labels():
    head_source = {3: {"ace_index": 0, "slot": 0}}
    msg = build("e3_sensor", 3, head_source)
    assert "(ACE 0 / Slot 0)" in msg


def test_plain_message_when_no_source():
    msg = build("e0_sensor", 0, None)
    assert msg.startswith("[multiACE] e0_sensor runout")
    assert "ACE" not in msg.split("runout", 1)[1].split("-", 1)[0]  # no location chunk
    assert "RESUME" in msg


def test_plain_when_head_absent_from_source():
    head_source = {1: {"ace_index": 0, "slot": 0}}
    msg = build("e0_sensor", 0, head_source)  # head 0 not present
    assert "/ Slot" not in msg


def test_plain_when_source_partial():
    head_source = {0: {"ace_index": 1}}  # no slot
    msg = build("e0_sensor", 0, head_source)
    assert "/ Slot" not in msg
