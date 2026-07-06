# License: GPL-3.0
"""Async client for the local multiACE web slot-override endpoint."""
from __future__ import annotations
import httpx


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
            return r.json()

    async def clear_override(self, ace: int, slot: int) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.delete(f"{self._base}/api/slot-override/{ace}/{slot}")
            r.raise_for_status()
            return r.json()
