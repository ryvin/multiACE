# License: GPL-3.0
"""Spoolman filament *profiles* (via FilamentHub's proxy).

A profile is a Spoolman ``filament`` (vendor + material + colour + temps), as
opposed to a ``spool`` (a physical instance of one). ``create_spool_from_filament``
mirrors FilamentHub ``public/app.js:cloneSpoolWithVirtualTag`` so a spool minted
here is indistinguishable from one cloned in the FilamentHub UI.
"""
from __future__ import annotations

import json
import secrets

import httpx

from .mapping import normalize_color

DEFAULT_TIMEOUT_S = 3.0
DEFAULT_SPOOL_WEIGHT_G = 1000
VIRTUAL_UID_PREFIX = "VIRT-"


async def fetch_filaments(base_url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[dict]:
    """Raw Spoolman filament records. Raises ``httpx.HTTPError`` on failure."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/api/v1/filament")
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


def filament_summary(fil: dict) -> dict:
    """Map one raw Spoolman filament to the picker's profile shape."""
    ven = fil.get("vendor")
    vendor = ven.get("name") if isinstance(ven, dict) else ven
    return {
        "filament_id": int(fil["id"]),
        "name": fil.get("name") or "",
        "vendor": vendor or "",
        "material": fil.get("material") or "",
        "color": normalize_color(fil.get("color_hex")),
        "extruder_temp": fil.get("settings_extruder_temp"),
        "bed_temp": fil.get("settings_bed_temp"),
    }


async def list_filaments(base_url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[dict]:
    """All filament profiles, sorted by vendor then name (case-insensitive)."""
    raw = await fetch_filaments(base_url, timeout_s)
    out = [filament_summary(f) for f in raw if isinstance(f, dict) and "id" in f]
    return sorted(out, key=lambda f: (f["vendor"].lower(), f["name"].lower()))


def make_virtual_uid() -> str:
    """``VIRT-`` + 8 uppercase hex chars, like FilamentHub ``makeVirtualUid``."""
    return f"{VIRTUAL_UID_PREFIX}{secrets.token_hex(4).upper()}"


async def create_spool_from_filament(base_url: str, filament: dict,
                                     timeout_s: float = DEFAULT_TIMEOUT_S) -> int:
    """POST a new full spool of ``filament`` carrying a virtual RFID tag.

    ``filament`` is a raw Spoolman filament (``id``) or a profile summary
    (``filament_id``). Returns the new spool id. Raises ``httpx.HTTPError``.
    """
    filament_id = filament.get("id", filament.get("filament_id"))
    if filament_id is None:
        raise ValueError("filament has no id")
    weight = filament.get("weight") or DEFAULT_SPOOL_WEIGHT_G
    payload = {
        "filament_id": int(filament_id),
        "remaining_weight": weight,
        "initial_weight": weight,
        "extra": {
            # Text extra fields: card_uid single-encoded (scalar),
            # filamenthub double-encoded (structured) — as FilamentHub does.
            "card_uid": json.dumps(make_virtual_uid()),
            "filamenthub": json.dumps(json.dumps({"schema": 1, "virtual_tag": True})),
        },
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{base_url.rstrip('/')}/api/v1/spool", json=payload)
        resp.raise_for_status()
        return int(resp.json()["id"])
