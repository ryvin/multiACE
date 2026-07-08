# License: GPL-3.0
"""Vendored per-ACE auto-dry FSM.

Ported (not imported) from ``multiace_web/src/multiace_web/autodryer.py`` —
this plugin is a standalone decay71 sidecar and must not depend on
multiace_web's Python package. The state machine shape (IDLE -> WATCHING ->
DRYING -> COOLDOWN, with a sticky FAULTED) and the safety knobs (debounce,
cooldown, daily-duty cap, max-run / min-delta fault checks) are carried over
faithfully. Differences from the source, made deliberately for this
Moonraker-only sidecar and called out for review:

- No filament-type / profile resolution (``DEFAULT_PROFILES``, ``keep_ready``,
  ``head_source`` lookups). This plugin's ``POST /config`` takes ``temp`` and
  ``duration_min`` directly per ACE, so there's no need to infer dryer
  parameters from a loaded filament's type metadata the way the source FSM
  does via ``reconcile_loaded_slots``.
- No ``OBSERVED_DRYING`` state (detecting a *manually* triggered dry via the
  ACE's own ``dryer_status``). This sidecar drives ``ACE_DRY`` itself for
  both auto-triggers and the manual ``POST /dry`` endpoint (see
  ``manual_trigger`` below), so every DRYING period is initiated by us —
  there's no third-party trigger to "observe".
- No serial/keepalive handling. The plugin only ever talks to Moonraker's
  HTTP API (``gcode/script``, ``objects/query``). Keeping the ACE Pro's USB
  link alive against its ~5s idle reset is decay71 firmware's own job (its
  ~1Hz heartbeat), not this sidecar's — see CLAUDE.md instinct #7.
- Persistence is schema-versioned (``{"schema": 2, "fsms": [...]}``) but has
  only one shape: there's no legacy v1 single-FSM file to migrate from,
  since this plugin never shipped a v1.
"""
from __future__ import annotations

import dataclasses
import enum
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("autodry.fsm")


class FSMState(str, enum.Enum):
    IDLE = "IDLE"
    WATCHING = "WATCHING"
    DRYING = "DRYING"
    COOLDOWN = "COOLDOWN"
    FAULTED = "FAULTED"


@dataclass
class Fault:
    code: str
    since_ts: float
    msg: str


@dataclass
class LastRun:
    kind: str          # "auto" | "manual"
    outcome: str        # "success" | "failed"
    started_ts: float
    ended_ts: float
    trigger_rh: float
    end_rh: float
    temp_c_used: int
    duration_min: int
    ran_min: int


@dataclass
class FSMSnapshot:
    """Persisted slice of one ACE's FSM. Excludes ephemeral fields (the
    debounce buffer + drying-cycle bookkeeping), which intentionally start
    empty/zero on every process restart — we never auto-trigger on stale
    in-memory data."""
    state: FSMState = FSMState.IDLE
    since_ts: float = 0.0
    fault: Fault | None = None
    last_run: LastRun | None = None
    daily_duty: list[dict[str, Any]] = field(default_factory=list)
    cooldown_until_ts: float = 0.0


@dataclass
class PerAceConfig:
    """Per-ACE autodry knobs, settable via POST /config."""
    enabled: bool = False
    target_pct: int = 15
    temp_c: int = 55
    duration_min: int = 240
    hysteresis_pp: int = 5


class DebounceBuffer:
    """Counts consecutive above-wake-threshold ticks. A single dip resets it.
    Used to reject sensor blips / lid-opening before auto-triggering a dry."""

    __slots__ = ("_required", "_count")

    def __init__(self, required: int) -> None:
        if required < 1:
            raise ValueError(f"required must be >= 1, got {required}")
        self._required = required
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def observe_above(self) -> None:
        self._count = min(self._count + 1, self._required)

    def observe_below(self) -> None:
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def is_above_threshold(self) -> bool:
        return self._count >= self._required


@dataclass
class Ephemeral:
    """In-memory-only bookkeeping for one ACE's FSM. Never serialized."""
    debounce: DebounceBuffer = field(default_factory=lambda: DebounceBuffer(required=3))
    drying_started_ts: float = 0.0
    drying_start_rh: float = 0.0
    skip_emitted_ts: dict[str, float] = field(default_factory=dict)


@dataclass
class PerAceFSM:
    """One autodry FSM bound to one ACE index."""
    ace: int
    config: PerAceConfig = field(default_factory=PerAceConfig)
    snapshot: FSMSnapshot = field(default_factory=FSMSnapshot)
    eph: Ephemeral = field(default_factory=Ephemeral)
    # Set by the tick loop when round-robin is off and this isn't the
    # currently-active ACE (single serial connection => only one ACE's live
    # dryer/humidity telemetry is available at a time).
    locked: bool = False


@dataclass
class Inputs:
    """Snapshot the tick loop feeds into tick_fsm for one ACE, one tick."""
    humidity_ok: bool
    humidity_pct: float
    print_state: str = "standby"     # "standby" | "printing" | "paused" | ...
    swap_in_progress: bool = False


@dataclass
class Transition:
    """One side-effect emission the caller may act on (fire ACE_DRY, log, …)."""
    event: str
    payload: dict[str, Any] = field(default_factory=dict)


# ---- safety caps (overridable per call / via Config) ----
DEFAULT_DEBOUNCE_REQUIRED = 3
DEFAULT_COOLDOWN_MIN = 30
DEFAULT_MAX_RUN_MIN = 720
DEFAULT_DAILY_DUTY_MAX_MIN = 1080
DEFAULT_MIN_DELTA_PCT = 3
DEFAULT_SKIP_RATE_LIMIT_SEC = DEFAULT_COOLDOWN_MIN * 60


def _emit_skip(
    eph: Ephemeral, code: str, payload: dict[str, Any], now_ts: float,
    rate_limit_sec: float = DEFAULT_SKIP_RATE_LIMIT_SEC,
) -> Transition | None:
    """Rate-limit AUTODRY_SKIPPED_* events to once per rate_limit_sec."""
    last = eph.skip_emitted_ts.get(code)
    if last is not None and now_ts - last < rate_limit_sec:
        return None
    eph.skip_emitted_ts[code] = now_ts
    return Transition(event=code, payload=payload)


def tick_fsm(
    config: PerAceConfig,
    snapshot: FSMSnapshot,
    eph: Ephemeral,
    inputs: Inputs,
    now_ts: float,
    *,
    debounce_required: int = DEFAULT_DEBOUNCE_REQUIRED,
    cooldown_min: int = DEFAULT_COOLDOWN_MIN,
    max_run_min: int = DEFAULT_MAX_RUN_MIN,
    daily_duty_max_min: int = DEFAULT_DAILY_DUTY_MAX_MIN,
    min_delta_pct: float = DEFAULT_MIN_DELTA_PCT,
) -> tuple[FSMSnapshot, list[Transition]]:
    """One FSM tick for one ACE. Pure function of (config, snapshot, eph,
    inputs, now_ts) -> (new_snapshot, transitions). Mutates `eph` in place
    (debounce buffer + drying-cycle bookkeeping), like the source FSM.
    """
    transitions: list[Transition] = []
    snap = dataclasses.replace(snapshot, daily_duty=list(snapshot.daily_duty))
    s = snap.state

    # FAULTED is sticky — only an explicit reset_fault (API layer) clears it.
    if s == FSMState.FAULTED:
        return snap, transitions

    sensor_ok = inputs.humidity_ok and 0.0 <= inputs.humidity_pct <= 100.0
    can_arm = (
        config.enabled
        and sensor_ok
        and inputs.print_state not in ("printing", "paused")
    )

    # ---- IDLE ----
    if s == FSMState.IDLE:
        if can_arm:
            snap.state = FSMState.WATCHING
            snap.since_ts = now_ts
            eph.debounce.reset()
        return snap, transitions

    # Demote WATCHING -> IDLE if an arming precondition is lost. DRYING /
    # COOLDOWN keep running — we never yank a heater mid-cycle.
    if s == FSMState.WATCHING and not can_arm:
        snap.state = FSMState.IDLE
        eph.debounce.reset()
        return snap, transitions

    # ---- WATCHING ----
    if s == FSMState.WATCHING:
        if inputs.swap_in_progress:
            t = _emit_skip(eph, "AUTODRY_SKIPPED_SWAP", {}, now_ts)
            if t:
                transitions.append(t)
            return snap, transitions

        recent_min = sum(
            d.get("ran_min", 0)
            for d in snap.daily_duty
            if now_ts - float(d.get("started_ts", 0)) < 86400
        )
        if recent_min >= daily_duty_max_min:
            t = _emit_skip(eph, "AUTODRY_SKIPPED_DAILY",
                            {"recent_min": recent_min, "cap_min": daily_duty_max_min}, now_ts)
            if t:
                transitions.append(t)
            return snap, transitions

        if now_ts < snap.cooldown_until_ts:
            return snap, transitions

        wake = config.target_pct + config.hysteresis_pp
        if inputs.humidity_pct > wake:
            eph.debounce.observe_above()
        else:
            eph.debounce.observe_below()

        if not eph.debounce.is_above_threshold():
            return snap, transitions

        transitions.append(Transition(
            event="AUTODRY_TRIGGERED",
            payload={
                "temp_c": config.temp_c,
                "duration_min": config.duration_min,
                "trigger_rh": inputs.humidity_pct,
                "reason": "wake_threshold",
            },
        ))
        snap.state = FSMState.DRYING
        snap.since_ts = now_ts
        eph.drying_started_ts = now_ts
        eph.drying_start_rh = inputs.humidity_pct
        eph.debounce.reset()
        return snap, transitions

    # ---- DRYING ----
    if s == FSMState.DRYING:
        if inputs.print_state in ("printing", "paused"):
            t = _emit_skip(eph, "AUTODRY_SKIPPED_PRINT", {"interrupted_drying": True}, now_ts)
            if t:
                transitions.append(t)
            snap.state = FSMState.COOLDOWN
            snap.cooldown_until_ts = now_ts + cooldown_min * 60
            return snap, transitions

        ran_min = int((now_ts - eph.drying_started_ts) / 60)

        if inputs.humidity_ok and inputs.humidity_pct <= config.target_pct:
            snap.last_run = LastRun(
                kind="auto", outcome="success",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=config.temp_c, duration_min=config.duration_min,
                ran_min=ran_min,
            )
            snap.daily_duty.append({"started_ts": eph.drying_started_ts, "ran_min": ran_min})
            transitions.append(Transition(
                event="AUTODRY_FINISHED",
                payload={"start_rh": eph.drying_start_rh, "end_rh": inputs.humidity_pct,
                         "ran_min": ran_min},
            ))
            snap.state = FSMState.COOLDOWN
            snap.cooldown_until_ts = now_ts + cooldown_min * 60
            return snap, transitions

        if ran_min >= max_run_min:
            snap.fault = Fault(
                code="FAILED_LIMIT", since_ts=now_ts,
                msg=f"{ran_min}m run did not cross target {config.target_pct}%",
            )
            snap.last_run = LastRun(
                kind="auto", outcome="failed",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=config.temp_c, duration_min=config.duration_min,
                ran_min=ran_min,
            )
            transitions.append(Transition(event="AUTODRY_FAILED_LIMIT", payload={"ran_min": ran_min}))
            snap.state = FSMState.FAULTED
            return snap, transitions

        if config.duration_min and ran_min >= config.duration_min:
            delta = eph.drying_start_rh - inputs.humidity_pct
            if delta < min_delta_pct:
                snap.fault = Fault(
                    code="FAILED_DELTA", since_ts=now_ts,
                    msg=(f"{ran_min}m run moved RH {eph.drying_start_rh:.1f}"
                         f"→{inputs.humidity_pct:.1f} (delta={delta:.1f}pp)"),
                )
                snap.last_run = LastRun(
                    kind="auto", outcome="failed",
                    started_ts=eph.drying_started_ts, ended_ts=now_ts,
                    trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                    temp_c_used=config.temp_c, duration_min=config.duration_min,
                    ran_min=ran_min,
                )
                transitions.append(Transition(event="AUTODRY_FAILED_DELTA", payload={"delta_pp": delta}))
                snap.state = FSMState.FAULTED
                return snap, transitions
            # Duration ran out but we made progress — retry next cycle.
            snap.last_run = LastRun(
                kind="auto", outcome="success",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=config.temp_c, duration_min=config.duration_min,
                ran_min=ran_min,
            )
            snap.daily_duty.append({"started_ts": eph.drying_started_ts, "ran_min": ran_min})
            transitions.append(Transition(
                event="AUTODRY_FINISHED",
                payload={"start_rh": eph.drying_start_rh, "end_rh": inputs.humidity_pct,
                         "ran_min": ran_min, "still_above_target": True},
            ))
            snap.state = FSMState.COOLDOWN
            snap.cooldown_until_ts = now_ts + cooldown_min * 60
            return snap, transitions

        return snap, transitions  # still drying

    # ---- COOLDOWN ----
    if s == FSMState.COOLDOWN:
        if now_ts >= snap.cooldown_until_ts:
            snap.state = FSMState.WATCHING
            snap.since_ts = now_ts
            eph.debounce.reset()
        return snap, transitions

    return snap, transitions


def manual_trigger(
    snapshot: FSMSnapshot, eph: Ephemeral, inputs: Inputs, now_ts: float,
) -> tuple[FSMSnapshot, Transition] | None:
    """Force an ACE straight into DRYING for POST /dry, bypassing debounce /
    cooldown / daily-duty (the operator asked for this explicitly). Returns
    None if the ACE is already drying (caller should surface a 409)."""
    if snapshot.state == FSMState.DRYING:
        return None
    snap = dataclasses.replace(snapshot)
    snap.state = FSMState.DRYING
    snap.since_ts = now_ts
    eph.drying_started_ts = now_ts
    eph.drying_start_rh = inputs.humidity_pct if inputs.humidity_ok else 0.0
    return snap, Transition(event="AUTODRY_MANUAL_TRIGGERED", payload={})


def reset_fault(snapshot: FSMSnapshot) -> FSMSnapshot:
    """Clear a FAULTED snapshot back to IDLE so the FSM can re-arm."""
    snap = dataclasses.replace(snapshot)
    snap.fault = None
    if snap.state == FSMState.FAULTED:
        snap.state = FSMState.IDLE
    return snap


# ---- (de)serialization ----

def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj


def _fault_from_dict(d: Any) -> Fault | None:
    if not isinstance(d, dict):
        return None
    try:
        return Fault(code=str(d["code"]), since_ts=float(d["since_ts"]), msg=str(d.get("msg", "")))
    except (KeyError, ValueError, TypeError):
        return None


def _last_run_from_dict(d: Any) -> LastRun | None:
    if not isinstance(d, dict):
        return None
    try:
        return LastRun(
            kind=str(d["kind"]), outcome=str(d["outcome"]),
            started_ts=float(d["started_ts"]), ended_ts=float(d["ended_ts"]),
            trigger_rh=float(d["trigger_rh"]), end_rh=float(d["end_rh"]),
            temp_c_used=int(d["temp_c_used"]), duration_min=int(d["duration_min"]),
            ran_min=int(d["ran_min"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def snapshot_from_dict(d: Any) -> FSMSnapshot:
    if not isinstance(d, dict):
        return FSMSnapshot()
    try:
        state = FSMState(d.get("state", "IDLE"))
    except ValueError:
        state = FSMState.IDLE
    return FSMSnapshot(
        state=state,
        since_ts=float(d.get("since_ts", 0.0)),
        fault=_fault_from_dict(d.get("fault")),
        last_run=_last_run_from_dict(d.get("last_run")),
        daily_duty=list(d.get("daily_duty") or []),
        cooldown_until_ts=float(d.get("cooldown_until_ts", 0.0)),
    )


def config_from_dict(d: Any) -> PerAceConfig:
    if not isinstance(d, dict):
        return PerAceConfig()
    return PerAceConfig(
        enabled=bool(d.get("enabled", False)),
        target_pct=int(d.get("target_pct", 15)),
        temp_c=int(d.get("temp_c", 55)),
        duration_min=int(d.get("duration_min", 240)),
        hysteresis_pp=int(d.get("hysteresis_pp", 5)),
    )


@dataclass
class AutodryManager:
    """Owns one PerAceFSM per ACE index, created lazily on first access."""
    fsms: dict[int, PerAceFSM] = field(default_factory=dict)

    def get(self, ace: int) -> PerAceFSM:
        if ace < 0:
            raise ValueError(f"ace index must be >= 0, got {ace}")
        if ace not in self.fsms:
            self.fsms[ace] = PerAceFSM(ace=ace)
        return self.fsms[ace]

    def all(self) -> list[PerAceFSM]:
        return [self.fsms[k] for k in sorted(self.fsms)]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema": 2,
            "fsms": [
                {
                    "ace": f.ace,
                    "config": _to_jsonable(f.config),
                    "snapshot": _to_jsonable(f.snapshot),
                }
                for f in self.all()
            ],
        }

    @classmethod
    def deserialize(cls, d: dict[str, Any]) -> "AutodryManager":
        mgr = cls()
        for raw in d.get("fsms") or []:
            if not isinstance(raw, dict) or "ace" not in raw:
                continue
            try:
                ace = int(raw["ace"])
            except (TypeError, ValueError):
                continue
            mgr.fsms[ace] = PerAceFSM(
                ace=ace,
                config=config_from_dict(raw.get("config")),
                snapshot=snapshot_from_dict(raw.get("snapshot")),
            )
        return mgr
