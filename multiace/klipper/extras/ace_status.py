# multiACE — pure, Klipper-free builder for the `ace` status object consumed by
# HelixScreen. No serial/Klipper imports so it is unit-testable standalone.
# Spec: docs/superpowers/specs/2026-05-27-helixscreen-multiace-sp1-status-contract-design.md
# License: GPL-3.0

DEFAULT_SLOT_COUNT = 4
DEFAULT_HEAD_COUNT = 4
DEFAULT_STALE_AFTER_S = 5.0


def _slot_status(frame_status):
    """Map an ACE frame slot status to the contract value.

    The frame uses 'empty'/'empty1' for unoccupied; anything else is occupied.
    """
    if not frame_status:
        return "empty"
    return "empty" if str(frame_status).startswith("empty") else "available"


def _coerce_color(color):
    """Coerce a frame color into a 3-int [r, g, b] list clamped to 0..255."""
    try:
        r, g, b = color
        return [
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        ]
    except (TypeError, ValueError):
        return [0, 0, 0]


def _build_mapped_tool_index(head_source):
    """Invert head_source (head -> {ace_index, slot}) into {(ace_index, slot): head}."""
    rev = {}
    if not head_source:
        return rev
    for head, source in head_source.items():
        if (source and source.get("ace_index") is not None
                and source.get("slot") is not None):
            rev[(int(source["ace_index"]), int(source["slot"]))] = int(head)
    return rev


def _build_head_source_out(head_source):
    """Emit exactly four head entries; empty heads carry unit/slot = None."""
    out = []
    for head in range(DEFAULT_HEAD_COUNT):
        source = head_source.get(head) if head_source else None
        if (source and source.get("ace_index") is not None
                and source.get("slot") is not None):
            entry = {"head": head, "unit": int(source["ace_index"]),
                     "slot": int(source["slot"])}
            for key in ("brand", "type"):
                if source.get(key):
                    entry[key] = source[key]
            if "color" in source:
                entry["color"] = _coerce_color(source.get("color"))
            out.append(entry)
        else:
            out.append({"head": head, "unit": None, "slot": None})
    return out


def _build_sensors_out(sensors_per_head):
    """SP3: emit list-of-4 bools for per-head filament-at-gate sensor truth.
    Lives at top-level on the ace status object — head_source[] is intentionally
    NOT exposed on the wire (ace.py:get_status excludes it to preserve the
    legacy self._head_source dict shape that poller.py et al depend on)."""
    sensors = sensors_per_head or {}
    return [bool(sensors.get(h, False)) for h in range(DEFAULT_HEAD_COUNT)]


def _build_slot(slot_index, global_index, slot_frame, mapped_tool):
    """Build one contract slot dict from a frame slot dict.

    Optional string fields (type/brand/sku) are omitted when empty. color and
    rfid are emitted whenever present in the frame.
    """
    sf = slot_frame if isinstance(slot_frame, dict) else {}
    slot = {
        "slot_index": slot_index,
        "global_index": global_index,
        "status": _slot_status(sf.get("status")),
        "mapped_tool": mapped_tool,
    }
    if "color" in sf:
        slot["color"] = _coerce_color(sf["color"])
    if sf.get("type"):
        slot["type"] = sf["type"]
    if sf.get("brand"):
        slot["brand"] = sf["brand"]
    if sf.get("sku"):
        slot["sku"] = sf["sku"]
    if "rfid" in sf:
        try:
            slot["rfid"] = int(sf["rfid"])
        except (TypeError, ValueError):
            pass
    return slot


def _build_environment(frame):
    """EnvironmentData from the frame: temperature_c from 'temp'; humidity only
    if the frame carries a 'humidity' key (forward-compat — the v1 frame has none).
    """
    try:
        temp = float(frame.get("temp", 0) or 0)
    except (TypeError, ValueError):
        temp = 0.0
    env = {"temperature_c": temp, "humidity_pct": 0.0, "has_humidity": False}
    if "humidity" in frame:
        try:
            env["humidity_pct"] = float(frame.get("humidity") or 0)
            env["has_humidity"] = True
        except (TypeError, ValueError):
            pass
    return env


def _unit_display_name(idx):
    return "ACE %s" % chr(ord("A") + idx) if idx < 26 else "ACE %d" % idx


def _offline_unit(idx, first_global, mapped_index, slot_count):
    slots = []
    for s in range(slot_count):
        slots.append({
            "slot_index": s,
            "global_index": first_global + s,
            "status": "unknown",
            "mapped_tool": mapped_index.get((idx, s), -1),
        })
    return {
        "unit_index": idx, "name": "ace_%d" % idx,
        "display_name": _unit_display_name(idx),
        "slot_count": slot_count, "first_slot_global_index": first_global,
        "connected": False, "status": "error",
        "environment": {"temperature_c": 0.0, "humidity_pct": 0.0, "has_humidity": False},
        "slots": slots,
    }


def _build_unit(idx, entry, now, stale_after_s, first_global, mapped_index,
                default_slot_count=DEFAULT_SLOT_COUNT):
    """Build one unit dict. Stale/missing frames yield a connected=False unit
    with `unknown` slots (never dropped, so on-screen indices stay stable)."""
    frame = None
    if isinstance(entry, dict):
        recv_ts = entry.get("recv_ts")
        candidate = entry.get("result")
        if (recv_ts is not None and isinstance(candidate, dict)
                and (now - recv_ts) <= stale_after_s):
            frame = candidate
    if frame is None:
        return _offline_unit(idx, first_global, mapped_index, default_slot_count)

    frame_slots = frame.get("slots") or []
    slot_count = len(frame_slots) or default_slot_count
    slots = []
    for s in range(slot_count):
        sf = frame_slots[s] if s < len(frame_slots) else {}
        slots.append(_build_slot(s, first_global + s, sf, mapped_index.get((idx, s), -1)))
    return {
        "unit_index": idx, "name": "ace_%d" % idx,
        "display_name": _unit_display_name(idx),
        "slot_count": slot_count, "first_slot_global_index": first_global,
        "connected": True, "status": frame.get("status") or "ready",
        "environment": _build_environment(frame),
        "slots": slots,
    }


def _minimal_frame(firmware_version, status="error"):
    return {
        "model": "ACE Pro", "firmware": firmware_version, "type_name": "multiACE",
        "device_count": 0, "active_unit": -1, "current_tool": -1, "current_slot": -1,
        "total_slots": 0, "head_source": [], "units": [], "slots": [],
        "sensors": [False] * DEFAULT_HEAD_COUNT,
        "humidity": 0.0, "status": status,
    }


def _derive_current(head_source, active_index, units):
    """Best-effort current tool/slot: lowest head sourced from the active unit.
    Exact semantics deferred to SP2 (see spec open questions)."""
    if not head_source:
        return -1, -1
    for head in range(DEFAULT_HEAD_COUNT):
        source = head_source.get(head)
        if (source and source.get("ace_index") == active_index
                and source.get("slot") is not None):
            unit_idx = int(source["ace_index"])
            if 0 <= unit_idx < len(units):
                return head, units[unit_idx]["first_slot_global_index"] + int(source["slot"])
    return -1, -1


def build_multiace_status(devices, active_index, head_source, last_status, now,
                          firmware_version, stale_after_s=DEFAULT_STALE_AFTER_S,
                          sensors_per_head=None):
    """Assemble the `ace` Klipper status object. Pure; never raises."""
    try:
        device_count = len(devices) if devices else 0
        if device_count == 0:
            return _minimal_frame(firmware_version)

        mapped_index = _build_mapped_tool_index(head_source)
        last_status = last_status or {}
        units = []
        flat_slots = []
        running_global = 0
        for i in range(device_count):
            unit = _build_unit(i, last_status.get(i), now, stale_after_s,
                               running_global, mapped_index)
            running_global += unit["slot_count"]
            units.append(unit)
            flat_slots.extend(unit["slots"])

        active_unit = active_index if 0 <= active_index < device_count else -1
        current_tool, current_slot = _derive_current(head_source, active_index, units)
        top_status = units[active_unit]["status"] if active_unit >= 0 else "ready"

        return {
            "model": "ACE Pro", "firmware": firmware_version, "type_name": "multiACE",
            "device_count": device_count, "active_unit": active_unit,
            "current_tool": current_tool, "current_slot": current_slot,
            "total_slots": running_global,
            "head_source": _build_head_source_out(head_source),
            "units": units, "slots": flat_slots,
            "sensors": _build_sensors_out(sensors_per_head),
            # legacy top-level aggregate; per-unit humidity lives in units[n]["environment"]
            "humidity": 0.0, "status": top_status,
        }
    except Exception:
        return _minimal_frame(firmware_version)
