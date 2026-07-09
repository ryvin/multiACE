# License: GPL-3.0
"""Unit tests for the pure reconcile planner."""
from filamenthub_plugin.reconcile import plan_reconcile


def _row(ace, slot, spool_id, material="PLA", color="#ffffff", name="n"):
    return {"ace": ace, "slot": slot, "spool_id": spool_id,
            "material": material, "color": color, "name": name}


def test_apply_maps_winners_with_brand():
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, [], {42: "PolyTerra"})
    assert to_apply == [{"ace": 0, "slot": 0, "material": "PLA",
                         "brand": "PolyTerra", "subtype": "n", "color": "#ffffff"}]
    assert to_clear == []


def test_clears_vacated_slot_on_known_ace():
    # ACE 0 has a winner at slot 0; slot 1 is currently labeled but no longer a
    # winner -> cleared (same ACE = FilamentHub-known).
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == [(0, 1)]


def test_does_not_clear_unknown_ace():
    # ACE 1 is not in the winner set -> its labels are left untouched.
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "1_3"], {})
    assert to_clear == []


def test_does_not_clear_a_slot_that_is_still_a_winner():
    winners = [_row(0, 0, 42), _row(0, 1, 43)]
    _, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == []


def test_skips_winner_with_none_slot():
    winners = [{"ace": 0, "slot": None, "spool_id": 1,
                "material": "PLA", "color": "#fff", "name": "n"}]
    to_apply, to_clear = plan_reconcile(winners, [], {})
    assert to_apply == []


def test_ignores_malformed_override_keys():
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "bogus", "0_x"], {})
    assert to_clear == []
