# License: GPL-3.0
"""Async client for the local multiACE web console — read-only state lookup.

Used only to fetch `swap_in_progress` (so the FSM can skip auto-triggering
mid-swap) and, when the Moonraker `ace` object doesn't report device_count,
a fallback source for it. Never writes anything to multiACE.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("autodry.multiace_client")


class MultiAceClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def get_state(self) -> dict[str, Any]:
        """GET /api/state. Returns {} on any failure — this is a soft
        dependency; autodry should keep running (minus the swap guard) even
        if multiACE's web console is briefly unreachable."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self._base}/api/state")
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.debug("multiACE /api/state unreachable: %s", e)
            return {}
