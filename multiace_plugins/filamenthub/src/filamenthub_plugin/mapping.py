# License: GPL-3.0
"""Map a FilamentHub spool record to a multiACE slot-override payload.

`subtype` is mapped from the spool `name` (the most informative variant we
have); change here if FilamentHub later exposes a dedicated SKU field.
"""
from __future__ import annotations

import re

_HEX6_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def normalize_color(color: str | None) -> str:
    if not color:
        return ""
    color = color.strip()
    if color.startswith("#"):
        color = color[1:]
    return f"#{color}" if _HEX6_RE.match(color) else ""


def spool_to_override(spool: dict, ace: int, slot: int) -> dict:
    return {
        "ace": ace,
        "slot": slot,
        "material": spool.get("material") or "",
        "brand": spool.get("vendor") or "",
        "subtype": spool.get("name") or "",
        "color": normalize_color(spool.get("color")),
    }


def ace_state_row_to_override(row: dict, brand: str) -> dict:
    """Map one FilamentHub ace-state slot row to a multiACE slot-override payload.

    ``brand`` is passed in (the ace-state row has no vendor); callers enrich it
    from the spool list. ``name`` is the most informative subtype we have.
    """
    return {
        "ace": int(row["ace"]),
        "slot": int(row["slot"]),
        "material": row.get("material") or "",
        "brand": brand or "",
        "subtype": row.get("name") or "",
        "color": normalize_color(row.get("color")),
    }
