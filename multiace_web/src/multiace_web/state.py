"""State models for multiACE web console."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Optional


STATE_MARKER = " STATE "

# Audit actions that mark the end of a SWITCH-family operation. All firmware
# emit sites for these are inside try blocks where _swap_in_progress is still
# True; the finally that clears the flag emits no follow-up audit. See the
# extended comment in CurrentState.apply_event below.
_SWITCH_TERMINAL_ACTIONS = frozenset({
    "SWITCH", "SWITCH_NOOP", "SWITCH_FAILED",
    "SWITCH_AUTO", "SWITCH_AUTO_NOOP", "SWITCH_AUTO_FAILED", "SWITCH_AUTO_PASSIVE",
    "SWITCH_TARGET", "SWITCH_TARGET_NOOP", "SWITCH_TARGET_FAILED",
})


def parse_state_log_line(line: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Parse one line of multiace_state.log.

    Format: "<YYYY-MM-DD HH:MM:SS> STATE <json>"
    Returns (timestamp, data) or None on malformed input.
    """
    line = line.rstrip("\n")
    idx = line.find(STATE_MARKER)
    if idx < 0:
        return None
    ts = line[:idx]
    body = line[idx + len(STATE_MARKER):]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return ts, data


@dataclass
class CurrentState:
    """Aggregated live state of the multiACE system. Single source of truth
    for what to push to clients."""

    active_device: Optional[int] = None
    device_count: int = 0
    connected: bool = False
    serial: Optional[str] = None
    mode: str = "multi"
    swap_in_progress: bool = False
    auto_feed: bool = False
    feed_assist: int = -1
    gate_status: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    head_source: dict[int, Optional[dict]] = field(
        default_factory=lambda: {0: None, 1: None, 2: None, 3: None}
    )
    sensors: dict[int, bool] = field(
        default_factory=lambda: {0: False, 1: False, 2: False, 3: False}
    )
    print_task_config: dict[int, dict] = field(
        default_factory=lambda: {
            i: {"type": "NONE", "color": 0xFFFFFFFF, "vendor": "NONE"}
            for i in range(4)
        }
    )
    last_error: Optional[dict] = None
    last_action_at: Optional[str] = None
    swap_park_available: bool = False

    def apply_event(self, event: dict[str, Any], *, ts: Optional[str] = None) -> None:
        """Update state from a multiace_state.log event payload."""
        if ts is not None:
            self.last_action_at = ts
        for field_name in (
            "active_device", "device_count", "connected", "serial",
            "mode", "swap_in_progress", "auto_feed", "feed_assist",
            "gate_status",
        ):
            if field_name in event:
                setattr(self, field_name, event[field_name])

        if "head_source" in event:
            self.head_source = {int(k): v for k, v in event["head_source"].items()}
        if "sensors" in event:
            self.sensors = {int(k): bool(v) for k, v in event["sensors"].items()}
        if "print_task_config" in event:
            self.print_task_config = {
                int(k): v for k, v in event["print_task_config"].items()
            }

        action = event.get("action", "")

        # All SWITCH-family audits fire inside cmd_ACE_SWITCH (or its
        # AUTO/TARGET cousins) from inside the try block — _swap_in_progress
        # is still True when the audit serializes, and the firmware's
        # `finally` clears the flag but emits no follow-up event. Every one
        # of these actions is the *terminal* event for a switch operation
        # (success, no-op, or failure), so by definition the swap is over.
        # Without this, the dashboard banner sticks on "Tool change in
        # progress" forever after any successful switch.
        if action in _SWITCH_TERMINAL_ACTIONS:
            self.swap_in_progress = False

        # TODO(v1.x): When the frontend grows per-toolhead error display, switch to
        # self.last_errors: dict[int, dict] keyed by head so per-toolhead errors
        # don't overwrite each other. For v1, last_error tracks the most recent failure.
        # SERIAL_WRITE_FAILED is a transport-level event, not a per-head failure —
        # it has no head and never gets cleared by LOAD_HEAD/UNLOAD_HEAD, so the
        # banner would stick forever. It still shows up in the activity feed.
        params = event.get("params", {}) or {}
        head = params.get("head")
        if action.endswith("_FAILED") and isinstance(head, int):
            self.last_error = {
                "action": action,
                "head": head,
                "slot": params.get("slot"),
                "ace": params.get("ace"),
                "reason": params.get("reason"),
                "error": params.get("error", ""),
            }
        elif action in ("LOAD_HEAD", "UNLOAD_HEAD") and isinstance(head, int):
            if self.last_error and self.last_error.get("head") == head:
                self.last_error = None  # cleared by successful retry

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot."""
        return {
            "active_device": self.active_device,
            "device_count": self.device_count,
            "connected": self.connected,
            "serial": self.serial,
            "mode": self.mode,
            "swap_in_progress": self.swap_in_progress,
            "auto_feed": self.auto_feed,
            "feed_assist": self.feed_assist,
            "gate_status": self.gate_status,
            "head_source": self.head_source,
            "sensors": self.sensors,
            "print_task_config": self.print_task_config,
            "last_error": self.last_error,
            "last_action_at": self.last_action_at,
            "swap_park_available": self.swap_park_available,
        }


class EventBuffer:
    """Ring buffer of recent state-log events with monotonic IDs."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._counter = count(1)

    def append(self, event: dict[str, Any]) -> int:
        eid = next(self._counter)
        entry = {"id": eid, **event}
        self._buf.append(entry)
        return eid

    def recent(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        return list(self._buf)[-limit:]

    def since(self, last_id: int) -> list[dict]:
        return [e for e in self._buf if e["id"] > last_id]
