"""Auto-dry FSM, persistence, and tick loop.

Single-FSM model managing the configured `target_ace`. See spec at
docs/superpowers/specs/2026-05-04-auto-dry-design.md.

This module is split into:
- Data model + persistence (top half — pure, fully testable)
- FSM transitions + tick loop (bottom half — pure too; injects time + state)
- AutoDryer class (the runtime task — wraps FSM + persistence + announcements)
"""
from __future__ import annotations

import dataclasses
import enum
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("multiace.autodryer")


class FSMState(str, enum.Enum):
    IDLE = "IDLE"
    WATCHING = "WATCHING"
    DRYING = "DRYING"
    OBSERVED_DRYING = "OBSERVED_DRYING"
    COOLDOWN = "COOLDOWN"
    FAULTED = "FAULTED"


@dataclass
class Fault:
    code: str
    since_ts: float
    msg: str


@dataclass
class LastRun:
    kind: str          # "auto" | "manual" | "auto_after_restart"
    outcome: str       # "success" | "failed"
    started_ts: float
    ended_ts: float
    trigger_rh: float
    end_rh: float
    temp_c_used: int
    duration_min: int  # requested
    ran_min: int       # actual elapsed


@dataclass
class FSMSnapshot:
    """Persisted slice of the FSM. Excludes ephemeral fields like the
    rolling debounce buffer (which intentionally starts empty on boot)."""
    state: FSMState = FSMState.IDLE
    since_ts: float = 0.0
    fault: Fault | None = None
    last_run: LastRun | None = None
    trigger_announcement_id: str | None = None
    daily_duty: list[dict[str, Any]] = field(default_factory=list)
    cooldown_until_ts: float = 0.0


@dataclass
class PersistedState:
    """The full on-disk JSON shape."""
    mode: str = "off"          # "off" | "log" | "active"
    target_ace: int = 0
    target_pct: int = 15
    hysteresis_pp: int = 5
    fsm: FSMSnapshot = field(default_factory=FSMSnapshot)


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses + enums to JSON-friendly types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj


def _from_dict_fault(d: Any) -> Fault | None:
    if not isinstance(d, dict):
        return None
    try:
        return Fault(code=str(d["code"]),
                     since_ts=float(d["since_ts"]),
                     msg=str(d.get("msg", "")))
    except (KeyError, ValueError, TypeError):
        return None


def _from_dict_last_run(d: Any) -> LastRun | None:
    if not isinstance(d, dict):
        return None
    try:
        return LastRun(
            kind=str(d["kind"]),
            outcome=str(d["outcome"]),
            started_ts=float(d["started_ts"]),
            ended_ts=float(d["ended_ts"]),
            trigger_rh=float(d["trigger_rh"]),
            end_rh=float(d["end_rh"]),
            temp_c_used=int(d["temp_c_used"]),
            duration_min=int(d["duration_min"]),
            ran_min=int(d["ran_min"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _from_dict_fsm(d: Any) -> FSMSnapshot:
    if not isinstance(d, dict):
        return FSMSnapshot()
    try:
        state = FSMState(d.get("state", "IDLE"))
    except ValueError:
        state = FSMState.IDLE
    return FSMSnapshot(
        state=state,
        since_ts=float(d.get("since_ts", 0.0)),
        fault=_from_dict_fault(d.get("fault")),
        last_run=_from_dict_last_run(d.get("last_run")),
        trigger_announcement_id=d.get("trigger_announcement_id"),
        daily_duty=list(d.get("daily_duty", []) or []),
        cooldown_until_ts=float(d.get("cooldown_until_ts", 0.0)),
    )


def load_persisted_state(path: Path) -> PersistedState:
    """Load state from disk. Missing file or corrupt JSON → defaults
    (auto-dry stays off, FSM IDLE, no faults). Never raises."""
    try:
        raw = path.read_text()
    except (FileNotFoundError, OSError):
        return PersistedState()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("autodry state file %s is corrupt; resetting to defaults", path)
        return PersistedState()
    if not isinstance(d, dict):
        return PersistedState()
    return PersistedState(
        mode=str(d.get("mode", "off")),
        target_ace=int(d.get("target_ace", 0)),
        target_pct=int(d.get("target_pct", 15)),
        hysteresis_pp=int(d.get("hysteresis_pp", 5)),
        fsm=_from_dict_fsm(d.get("fsm")),
    )


def save_persisted_state(path: Path, state: PersistedState) -> None:
    """Atomic write via temp file + os.replace. Creates parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_to_jsonable(state), indent=2, default=str)
    fd, tmpname = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmpname, path)
    except Exception:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise


# ---- per-filament defaults (cross-referenced manufacturer recommendations) ----

# Cycle DURATION upper bound enforced at issue time. Profiles can store any
# value; the FSM clamps before passing to ACE_DRY (see spec hardware section).
PER_CYCLE_MAX_MIN = 480

DEFAULT_PROFILES: list[dict[str, Any]] = [
    {"id": "PLA",  "temp_c": 50, "duration_min": 360},
    {"id": "PETG", "temp_c": 60, "duration_min": 480},
    {"id": "TPU",  "temp_c": 50, "duration_min": 480},  # wants 720; capped
    {"id": "ABS",  "temp_c": 65, "duration_min": 360},
    {"id": "ASA",  "temp_c": 65, "duration_min": 360},
    {"id": "PA",   "temp_c": 70, "duration_min": 1440},  # clamped at 480 per cycle; FSM retries
    {"id": "PC",   "temp_c": 70, "duration_min": 1440},
    {"id": "PVA",  "temp_c": 45, "duration_min": 480},
]

_FALLBACK_PROFILE = {"id": "(unknown)", "temp_c": 50, "duration_min": 360}


def cycle_params_for(
    filament_type: str,
    user_profiles: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Resolve cycle params for a filament type.

    Lookup order: user_profiles (case-insensitive id match), then
    DEFAULT_PROFILES, then the conservative fallback. The user_profiles
    list is the localStorage `multiace_dryer_profiles` shape — entries
    have either {temp, duration} (existing manual-dryer schema) OR
    {temp_c, duration_min} (newer auto-dry schema). We accept both.
    """
    target = (filament_type or "").strip().upper()

    def _normalize(p: dict[str, Any]) -> dict[str, int]:
        return {
            "temp_c": int(p.get("temp_c", p.get("temp", 50))),
            "duration_min": int(p.get("duration_min", p.get("duration", 360))),
        }

    if user_profiles:
        for p in user_profiles:
            if str(p.get("id", "")).strip().upper() == target:
                return _normalize(p)
    for p in DEFAULT_PROFILES:
        if p["id"].upper() == target:
            return _normalize(p)
    return _normalize(_FALLBACK_PROFILE)


def reconcile_loaded_slots(
    loaded_types: list[str],
    user_profiles: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Reconcile cycle params for a set of loaded filament types in one ACE.

    Rule:
    - effective_temp_c     = min(per-slot temp_c)        — strictest cap
    - effective_duration   = min(per-slot duration_min)  — never bake the soft one
    - mixed_filament_warning = True if >1 distinct type
    - Per-cycle DURATION clamped at PER_CYCLE_MAX_MIN (480) before return
    """
    if not loaded_types:
        return {
            "effective_temp_c": None,
            "effective_duration_min": None,
            "mixed_filament_warning": False,
        }
    cycles = [cycle_params_for(t, user_profiles) for t in loaded_types]
    distinct = {(t or "").strip().upper() for t in loaded_types}
    return {
        "effective_temp_c": min(c["temp_c"] for c in cycles),
        "effective_duration_min": min(
            min(c["duration_min"] for c in cycles),
            PER_CYCLE_MAX_MIN,
        ),
        "mixed_filament_warning": len(distinct) > 1,
    }


class DebounceBuffer:
    """Counts consecutive above-threshold observations.

    Used to debounce wake-threshold crossings — we want N samples in a row
    above the wake line before we trust it (lid-opening / sensor blip
    rejection). A single dip below resets the count.
    """

    __slots__ = ("_required", "_count")

    def __init__(self, required: int) -> None:
        if required < 1:
            raise ValueError(f"required must be >= 1, got {required}")
        self._required = required
        self._count = 0

    def __len__(self) -> int:  # for test introspection
        return self._count

    def observe_above(self) -> None:
        self._count = min(self._count + 1, self._required)

    def observe_below(self) -> None:
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def is_above_threshold(self) -> bool:
        return self._count >= self._required


# ---- FSM inputs/outputs ----

@dataclass
class Inputs:
    """Everything the FSM reads from the outside world per tick.

    All fields are snapshot values; the FSM is a pure function of these
    plus the persisted/ephemeral state plus now_ts.
    """
    active_device: int | None
    head_source: dict[str, dict[str, Any] | None]   # /api/state["head_source"]
    swap_in_progress: bool
    humidity_ok: bool
    humidity_pct: float
    cavity_temp_c: float | None
    klipper_print_state: str  # "standby" | "printing" | "paused" | "complete" | "cancelled" | "error"
    dryer_status: str         # "stop" | "drying"
    user_profiles: list[dict[str, Any]] | None  # localStorage profiles, or None to use defaults


@dataclass
class Ephemeral:
    """In-memory state that doesn't persist across restarts (intentionally).

    The debounce buffer starts empty on every boot — we never auto-trigger
    on stale data. Drying-cycle bookkeeping (start ts, start RH, effective
    cycle params) is needed across ticks within one DRYING run but not
    across multiace-web restarts (boot reconciliation handles that case).
    """
    debounce: DebounceBuffer = field(default_factory=lambda: DebounceBuffer(required=5))
    sensor_miss_count: int = 0
    drying_started_ts: float = 0.0
    drying_start_rh: float = 0.0
    effective_temp_c: int | None = None
    effective_duration_min: int | None = None
    # Per-cooldown-period skip-event rate limit: track the last-emitted ts per code.
    skip_emitted_ts: dict[str, float] = field(default_factory=dict)


@dataclass
class Transition:
    """One side-effect emission. The runtime task converts these into
    Activity events + (selectively) Moonraker toasts."""
    event: str            # AUTODRY_TRIGGERED, AUTODRY_FINISHED, AUTODRY_SKIPPED_*, etc.
    payload: dict[str, Any] = field(default_factory=dict)


# ---- FSM safety caps (constants — overridable via env in the runtime task) ----

DEFAULT_DEBOUNCE_REQUIRED = 5
DEFAULT_COOLDOWN_MIN = 30
DEFAULT_MAX_RUN_MIN = 720
DEFAULT_DAILY_DUTY_MAX_MIN = 1080
DEFAULT_MIN_DELTA_PCT = 3
DEFAULT_SKIP_RATE_LIMIT_SEC = DEFAULT_COOLDOWN_MIN * 60  # one of each skip type per cooldown


def _filament_types_for_ace(
    head_source: dict[str, dict[str, Any] | None],
    target_ace: int,
) -> list[str]:
    """Extract the filament `type` strings for slots that feed any toolhead
    sourced from `target_ace`. Used to drive the strictest-rule reconciler."""
    out = []
    for src in head_source.values():
        if not src:
            continue
        if src.get("ace") == target_ace:
            t = src.get("type") or ""
            if t.strip():
                out.append(t)
    return out


def _emit_skip(
    eph: Ephemeral,
    code: str,
    payload: dict[str, Any],
    now_ts: float,
    rate_limit_sec: float = DEFAULT_SKIP_RATE_LIMIT_SEC,
) -> Transition | None:
    """Rate-limit AUTODRY_SKIPPED_* events to once per `rate_limit_sec`.

    A missing entry means "never emitted" — emit immediately. After that,
    subsequent calls within the window are suppressed.
    """
    if code in eph.skip_emitted_ts:
        last = eph.skip_emitted_ts[code]
        if now_ts - last < rate_limit_sec:
            return None
    eph.skip_emitted_ts[code] = now_ts
    return Transition(event=code, payload=payload)


def tick_fsm(
    persisted: PersistedState,
    eph: Ephemeral,
    inputs: Inputs,
    now_ts: float,
    *,
    debounce_required: int = DEFAULT_DEBOUNCE_REQUIRED,
    cooldown_min: int = DEFAULT_COOLDOWN_MIN,
    max_run_min: int = DEFAULT_MAX_RUN_MIN,
    daily_duty_max_min: int = DEFAULT_DAILY_DUTY_MAX_MIN,
    min_delta_pct: float = DEFAULT_MIN_DELTA_PCT,
) -> tuple[PersistedState, list[Transition]]:
    """One FSM tick. Pure function: same inputs → same outputs.

    Returns (new_persisted_state, transitions_to_emit). Mutates `eph`
    in-place (the caller owns the Ephemeral instance for the FSM's
    lifetime).
    """
    transitions: list[Transition] = []
    p = dataclasses.replace(
        persisted,
        fsm=dataclasses.replace(persisted.fsm),  # shallow-copy fsm too
    )
    s = p.fsm.state

    # FAULTED is sticky — only reset_fault clears it (handled in API layer).
    if s == FSMState.FAULTED:
        return p, transitions

    # IDLE / WATCHING entry-condition shared evaluation
    target_loaded_types = _filament_types_for_ace(inputs.head_source, p.target_ace)
    sensor_ok = inputs.humidity_ok and 0.0 <= inputs.humidity_pct <= 100.0
    target_active = inputs.active_device == p.target_ace
    has_filament = bool(target_loaded_types)
    enabled = p.mode in ("log", "active")

    can_be_armed = enabled and sensor_ok and target_active and has_filament

    # ---- IDLE ----
    if s == FSMState.IDLE:
        if can_be_armed:
            p.fsm.state = FSMState.WATCHING
            p.fsm.since_ts = now_ts
            eph.debounce.reset()
        return p, transitions

    # If we lose any IDLE precondition while not in DRYING/OBSERVED_DRYING/COOLDOWN
    # demote to IDLE. (DRYING/OBSERVED keep running — we don't yank a heater mid-cycle.)
    if s == FSMState.WATCHING and not can_be_armed:
        p.fsm.state = FSMState.IDLE
        eph.debounce.reset()
        return p, transitions

    # ---- WATCHING ----
    if s == FSMState.WATCHING:
        # OBSERVED_DRYING entry: user clicked manual dry while we were watching.
        if inputs.dryer_status == "drying":
            p.fsm.state = FSMState.OBSERVED_DRYING
            eph.drying_started_ts = now_ts
            eph.drying_start_rh = inputs.humidity_pct
            eph.debounce.reset()
            return p, transitions

        # Other guards (rate-limited skips). Order: most-likely-to-fire first.
        if inputs.klipper_print_state in ("printing", "paused"):
            t = _emit_skip(eph, "AUTODRY_SKIPPED_PRINT",
                           {"klipper_state": inputs.klipper_print_state}, now_ts)
            if t:
                transitions.append(t)
            eph.debounce.reset()
            return p, transitions

        if inputs.swap_in_progress:
            t = _emit_skip(eph, "AUTODRY_SKIPPED_SWAP", {}, now_ts)
            if t:
                transitions.append(t)
            return p, transitions

        # Daily-duty cap
        recent_min = sum(
            d.get("ran_min", 0)
            for d in p.fsm.daily_duty
            if now_ts - float(d.get("started_ts", 0)) < 86400
        )
        if recent_min >= daily_duty_max_min:
            t = _emit_skip(eph, "AUTODRY_SKIPPED_DAILY",
                           {"recent_min": recent_min, "cap_min": daily_duty_max_min},
                           now_ts)
            if t:
                transitions.append(t)
            return p, transitions

        # In cooldown? (cooldown_until_ts is set on entry to COOLDOWN)
        if now_ts < p.fsm.cooldown_until_ts:
            return p, transitions

        # Wake-threshold debounce
        wake = p.target_pct + p.hysteresis_pp
        if inputs.humidity_pct > wake:
            eph.debounce.observe_above()
        else:
            eph.debounce.observe_below()

        if not eph.debounce.is_above_threshold():
            return p, transitions

        # All guards passed + debounce satisfied → resolve cycle params
        rec = reconcile_loaded_slots(target_loaded_types, inputs.user_profiles)
        eph.effective_temp_c = rec["effective_temp_c"]
        eph.effective_duration_min = rec["effective_duration_min"]

        if p.mode == "log":
            transitions.append(Transition(
                event="AUTODRY_DRY_RUN",
                payload={
                    "ace": p.target_ace,
                    "target_temp": eph.effective_temp_c,
                    "duration_min": eph.effective_duration_min,
                    "trigger_rh": inputs.humidity_pct,
                    "reason": "wake_threshold",
                    "dry_run": True,
                },
            ))
            p.fsm.state = FSMState.COOLDOWN
            p.fsm.cooldown_until_ts = now_ts + cooldown_min * 60
            eph.debounce.reset()
            return p, transitions

        # mode=active → enter DRYING
        transitions.append(Transition(
            event="AUTODRY_TRIGGERED",
            payload={
                "ace": p.target_ace,
                "target_temp": eph.effective_temp_c,
                "duration_min": eph.effective_duration_min,
                "trigger_rh": inputs.humidity_pct,
                "reason": "wake_threshold",
                "dry_run": False,
            },
        ))
        p.fsm.state = FSMState.DRYING
        p.fsm.since_ts = now_ts
        eph.drying_started_ts = now_ts
        eph.drying_start_rh = inputs.humidity_pct
        eph.debounce.reset()
        return p, transitions

    # ---- DRYING ----
    if s == FSMState.DRYING:
        # Print started mid-cycle? Skip + COOLDOWN. (Klipper would halt the
        # heater anyway when it grabs the bus; we stop tracking.)
        if inputs.klipper_print_state in ("printing", "paused"):
            t = _emit_skip(eph, "AUTODRY_SKIPPED_PRINT",
                           {"interrupted_drying": True}, now_ts)
            if t:
                transitions.append(t)
            p.fsm.state = FSMState.COOLDOWN
            p.fsm.cooldown_until_ts = now_ts + cooldown_min * 60
            return p, transitions

        ran_min = int((now_ts - eph.drying_started_ts) / 60)

        # Success: target reached
        if inputs.humidity_pct <= p.target_pct:
            p.fsm.last_run = LastRun(
                kind="auto",
                outcome="success",
                started_ts=eph.drying_started_ts,
                ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh,
                end_rh=inputs.humidity_pct,
                temp_c_used=eph.effective_temp_c or 0,
                duration_min=eph.effective_duration_min or 0,
                ran_min=ran_min,
            )
            p.fsm.daily_duty.append({"started_ts": eph.drying_started_ts, "ran_min": ran_min})
            transitions.append(Transition(
                event="AUTODRY_FINISHED",
                payload={
                    "ace": p.target_ace,
                    "start_rh": eph.drying_start_rh,
                    "end_rh": inputs.humidity_pct,
                    "ran_min": ran_min,
                },
            ))
            p.fsm.state = FSMState.COOLDOWN
            p.fsm.cooldown_until_ts = now_ts + cooldown_min * 60
            return p, transitions

        # FAULTED: max_run_min exceeded
        if ran_min >= max_run_min:
            p.fsm.fault = Fault(
                code="FAILED_LIMIT",
                since_ts=now_ts,
                msg=f"{ran_min}m run did not cross target {p.target_pct}%",
            )
            p.fsm.last_run = LastRun(
                kind="auto", outcome="failed",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=eph.effective_temp_c or 0,
                duration_min=eph.effective_duration_min or 0,
                ran_min=ran_min,
            )
            transitions.append(Transition(event="AUTODRY_FAILED_LIMIT",
                                          payload={"ace": p.target_ace, "ran_min": ran_min}))
            p.fsm.state = FSMState.FAULTED
            return p, transitions

        # FAULTED: min_delta not met after the requested duration
        if (eph.effective_duration_min and ran_min >= eph.effective_duration_min):
            delta = eph.drying_start_rh - inputs.humidity_pct
            if delta < min_delta_pct:
                p.fsm.fault = Fault(
                    code="FAILED_DELTA",
                    since_ts=now_ts,
                    msg=f"{ran_min}m run moved RH {eph.drying_start_rh:.1f}→{inputs.humidity_pct:.1f} (Δ={delta:.1f}pp)",
                )
                p.fsm.last_run = LastRun(
                    kind="auto", outcome="failed",
                    started_ts=eph.drying_started_ts, ended_ts=now_ts,
                    trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                    temp_c_used=eph.effective_temp_c or 0,
                    duration_min=eph.effective_duration_min,
                    ran_min=ran_min,
                )
                transitions.append(Transition(event="AUTODRY_FAILED_DELTA",
                                              payload={"ace": p.target_ace,
                                                       "delta_pp": delta}))
                p.fsm.state = FSMState.FAULTED
                return p, transitions
            # Duration ran out but delta is OK → treat as success-ish (still above
            # target, but we made progress). Go to COOLDOWN, retry next cycle.
            p.fsm.last_run = LastRun(
                kind="auto", outcome="success",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=eph.effective_temp_c or 0,
                duration_min=eph.effective_duration_min,
                ran_min=ran_min,
            )
            p.fsm.daily_duty.append({"started_ts": eph.drying_started_ts,
                                     "ran_min": ran_min})
            transitions.append(Transition(
                event="AUTODRY_FINISHED",
                payload={"ace": p.target_ace,
                         "start_rh": eph.drying_start_rh,
                         "end_rh": inputs.humidity_pct,
                         "ran_min": ran_min,
                         "still_above_target": True},
            ))
            p.fsm.state = FSMState.COOLDOWN
            p.fsm.cooldown_until_ts = now_ts + cooldown_min * 60
            return p, transitions

        # Otherwise: still drying, keep going.
        return p, transitions

    # ---- OBSERVED_DRYING ----
    if s == FSMState.OBSERVED_DRYING:
        if inputs.dryer_status == "stop":
            ran_min = int((now_ts - eph.drying_started_ts) / 60)
            p.fsm.last_run = LastRun(
                kind="manual", outcome="success",
                started_ts=eph.drying_started_ts, ended_ts=now_ts,
                trigger_rh=eph.drying_start_rh, end_rh=inputs.humidity_pct,
                temp_c_used=0,
                duration_min=0,
                ran_min=ran_min,
            )
            p.fsm.state = FSMState.COOLDOWN
            p.fsm.cooldown_until_ts = now_ts + cooldown_min * 60
        return p, transitions

    # ---- COOLDOWN ----
    if s == FSMState.COOLDOWN:
        if now_ts >= p.fsm.cooldown_until_ts:
            p.fsm.state = FSMState.WATCHING
            p.fsm.since_ts = now_ts
            eph.debounce.reset()
        return p, transitions

    return p, transitions
