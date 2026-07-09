# License: GPL-3.0
"""Pure planner: turn FilamentHub's desired ace-state into apply/clear actions.

No I/O — takes plain data, returns the two action lists the endpoint executes.
Kept pure so the scoping rule (only clear on FilamentHub-known ACEs) is unit-
testable without mocking any HTTP.
"""
from __future__ import annotations

from typing import Iterable

from .mapping import ace_state_row_to_override


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
