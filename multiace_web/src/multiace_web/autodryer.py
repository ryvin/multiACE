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
