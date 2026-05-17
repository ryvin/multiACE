"""Govee BLE -> HTTP bridge for the multiACE dashboard (multi-device).

Listens for BLE advertisements from one or more Govee H5074/H5075/H5104/
H5105 hygrometers (broadcasts every ~2 s, no pairing), decodes the
manufacturer-data payload, and exposes the latest readings as JSON.

Endpoints:

    GET /sensor
        Backwards-compat: returns the reading from the PRIMARY device
        (the first MAC in GOVEE_BRIDGE_MACS / GOVEE_BRIDGE_MAC). 503 if
        no reading yet.
        Older callers (single-device installations) get the legacy
        ``_state['reading']`` mirror; the JSON shape is unchanged.

    GET /sensors
        Returns all configured devices, keyed by normalized MAC:
        {
          "E8:76:C6:46:55:68": {
            "name": "GVH5104_5568",
            "temperature": 22.5, "humidity": 47.2,
            "battery": 95, "rssi": -60, "age_s": 1.2
          },
          ...
        }
        Devices that have not produced a reading yet appear with value
        ``null`` so the dashboard can render a "warming up" tile rather
        than treating the absence as a 404.

    GET /sensor/{key}
        Returns one device's reading. ``key`` matches either the
        normalized MAC or the BLE local-name (e.g. ``GVH5104_5568``,
        case-insensitive). 404 if unknown; 503 if no reading yet.

    GET /health
        {"ok": true, "scan_started": true, "configured": N,
         "devices": [...], "last_error": null}

Configuration via environment:

    GOVEE_BRIDGE_MACS  - comma- or space-separated list of target MACs.
                         The first entry is the primary device that
                         /sensor returns. If unset, falls back to
                         GOVEE_BRIDGE_MAC.
    GOVEE_BRIDGE_MAC   - legacy single-device variable; honored when
                         GOVEE_BRIDGE_MACS is empty.
    GOVEE_BRIDGE_PORT  - optional; default 7127.

Run via ``S64govee-bridge`` (BusyBox sysvinit) on the printer; locally
during dev::

    GOVEE_BRIDGE_MACS=A4:C1:38:XX:XX:XX,A4:C1:38:YY:YY:YY \\
      uvicorn govee_bridge:app --host 127.0.0.1 --port 7127

Pure decoder logic lives in ``govee_decode.py`` so tests don't need
fastapi/bleak available.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from govee_decode import _state, ingest_advertisement, normalize_mac

log = logging.getLogger("multiace.govee")


def _parse_macs() -> list[str]:
    """Parse GOVEE_BRIDGE_MACS (comma or whitespace separated), falling
    back to legacy GOVEE_BRIDGE_MAC. Returns a normalized, de-duplicated
    list preserving config order - index 0 is the primary device."""
    raw = os.environ.get("GOVEE_BRIDGE_MACS", "").strip()
    if not raw:
        raw = os.environ.get("GOVEE_BRIDGE_MAC", "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for token in raw.replace(",", " ").split():
        mac = normalize_mac(token)
        if mac and mac not in seen:
            seen.add(mac)
            out.append(mac)
    return out


async def _scan_forever(target_macs: set[str]) -> None:
    """Run a continuous passive BLE scan, retrying on bleak/dbus failures.

    Imports bleak lazily so the module can still be imported (and the
    route handlers exercised) on machines without BLE hardware/drivers."""
    from bleak import BleakScanner  # type: ignore  # noqa: PLC0415  (lazy: optional dep)

    def _cb(device: Any, ad: Any) -> None:
        rssi = getattr(ad, "rssi", None) or getattr(device, "rssi", None)
        name = getattr(ad, "local_name", None) or getattr(device, "name", None)
        ingest_advertisement(
            getattr(device, "address", ""),
            getattr(ad, "manufacturer_data", {}) or {},
            rssi,
            target_macs,
            name=name,
        )

    backoff = 1.0
    while True:
        try:
            async with BleakScanner(detection_callback=_cb):
                _state["scan_started"] = True
                backoff = 1.0
                while True:
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # bleak / dbus / hci0 hiccups
            _state["scan_started"] = False
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.warning("bleak scan failed: %s; retrying in %.1fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    target_macs = _parse_macs()
    app.state.target_macs = target_macs
    if not target_macs:
        log.error("no GOVEE_BRIDGE_MACS / GOVEE_BRIDGE_MAC set; bridge will not scan")
        yield
        return
    log.info(
        "scanning for %d Govee device(s): %s",
        len(target_macs),
        ",".join(target_macs),
    )
    task = asyncio.create_task(_scan_forever(set(target_macs)))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="multiACE Govee bridge", lifespan=lifespan)


def _device_payload(mac: str, *, now: float | None = None) -> dict[str, Any] | None:
    """JSON-ready payload for one device, or None if no reading yet."""
    dev = _state["devices"].get(mac)
    if dev is None or dev.get("reading") is None:
        return None
    if now is None:
        now = time.time()
    return {
        **dev["reading"],
        "name": dev.get("name"),
        "age_s": round(now - dev["last_seen_ts"], 1),
    }


def _legacy_payload() -> dict[str, Any] | None:
    """Backwards-compat payload sourced from ``_state['reading']`` mirror.

    Used by /sensor when no MACs have been configured (very old installs
    that set GOVEE_BRIDGE_MAC at process start without using lifespan)."""
    reading = _state["reading"]
    if reading is None:
        return None
    return {**reading, "age_s": round(time.time() - _state["last_seen_ts"], 1)}


@app.get("/sensor")
def sensor() -> dict[str, Any]:
    """Primary device reading - the first MAC in GOVEE_BRIDGE_MACS, or
    the legacy mirror if no MACs were parsed at startup."""
    macs: list[str] = getattr(app.state, "target_macs", []) or []
    if macs:
        payload = _device_payload(macs[0])
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "warming-up" if _state["scan_started"] else "no-scan",
                    "last_error": _state["last_error"],
                    "primary_mac": macs[0],
                },
            )
        return payload
    # No app.state.target_macs - fall back to the legacy mirror.
    payload = _legacy_payload()
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "warming-up" if _state["scan_started"] else "no-scan",
                "last_error": _state["last_error"],
            },
        )
    return payload


@app.get("/sensors")
def sensors() -> dict[str, Any]:
    """All configured devices, keyed by normalized MAC. Devices with no
    reading yet appear with value ``null`` so the dashboard can render
    a 'warming up' tile rather than 404'ing."""
    macs: list[str] = getattr(app.state, "target_macs", []) or []
    now = time.time()
    return {mac: _device_payload(mac, now=now) for mac in macs}


@app.get("/sensor/{key}")
def sensor_by_key(key: str) -> dict[str, Any]:
    """Look up one device by MAC or BLE local-name (case-insensitive)."""
    macs: list[str] = getattr(app.state, "target_macs", []) or []
    canonical = normalize_mac(key)
    # Try config order first: covers the case where the device hasn't
    # advertised yet (no entry in _state["devices"] yet).
    if canonical in macs:
        payload = _device_payload(canonical)
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "warming-up" if _state["scan_started"] else "no-scan",
                    "mac": canonical,
                },
            )
        return payload
    # Fall back to a name-based lookup across all seen devices.
    key_lc = key.lower()
    for mac, dev in _state["devices"].items():
        name = (dev.get("name") or "").lower()
        if name == key_lc or mac == canonical:
            payload = _device_payload(mac)
            if payload is None:
                raise HTTPException(
                    status_code=503,
                    detail={"status": "warming-up", "mac": mac},
                )
            return payload
    raise HTTPException(status_code=404, detail={"unknown_key": key})


@app.get("/health")
def health() -> dict[str, Any]:
    macs: list[str] = getattr(app.state, "target_macs", []) or []
    now = time.time()
    devices = []
    for mac in macs:
        dev = _state["devices"].get(mac)
        devices.append(
            {
                "mac": mac,
                "name": (dev or {}).get("name"),
                "have_reading": bool(dev and dev.get("reading") is not None),
                "age_s": (
                    round(now - dev["last_seen_ts"], 1)
                    if dev and dev.get("reading") is not None
                    else None
                ),
            }
        )
    return {
        "ok": True,
        "scan_started": _state["scan_started"],
        "configured": len(macs),
        "devices": devices,
        # Legacy field for backwards-compat with old dashboards reading
        # have_reading on the single-device bridge.
        "have_reading": _state["reading"] is not None,
        "last_error": _state["last_error"],
    }
