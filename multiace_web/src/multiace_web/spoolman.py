"""Async client for Spoolman (proxied through FilamentHub's nginx).

Returns spool bindings grouped by (ACE, slot) for the configured printer.
Treats spools whose `extra.filamenthub.location.ace` is missing as ace=0
so single-ACE installs and pre-migration spools render correctly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("multiace.spoolman")


@dataclass
class SpoolBinding:
    spool_id: int
    name: str | None
    material: str | None
    color: str | None
    weight_remaining_g: float | None


class SpoolmanClient:
    def __init__(self, base_url: str, printer_id: str, timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._printer_id = printer_id
        self._timeout = timeout_s

    async def list_all_bindings(self) -> dict[int, dict[int, SpoolBinding]]:
        """Returns {ace: {slot: SpoolBinding}} for spools bound to this printer.
        Empty dict on timeout, network error, or non-2xx response."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base}/api/v1/spool")
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Spoolman unreachable: %s", e)
            return {}
        if resp.status_code >= 400:
            log.warning("Spoolman returned %d", resp.status_code)
            return {}

        try:
            spools = resp.json()
        except ValueError:
            log.warning("Spoolman returned non-JSON")
            return {}

        out: dict[int, dict[int, SpoolBinding]] = {}
        for sp in spools:
            try:
                fh_raw = (sp.get("extra") or {}).get("filamenthub")
                if not fh_raw:
                    continue
                fh = json.loads(fh_raw) if isinstance(fh_raw, str) else fh_raw
                loc = fh.get("location") or {}
                if loc.get("printer") != self._printer_id:
                    continue
                ace = int(loc.get("ace", 0))
                slot = int(loc["slot"])
            except (KeyError, ValueError, TypeError) as e:
                log.debug("Skipping malformed spool extra: %s", e)
                continue

            fil = sp.get("filament") or {}
            binding = SpoolBinding(
                spool_id=int(sp["id"]),
                name=fil.get("name"),
                material=fil.get("material"),
                color=fil.get("color_hex"),
                weight_remaining_g=sp.get("remaining_weight"),
            )
            out.setdefault(ace, {})[slot] = binding
        return out
