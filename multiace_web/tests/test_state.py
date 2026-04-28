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
