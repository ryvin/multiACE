"""Tests for the auto-dry FSM. Pure logic only — no HTTP, no Moonraker.

The live scan loop and Moonraker calls are tested separately in
test_announcements.py and the server-level integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multiace_web.autodryer import (
    FSMState,
    PersistedState,
    load_persisted_state,
    save_persisted_state,
)


def test_persisted_state_defaults():
    s = PersistedState()
    assert s.mode == "off"
    assert s.target_ace == 0
    assert s.target_pct == 15
    assert s.hysteresis_pp == 5
    assert s.fsm.state == FSMState.IDLE
    assert s.fsm.fault is None
    assert s.fsm.last_run is None
    assert s.fsm.daily_duty == []
    assert s.fsm.trigger_announcement_id is None


def test_save_then_load_roundtrip(tmp_path: Path):
    path = tmp_path / "autodry.json"
    s = PersistedState(mode="log", target_pct=12, hysteresis_pp=3)
    s.fsm.state = FSMState.WATCHING
    save_persisted_state(path, s)

    loaded = load_persisted_state(path)
    assert loaded.mode == "log"
    assert loaded.target_pct == 12
    assert loaded.hysteresis_pp == 3
    assert loaded.fsm.state == FSMState.WATCHING


def test_load_returns_defaults_when_file_missing(tmp_path: Path):
    s = load_persisted_state(tmp_path / "nope.json")
    assert s.mode == "off"
    assert s.fsm.state == FSMState.IDLE


def test_load_returns_defaults_on_corrupt_file(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("{garbage")
    s = load_persisted_state(path)
    # We don't crash — broken file means "start over with defaults".
    assert s.mode == "off"


def test_save_is_atomic(tmp_path: Path):
    """save writes via tmp + os.replace so a crash mid-write can't corrupt."""
    path = tmp_path / "autodry.json"
    s = PersistedState(mode="active")
    save_persisted_state(path, s)
    # The temp file from the atomic-write should be cleaned up.
    assert not (tmp_path / "autodry.json.tmp").exists()
    # Loaded back, the value is intact.
    assert load_persisted_state(path).mode == "active"


def test_save_creates_parent_dir(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "autodry.json"
    save_persisted_state(path, PersistedState(mode="log"))
    assert path.exists()
    assert load_persisted_state(path).mode == "log"


def test_unknown_fields_in_loaded_json_are_ignored(tmp_path: Path):
    """Forward-compatible: a future spec adds a field; we don't crash on old multiace-web."""
    path = tmp_path / "autodry.json"
    path.write_text(json.dumps({
        "mode": "log",
        "target_ace": 0,
        "target_pct": 15,
        "hysteresis_pp": 5,
        "future_field_we_dont_know_about": "hello",
        "fsm": {"state": "WATCHING", "future_inner_field": 42},
    }))
    s = load_persisted_state(path)
    assert s.mode == "log"
    assert s.fsm.state == FSMState.WATCHING
