from multiace_web.state import CurrentState, EventBuffer, parse_state_log_line, STATE_MARKER


def test_parse_state_log_line_returns_timestamp_and_data(sample_state_log_line):
    ts, data = parse_state_log_line(sample_state_log_line)
    assert ts == "2026-04-27 23:40:52"
    assert data["action"] == "LOAD_HEAD"
    assert data["active_device"] == 0
    assert data["sensors"]["1"] is True


def test_parse_state_log_line_returns_none_on_malformed():
    result = parse_state_log_line("not a state log line\n")
    assert result is None


def test_current_state_initial_values():
    state = CurrentState()
    assert state.active_device is None
    assert state.connected is False
    assert state.swap_in_progress is False
    assert state.gate_status == [0, 0, 0, 0]
    assert state.head_source == {0: None, 1: None, 2: None, 3: None}
    assert state.sensors == {0: False, 1: False, 2: False, 3: False}
    assert state.last_error is None


def test_current_state_apply_event_updates_fields(sample_state_event):
    state = CurrentState()
    state.apply_event(sample_state_event)
    assert state.active_device == 0
    assert state.connected is True
    assert state.gate_status == [1, 1, 1, 1]
    assert state.head_source[0] == {"ace": 0, "slot": 0, "type": "", "color": "000000"}
    assert state.sensors[1] is True
    assert state.mode == "multi"


def test_current_state_load_head_failed_sets_last_error():
    state = CurrentState()
    state.apply_event({
        "action": "LOAD_HEAD_FAILED",
        "params": {"head": 1, "ace": 0, "slot": 1, "reason": "feed_auto_error",
                   "error": "extruder[1]: timeout!"},
        "active_device": 0,
        "connected": True,
        "swap_in_progress": False,
        "gate_status": [1, 1, 1, 1],
        "head_source": {"0": None, "1": None, "2": None, "3": None},
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    })
    assert state.last_error is not None
    assert state.last_error["head"] == 1
    assert "timeout" in state.last_error["error"]


def test_event_buffer_append_and_read():
    buf = EventBuffer(maxlen=3)
    buf.append({"action": "A"})
    buf.append({"action": "B"})
    buf.append({"action": "C"})
    buf.append({"action": "D"})  # evicts A
    events = buf.recent(limit=10)
    actions = [e["action"] for e in events]
    assert actions == ["B", "C", "D"]


def test_event_buffer_since_id_returns_only_newer():
    buf = EventBuffer(maxlen=10)
    e1 = buf.append({"action": "A"})
    e2 = buf.append({"action": "B"})
    e3 = buf.append({"action": "C"})
    new = buf.since(e1)
    assert [e["action"] for e in new] == ["B", "C"]


def test_apply_event_with_ts_updates_last_action_at():
    state = CurrentState()
    assert state.last_action_at is None
    state.apply_event({"action": "LOAD_HEAD", "active_device": 0, "connected": True,
                       "swap_in_progress": False, "gate_status": [1,1,1,1],
                       "head_source": {"0": None, "1": None, "2": None, "3": None},
                       "sensors": {"0": False, "1": False, "2": False, "3": False}},
                      ts="2026-04-27 23:40:52")
    assert state.last_action_at == "2026-04-27 23:40:52"


def test_event_buffer_recent_zero_returns_empty():
    buf = EventBuffer(maxlen=10)
    buf.append({"action": "A"})
    buf.append({"action": "B"})
    assert buf.recent(0) == []


def test_parse_state_log_line_returns_none_on_non_dict_body():
    line = "2026-04-27 23:40:52 STATE [1, 2, 3]"
    result = parse_state_log_line(line)
    assert result is None


def test_load_head_clears_matching_last_error():
    state = CurrentState()
    state.apply_event({
        "action": "LOAD_HEAD_FAILED",
        "params": {"head": 1, "ace": 0, "slot": 1, "error": "fail"},
        "active_device": 0, "connected": True, "swap_in_progress": False,
        "gate_status": [1,1,1,1],
        "head_source": {"0": None, "1": None, "2": None, "3": None},
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    })
    assert state.last_error is not None
    state.apply_event({
        "action": "LOAD_HEAD",
        "params": {"head": 1, "ace": 0, "slot": 1},
        "active_device": 0, "connected": True, "swap_in_progress": False,
        "gate_status": [1,1,1,1],
        "head_source": {"0": None, "1": {"ace":0,"slot":1}, "2": None, "3": None},
        "sensors": {"0": False, "1": True, "2": False, "3": False},
    })
    assert state.last_error is None


def test_switch_terminal_actions_force_clear_swap_in_progress():
    """Every SWITCH-family audit fires inside cmd_ACE_SWITCH's try block
    while _swap_in_progress is still True; the firmware's finally clears
    the flag but emits no follow-up audit. apply_event must treat the
    terminal SWITCH/SWITCH_NOOP/SWITCH_FAILED/SWITCH_AUTO* /SWITCH_TARGET*
    actions as swap-complete to avoid permanently sticking the
    "Tool change in progress" banner."""
    base = {
        "active_device": 1,
        "connected": True,
        "swap_in_progress": True,
        "gate_status": [1, 1, 1, 1],
        "head_source": {"0": None, "1": None, "2": None, "3": None},
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    }
    terminal = [
        "SWITCH", "SWITCH_NOOP", "SWITCH_FAILED",
        "SWITCH_AUTO", "SWITCH_AUTO_NOOP", "SWITCH_AUTO_FAILED", "SWITCH_AUTO_PASSIVE",
        "SWITCH_TARGET", "SWITCH_TARGET_NOOP", "SWITCH_TARGET_FAILED",
    ]
    for action in terminal:
        state = CurrentState()
        state.swap_in_progress = True
        state.apply_event({"action": action, "params": {}, **base})
        assert state.swap_in_progress is False, f"action={action} did not clear sip"


def test_non_switch_actions_preserve_sip_from_payload():
    """A non-SWITCH event should leave swap_in_progress as the payload says,
    so a real in-flight swap (LOAD_HEAD audit during the swap) remains
    visible until firmware clears it."""
    state = CurrentState()
    state.apply_event({
        "action": "LOAD_HEAD",
        "params": {"head": 0, "ace": 0, "slot": 0},
        "active_device": 0, "connected": True,
        "swap_in_progress": True,
        "gate_status": [1, 1, 1, 1],
        "head_source": {"0": {"ace": 0, "slot": 0}, "1": None, "2": None, "3": None},
        "sensors": {"0": True, "1": False, "2": False, "3": False},
    })
    assert state.swap_in_progress is True


def test_head_source_parked_field_round_trips():
    """parked:True on head_source must survive apply_event → serialise."""
    state = CurrentState()
    state.apply_event({
        "action": "PARK_HEAD",
        "params": {"head": 0, "ace": 0, "slot": 0},
        "active_device": 0,
        "connected": True,
        "swap_in_progress": False,
        "gate_status": [1, 1, 1, 1],
        "head_source": {
            "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000", "parked": True},
            "1": None, "2": None, "3": None,
        },
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    })
    assert state.head_source[0] is not None
    assert state.head_source[0].get("parked") is True
    payload = state.to_dict()
    assert payload["head_source"][0]["parked"] is True


def test_apply_event_ignores_unknown_fields():
    # Forward-compat: when the firmware adds new top-level audit fields
    # (e.g. fa_context from the decay71 0.95b port, or future additions),
    # the tailer must not crash and must still apply the known fields.
    state = CurrentState()
    state.apply_event({
        "action": "LOAD_HEAD",
        "params": {},
        "active_device": 0,
        "connected": True,
        "swap_in_progress": False,
        "gate_status": [1, 1, 1, 1],
        "head_source": {"0": None, "1": None, "2": None, "3": None},
        "sensors": {"0": False, "1": False, "2": False, "3": False},
        "fa_context": "print",                # new in 0.82+
        "tip_refresh_active": True,           # hypothetical future field
        "nested_unknown": {"a": 1, "b": [2]}, # nested unknowns must not raise
    })
    assert state.active_device == 0
    assert state.connected is True
