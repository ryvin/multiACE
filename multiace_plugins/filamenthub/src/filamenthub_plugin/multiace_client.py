# License: GPL-3.0
"""Async client for the local multiACE (decay71) web backend."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("filamenthub.multiace_client")

# decay71 ace.py registers this; it reloads slot_overrides.json into Klipper
# and pushes the labels to the display. The web console's own UI enqueues it
# after every override edit (frontend app.js); we do the same.
REFRESH_OVERRIDES_SCRIPT = "MULTIACE_REFRESH_OVERRIDES"


class MultiAceClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def set_override(self, ace: int, slot: int, material: str,
                           brand: str, subtype: str, color: str) -> dict:
        payload = {"ace": ace, "slot": slot, "material": material,
                   "brand": brand, "subtype": subtype, "color": color}
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(f"{self._base}/api/slot-override", json=payload)
            r.raise_for_status()
            body = r.json()
        await self.refresh_overrides()
        return body

    async def clear_override(self, ace: int, slot: int) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.delete(f"{self._base}/api/slot-override/{ace}/{slot}")
            r.raise_for_status()
            body = r.json()
        await self.refresh_overrides()
        return body

    async def refresh_overrides(self) -> bool:
        """Best-effort ``MULTIACE_REFRESH_OVERRIDES`` via decay71
        ``POST /api/plugin-api/gcode``. The override is already persisted by
        the time this runs, so a failure (printer busy, Moonraker down) is
        logged and swallowed; the label still lands on the next refresh."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(f"{self._base}/api/plugin-api/gcode",
                                 json={"script": REFRESH_OVERRIDES_SCRIPT})
                r.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001 - best-effort, never fail the write
            log.warning("override refresh (%s) failed: %s",
                        REFRESH_OVERRIDES_SCRIPT, e)
            return False

    async def get_materials(self) -> dict:
        """decay71 ``GET /api/materials`` -> its ``db`` field
        (``{material: {vendor: [subtypes]}}``). ``{}`` on any failure."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self._base}/api/materials")
                r.raise_for_status()
                db = r.json().get("db")
        except Exception as e:  # noqa: BLE001 - mapping degrades to no-DB rules
            log.warning("multiACE materials DB fetch failed: %s", e)
            return {}
        if not isinstance(db, dict):
            log.warning("multiACE materials DB malformed: %r", type(db).__name__)
            return {}
        return db

    async def list_overrides(self) -> dict[str, dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base}/api/slot-override")
            r.raise_for_status()
            return r.json().get("overrides", {})

    async def get_state(self) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base}/api/plugin-api/state")
            r.raise_for_status()
            return r.json()
