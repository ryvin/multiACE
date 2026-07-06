# License: GPL-3.0
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


def _decode_fh(raw) -> dict:
    """Decode a Spoolman ``extra.filamenthub`` value into a dict.

    The field is a *text* extra field whose value is a JSON-encoded string —
    and FilamentHub stores it double-encoded (a JSON string whose content is
    the object's JSON text). Peel up to a few layers until a dict is reached;
    return {} if it can't be decoded to one.
    """
    val = raw
    for _ in range(4):
        if isinstance(val, dict):
            return val
        if not isinstance(val, str):
            return {}
        try:
            val = json.loads(val)
        except (ValueError, TypeError):
            return {}
    return val if isinstance(val, dict) else {}


def _encode_fh(fh: dict) -> str:
    """Encode a filamenthub dict the way Spoolman's text extra field expects:
    a JSON string whose content is the object's JSON text (double-encoded), so
    a scan and an assign produce byte-compatible values."""
    return json.dumps(json.dumps(fh))


@dataclass
class SpoolBinding:
    spool_id: int
    name: str | None
    material: str | None
    color: str | None
    weight_remaining_g: float | None
    vendor: str | None = None


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
                fh = _decode_fh(fh_raw)
                loc = fh.get("location")
                if not isinstance(loc, dict) or loc.get("printer") != self._printer_id:
                    continue
                ace = int(loc.get("ace", 0))
                slot = int(loc["slot"])
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                log.debug("Skipping malformed spool extra: %s", e)
                continue

            fil = sp.get("filament") or {}
            ven = fil.get("vendor")
            vendor = ven.get("name") if isinstance(ven, dict) else ven
            binding = SpoolBinding(
                spool_id=int(sp["id"]),
                name=fil.get("name"),
                material=fil.get("material"),
                color=fil.get("color_hex"),
                weight_remaining_g=sp.get("remaining_weight"),
                vendor=vendor,
            )
            out.setdefault(ace, {})[slot] = binding
        return out

    async def list_spools(self) -> list[dict]:
        """Return all (non-archived) spools for the FilamentHub picker, each as
        ``{spool_id, name, material, color, vendor, weight_remaining_g,
        location}`` where ``location`` is ``{ace, slot}`` if the spool is placed
        on *this* printer, else ``None``."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base}/api/v1/spool")
                resp.raise_for_status()
                spools = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("list_spools failed: %s", e)
            return []

        out: list[dict] = []
        for sp in spools:
            if sp.get("archived"):
                continue
            fil = sp.get("filament") or {}
            ven = fil.get("vendor")
            vendor = ven.get("name") if isinstance(ven, dict) else ven
            location = None
            fh_raw = (sp.get("extra") or {}).get("filamenthub")
            if fh_raw:
                try:
                    fh = _decode_fh(fh_raw)
                    loc = fh.get("location")
                    if (isinstance(loc, dict) and loc.get("printer") == self._printer_id
                            and loc.get("slot") is not None):
                        location = {"ace": int(loc.get("ace", 0)), "slot": int(loc["slot"])}
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
            out.append({
                "spool_id": int(sp["id"]),
                "name": fil.get("name"),
                "material": fil.get("material"),
                "color": fil.get("color_hex"),
                "vendor": vendor,
                "weight_remaining_g": sp.get("remaining_weight"),
                "location": location,
            })
        return out

    async def _set_location(self, client, spool_id, location) -> None:
        """GET a spool, set its ``extra.filamenthub`` location (preserving the
        other fields like ``schema``/``td``), and PATCH it back. ``location``
        is a {printer, ace, slot} dict to bind, or None to clear."""
        resp = await client.get(f"{self._base}/api/v1/spool/{spool_id}")
        resp.raise_for_status()
        fh = _decode_fh((resp.json().get("extra") or {}).get("filamenthub"))
        fh.setdefault("schema", 1)
        fh["location"] = location
        patch = {"extra": {"filamenthub": _encode_fh(fh)}}
        presp = await client.patch(f"{self._base}/api/v1/spool/{spool_id}", json=patch)
        presp.raise_for_status()

    async def assign_spool(self, spool_id: int, ace: int, slot: int) -> dict:
        """Bind a spool to (ace, slot) on this printer — the same effect as an
        RFID scan. Returns the written location dict."""
        location = {"printer": self._printer_id, "ace": int(ace), "slot": int(slot)}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            await self._set_location(client, spool_id, location)
        return location

    async def unassign_slot(self, ace: int, slot: int):
        """Make a slot blank: clear the location of whatever spool is currently
        bound to (ace, slot) on this printer. Returns the cleared spool_id, or
        None if the slot was already empty."""
        want = {"ace": int(ace), "slot": int(slot)}
        target = next((s for s in await self.list_spools() if s["location"] == want), None)
        if target is None:
            return None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            await self._set_location(client, target["spool_id"], None)
        return target["spool_id"]