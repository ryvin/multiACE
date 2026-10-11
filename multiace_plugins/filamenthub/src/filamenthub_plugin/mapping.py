# License: GPL-3.0
"""Map a FilamentHub spool record to a multiACE slot-override payload.

Material and sub-type follow decay71's own Spoolman import rules
(``multiace/web/backend/main.py``: ``_spoolman_material_split``,
``_spoolman_subtype_guess``, ``_known_subtypes_for``), ported here so a slot
labelled by this plugin reads exactly like one decay71 adopted itself.

``materials_db`` is the ``db`` field of decay71 ``GET /api/materials``:
``{material: {vendor: [subtypes]}}``. It is optional everywhere; without it
the material passes through unsplit and the sub-type falls back to the
"text after the material token" rule (usually "").
"""
from __future__ import annotations

import re

_HEX6_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
_FAMILY_SEPARATORS = "+-_"
_SUBTYPE_FALLBACK_MAX = 32

MaterialsDb = dict[str, dict[str, list[str]]]


def normalize_color(color: str | None) -> str:
    if not color:
        return ""
    color = color.strip()
    if color.startswith("#"):
        color = color[1:]
    return f"#{color}" if _HEX6_RE.match(color) else ""


def known_subtypes_for(material: str, materials_db: MaterialsDb | None) -> list[str]:
    """Sub-types the DB knows for ``material``, longest first. Port of
    decay71 ``_known_subtypes_for``: case-insensitive material lookup, union
    over vendors."""
    if not materials_db or not material:
        return []
    want = material.strip().lower()
    entry = None
    for key, value in materials_db.items():
        if str(key).strip().lower() == want:
            entry = value
            break
    if not isinstance(entry, dict):
        return []
    subs: set[str] = set()
    for vendor_subs in entry.values():
        if isinstance(vendor_subs, (list, tuple)):
            subs.update(str(s).strip() for s in vendor_subs if str(s).strip())
    return sorted(subs, key=len, reverse=True)


def material_split(material: str | None,
                   materials_db: MaterialsDb | None = None) -> tuple[str, str]:
    """Port of decay71 ``_spoolman_material_split``: a known base glued to one
    of ``+-_`` is split into (base, family) -- ``'PLA+'`` -> ``('PLA', 'Plus')``,
    ``'PLA-HF'`` -> ``('PLA', 'HF')``. A material that IS a DB type keeps the
    DB spelling; anything else (or no DB) passes through unchanged."""
    m = (material or "").strip()
    known = {str(k).strip().upper(): str(k).strip() for k in (materials_db or {})}
    if not m or not known:
        return (m, "")
    upper = m.upper()
    if upper in known:
        return (known[upper], "")
    for k in sorted(known, key=len, reverse=True):
        if len(upper) > len(k) and upper.startswith(k) and upper[len(k)] in _FAMILY_SEPARATORS:
            rest = m[len(k):].strip("+-_ ")
            if m[len(k)] == "+" and not rest:
                rest = "Plus"
            return (known[k], rest)
    return (m, "")


def subtype_guess(name: str | None, material: str | None,
                  materials_db: MaterialsDb | None = None) -> str:
    """Port of decay71 ``_spoolman_subtype_guess``. Known DB sub-types are
    matched anywhere in the name as whole words, longest first, returning the
    DB spelling. Fallback: the text after the material token in the name,
    capped at 32 chars; "" when the name does not contain the material."""
    n = (name or "").strip()
    m = (material or "").strip()
    if not n:
        return ""
    for sub in known_subtypes_for(m, materials_db):
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(sub), n, re.IGNORECASE):
            return sub
    if not m:
        return ""
    low, mlow = n.lower(), m.lower()
    if mlow not in low:
        return ""
    rest = n[low.index(mlow) + len(m):].strip(" -_/")
    return rest[:_SUBTYPE_FALLBACK_MAX]


def _material_and_subtype(raw_material: str | None, name: str | None,
                          materials_db: MaterialsDb | None) -> tuple[str, str]:
    """decay71 order (main.py spool import): split the material, guess the
    sub-type from the name against the SPLIT material, else use the family."""
    material, family = material_split(raw_material, materials_db)
    return material, (subtype_guess(name, material, materials_db) or family)


def spool_to_override(spool: dict, ace: int, slot: int,
                      materials_db: MaterialsDb | None = None) -> dict:
    material, subtype = _material_and_subtype(
        spool.get("material"), spool.get("name"), materials_db)
    return {
        "ace": ace,
        "slot": slot,
        "material": material,
        "brand": spool.get("vendor") or "",
        "subtype": subtype,
        "color": normalize_color(spool.get("color")),
    }


def ace_state_row_to_override(row: dict, brand: str,
                              materials_db: MaterialsDb | None = None) -> dict:
    """Map one FilamentHub ace-state slot row to a multiACE slot-override payload.

    ``brand`` is passed in (the ace-state row has no vendor); callers enrich it
    from the spool list.
    """
    material, subtype = _material_and_subtype(
        row.get("material"), row.get("name"), materials_db)
    return {
        "ace": int(row["ace"]),
        "slot": int(row["slot"]),
        "material": material,
        "brand": brand or "",
        "subtype": subtype,
        "color": normalize_color(row.get("color")),
    }
