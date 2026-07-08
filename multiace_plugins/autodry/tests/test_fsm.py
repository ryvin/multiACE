# License: GPL-3.0
"""Unit tests for the vendored FSM (fsm.py) — no FastAPI/HTTP involved."""
from autodry_plugin.fsm import (
    AutodryManager,
    DebounceBuffer,
    Ephemeral,
    Fault,
    FSMSnapshot,
    FSMState,
    Inputs,
    PerAceConfig,
    manual_trigger,
    reset_fault,
    tick_fsm,
)


def _arm_to_watching(config, now_ts=1000.0):
    eph = Ephemeral(debounce=DebounceBuffer(required=3))
    inputs = Inputs(humidity_ok=True, humidity_pct=10.0, print_state="standby")
    snap, _ = tick_fsm(config, FSMSnapshot(), eph, inputs, now_ts)
    assert snap.state == FSMState.WATCHING
    return snap, eph


def test_idle_arms_to_watching_when_enabled_and_sensor_ok():
    config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5)
    snap, _eph = _arm_to_watching(config)
    assert snap.state == FSMState.WATCHING


def test_idle_stays_idle_when_disabled():
    config = PerAceConfig(enabled=False)
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="standby")
    snap, transitions = tick_fsm(config, FSMSnapshot(), Ephemeral(), inputs, 1000.0)
    assert snap.state == FSMState.IDLE
    assert transitions == []


def test_idle_stays_idle_when_sensor_not_ok():
    config = PerAceConfig(enabled=True)
    inputs = Inputs(humidity_ok=False, humidity_pct=0.0, print_state="standby")
    snap, transitions = tick_fsm(config, FSMSnapshot(), Ephemeral(), inputs, 1000.0)
    assert snap.state == FSMState.IDLE


def test_watching_triggers_dry_when_humidity_above_target_after_debounce():
    config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5, temp_c=55, duration_min=240)
    snap, eph = _arm_to_watching(config)
    # wake threshold = target(15) + hysteresis(5) = 20; 30% is above it
    inputs = Inputs(humidity_ok=True, humidity_pct=30.0, print_state="standby")
    transitions = []
    for i in range(3):
        snap, transitions = tick_fsm(config, snap, eph, inputs, 1001.0 + i)
    assert snap.state == FSMState.DRYING
    triggered = next(t for t in transitions if t.event == "AUTODRY_TRIGGERED")
    assert triggered.payload["temp_c"] == 55
    assert triggered.payload["duration_min"] == 240
    assert triggered.payload["trigger_rh"] == 30.0


def test_watching_does_not_trigger_when_humidity_below_wake_threshold():
    config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5)
    snap, eph = _arm_to_watching(config)
    # wake threshold = 20%; 18% never crosses it
    inputs = Inputs(humidity_ok=True, humidity_pct=18.0, print_state="standby")
    transitions = []
    for i in range(5):
        snap, transitions = tick_fsm(config, snap, eph, inputs, 1001.0 + i)
    assert snap.state == FSMState.WATCHING
    assert transitions == []


def test_watching_debounce_resets_on_a_dip_below_threshold():
    config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5)
    snap, eph = _arm_to_watching(config)
    above = Inputs(humidity_ok=True, humidity_pct=30.0, print_state="standby")
    below = Inputs(humidity_ok=True, humidity_pct=10.0, print_state="standby")
    snap, _ = tick_fsm(config, snap, eph, above, 1001.0)
    snap, _ = tick_fsm(config, snap, eph, above, 1002.0)
    snap, _ = tick_fsm(config, snap, eph, below, 1003.0)  # resets debounce
    snap, _ = tick_fsm(config, snap, eph, above, 1004.0)
    assert snap.state == FSMState.WATCHING  # only 1/3 consecutive so far


def test_watching_demotes_to_idle_when_disabled_mid_watch():
    config = PerAceConfig(enabled=True, target_pct=15)
    snap, eph = _arm_to_watching(config)
    config.enabled = False
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="standby")
    snap, _ = tick_fsm(config, snap, eph, inputs, 1001.0)
    assert snap.state == FSMState.IDLE


def test_watching_skips_during_print():
    config = PerAceConfig(enabled=True, target_pct=15)
    snap = FSMSnapshot(state=FSMState.WATCHING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="printing")
    snap, transitions = tick_fsm(config, snap, Ephemeral(), inputs, 100.0)
    assert snap.state == FSMState.IDLE  # can_arm false while printing -> demoted


def test_watching_skips_during_swap():
    config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5)
    snap = FSMSnapshot(state=FSMState.WATCHING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="standby", swap_in_progress=True)
    snap, transitions = tick_fsm(config, snap, Ephemeral(), inputs, 100.0)
    assert snap.state == FSMState.WATCHING
    assert any(t.event == "AUTODRY_SKIPPED_SWAP" for t in transitions)


def test_drying_finishes_when_target_reached():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=240)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=10.0, print_state="standby")
    snap, transitions = tick_fsm(config, snap, eph, inputs, 60.0)
    assert snap.state == FSMState.COOLDOWN
    finished = next(t for t in transitions if t.event == "AUTODRY_FINISHED")
    assert finished.payload["end_rh"] == 10.0
    assert snap.last_run.outcome == "success"


def test_drying_continues_while_above_target_and_within_duration():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=240)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=30.0, print_state="standby")
    snap, transitions = tick_fsm(config, snap, eph, inputs, 60.0)  # 1 minute in
    assert snap.state == FSMState.DRYING
    assert transitions == []


def test_drying_faults_after_max_run_min():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=240)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=39.0, print_state="standby")
    snap, transitions = tick_fsm(config, snap, eph, inputs, 721 * 60, max_run_min=720)
    assert snap.state == FSMState.FAULTED
    assert snap.fault.code == "FAILED_LIMIT"
    assert any(t.event == "AUTODRY_FAILED_LIMIT" for t in transitions)


def test_drying_faults_on_insufficient_delta_after_duration():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=10)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    # only moved 1pp in the full requested duration -> below min_delta_pct default (3)
    inputs = Inputs(humidity_ok=True, humidity_pct=39.0, print_state="standby")
    snap, transitions = tick_fsm(config, snap, eph, inputs, 10 * 60, min_delta_pct=3)
    assert snap.state == FSMState.FAULTED
    assert snap.fault.code == "FAILED_DELTA"


def test_drying_retries_when_duration_elapsed_but_delta_ok():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=10)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=30.0, print_state="standby")  # delta 10pp
    snap, transitions = tick_fsm(config, snap, eph, inputs, 10 * 60, min_delta_pct=3)
    assert snap.state == FSMState.COOLDOWN
    assert snap.fault is None
    finished = next(t for t in transitions if t.event == "AUTODRY_FINISHED")
    assert finished.payload["still_above_target"] is True


def test_drying_interrupted_by_print_goes_to_cooldown():
    config = PerAceConfig(enabled=True, target_pct=15, temp_c=55, duration_min=240)
    eph = Ephemeral(drying_started_ts=0.0, drying_start_rh=40.0)
    snap = FSMSnapshot(state=FSMState.DRYING, since_ts=0.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=30.0, print_state="printing")
    snap, transitions = tick_fsm(config, snap, eph, inputs, 60.0)
    assert snap.state == FSMState.COOLDOWN
    assert any(t.event == "AUTODRY_SKIPPED_PRINT" for t in transitions)


def test_cooldown_returns_to_watching_after_expiry():
    config = PerAceConfig(enabled=True, target_pct=15)
    snap = FSMSnapshot(state=FSMState.COOLDOWN, cooldown_until_ts=100.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="standby")
    snap, _ = tick_fsm(config, snap, Ephemeral(), inputs, 100.0)
    assert snap.state == FSMState.WATCHING


def test_cooldown_stays_until_expiry():
    config = PerAceConfig(enabled=True, target_pct=15)
    snap = FSMSnapshot(state=FSMState.COOLDOWN, cooldown_until_ts=100.0)
    inputs = Inputs(humidity_ok=True, humidity_pct=50.0, print_state="standby")
    snap, _ = tick_fsm(config, snap, Ephemeral(), inputs, 99.0)
    assert snap.state == FSMState.COOLDOWN


def test_faulted_is_sticky_until_reset():
    config = PerAceConfig(enabled=True)
    snap = FSMSnapshot(state=FSMState.FAULTED, fault=Fault(code="FAILED_LIMIT", since_ts=0.0, msg="x"))
    inputs = Inputs(humidity_ok=True, humidity_pct=5.0, print_state="standby")
    snap2, transitions = tick_fsm(config, snap, Ephemeral(), inputs, 100.0)
    assert snap2.state == FSMState.FAULTED
    assert transitions == []
    cleared = reset_fault(snap2)
    assert cleared.state == FSMState.IDLE
    assert cleared.fault is None


def test_manual_trigger_forces_drying():
    snap = FSMSnapshot(state=FSMState.IDLE)
    eph = Ephemeral()
    inputs = Inputs(humidity_ok=True, humidity_pct=42.0)
    result = manual_trigger(snap, eph, inputs, 500.0)
    assert result is not None
    new_snap, transition = result
    assert new_snap.state == FSMState.DRYING
    assert eph.drying_start_rh == 42.0
    assert transition.event == "AUTODRY_MANUAL_TRIGGERED"


def test_manual_trigger_rejects_when_already_drying():
    snap = FSMSnapshot(state=FSMState.DRYING)
    result = manual_trigger(snap, Ephemeral(), Inputs(humidity_ok=True, humidity_pct=10.0), 500.0)
    assert result is None


def test_manager_get_creates_lazily_with_defaults():
    mgr = AutodryManager()
    fsm = mgr.get(2)
    assert fsm.ace == 2
    assert fsm.config.enabled is False
    assert mgr.get(2) is fsm  # same instance on repeat access


def test_manager_serialize_deserialize_round_trip():
    mgr = AutodryManager()
    fsm0 = mgr.get(0)
    fsm0.config.enabled = True
    fsm0.config.target_pct = 20
    fsm0.snapshot.state = FSMState.WATCHING
    d = mgr.serialize()
    assert d["schema"] == 2

    mgr2 = AutodryManager.deserialize(d)
    fsm0b = mgr2.get(0)
    assert fsm0b.config.enabled is True
    assert fsm0b.config.target_pct == 20
    assert fsm0b.snapshot.state == FSMState.WATCHING


def test_manager_deserialize_ignores_malformed_entries():
    mgr = AutodryManager.deserialize({"fsms": [{"no_ace_key": True}, {"ace": "not-an-int"}]})
    assert mgr.fsms == {}


def test_manager_deserialize_empty_dict_is_safe():
    mgr = AutodryManager.deserialize({})
    assert mgr.fsms == {}
