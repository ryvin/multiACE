# License: GPL-3.0
"""Client for FilamentHub's ACE-state read seam (Phase 3 → Phase 4 puller).

Reads GET {base_url}?printer=<id> — FilamentHub's authoritative desired ACE
slot→spool state, one priority-resolved winner per (ace, slot) plus losing
claims under ``disputed``. See ``FilamentHub/scripts/sentinel/ace_state.py``
for the server side (do not import it — different repo/process).
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("filamenthub.ace_state")

# Must match FilamentHub scripts/sentinel/ace_state.py:ACE_STATE_SCHEMA.
EXPECTED_ACE_STATE_SCHEMA = 1


class AceStateError(Exception):
    """Base for ace-state fetch failures. ``str(e)`` is operator-facing."""


class AceStateSeamDisabled(AceStateError):
    """503 — the FilamentHub watcher didn't wire the ace-state provider."""


class AceStateProviderError(AceStateError):
    """502 — the provider raised while building state."""


class AceStateBadRequest(AceStateError):
    """400 — bad/missing printer parameter."""


class AceStateUnreachable(AceStateError):
    """Network/timeout/transport failure reaching FilamentHub."""


class AceStateClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._url = base_url
        self._timeout = timeout_s

    async def get_ace_state(self, printer_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.get(self._url, params={"printer": printer_id})
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            raise AceStateUnreachable(f"FilamentHub ace-state unreachable: {e}") from e

        if resp.status_code == 503:
            raise AceStateSeamDisabled(
                "FilamentHub ace-state not enabled (watcher provider unwired)")
        if resp.status_code == 502:
            raise AceStateProviderError("FilamentHub ace-state provider error")
        if resp.status_code == 400:
            raise AceStateBadRequest("FilamentHub rejected the ace-state request (400)")
        if resp.status_code >= 400:
            raise AceStateError(f"FilamentHub ace-state returned {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as e:
            raise AceStateError("FilamentHub ace-state returned non-JSON") from e

        schema = body.get("schema")
        if schema != EXPECTED_ACE_STATE_SCHEMA:
            log.warning("ace-state schema %r != expected %d; parsing best-effort",
                        schema, EXPECTED_ACE_STATE_SCHEMA)
        body.setdefault("slots", [])
        body.setdefault("disputed", [])
        return body
