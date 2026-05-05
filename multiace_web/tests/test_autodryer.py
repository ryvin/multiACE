"""Tests for the auto-dry FSM. Pure logic only — no HTTP, no Moonraker.

The live scan loop and Moonraker calls are tested separately in
test_announcements.py and the server-level integration tests."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from multiace_web.autodryer import (
    Fault,
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


from multiace_web.autodryer import (
    Ephemeral,
    Inputs,
    Transition,
    tick_fsm,
)


def _base_inputs(**overrides) -> Inputs:
    """Convenient default inputs for tests — single-ACE, single-PLA, idle Klipper."""
    base = Inputs(
        active_device=0,
        head_source={"0": {"ace": 0, "slot": 0, "type": "PLA"}},
        swap_in_progress=False,
        humidity_ok=True,
        humidity_pct=18.0,
        cavity_temp_c=22.0,
        klipper_print_state="standby",
        dryer_status="stop",
        user_profiles=None,
    )
    return dataclasses.replace(base, **overrides)


def _base_persisted(**overrides) -> PersistedState:
    base = PersistedState(mode="active", target_ace=0, target_pct=15, hysteresis_pp=5)
    base.fsm.state = FSMState.WATCHING
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---- IDLE → WATCHING ----

def test_idle_to_watching_when_mode_log_and_loaded():
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.mode = "log"
    eph = Ephemeral()
    new, transitions = tick_fsm(p, eph, _base_inputs(humidity_pct=10.0), now_ts=1000.0)
    assert new.fsm.state == FSMState.WATCHING


def test_idle_when_mode_off():
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.mode = "off"
    eph = Ephemeral()
    new, transitions = tick_fsm(p, eph, _base_inputs(), now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


def test_idle_when_active_device_not_target():
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.target_ace = 0
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(active_device=1), now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


def test_idle_when_no_filament_in_target_ace():
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    eph = Ephemeral()
    inputs = _base_inputs(head_source={})
    new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


def test_idle_when_sensor_unavailable():
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(humidity_ok=False), now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


# ---- WATCHING → DRYING ----

def test_watching_does_not_trigger_below_wake_threshold():
    p = _base_persisted()
    eph = Ephemeral()
    # Wake = 15 + 5 = 20%. RH at 18% should not trigger.
    new, _ = tick_fsm(p, eph, _base_inputs(humidity_pct=18.0), now_ts=1000.0)
    assert new.fsm.state == FSMState.WATCHING


def test_watching_requires_5_consecutive_above_to_trigger():
    p = _base_persisted()
    eph = Ephemeral()
    inputs = _base_inputs(humidity_pct=22.0)
    # First 4 ticks above wake — still WATCHING (debounce not satisfied)
    for i in range(4):
        new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0 + 60 * i)
        assert new.fsm.state == FSMState.WATCHING
        p = new
    # 5th tick triggers
    new, transitions = tick_fsm(p, eph, inputs, now_ts=1000.0 + 60 * 4)
    assert new.fsm.state == FSMState.DRYING
    assert any(t.event == "AUTODRY_TRIGGERED" for t in transitions)


def test_watching_single_dip_resets_debounce():
    p = _base_persisted()
    eph = Ephemeral()
    high = _base_inputs(humidity_pct=22.0)
    low = _base_inputs(humidity_pct=10.0)
    for i in range(4):
        new, _ = tick_fsm(p, eph, high, now_ts=1000.0 + 60 * i)
        p = new
    # Dip below
    new, _ = tick_fsm(p, eph, low, now_ts=1000.0 + 60 * 4)
    assert new.fsm.state == FSMState.WATCHING
    p = new
    # Need 5 fresh aboves now, not 1 more
    new, _ = tick_fsm(p, eph, high, now_ts=1000.0 + 60 * 5)
    assert new.fsm.state == FSMState.WATCHING


# ---- mode=log gates the trigger ----

def test_mode_log_emits_dry_run_event_no_drying_state():
    p = _base_persisted()
    p.mode = "log"
    eph = Ephemeral()
    inputs = _base_inputs(humidity_pct=22.0)
    for _ in range(5):
        p, transitions = tick_fsm(p, eph, inputs, now_ts=1000.0)
    # Mode=log: emits AUTODRY_DRY_RUN, goes straight to COOLDOWN (no DRYING)
    assert p.fsm.state == FSMState.COOLDOWN
    assert any(t.event == "AUTODRY_DRY_RUN" for t in transitions)
    assert not any(t.event == "AUTODRY_TRIGGERED" for t in transitions)


# ---- guards (skip events) ----

def test_skipped_print_when_klipper_printing():
    p = _base_persisted()
    eph = Ephemeral()
    inputs = _base_inputs(humidity_pct=22.0, klipper_print_state="printing")
    all_transitions = []
    # Even with 5 sustained above-wake samples, the print guard blocks.
    for _ in range(5):
        p, transitions = tick_fsm(p, eph, inputs, now_ts=1000.0)
        all_transitions.extend(transitions)
    assert p.fsm.state == FSMState.WATCHING
    assert any(t.event == "AUTODRY_SKIPPED_PRINT" for t in all_transitions)


def test_skipped_swap_when_swap_in_progress():
    p = _base_persisted()
    eph = Ephemeral()
    inputs = _base_inputs(humidity_pct=22.0, swap_in_progress=True)
    all_transitions = []
    for _ in range(5):
        p, transitions = tick_fsm(p, eph, inputs, now_ts=1000.0)
        all_transitions.extend(transitions)
    assert p.fsm.state == FSMState.WATCHING
    assert any(t.event == "AUTODRY_SKIPPED_SWAP" for t in all_transitions)


# ---- OBSERVED_DRYING ----

def test_watching_to_observed_drying_when_user_starts_manual():
    p = _base_persisted()
    eph = Ephemeral()
    inputs = _base_inputs(dryer_status="drying")
    new, transitions = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.OBSERVED_DRYING
    # No TRIGGERED event — user already knows
    assert not any(t.event == "AUTODRY_TRIGGERED" for t in transitions)


def test_observed_drying_to_cooldown_when_dryer_stops():
    p = _base_persisted()
    p.fsm.state = FSMState.OBSERVED_DRYING
    eph = Ephemeral()
    inputs = _base_inputs(dryer_status="stop")
    new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.COOLDOWN


# ---- DRYING → COOLDOWN (success) ----

def test_drying_to_cooldown_when_target_reached():
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    p.fsm.since_ts = 1000.0
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    new, transitions = tick_fsm(
        p, eph, _base_inputs(humidity_pct=14.0), now_ts=1000.0 + 60 * 30,
    )
    assert new.fsm.state == FSMState.COOLDOWN
    assert any(t.event == "AUTODRY_FINISHED" for t in transitions)
    assert new.fsm.last_run is not None
    assert new.fsm.last_run.outcome == "success"


# ---- DRYING → FAULTED (max_run_min reached without crossing target) ----

def test_drying_to_faulted_on_max_run():
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    # Tick 13 hours later (> 720 min cap), still above target
    new, transitions = tick_fsm(
        p, eph, _base_inputs(humidity_pct=22.0), now_ts=1000.0 + 60 * 60 * 13,
    )
    assert new.fsm.state == FSMState.FAULTED
    assert new.fsm.fault is not None
    assert new.fsm.fault.code == "FAILED_LIMIT"


# ---- DRYING → FAULTED (min_delta not met) ----

def test_drying_to_faulted_when_delta_too_small():
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    # Cycle ran the full 360 min but RH only dropped 22 → 21 (Δ=1pp; min=3)
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    new, transitions = tick_fsm(
        p, eph, _base_inputs(humidity_pct=21.0),
        now_ts=1000.0 + 60 * 360,
    )
    assert new.fsm.state == FSMState.FAULTED
    assert new.fsm.fault.code == "FAILED_DELTA"


# ---- COOLDOWN → WATCHING ----

def test_cooldown_to_watching_after_cooldown_min():
    p = _base_persisted()
    p.fsm.state = FSMState.COOLDOWN
    p.fsm.cooldown_until_ts = 1000.0 + 60 * 30  # 30 min cooldown
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(), now_ts=1000.0 + 60 * 31)
    assert new.fsm.state == FSMState.WATCHING


def test_cooldown_stays_during_window():
    p = _base_persisted()
    p.fsm.state = FSMState.COOLDOWN
    p.fsm.cooldown_until_ts = 1000.0 + 60 * 30
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(), now_ts=1000.0 + 60 * 15)
    assert new.fsm.state == FSMState.COOLDOWN


# ---- FAULTED is sticky ----

def test_faulted_stays_faulted():
    p = _base_persisted()
    p.fsm.state = FSMState.FAULTED
    p.fsm.fault = Fault(code="FAILED_DELTA", since_ts=1.0, msg="x")
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(humidity_pct=22.0), now_ts=1000.0)
    assert new.fsm.state == FSMState.FAULTED
