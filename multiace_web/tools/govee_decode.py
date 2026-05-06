"""Pure-Python Govee H5074/H5075/H5104/H5105 advertisement decoder.

Split out from ``govee_bridge.py`` so the unit tests can verify decoding
without pulling in fastapi/bleak. The bridge imports from this module.

Decoder reference: https://github.com/wcbonner/GoveeBTTempLogger
"""
from __future__ import annotations

import time
from typing import Any

# Manufacturer ID 0xEC88 (LE bytes 0x88 0xEC) — H5075/H5104/H5105.
# 0x0001 used by some firmware revs of the same family.
GOVEE_MFG_IDS = (0xEC88, 0x0001)

# In-memory state for the bridge process. Module-private; tests reset it
# via the autouse fixture in tests/test_govee_bridge.py.
_state: dict[str, Any] = {
    "reading": None,        # last decoded {temperature, humidity, battery, rssi}
    "last_seen_ts": 0.0,    # wall-clock time of last reading
    "scan_started": False,  # set True when the BLE scan task is running
    "last_error": None,     # most recent exception message, if any
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

    (Note: temp uses ``/10000.0`` for full precision; older revisions of
    this decoder used integer ``// 1000 / 10`` which lost the sub-decimal
    digit but rounded to the same display value.)

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


def ingest_advertisement(
    address: str,
    manufacturer_data: dict[int, bytes],
    rssi: int | None,
    target_mac: str,
    *,
    now: float | None = None,
) -> bool:
    """Feed an advertisement into the cache. Returns True if it matched the
    target MAC and decoded a plausible reading. Pure function for testability
    (the live scan loop calls this from its detection callback)."""
    target = normalize_mac(target_mac)
    if normalize_mac(address) != target:
        return False
    for mfg_id, mfg_data in (manufacturer_data or {}).items():
        if mfg_id not in GOVEE_MFG_IDS:
            continue
        decoded = decode_govee_h5x(mfg_data)
        if decoded is None:
            continue
        temp_c, humidity, battery = decoded
        _state["reading"] = {
            "temperature": round(temp_c, 2),
            "humidity": round(humidity, 1),
            "battery": battery,
            "rssi": rssi,
        }
        _state["last_seen_ts"] = now if now is not None else time.time()
        _state["last_error"] = None
        return True
    return False
