"""Tests for the Govee BLE bridge decoder + advertisement ingestion.

The live BLE scan loop is not exercised here — that path requires real
``bleak``/``bluetoothd``/``hci0`` and is validated on the printer. These
tests cover the *pure* logic: payload decoding, MAC matching, and the
in-memory cache update.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``tools/`` importable as ``govee_bridge`` even though it lives outside
# the ``src/`` package tree (it's a separate uvicorn entrypoint).
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from govee_decode import (  # noqa: E402  (path manipulation above)
    GOVEE_MFG_IDS,
    _state,
    decode_govee_h5x,
    ingest_advertisement,
    normalize_mac as _normalize_mac,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test starts with an empty cache."""
    _state["reading"] = None
    _state["last_seen_ts"] = 0.0
    _state["scan_started"] = False
    _state["last_error"] = None
    yield


# ----- decoder ---------------------------------------------------------------


def test_decode_positive_temperature():
    # 23.4 C, 47.2% RH, 95% battery
    # magnitude = 234*1000 + 472 = 234472 (= 0x039478)
    # battery byte = 95 (0x5F)
    payload = bytes([0x00, 0x03, 0x93, 0xE8, 0x5F, 0x00])
    temp, hum, batt = decode_govee_h5x(payload)
    assert temp == pytest.approx(23.4, abs=0.05)
    assert hum == pytest.approx(47.2, abs=0.05)
    assert batt == 95


def test_decode_negative_temperature():
    # -5.0 C, 30.5% RH, 88% battery
    # magnitude = 50*1000 + 305 = 50305 (= 0x00C481)
    # combined = magnitude | 0x800000 = 0x80C481
    payload = bytes([0x00, 0x80, 0xC4, 0x81, 0x58, 0x00])
    temp, hum, batt = decode_govee_h5x(payload)
    assert temp == pytest.approx(-5.0, abs=0.05)
    assert hum == pytest.approx(30.5, abs=0.05)
    assert batt == 88


def test_decode_short_payload_returns_none():
    assert decode_govee_h5x(b"\x00\x03") is None
    assert decode_govee_h5x(b"") is None


def test_decode_implausible_humidity_returns_none():
    # Force humidity = 999/10 = 99.9 actually still passes; build one that's >100.
    # magnitude % 1000 caps at 999 → impossible to exceed 99.9 by construction.
    # Test the temperature out-of-range gate instead: 100 C is filtered.
    # 100.0 C, 0% RH:
    # magnitude = 1000*1000 + 0 = 1000000 (= 0x0F4240) — temp 100, hum 0
    payload = bytes([0x00, 0x0F, 0x42, 0x40, 0x50, 0x00])
    assert decode_govee_h5x(payload) is None


# ----- MAC normalization -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a4:c1:38:11:22:33", "A4:C1:38:11:22:33"),
        ("A4-C1-38-11-22-33", "A4:C1:38:11:22:33"),
        ("A4C138112233", "A4:C1:38:11:22:33"),
        ("a4 c1 38 11 22 33", "A4:C1:38:11:22:33"),
    ],
)
def test_normalize_mac(raw, expected):
    assert _normalize_mac(raw) == expected


# ----- advertisement ingestion -----------------------------------------------


def _good_payload():
    """Returns the same 23.4 C / 47.2% / 95% payload used above."""
    return bytes([0x00, 0x03, 0x93, 0xE8, 0x5F, 0x00])


def test_ingest_matching_mac_updates_cache():
    target = "A4:C1:38:11:22:33"
    matched = ingest_advertisement(
        address="A4:C1:38:11:22:33",
        manufacturer_data={0xEC88: _good_payload()},
        rssi=-45,
        target_mac=target,
        now=1234.5,
    )
    assert matched is True
    assert _state["reading"]["humidity"] == pytest.approx(47.2)
    assert _state["reading"]["temperature"] == pytest.approx(23.4)
    assert _state["reading"]["battery"] == 95
    assert _state["reading"]["rssi"] == -45
    assert _state["last_seen_ts"] == 1234.5


def test_ingest_lowercase_address_still_matches():
    matched = ingest_advertisement(
        address="a4:c1:38:11:22:33",
        manufacturer_data={0xEC88: _good_payload()},
        rssi=-50,
        target_mac="A4:C1:38:11:22:33",
    )
    assert matched is True


def test_ingest_wrong_mac_does_not_update():
    matched = ingest_advertisement(
        address="A4:C1:38:99:99:99",
        manufacturer_data={0xEC88: _good_payload()},
        rssi=-50,
        target_mac="A4:C1:38:11:22:33",
    )
    assert matched is False
    assert _state["reading"] is None


def test_ingest_wrong_manufacturer_id_does_not_update():
    matched = ingest_advertisement(
        address="A4:C1:38:11:22:33",
        manufacturer_data={0x004C: _good_payload()},  # Apple's mfg id
        rssi=-50,
        target_mac="A4:C1:38:11:22:33",
    )
    assert matched is False
    assert _state["reading"] is None


def test_ingest_alt_manufacturer_id_0x0001_works():
    # Some H5104 firmware revs broadcast under 0x0001 instead of 0xEC88.
    # The bridge accepts either.
    assert 0x0001 in GOVEE_MFG_IDS
    matched = ingest_advertisement(
        address="A4:C1:38:11:22:33",
        manufacturer_data={0x0001: _good_payload()},
        rssi=-50,
        target_mac="A4:C1:38:11:22:33",
    )
    assert matched is True


def test_ingest_implausible_payload_does_not_update():
    # 100 C — filtered by the decoder's range check
    matched = ingest_advertisement(
        address="A4:C1:38:11:22:33",
        manufacturer_data={0xEC88: bytes([0x00, 0x0F, 0x42, 0x40, 0x50, 0x00])},
        rssi=-50,
        target_mac="A4:C1:38:11:22:33",
    )
    assert matched is False
    assert _state["reading"] is None
