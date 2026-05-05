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


from multiace_web.autodryer import (
    DEFAULT_PROFILES,
    cycle_params_for,
    reconcile_loaded_slots,
)


# ---- profile lookup ----

def test_default_profiles_cover_industry_set():
    """All major filament types have a default profile."""
    ids = {p["id"] for p in DEFAULT_PROFILES}
    for required in {"PLA", "PETG", "TPU", "ABS", "ASA", "PA", "PC", "PVA"}:
        assert required in ids


def test_cycle_params_for_known_filament_uses_default():
    p = cycle_params_for("PLA", user_profiles=None)
    assert p["temp_c"] == 50
    assert p["duration_min"] == 360


def test_cycle_params_for_user_override():
    user = [{"id": "PLA", "temp": 55, "duration": 420}]
    p = cycle_params_for("PLA", user_profiles=user)
    assert p["temp_c"] == 55
    assert p["duration_min"] == 420


def test_cycle_params_for_unknown_filament_uses_fallback():
    p = cycle_params_for("UnknownExoticBlend", user_profiles=None)
    # 50°C × 360 min — conservative PLA-equivalent
    assert p["temp_c"] == 50
    assert p["duration_min"] == 360


def test_cycle_params_for_case_insensitive_match():
    p = cycle_params_for("pla", user_profiles=None)
    assert p["temp_c"] == 50


# ---- mixed-load reconciliation ----

def test_reconcile_single_slot_no_warning():
    r = reconcile_loaded_slots(["PLA"], user_profiles=None)
    assert r["effective_temp_c"] == 50
    assert r["effective_duration_min"] == 360
    assert r["mixed_filament_warning"] is False


def test_reconcile_pla_plus_nylon_uses_strictest_temp_and_shortest_duration():
    """PLA = 50°C × 360 min, Nylon = 70°C × 1440 min (capped at 480).
    Mixed: 50°C (strictest cap) × 360 min (shortest), with warning."""
    r = reconcile_loaded_slots(["PLA", "PA"], user_profiles=None)
    assert r["effective_temp_c"] == 50  # min(50, 70) = 50 (PLA)
    assert r["effective_duration_min"] == 360  # min(360, 1440) = 360 (PLA)
    assert r["mixed_filament_warning"] is True


def test_reconcile_clamps_duration_at_480():
    """Even a single-load Nylon profile (1440 min) gets clamped at the
    hardware ACE cycle limit of 480 min."""
    r = reconcile_loaded_slots(["PA"], user_profiles=None)
    assert r["effective_duration_min"] == 480
    assert r["mixed_filament_warning"] is False


def test_reconcile_empty_load_returns_none():
    r = reconcile_loaded_slots([], user_profiles=None)
    assert r["effective_temp_c"] is None
    assert r["effective_duration_min"] is None


def test_reconcile_dedupes_same_type():
    """PLA + PLA isn't mixed — no warning."""
    r = reconcile_loaded_slots(["PLA", "PLA"], user_profiles=None)
    assert r["mixed_filament_warning"] is False


from multiace_web.autodryer import DebounceBuffer


def test_debounce_buffer_starts_unfilled():
    b = DebounceBuffer(required=5)
    assert b.is_above_threshold() is False
    assert len(b) == 0


def test_debounce_buffer_fills_after_5_consecutive_above():
    b = DebounceBuffer(required=5)
    for _ in range(4):
        b.observe_above()
        assert b.is_above_threshold() is False
    b.observe_above()
    assert b.is_above_threshold() is True


def test_debounce_buffer_single_dip_resets_count():
    b = DebounceBuffer(required=5)
    for _ in range(4):
        b.observe_above()
    b.observe_below()  # one dip
    assert b.is_above_threshold() is False
    # Need 5 fresh consecutive aboves, not just 1 more.
    for _ in range(4):
        b.observe_above()
        assert b.is_above_threshold() is False
    b.observe_above()
    assert b.is_above_threshold() is True


def test_debounce_buffer_required_one_triggers_immediately():
    b = DebounceBuffer(required=1)
    b.observe_above()
    assert b.is_above_threshold() is True


def test_debounce_buffer_reset_clears_count():
    b = DebounceBuffer(required=3)
    b.observe_above()
    b.observe_above()
    b.reset()
    assert b.is_above_threshold() is False
    b.observe_above()
    assert b.is_above_threshold() is False  # need 3 fresh, not continue
