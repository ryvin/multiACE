# License: GPL-3.0
"""Load/save the per-ACE AutodryManager to a plugin-local JSON file.

Load-tolerant by design: a missing or corrupt file must never crash the
plugin at startup — it just starts fresh (all ACEs default to disabled,
IDLE). Writes are atomic (temp file + rename) so a crash mid-write can't
leave a half-written state file behind.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .fsm import AutodryManager

log = logging.getLogger("autodry.persistence")


def load_manager(path: Path) -> AutodryManager:
    try:
        raw = path.read_text()
    except (FileNotFoundError, OSError):
        return AutodryManager()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("autodry state file %s is corrupt; starting fresh", path)
        return AutodryManager()
    if not isinstance(d, dict):
        return AutodryManager()
    return AutodryManager.deserialize(d)


def save_manager(path: Path, manager: AutodryManager) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manager.serialize(), indent=2, default=str)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.replace(path)
