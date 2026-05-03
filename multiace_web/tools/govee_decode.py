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
    """Decode a Govee H5074/H5075/H5104/H5105 manufacturer-data payload.

    Layout (after the 0xEC88 LE manufacturer-id prefix is stripped by bleak):

        byte 0      reserved / status flags
        bytes 1-3   packed (signed int24, big-endian) encoding both
                    temperature and humidity:
                      combined = (b1<<16) | (b2<<8) | b3
                      sign     = -1 if MSB set, else +1
                      magnitude = combined & 0x7fffff
                      temp_C   = sign * (magnitude // 1000) / 10.0
                      humidity = (magnitude % 1000) / 10.0
        byte 4      battery percent
        byte 5      reserved

    Returns ``(temperature_c, humidity_pct, battery_pct)`` or ``None`` if
    the payload is too short or values are out of plausible range.
    """
    if len(data) < 5:
        return None
    combined = (data[1] << 16) | (data[2] << 8) | data[3]
    sign = -1 if combined & 0x800000 else 1
    magnitude = combined & 0x7FFFFF
    temp_c = sign * (magnitude // 1000) / 10.0
    humidity = (magnitude % 1000) / 10.0
    battery = data[4] if len(data) > 4 else 0
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
