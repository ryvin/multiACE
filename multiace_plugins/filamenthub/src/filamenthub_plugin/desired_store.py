# License: GPL-3.0
"""Plugin-local durable store of FilamentHub's desired per-slot loadout.

Owned by the plugin (not decay71's override store) so a desired label for a
physically-empty slot survives decay71's eject-debounce garbage collection.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile

log = logging.getLogger("filamenthub.plugin")


def load_desired(path: str) -> dict[str, dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("desired store unreadable (%s): %s", path, e)
        return {}
    slots = data.get("slots") if isinstance(data, dict) else None
    return slots if isinstance(slots, dict) else {}


def save_desired(path: str, printer: str, slots: dict[str, dict]) -> None:
    data = {"schema": 1, "printer": printer, "slots": slots}
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
