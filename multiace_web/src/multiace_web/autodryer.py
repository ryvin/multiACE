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
