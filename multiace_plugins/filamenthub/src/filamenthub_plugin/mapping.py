# License: GPL-3.0
"""Map a FilamentHub spool record to a multiACE slot-override payload.

`subtype` is mapped from the spool `name` (the most informative variant we
have); change here if FilamentHub later exposes a dedicated SKU field.
"""
from __future__ import annotations


def normalize_color(color: str | None) -> str:
    if not color:
        return ""
    color = color.strip()
    return color if color.startswith("#") else f"#{color}"


def spool_to_override(spool: dict, ace: int, slot: int) -> dict:
    return {
        "ace": ace,
        "slot": slot,
        "material": spool.get("material") or "",
        "brand": spool.get("vendor") or "",
        "subtype": spool.get("name") or "",
        "color": normalize_color(spool.get("color")),
    }
