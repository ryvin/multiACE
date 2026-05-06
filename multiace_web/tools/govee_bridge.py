"""Govee BLE → HTTP bridge for the multiACE dashboard.

Listens for BLE advertisements from a single Govee H5074/H5075/H5104/H5105
hygrometer (broadcasts every ~2 s, no pairing), decodes the manufacturer-data
payload, and exposes the latest reading as JSON at:

    GET http://127.0.0.1:7127/sensor
    -> {"humidity": 47.2, "temperature": 23.4, "battery": 95,
        "rssi": -45, "age_s": 1.2}
    GET http://127.0.0.1:7127/health
    -> {"ok": true, ...}

The multiACE web backend reads this URL via ``MULTIACE_HUMIDITY_URL`` and
renders the result in the dashboard's environment strip. The bridge runs as
a *separate* uvicorn process to keep BLE I/O off the main web service event
loop and so a `bleak`/`bluetoothd` hiccup can't take down the dashboard.

Configuration via environment:

    GOVEE_BRIDGE_MAC   – required; MAC address of the Govee device.
    GOVEE_BRIDGE_PORT  – optional; default 7127.

Run via ``S63govee-bridge`` (BusyBox sysvinit) on the printer; locally
during dev::

    GOVEE_BRIDGE_MAC=A4:C1:38:XX:XX:XX \\
      uvicorn govee_bridge:app --host 127.0.0.1 --port 7127

Pure decoder logic lives in ``govee_decode.py`` so tests don't need
fastapi/bleak available.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from govee_decode import _state, ingest_advertisement

log = logging.getLogger("multiace.govee")


async def _scan_forever(target_mac: str) -> None:
    """Run a continuous passive BLE scan, retrying on bleak/dbus failures.

    Imports bleak lazily so the module can still be imported (and the route
    handlers exercised) on machines without BLE hardware/drivers."""
    from bleak import BleakScanner  # type: ignore

    def _cb(device: Any, ad: Any) -> None:
        rssi = getattr(ad, "rssi", None) or getattr(device, "rssi", None)
        ingest_advertisement(
            getattr(device, "address", ""),
            getattr(ad, "manufacturer_data", {}) or {},
            rssi,
            target_mac,
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
async def lifespan(app: FastAPI):  # noqa: ARG001 — fastapi protocol
    target = os.environ.get("GOVEE_BRIDGE_MAC", "").strip()
    if not target:
        log.error("GOVEE_BRIDGE_MAC not set; bridge will not scan")
        yield
        return
    task = asyncio.create_task(_scan_forever(target))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="multiACE Govee bridge", lifespan=lifespan)


@app.get("/sensor")
def sensor() -> dict[str, Any]:
    reading = _state["reading"]
    if reading is None:
        # 503 keeps the upstream cache from poisoning with bad data.
        # The dashboard surfaces this as a "sensor offline" tile.
        raise HTTPException(
            status_code=503,
            detail={
                "status": "warming-up" if _state["scan_started"] else "no-scan",
                "last_error": _state["last_error"],
            },
        )
    age = time.time() - _state["last_seen_ts"]
    return {**reading, "age_s": round(age, 1)}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "scan_started": _state["scan_started"],
        "have_reading": _state["reading"] is not None,
        "last_error": _state["last_error"],
    }
