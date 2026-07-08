# License: GPL-3.0
"""Async client for the local Moonraker HTTP API — gcode + objects/query only.

This plugin never touches serial. It fires ACE_DRY / ACE_SWITCH the same way
the multiACE web console does (POST gcode/script), and reads live ACE state
the same way the web console's poller does (GET objects/query?ace).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class MoonrakerError(Exception):
    """Raised when a Moonraker call fails (HTTP error or connection error)."""


class MoonrakerClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def run_gcode(self, script: str, timeout: float = 600.0) -> str:
        """POST /printer/gcode/script?script=<encoded>.

        Long default timeout — ACE_DRY's ACE_SWITCH leg and drying-cycle
        macros can legitimately take a while on hardware; Moonraker holds
        the HTTP response open until the gcode completes.
        """
        url = f"/printer/gcode/script?script={quote(script)}"
        try:
            resp = await self._client.post(url, timeout=timeout)
        except httpx.HTTPError as e:
            raise MoonrakerError(f"run_gcode failed (connection error)") from e
        if resp.status_code >= 400:
            raise MoonrakerError(f"run_gcode failed (status {resp.status_code})")
        return resp.json().get("result", "ok")

    async def query_objects(self, objects: list[str]) -> dict[str, Any]:
        """GET /printer/objects/query?o1&o2&… -> the status dict."""
        if not objects:
            return {}
        query = "&".join(quote(obj, safe="") for obj in objects)
        url = f"/printer/objects/query?{query}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"query_objects failed") from e
        return resp.json().get("result", {}).get("status", {})


@dataclass
class AceLiveSnapshot:
    """One ACE's live telemetry, as best we could extract it from the
    Klipper `ace` printer object. `humidity_ok=False` means "no usable
    reading this tick" (unknown shape, stale, or not the active device on a
    firmware that only reports the currently-active ACE's sensors)."""
    humidity_ok: bool
    humidity_pct: float
    connected: bool = True


def parse_ace_object(obj: dict[str, Any]) -> dict[int, AceLiveSnapshot]:
    """Extract per-ACE humidity snapshots from the `ace` Klipper object.

    Two shapes are supported, most-specific first:

    1. Per-unit (`obj["units"]`, a list of dicts with
       `environment: {humidity_pct, has_humidity}` — the SP2/SP3 HelixScreen
       contract already shipped in this repo's ace_status.py). Preferred:
       gives independent humidity per ACE regardless of which one is
       currently "active" on the single serial connection.
    2. Legacy single-active shape: only the currently-active ACE (from
       `active_device`, 1-based) has usable sensor data; every other index
       is reported as humidity_ok=False (unknown) rather than guessed.

    NOTE: decay71's exact `ace` object shape wasn't available to verify
    against real hardware while building this plugin (out of tree, not
    vendored into this repo) — shape (1) mirrors this repo's own
    ace_status.py `units[]` contract; shape (2) is the safe fallback if
    decay71 exposes something else. Worth confirming against a live decay71
    printer before relying on multi-ACE (non-active) auto-trigger.
    """
    out: dict[int, AceLiveSnapshot] = {}

    units = obj.get("units")
    if isinstance(units, list) and units:
        for i, unit in enumerate(units):
            if not isinstance(unit, dict):
                continue
            idx = unit.get("unit_index", i)
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = i
            env = unit.get("environment") or {}
            has_humidity = bool(env.get("has_humidity"))
            try:
                pct = float(env.get("humidity_pct", 0.0) or 0.0)
            except (TypeError, ValueError):
                pct = 0.0
                has_humidity = False
            out[idx] = AceLiveSnapshot(
                humidity_ok=has_humidity,
                humidity_pct=pct,
                connected=bool(unit.get("connected", True)),
            )
        return out

    # Legacy fallback: only the active device's dryer/humidity data is live.
    active = active_device_0based(obj)
    if active is not None:
        ds = obj.get("dryer_status") or {}
        # No hardware humidity field in the legacy shape; humidity comes
        # from an external sensor bridge in the source fork, which this
        # Moonraker-only sidecar deliberately does not vendor (see
        # README "Known limitations"). Report unknown rather than guessing.
        del ds  # not usable for humidity; kept for readability of intent
        out[active] = AceLiveSnapshot(humidity_ok=False, humidity_pct=0.0)
    return out


def active_device_0based(obj: dict[str, Any]) -> int | None:
    """`active_device` in the legacy `ace` object is 1-based (0 = none)."""
    raw = obj.get("active_device")
    if raw is None:
        raw = obj.get("active_unit")
        if raw is None:
            return None
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return None
        return v if v >= 0 else None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v - 1 if v > 0 else None
