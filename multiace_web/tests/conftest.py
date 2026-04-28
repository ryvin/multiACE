import pytest


@pytest.fixture
def sample_state_event():
    """One realistic line from multiace_state.log, parsed."""
    return {
        "action": "LOAD_HEAD",
        "params": {"head": 1, "ace": 0, "slot": 1},
        "active_device": 0,
        "device_count": 1,
        "connected": True,
        "serial": "/dev/serial/by-path/example",
        "mode": "multi",
        "swap_in_progress": False,
        "auto_feed": False,
        "feed_assist": 1,
        "gate_status": [1, 1, 1, 1],
        "head_source": {
            "0": {"ace": 0, "slot": 0, "type": "", "color": "000000"},
            "1": {"ace": 0, "slot": 1, "type": "", "color": "000000"},
            "2": None,
            "3": None,
        },
        "sensors": {"0": True, "1": True, "2": False, "3": False},
        "print_task_config": {
            "0": {"type": "NONE", "color": 4294967295, "vendor": "NONE"},
            "1": {"type": "", "color": 4278190080, "vendor": "Generic"},
            "2": {"type": "", "color": 4278190080, "vendor": "Generic"},
            "3": {"type": "", "color": 4278190080, "vendor": "Generic"},
        },
    }


@pytest.fixture
def sample_state_log_line():
    """Raw line as it appears in multiace_state.log (timestamp + STATE + JSON)."""
    return (
        "2026-04-27 23:40:52 STATE "
        '{"action": "LOAD_HEAD", "params": {"head": 1, "ace": 0, "slot": 1}, '
        '"active_device": 0, "device_count": 1, "connected": true, '
        '"serial": "/dev/serial/by-path/example", "mode": "multi", '
        '"swap_in_progress": false, "auto_feed": false, "feed_assist": 1, '
        '"gate_status": [1, 1, 1, 1], '
        '"head_source": {"0": {"ace": 0, "slot": 0, "type": "", "color": "000000"}, '
        '"1": {"ace": 0, "slot": 1, "type": "", "color": "000000"}, "2": null, "3": null}, '
        '"sensors": {"0": true, "1": true, "2": false, "3": false}, '
        '"print_task_config": {"0": {"type": "NONE", "color": 4294967295, "vendor": "NONE"}, '
        '"1": {"type": "", "color": 4278190080, "vendor": "Generic"}, '
        '"2": {"type": "", "color": 4278190080, "vendor": "Generic"}, '
        '"3": {"type": "", "color": 4278190080, "vendor": "Generic"}}}\n'
    )
