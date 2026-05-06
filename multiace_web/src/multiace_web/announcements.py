"""Async client for Moonraker's [server_announcements] API.

Used by the auto-dry FSM to surface user-relevant transitions as native
toasts in Mainsail/Fluidd's notification bell. Errors are logged and
swallowed — auto-dry keeps running even if the announcements API is
unreachable. We are not in the safety-critical path.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("multiace.announcements")


class AnnouncementsClient:
    def __init__(self, http: httpx.AsyncClient, moonraker_base: str) -> None:
        self._http = http
        self._base = moonraker_base.rstrip("/")

    async def post(
        self,
        *,
        title: str,
        description: str,
        entry_type: str = "info",      # "info" | "warning"
        priority: str = "normal",      # "low" | "normal" | "high"
    ) -> str | None:
        """POST a new announcement; return entry_id, or None on any failure."""
        url = f"{self._base}/server/announcements/post"
        body: dict[str, Any] = {
            "title": title,
            "description": description,
            "entry_type": entry_type,
            "priority": priority,
        }
        try:
            r = await self._http.post(url, json=body, timeout=5.0)
            r.raise_for_status()
            data = r.json() or {}
            entry_id = (data.get("result") or {}).get("entry_id")
            if entry_id:
                return str(entry_id)
            log.warning("announcement post returned no entry_id: %s", data)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("announcement post failed (%s): %s", type(exc).__name__, exc)
            return None

    async def dismiss(self, entry_id: str) -> bool:
        """Dismiss an announcement by id. Returns True iff Moonraker accepted."""
        url = f"{self._base}/server/announcements/dismiss"
        try:
            r = await self._http.post(url, params={"entry_id": entry_id}, timeout=5.0)
            r.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("announcement dismiss(%s) failed: %s", entry_id, exc)
            return False
