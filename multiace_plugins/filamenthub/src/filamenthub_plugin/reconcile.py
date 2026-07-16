# License: GPL-3.0
"""Pure planner: turn FilamentHub's desired ace-state into apply/clear actions.

No I/O — takes plain data, returns the two action lists the endpoint executes.
Kept pure so the scoping rule (only clear on FilamentHub-known ACEs) is unit-
testable without mocking any HTTP.
"""
from __future__ import annotations

from typing import Iterable

from .mapping import ace_state_row_to_override, normalize_color


def _parse_key(key: str) -> tuple[int, int] | None:
    """Parse a decay71 override key ``"<ace>_<slot>"`` -> (ace, slot), or None."""
    parts = key.split("_")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def plan_reconcile(
    winners: list[dict],
    current_override_keys: Iterable[str],
    brand_by_spool_id: dict[int, str],
    disputed_keys: set[tuple[int, int]] | None = None,
) -> tuple[list[dict], list[tuple[int, int]]]:
    disputed_keys = disputed_keys or set()
    desired: set[tuple[int, int]] = set()
    to_apply: list[dict] = []
    for row in winners:
        if row.get("slot") is None:
            continue
        brand = brand_by_spool_id.get(row.get("spool_id"), "")
        payload = ace_state_row_to_override(row, brand)
        desired.add((payload["ace"], payload["slot"]))
        to_apply.append(payload)

    known_aces = {ace for ace, _ in desired}
    to_clear: list[tuple[int, int]] = []
    for key in current_override_keys:
        parsed = _parse_key(key)
        if parsed is None:
            continue
        ace, slot = parsed
        # A disputed slot's winner can transiently drop out of `winners`; never
        # delete a contested label on that account. Disputes are shown, never
        # written — and clearing an override IS a write.
        if (ace, slot) in disputed_keys:
            continue
        if ace in known_aces and (ace, slot) not in desired:
            to_clear.append((ace, slot))
    to_clear.sort()
    return to_apply, to_clear


def reconcile_slots(desired: dict[str, dict],
                    observed_aces: list[dict]) -> list[dict]:
    observed: dict[tuple[int, int], dict] = {}
    for ace in observed_aces or []:
        ai = int(ace.get("idx"))
        for s in ace.get("slots") or []:
            observed[(ai, int(s.get("idx")))] = s
    keys = set(observed.keys())
    for k in desired:
        parsed = _parse_key(k)
        if parsed is not None:
            keys.add(parsed)
    rows: list[dict] = []
    for ace, slot in sorted(keys):
        d = desired.get(f"{ace}_{slot}")
        o = observed.get((ace, slot))
        occupied = bool(o) and o.get("state") != "empty"
        rfid_identity = bool(o) and o.get("rfid") == 1 and bool(
            (o.get("material") or "") or (o.get("color") or ""))
        if occupied and d:
            if rfid_identity:
                mat_ok = (o.get("material") or "") == (d.get("material") or "")
                col_ok = normalize_color(o.get("color")) == normalize_color(d.get("color"))
                state = "VERIFIED" if (mat_ok and col_ok) else "CONFLICT"
            else:
                state = "ASSERTED"
        elif occupied:
            state = "UNKNOWN_LOADED"
        elif d:
            state = "EXPECTED_NOT_LOADED"
        else:
            state = "EMPTY"
        if d and state in ("VERIFIED", "ASSERTED", "CONFLICT", "EXPECTED_NOT_LOADED"):
            name = d.get("subtype") or ""
            material = d.get("material") or ""
            color = normalize_color(d.get("color"))
        elif state == "UNKNOWN_LOADED":
            name = ""
            material = (o.get("material") or "") if o else ""
            color = normalize_color(o.get("color")) if o else ""
        else:
            name = material = color = ""
        rows.append({
            "ace": ace, "slot": slot, "recon_state": state,
            "display_name": name, "display_material": material, "display_color": color,
            "desired": d,
            "observed": ({"state": o.get("state"), "material": o.get("material"),
                          "color": o.get("color"), "rfid": o.get("rfid")} if o else None),
        })
    return rows
