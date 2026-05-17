"""Pure-Python Govee H5074/H5075/H5104/H5105 advertisement decoder.

Split out from ``govee_bridge.py`` so the unit tests can verify decoding
without pulling in fastapi/bleak. The bridge imports from this module.

Decoder reference: https://github.com/wcbonner/GoveeBTTempLogger

Multi-device support: ``ingest_advertisement`` accepts either a single
target MAC string (legacy single-device API) or any iterable of MAC
strings (new multi-device API). Each matched device is stored under
``_state['devices'][canonical_mac]``; the most recent reading is also
mirrored into the legacy ``_state['reading']`` / ``_state['last_seen_ts']``
fields so older bridge endpoints and tests continue to work unchanged.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# Manufacturer ID 0xEC88 (LE bytes 0x88 0xEC) - H5075/H5104/H5105.
# 0x0001 used by some firmware revs of the same family.
GOVEE_MFG_IDS = (0xEC88, 0x0001)

_state: dict[str, Any] = {
    # Legacy single-device fields - mirrored from the most-recently-updated
    # device. Kept so callers that predate multi-device support keep working.
    "reading": None,
    "last_seen_ts": 0.0,
    # Shared bridge state.
    "scan_started": False,
    "last_error": None,
    # New: per-device cache keyed by normalized uppercase MAC. Each value
    # is {"reading": dict|None, "last_seen_ts": float, "name": str|None}.
    "devices": {},
}


def decode_govee_h5x(data: bytes) -> tuple[float, float, int] | None:
    """Decode a Govee H5074/H5075/H5101/H5102/H5104/H5105 manufacturer-data
    payload (under mfg id 0xEC88 *or* 0x0001).

    The family ships with two slightly different layouts depending on the
    chip + firmware revision. Both are 6 bytes; we tell them apart by the
    leading "framing" byte(s):

    **H5075 layout** (starts with 0x00):
        byte 0      0x00 (status)
        bytes 1-3   24-bit BE packed magnitude
        byte 4      battery %
        byte 5      reserved

    **H5104 layout** (starts with 0x01 0x01):
        bytes 0-1   0x01 0x01 (frame header)
        bytes 2-4   24-bit BE packed magnitude
        byte 5      battery %

    Both pack ``magnitude`` as::

        combined = (b<<16) | (b<<8) | b      # offset depends on layout
        sign     = -1 if combined & 0x800000 else +1
        magnitude_abs = combined & 0x7fffff
        temp_C   = sign * magnitude_abs / 10000.0
        humidity = (magnitude_abs % 1000) / 10.0

    Returns ``(temperature_c, humidity_pct, battery_pct)`` or ``None`` if
    the payload is too short or the decoded values are out of plausible
    range (sensor offline, garbled advert).
    """
    if len(data) < 6:
        return None
    if data[0] == 0x01 and data[1] == 0x01:
        # H5104 / newer firmware: two header bytes, magnitude at 2-4, battery at 5.
        combined = (data[2] << 16) | (data[3] << 8) | data[4]
        battery = data[5]
    else:
        # H5075 / H5074: one header byte, magnitude at 1-3, battery at 4.
        combined = (data[1] << 16) | (data[2] << 8) | data[3]
        battery = data[4]
    sign = -1 if combined & 0x800000 else 1
    magnitude_abs = combined & 0x7FFFFF
    temp_c = sign * magnitude_abs / 10000.0
    humidity = (magnitude_abs % 1000) / 10.0
    if not (-20.0 <= temp_c <= 80.0):
        return None
    if not (0.0 <= humidity <= 100.0):
        return None
    return temp_c, humidity, battery


def normalize_mac(mac: str) -> str:
    """Uppercase, colon-separated MAC for case-insensitive matching."""
    s = mac.upper().replace("-", ":").replace(" ", "")
    if ":" not in s and len(s) == 12:
        s = ":".join(s[i : i + 2] for i in range(0, 12, 2))
    return s


def _coerce_targets(target: str | Iterable[str]) -> set[str]:
    """Accept either a single MAC string (legacy API) or an iterable of
    MACs (multi-device API). Returns a normalized set."""
    if isinstance(target, str):
        return {normalize_mac(target)} if target else set()
    return {normalize_mac(m) for m in target if m}


def ingest_advertisement(
    address: str,
    manufacturer_data: dict[int, bytes],
    rssi: int | None,
    target_mac: str | Iterable[str],
    *,
    name: str | None = None,
    now: float | None = None,
) -> bool:
    """Feed an advertisement into the cache. Returns True if it matched
    one of the target MAC(s) and decoded a plausible reading.

    The parameter is kept named ``target_mac`` for backwards compatibility
    with the original single-device callers and existing tests; it accepts
    either a single MAC string or any iterable of MAC strings.

    On success, updates both ``_state['devices'][canonical]`` (new per-device
    cache) AND ``_state['reading']`` / ``_state['last_seen_ts']`` (legacy
    single-device mirror) so older endpoints keep returning data.
    """
    target_set = _coerce_targets(target_mac)
    if not target_set:
        return False
    canonical = normalize_mac(address)
    if canonical not in target_set:
        return False
    for mfg_id, mfg_data in (manufacturer_data or {}).items():
        if mfg_id not in GOVEE_MFG_IDS:
            continue
        decoded = decode_govee_h5x(mfg_data)
        if decoded is None:
            continue
        temp_c, humidity, battery = decoded
        reading = {
            "temperature": round(temp_c, 2),
            "humidity": round(humidity, 1),
            "battery": battery,
            "rssi": rssi,
        }
        seen_ts = now if now is not None else time.time()
        # New per-device cache.
        device = _state["devices"].setdefault(
            canonical,
            {"reading": None, "last_seen_ts": 0.0, "name": name},
        )
        device["reading"] = reading
        device["last_seen_ts"] = seen_ts
        if name and not device.get("name"):
            device["name"] = name
        # Legacy mirror.
        _state["reading"] = reading
        _state["last_seen_ts"] = seen_ts
        _state["last_error"] = None
        return True
    return False
