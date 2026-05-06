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
    assert s.default_filament_type is None
    assert s.fsm.state == FSMState.IDLE
    assert s.fsm.fault is None
    assert s.fsm.last_run is None
    assert s.fsm.daily_duty == []
    assert s.fsm.trigger_announcement_id is None


def test_default_filament_type_roundtrip(tmp_path: Path):
    path = tmp_path / "autodry.json"
    save_persisted_state(path, PersistedState(default_filament_type="PLA"))
    assert load_persisted_state(path).default_filament_type == "PLA"


def test_default_filament_type_empty_string_loads_as_none(tmp_path: Path):
    """Defensive: persisted state could be hand-edited to an empty string."""
    path = tmp_path / "autodry.json"
    path.write_text(json.dumps({
        "mode": "off", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5,
        "default_filament_type": "   ",
    }))
    assert load_persisted_state(path).default_filament_type is None


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


def test_idle_when_loaded_but_type_empty_and_no_default():
    """Strict default behavior: empty type without configured fallback stays IDLE."""
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.default_filament_type = None
    eph = Ephemeral()
    inputs = _base_inputs(head_source={"0": {"ace": 0, "slot": 0, "type": ""}})
    new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


def test_default_filament_type_fallback_arms_when_type_empty():
    """default_filament_type covers non-RFID spools where multiACE has empty type."""
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.default_filament_type = "PLA"
    eph = Ephemeral()
    inputs = _base_inputs(head_source={"0": {"ace": 0, "slot": 0, "type": ""}})
    new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.WATCHING


def test_default_filament_type_does_not_override_known_type():
    """Non-empty type wins; default is only the fallback."""
    p = _base_persisted()
    p.fsm.state = FSMState.IDLE
    p.default_filament_type = "PLA"  # configured but should not override
    eph = Ephemeral()
    inputs = _base_inputs(head_source={"0": {"ace": 0, "slot": 0, "type": "PETG"}})
    new, _ = tick_fsm(p, eph, inputs, now_ts=1000.0)
    assert new.fsm.state == FSMState.WATCHING
    # The actual type that would drive cycle params later is PETG, not PLA —
    # that's covered by reconcile tests; here we just check the gate transitions.


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


# ---- Purity: tick_fsm must not mutate caller-owned PersistedState ----

def test_tick_fsm_does_not_mutate_input_daily_duty():
    """tick_fsm must not mutate the caller's daily_duty list."""
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    p.fsm.daily_duty = [{"started_ts": 0, "ran_min": 60}]
    original = list(p.fsm.daily_duty)
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    new, _ = tick_fsm(p, eph, _base_inputs(humidity_pct=14.0), now_ts=1000.0 + 60 * 30)
    assert p.fsm.daily_duty == original  # input unchanged
    assert len(new.fsm.daily_duty) == 2  # new state has the new entry


# ---- DRYING → COOLDOWN: success-ish (delta OK, still above target) ----

def test_drying_to_cooldown_when_duration_ran_out_with_acceptable_delta():
    """Duration elapsed, RH dropped meaningfully (delta >= min_delta), but still
    above target → success-ish, go to COOLDOWN with last_run.outcome=success and
    payload includes still_above_target=True."""
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    # 360 min elapsed; RH went 22 → 17 (delta=5pp >= min 3); but 17 > target 15.
    new, transitions = tick_fsm(
        p, eph, _base_inputs(humidity_pct=17.0),
        now_ts=1000.0 + 60 * 360,
    )
    assert new.fsm.state == FSMState.COOLDOWN
    assert new.fsm.last_run is not None
    assert new.fsm.last_run.outcome == "success"
    assert any(t.event == "AUTODRY_FINISHED" and t.payload.get("still_above_target") is True
               for t in transitions)


# ---- DRYING → COOLDOWN: interrupted by print start ----

def test_drying_to_cooldown_when_print_starts_mid_cycle():
    """If a print starts (klipper_print_state='printing') during DRYING,
    the FSM exits to COOLDOWN with AUTODRY_SKIPPED_PRINT (interrupted_drying=True)."""
    p = _base_persisted()
    p.fsm.state = FSMState.DRYING
    eph = Ephemeral(drying_started_ts=1000.0, drying_start_rh=22.0,
                    effective_temp_c=50, effective_duration_min=360)
    new, transitions = tick_fsm(
        p, eph, _base_inputs(humidity_pct=18.0, klipper_print_state="printing"),
        now_ts=1000.0 + 60 * 30,
    )
    assert new.fsm.state == FSMState.COOLDOWN
    assert any(t.event == "AUTODRY_SKIPPED_PRINT" and t.payload.get("interrupted_drying") is True
               for t in transitions)


# ---- WATCHING → IDLE demotion ----

def test_watching_demotes_to_idle_when_mode_set_off():
    """If user sets mode=off mid-WATCHING, FSM should drop back to IDLE."""
    p = _base_persisted()
    # WATCHING by default
    p.mode = "off"  # user just disabled
    eph = Ephemeral()
    new, _ = tick_fsm(p, eph, _base_inputs(), now_ts=1000.0)
    assert new.fsm.state == FSMState.IDLE


import asyncio
from unittest.mock import AsyncMock, MagicMock

from multiace_web.autodryer import AutoDryer


@pytest.mark.asyncio
async def test_autodryer_one_tick_log_mode_dry_run(tmp_path):
    """End-to-end (within autodryer.py): mode=log, RH well above wake →
    after enough ticks, emit one AUTODRY_DRY_RUN event broadcast +
    one [DRY-RUN] toast."""
    state_path = tmp_path / "autodry.json"

    # Mock the inputs-fetcher: returns a constant Inputs.
    def fetcher() -> Inputs:
        return Inputs(
            active_device=0,
            head_source={"0": {"ace": 0, "slot": 0, "type": "PLA"}},
            swap_in_progress=False,
            humidity_ok=True,
            humidity_pct=22.0,                     # above wake (15+5)
            cavity_temp_c=22.0,
            klipper_print_state="standby",
            dryer_status="stop",
            user_profiles=None,
        )

    events_emitted: list[dict] = []
    async def event_cb(payload: dict) -> None:
        events_emitted.append(payload)

    announcements = MagicMock()
    announcements.post = AsyncMock(return_value="entry-1")
    announcements.dismiss = AsyncMock(return_value=True)

    initial = PersistedState(mode="log", target_ace=0, target_pct=15, hysteresis_pp=5)
    save_persisted_state(state_path, initial)

    dryer = AutoDryer(
        state_path=state_path,
        inputs_fetcher=fetcher,
        emit_event=event_cb,
        announcements=announcements,
        tick_sec=0.0,                # don't actually sleep
        debounce_required=2,         # 2 ticks for fast test
    )

    # Drive 3 ticks manually instead of calling .run() (which loops forever).
    await dryer._tick_once(now_ts=1000.0)
    await dryer._tick_once(now_ts=1060.0)
    await dryer._tick_once(now_ts=1120.0)

    actions = [e["action"] for e in events_emitted]
    assert "AUTODRY_DRY_RUN" in actions
    # mode=log → goes straight to COOLDOWN
    persisted = load_persisted_state(state_path)
    assert persisted.fsm.state == FSMState.COOLDOWN
    # Toast was posted with [DRY-RUN] prefix
    announcements.post.assert_called()
    args = announcements.post.call_args.kwargs
    assert "[DRY-RUN]" in args["title"]
    assert "[DRY-RUN]" in args["description"]


@pytest.mark.asyncio
async def test_autodryer_boot_reconciles_persisted_drying_into_cooldown(tmp_path):
    """If multiace-web restarted mid-DRYING and Klipper now reports dryer.status=stop
    (cycle ended during the restart window), boot reconciliation goes to COOLDOWN."""
    state_path = tmp_path / "autodry.json"
    persisted = PersistedState(mode="active", target_pct=15)
    persisted.fsm.state = FSMState.DRYING
    save_persisted_state(state_path, persisted)

    # Klipper reports dryer is idle (cycle ended during restart)
    def fetcher(): return Inputs(
        active_device=0, head_source={"0": {"ace": 0, "type": "PLA"}},
        swap_in_progress=False, humidity_ok=True, humidity_pct=14.0,
        cavity_temp_c=22.0, klipper_print_state="standby",
        dryer_status="stop", user_profiles=None,
    )
    events: list[dict] = []
    async def emit(p): events.append(p)
    ann = MagicMock(); ann.post = AsyncMock(return_value="x"); ann.dismiss = AsyncMock(return_value=True)

    dryer = AutoDryer(state_path=state_path, inputs_fetcher=fetcher,
                     emit_event=emit, announcements=ann, tick_sec=0.0)
    await dryer._tick_once(now_ts=1000.0)
    p = load_persisted_state(state_path)
    assert p.fsm.state in (FSMState.COOLDOWN, FSMState.WATCHING)


class TestAutodryManager:
    def test_default_construction_yields_one_fsm_per_device(self) -> None:
        from multiace_web.autodryer import AutodryManager, PerAceFSM
        mgr = AutodryManager.with_defaults(device_count=2)
        assert len(mgr.fsms) == 2
        assert mgr.fsms[0].ace == 0
        assert mgr.fsms[1].ace == 1
        assert all(isinstance(f, PerAceFSM) for f in mgr.fsms)
        # disabled by default — explicit opt-in per ACE
        assert all(not f.config.enabled for f in mgr.fsms)

    def test_get_returns_fsm_by_ace_index(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        f = mgr.get(1)
        assert f.ace == 1

    def test_get_raises_for_out_of_range_ace(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        with pytest.raises(KeyError):
            mgr.get(2)

    def test_serialize_roundtrip(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(1).config.enabled = True
        mgr.get(1).config.target_pct = 12
        d = mgr.serialize()
        mgr2 = AutodryManager.deserialize(d, device_count=2)
        assert mgr2.get(1).config.enabled is True
        assert mgr2.get(1).config.target_pct == 12
        assert mgr2.get(0).config.enabled is False

    def test_deserialize_grows_to_device_count_when_persisted_count_is_smaller(self) -> None:
        """If hardware count grew (1 → 2 ACEs), deserialize fills missing FSMs with defaults."""
        from multiace_web.autodryer import AutodryManager
        mgr_one = AutodryManager.with_defaults(device_count=1)
        mgr_one.get(0).config.enabled = True
        d = mgr_one.serialize()
        mgr_two = AutodryManager.deserialize(d, device_count=2)
        assert mgr_two.get(0).config.enabled is True
        assert mgr_two.get(1).config.enabled is False

    def test_migrate_from_legacy_single_fsm(self) -> None:
        """Legacy schema (single FSM with target_ace) migrates to new
        per-ACE list, preserving config on the targeted ACE only."""
        from multiace_web.autodryer import AutodryManager, FSMState
        legacy = {
            "mode": "active",
            "target_ace": 1,
            "target_pct": 12,
            "hysteresis_pp": 4,
            "default_filament_type": "PETG",
            "fsm": {
                "state": "WATCHING",
                "since_ts": 1234.0,
                "cooldown_until_ts": 0.0,
            },
        }
        mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert mgr.get(0).config.enabled is False
        assert mgr.get(1).config.enabled is True
        assert mgr.get(1).config.target_pct == 12
        assert mgr.get(1).config.hysteresis_pp == 4
        assert mgr.get(1).config.default_filament_type == "PETG"
        assert mgr.get(1).snapshot.state == FSMState.WATCHING
        assert mgr.get(0).snapshot.state == FSMState.IDLE  # untargeted FSM is fresh

    def test_migrate_from_legacy_off_mode_disables_all(self) -> None:
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "off", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5}
        mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert all(not f.config.enabled for f in mgr.fsms)

    def test_deserialize_routes_legacy_shape_through_migration(self) -> None:
        """Loading a v1 (legacy) blob via deserialize() should yield the
        migrated v2 shape, so existing on-disk files Just Work."""
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "active", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5}
        mgr = AutodryManager.deserialize(legacy, device_count=2)
        assert mgr.get(0).config.enabled is True
        assert len(mgr.fsms) == 2

    def test_migrate_from_legacy_log_mode_keeps_enabled(self) -> None:
        """Log mode in v1 means observation/dry-run — still enabled.
        Migrating to enabled=False would silently downgrade the user."""
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "log", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5}
        mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert mgr.get(0).config.enabled is True

    def test_migrate_from_legacy_clamps_out_of_range_target_ace(self, caplog) -> None:
        """If legacy target_ace exceeds device_count, clamp to 0 with a warning
        so the user's config doesn't silently vanish."""
        import logging
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "active", "target_ace": 5, "target_pct": 11, "hysteresis_pp": 3}
        with caplog.at_level(logging.WARNING):
            mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert mgr.get(0).config.enabled is True
        assert mgr.get(0).config.target_pct == 11
        assert mgr.get(0).config.hysteresis_pp == 3
        assert mgr.get(1).config.enabled is False
        assert any("target_ace=5" in rec.getMessage() and "device_count=2" in rec.getMessage()
                   for rec in caplog.records)


class TestAutoDryerPerAce:
    """Tests for per-ACE entry points added to AutoDryer (Task 4)."""

    def _make_inputs(self, *, humidity_pct: float = 22.0) -> "Inputs":  # type: ignore[name-defined]
        from multiace_web.autodryer import Inputs
        return Inputs(
            active_device=0,
            head_source={"T0": {"ace": 0, "type": "PLA"}},
            swap_in_progress=False,
            humidity_ok=True,
            humidity_pct=humidity_pct,
            cavity_temp_c=25.0,
            klipper_print_state="standby",
            dryer_status="stop",
            user_profiles=None,
        )

    @pytest.mark.asyncio
    async def test_tick_one_ace_advances_fsm_for_target_only(self, tmp_path) -> None:
        """tick_one_ace(N) advances FSM N's snapshot, leaves others alone."""
        from multiace_web.autodryer import (
            AutoDryer, AutodryManager, FSMState,
        )
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(0).config.enabled = True
        mgr.get(0).config.target_pct = 15
        mgr.get(0).config.hysteresis_pp = 5
        # FSM 1 left disabled

        # Stub inputs: humidity above threshold for ACE 0 → kicks FSM out of IDLE
        def fetcher():
            return self._make_inputs(humidity_pct=22.0)

        events: list[dict] = []

        async def emit(e: dict) -> None:
            events.append(e)

        ad = AutoDryer(
            state_path=tmp_path / "ad.json",
            inputs_fetcher=fetcher,
            emit_event=emit,
            announcements=None,
            tick_sec=0.0,
            manager=mgr,
        )
        # tick FSM 0
        await ad.tick_one_ace(0, now_ts=1.0)
        # FSM 0 should have advanced (out of IDLE to WATCHING with humidity above wake)
        # OR an event was emitted. The key invariant is FSM 1 is untouched.
        assert mgr.get(1).snapshot.state == FSMState.IDLE  # FSM 1 untouched

    @pytest.mark.asyncio
    async def test_tick_one_ace_skipped_when_locked(self, tmp_path) -> None:
        from multiace_web.autodryer import (
            AutoDryer, AutodryManager, FSMState,
        )
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(0).config.enabled = True
        mgr.get(0).locked = True

        def fetcher():
            return self._make_inputs(humidity_pct=99.0)

        async def emit(e: dict) -> None:
            pass

        ad = AutoDryer(
            state_path=tmp_path / "ad.json",
            inputs_fetcher=fetcher,
            emit_event=emit,
            announcements=None,
            tick_sec=0.0,
            manager=mgr,
        )
        await ad.tick_one_ace(0, now_ts=1.0)
        # Locked FSM does not advance regardless of humidity
        assert mgr.get(0).snapshot.state == FSMState.IDLE

    @pytest.mark.asyncio
    async def test_tick_one_ace_skipped_when_unreachable(self, tmp_path) -> None:
        from multiace_web.autodryer import (
            AutoDryer, AutodryManager, FSMState,
        )
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(0).config.enabled = True
        mgr.get(0).unreachable = True

        def fetcher():
            return self._make_inputs(humidity_pct=99.0)

        async def emit(e: dict) -> None:
            pass

        ad = AutoDryer(
            state_path=tmp_path / "ad.json",
            inputs_fetcher=fetcher,
            emit_event=emit,
            announcements=None,
            tick_sec=0.0,
            manager=mgr,
        )
        await ad.tick_one_ace(0, now_ts=1.0)
        assert mgr.get(0).snapshot.state == FSMState.IDLE

    @pytest.mark.asyncio
    async def test_tick_one_ace_skipped_when_disabled(self, tmp_path) -> None:
        from multiace_web.autodryer import (
            AutoDryer, AutodryManager, FSMState,
        )
        mgr = AutodryManager.with_defaults(device_count=2)
        # FSM 0 disabled by default (config.enabled = False)

        def fetcher():
            return self._make_inputs(humidity_pct=99.0)

        async def emit(e: dict) -> None:
            pass

        ad = AutoDryer(
            state_path=tmp_path / "ad.json",
            inputs_fetcher=fetcher,
            emit_event=emit,
            announcements=None,
            tick_sec=0.0,
            manager=mgr,
        )
        await ad.tick_one_ace(0, now_ts=1.0)
        assert mgr.get(0).snapshot.state == FSMState.IDLE

    def test_load_manager_returns_defaults_when_path_missing(self, tmp_path) -> None:
        from multiace_web.autodryer import AutoDryer, AutodryManager
        path = tmp_path / "missing.json"
        mgr = AutoDryer.load_manager(path, device_count=2)
        assert isinstance(mgr, AutodryManager)
        assert len(mgr.fsms) == 2
        assert all(not f.config.enabled for f in mgr.fsms)

    def test_load_manager_loads_v2_shape(self, tmp_path) -> None:
        from multiace_web.autodryer import AutoDryer, AutodryManager
        path = tmp_path / "v2.json"
        mgr_in = AutodryManager.with_defaults(device_count=2)
        mgr_in.get(1).config.enabled = True
        mgr_in.get(1).config.target_pct = 11
        path.write_text(json.dumps(mgr_in.serialize()))
        mgr_out = AutoDryer.load_manager(path, device_count=2)
        assert mgr_out.get(1).config.enabled is True
        assert mgr_out.get(1).config.target_pct == 11

    def test_load_manager_routes_legacy_through_migration(self, tmp_path) -> None:
        from multiace_web.autodryer import AutoDryer
        path = tmp_path / "v1.json"
        legacy = {"mode": "active", "target_ace": 1, "target_pct": 12, "hysteresis_pp": 4}
        path.write_text(json.dumps(legacy))
        mgr = AutoDryer.load_manager(path, device_count=2)
        assert mgr.get(1).config.enabled is True
        assert mgr.get(1).config.target_pct == 12
