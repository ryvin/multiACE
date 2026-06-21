# Copyright (C) 2026  multiACE contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Saveable filament loadouts ("snapshots").

A snapshot is a named capture of which slot feeds each toolhead, plus the
material/color/spool that was there. Re-applying one produces a *plan* — the
ACE_LOAD_HEAD actions to recreate it, plus warnings where the current slot
bindings no longer match — so the UI can review before enqueuing. The store is
plain JSON files; everything here is framework-free for unit-testing.
"""
import json
import re
from pathlib import Path

# A snapshot name is human-friendly but must not escape the store directory.
_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")


def _sanitize_name(name):
    """Return a filesystem-safe snapshot name, or None if it is empty/unsafe
    (path separators, traversal, control chars)."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or not _NAME_RE.match(name):
        return None
    return name


def _bindings_by_slot(slots_response):
    """(ace, slot) -> spool dict (or None) from an /api/slots response."""
    out = {}
    for ace_block in (slots_response.get("aces") or []):
        ace = int(ace_block["index"])
        for sb in (ace_block.get("slots") or []):
            out[(ace, int(sb["slot"]))] = sb.get("spool")
    return out


def capture_loadout(head_source, slots_response):
    """Capture the current loadout: for each loaded head, the (ace, slot) it
    feeds from plus material/color/spool pulled from the slot binding (falling
    back to the head_source entry's own type/color). Empty heads are skipped.
    """
    bindings = _bindings_by_slot(slots_response)
    heads = {}
    for head, src in (head_source or {}).items():
        if not src:
            continue
        ace, slot = src.get("ace"), src.get("slot")
        if ace is None or slot is None:
            continue
        b = bindings.get((int(ace), int(slot))) or {}
        material = (b.get("material") or src.get("type") or "").strip().upper() or None
        color = (b.get("color") or src.get("color") or "").strip().lstrip("#").lower() or None
        heads[str(head)] = {
            "ace": int(ace),
            "slot": int(slot),
            "material": material,
            "color": color,
            "spool_name": b.get("name"),
        }
    return {"heads": heads}


def plan_apply(snapshot, slots_response):
    """Compute the actions to re-apply a snapshot against the current slot
    bindings, plus warnings where a slot no longer holds the expected spool.
    Does not execute anything."""
    bindings = _bindings_by_slot(slots_response)
    actions, warnings = [], []
    heads = (snapshot or {}).get("heads") or {}
    for head, entry in sorted(heads.items(), key=lambda kv: int(kv[0])):
        ace, slot = entry.get("ace"), entry.get("slot")
        if ace is None or slot is None:
            continue
        head_i, ace_i, slot_i = int(head), int(ace), int(slot)
        actions.append({
            "head": head_i, "ace": ace_i, "slot": slot_i,
            "gcode": "ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d" % (head_i, ace_i, slot_i),
        })
        want = (entry.get("material") or "").strip().upper()
        cur = bindings.get((ace_i, slot_i))
        if cur is None:
            warnings.append(
                "Head %d: ACE %d / Slot %d has no bound spool now (snapshot expected %s)."
                % (head_i, ace_i, slot_i, entry.get("spool_name") or want or "unknown"))
        elif want and (cur.get("material") or "").strip().upper() != want:
            warnings.append(
                "Head %d: ACE %d / Slot %d now holds %s, snapshot expected %s."
                % (head_i, ace_i, slot_i, cur.get("material") or "?", want))
    return {"actions": actions, "warnings": warnings}


class SnapshotStore:
    """File-backed store of named loadout snapshots (one JSON file each)."""

    def __init__(self, directory):
        self.dir = Path(directory)

    def _path(self, name):
        safe = _sanitize_name(name)
        return None if safe is None else self.dir / (safe + ".json")

    def list(self):
        if not self.dir.is_dir():
            return []
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({
                "name": f.stem,
                "created_at": data.get("created_at"),
                "heads": data.get("heads", {}),
            })
        return out

    def save(self, name, data):
        path = self._path(name)
        if path is None:
            raise ValueError("invalid snapshot name: %r" % (name,))
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, name):
        path = self._path(name)
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def delete(self, name):
        path = self._path(name)
        if path is None or not path.exists():
            return False
        path.unlink()
        return True
